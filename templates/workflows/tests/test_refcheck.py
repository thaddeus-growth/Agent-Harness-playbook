"""refcheck.py: only a commit that is really on its branch, on top of its base, is pushed.

Each test builds a throw-away git repo with a base commit and a branch `feat` checked out in its
own worktree, then states a pair: the bad case is refused AND the good case is accepted, so no
test passes on a checker that refuses everything.
"""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402

sys.path.insert(0, _t.WORKFLOWS)
import refcheck  # noqa: E402

# the tests' git must not read the user's config or hooks, nor sign or template anything
os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                  GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                  GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def g(where, *args):
    return subprocess.run(["git", "-C", where, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(where, name):
    """One new file `name` committed in `where`; returns the new head."""
    with open(os.path.join(where, name), "w") as f:
        f.write(name + "\n")
    g(where, "add", name)
    g(where, "commit", "-q", "-m", name)
    return g(where, "rev-parse", "HEAD")


@contextmanager
def repo():
    """A repo with a base commit on main and branch feat (one commit) in its own worktree."""
    with _t.tmpdir() as d:
        r, wt = os.path.join(d, "repo"), os.path.join(d, "wt-feat")
        os.makedirs(r)
        g(r, "init", "-q", "-b", "main")
        base = commit(r, "base.txt")
        g(r, "worktree", "add", "-q", "-b", "feat", wt, base)
        head = commit(wt, "feat.txt")
        yield SimpleNamespace(dir=d, repo=r, wt=wt, base=base, head=head)


def item(ns, **over):
    it = {"key": "k", "branch": "feat", "base": ns.base, "head_sha": ns.head,
          "worktree": ns.wt, "status": "ready"}
    it.update(over)
    return it


def check(ns, *items):
    code, out = refcheck.run({"repo": ns.repo, "items": list(items)}, None, "origin")
    assert code == (0 if out["ok"] else 2), (code, out)
    return out


def refused(out, code):
    return [r["key"] for r in out["refused"] if r["code"] == code]


# ---------------------------------------------------------------- the pushed commit --

def test_a_ready_item_pushes_the_checked_commit_id_not_the_branch_name():
    with repo() as ns:
        out = check(ns, item(ns))
        assert out["ok"] and not out["refused"], out
        (p,) = out["push"]
        assert p["head"] == ns.head
        assert p["command"].endswith(f"push origin {ns.head}:refs/heads/feat"), p


def test_a_moved_ref_is_refused_and_the_matching_one_accepted():
    with repo() as ns:
        later = commit(ns.wt, "later.txt")          # the branch moved after the report
        out = check(ns, item(ns, head_sha=ns.head))
        assert refused(out, "ref_moved") == ["k"] and not out["push"], out
        out = check(ns, item(ns, head_sha=later))
        assert out["ok"] and out["push"][0]["head"] == later, out


def test_a_short_head_id_is_resolved_before_the_comparison():
    with repo() as ns:
        assert check(ns, item(ns, head_sha=ns.head[:10]))["ok"]
        out = check(ns, item(ns, head_sha=ns.base[:10]))
        assert refused(out, "ref_moved") == ["k"], out


def test_a_fix_left_on_a_detached_head_is_refused_until_the_branch_is_moved():
    """The fixer could not write in the builder's worktree, fixed in its own detached one and
    reported that commit; the branch still points at the unreviewed one."""
    with repo() as ns:
        fx = os.path.join(ns.dir, "fx")
        g(ns.repo, "worktree", "add", "-q", "--detach", fx, ns.head)
        fixed = commit(fx, "fix.txt")
        out = check(ns, item(ns, head_sha=fixed))
        assert refused(out, "ref_moved") == ["k"] and not out["push"], out
        # the compare-and-swap refuses a stale old value, then moves the branch
        bad = subprocess.run(["git", "-C", ns.repo, "update-ref", "refs/heads/feat", fixed, ns.base],
                             capture_output=True, text=True)
        assert bad.returncode != 0 and g(ns.repo, "rev-parse", "feat") == ns.head
        g(ns.repo, "update-ref", "refs/heads/feat", fixed, ns.head)
        g(ns.wt, "checkout", "-q", "-f")            # the builder's worktree follows its branch
        out = check(ns, item(ns, head_sha=fixed))
        assert out["ok"] and out["push"][0]["head"] == fixed, out


# ---------------------------------------------------------------- base and stack --

def test_a_base_that_is_not_an_ancestor_is_refused_and_the_real_base_accepted():
    with repo() as ns:
        g(ns.repo, "checkout", "-q", "-b", "other", ns.base)
        elsewhere = commit(ns.repo, "other.txt")
        out = check(ns, item(ns, base=elsewhere))
        assert refused(out, "base_not_ancestor") == ["k"], out
        assert check(ns, item(ns, base=ns.base))["ok"]


def test_a_stack_step_not_carried_up_after_a_fix_below_it_is_refused():
    """feat2 stacks on feat. A later fix lands on feat; until feat2 is rebased onto it, feat2's
    recorded base (feat's new head) is not below feat2's head."""
    with repo() as ns:
        wt2 = os.path.join(ns.dir, "wt-feat2")
        g(ns.repo, "worktree", "add", "-q", "-b", "feat2", wt2, ns.head)
        head2 = commit(wt2, "step2.txt")
        fixed = commit(ns.wt, "fix-below.txt")
        step2 = item(ns, key="k2", branch="feat2", base=fixed, head_sha=head2, worktree=wt2)
        out = check(ns, item(ns, head_sha=fixed), step2)
        assert refused(out, "base_not_ancestor") == ["k2"], out
        assert [p["key"] for p in out["push"]] == ["k"], out
        g(wt2, "rebase", "-q", "feat")               # carry the fix up
        carried = g(ns.repo, "rev-parse", "feat2")
        out = check(ns, item(ns, head_sha=fixed), step2 | {"head_sha": carried})
        assert out["ok"] and [p["key"] for p in out["push"]] == ["k", "k2"], out


def test_a_head_equal_to_its_base_is_refused():
    with repo() as ns:
        g(ns.repo, "branch", "empty", ns.base)
        out = check(ns, item(ns, branch="empty", head_sha=ns.base, worktree=""))
        assert refused(out, "no_commits") == ["k"], out
        assert check(ns, item(ns))["ok"]


# ---------------------------------------------------------------- the worktree --

def test_uncommitted_or_untracked_work_in_the_worktree_is_refused():
    with repo() as ns:
        with open(os.path.join(ns.wt, "forgotten.txt"), "w") as f:
            f.write("never committed\n")
        assert refused(check(ns, item(ns)), "worktree_dirty") == ["k"]
        os.remove(os.path.join(ns.wt, "forgotten.txt"))
        with open(os.path.join(ns.wt, "feat.txt"), "a") as f:
            f.write("edited, not committed\n")
        assert refused(check(ns, item(ns)), "worktree_dirty") == ["k"]
        g(ns.wt, "checkout", "-q", "--", "feat.txt")
        assert check(ns, item(ns))["ok"]


def test_a_removed_worktree_is_not_a_refusal():
    with repo() as ns:
        g(ns.repo, "worktree", "remove", ns.wt)
        assert check(ns, item(ns))["ok"]


# ---------------------------------------------------------------- what is never pushed --

def test_items_that_are_not_ready_are_listed_and_never_pushed():
    with repo() as ns:
        others = [item(ns, key=s, branch=f"b-{s}", status=s)
                  for s in ("not_ready", "stopped_for_owner", "skipped", "no_result", "no_commit", "not_run")]
        out = check(ns, item(ns), *others)
        assert out["ok"] and [p["key"] for p in out["push"]] == ["k"], out
        assert sorted(s["key"] for s in out["skipped"]) == sorted(o["key"] for o in others), out


def test_two_ready_items_on_one_branch_are_both_refused():
    with repo() as ns:
        out = check(ns, item(ns, key="a"), item(ns, key="b"))
        assert sorted(refused(out, "duplicate_branch")) == ["a", "b"] and not out["push"], out
        assert check(ns, item(ns, key="a"))["ok"]


def test_option_like_or_unknown_names_are_refused():
    with repo() as ns:
        cases = {"bad_branch": {"branch": "--all"}, "no_branch": {"branch": "nope"},
                 "bad_head": {"head_sha": "--all"}, "unknown_head": {"head_sha": "0" * 40},
                 "unknown_base": {"base": "-x"}}
        for code, over in cases.items():
            out = check(ns, item(ns, **over))
            assert refused(out, code) == ["k"], (code, out)
        assert check(ns, item(ns))["ok"]


# ---------------------------------------------------------------- the command line --

def cli(*args, stdin=None):
    p = subprocess.run([sys.executable, os.path.join(_t.WORKFLOWS, "refcheck.py"), *args],
                       input=stdin, capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout)


def test_the_cli_prints_one_document_and_exits_2_on_any_refusal():
    with repo() as ns:
        path = os.path.join(ns.dir, "result.json")
        with open(path, "w") as f:
            json.dump({"repo": ns.repo, "items": [item(ns)]}, f)
        code, out = cli(path)
        assert code == 0 and out["ok"] and len(out["push"]) == 1, out
        code, out = cli("-", stdin=json.dumps([item(ns, head_sha=ns.base)]))
        assert code == 2 and out["code"] == "bad_request", out      # a bare list names no repo
        code, out = cli("-", "--repo", ns.repo, stdin=json.dumps([item(ns, head_sha=ns.base)]))
        assert code == 2 and refused(out, "ref_moved") == ["k"], out
        code, out = cli("-", stdin="not json")
        assert code == 2 and out == {"ok": False, "code": "bad_request", "message": out["message"]}, out
        code, out = cli("-", "--repo", os.path.join(ns.dir, "missing"), stdin="[]")
        assert code == 2 and out["code"] == "bad_request", out      # not a git checkout


if __name__ == "__main__":
    _t.main(globals())
