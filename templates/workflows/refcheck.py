#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check a build-review-fix-verify result against the real git refs before anything is pushed.

    python3 refcheck.py RESULT.json [--repo DIR] [--remote NAME]

RESULT.json is the workflow's return value, `{base, repo, items}`, or just its `items` list.
Agents report the commits they made; what gets pushed is whatever the branch ref points at. A
fixer that could not write in the builder's worktree once left its fix on a detached HEAD while
the branch kept the unreviewed commit, and reported success. So every item whose status is
`ready` is checked with git, read-only:

    bad_branch          the branch name is not a valid, plain branch name
    no_branch           refs/heads/<branch> does not exist
    bad_head            head_sha is not a hex commit id
    unknown_head        head_sha names no commit (or an ambiguous short one)
    ref_moved           refs/heads/<branch> is not head_sha: the ref moved, or the report is wrong
    unknown_base        the item's base names no commit
    base_not_ancestor   the base is not an ancestor of the head: wrong base, or a stack not carried up
    no_commits          the head is the base
    worktree_dirty      the item's worktree still exists and has uncommitted or untracked files
                        (or is no longer a git worktree)
    duplicate_branch    two ready items name one branch

Other items are listed under `skipped` with their status and never pushed. Each push command
pushes the checked commit id, not the branch name (`git push REMOTE SHA:refs/heads/BRANCH`), so a
ref that moves after the check cannot slip into the push. Stacked items come after the item they
stack on, as in the result.

Output: one JSON document `{ok, push, refused, skipped}`; exit 0 when no ready item was refused,
2 otherwise (and for unreadable input: `{ok: false, code: "bad_request", message}`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

HEX = re.compile(r"[0-9a-f]{7,64}")


def git(repo: str, *args: str) -> tuple[int, str]:
    """(exit code, stripped stdout) of one read-only git command; never raises on a git error."""
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def resolve(repo: str, rev: str) -> str | None:
    """The full commit id `rev` names, or None. A rev that looks like an option is refused."""
    if not rev or rev.startswith("-"):
        return None
    code, out = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    return out if code == 0 and out else None


def check(repo: str, item: dict) -> tuple[str, str] | tuple[None, str]:
    """(refusal code, why) for one ready item, or (None, full head id) when it may be pushed."""
    branch = str(item.get("branch") or "")
    if not branch or branch.startswith("-") or git(repo, "check-ref-format", f"refs/heads/{branch}")[0]:
        return "bad_branch", f"not a plain branch name: {branch!r}"
    ref = resolve(repo, f"refs/heads/{branch}")
    if ref is None:
        return "no_branch", f"refs/heads/{branch} does not exist"
    head = str(item.get("head_sha") or "")
    if not HEX.fullmatch(head):
        return "bad_head", f"head_sha is not a commit id: {head!r}"
    full = resolve(repo, head)
    if full is None:
        return "unknown_head", f"{head} names no single commit"
    if full != ref:
        return "ref_moved", f"refs/heads/{branch} is at {ref}; the verified head was {full}"
    base = resolve(repo, str(item.get("base") or ""))
    if base is None:
        return "unknown_base", f"base {item.get('base')!r} names no commit"
    if base == full:
        return "no_commits", f"{branch} has no commit on top of its base"
    if git(repo, "merge-base", "--is-ancestor", base, full)[0] != 0:
        return "base_not_ancestor", f"base {base} is not an ancestor of {full}"
    wt = str(item.get("worktree") or "")
    if wt and os.path.isdir(wt):
        code, out = git(wt, "status", "--porcelain")
        if code != 0 or out:
            return "worktree_dirty", f"{wt} has uncommitted or untracked files, or is not a worktree"
    return None, full


def run(doc: object, repo: str | None, remote: str) -> tuple[int, dict]:
    """The exit code and the output document for one parsed result."""
    items = doc.get("items") if isinstance(doc, dict) else doc
    repo = repo or (doc.get("repo") if isinstance(doc, dict) else None)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return 2, {"ok": False, "code": "bad_request", "message": "no list of items in the input"}
    if not repo or git(repo, "rev-parse", "--git-dir")[0] != 0:
        return 2, {"ok": False, "code": "bad_request", "message": f"not a git checkout: {repo!r} (pass --repo)"}
    ready = [i for i in items if i.get("status") == "ready"]
    names = [str(i.get("branch")) for i in ready]
    push, refused = [], []
    for item in ready:
        key, branch = item.get("key"), str(item.get("branch"))
        if names.count(branch) > 1:
            refused.append({"key": key, "branch": branch, "code": "duplicate_branch",
                            "why": f"{names.count(branch)} ready items name {branch}"})
            continue
        code, detail = check(repo, item)
        if code:
            refused.append({"key": key, "branch": branch, "code": code, "why": detail})
        else:
            cmd = ["git", "-C", repo, "push", remote, f"{detail}:refs/heads/{branch}"]
            push.append({"key": key, "branch": branch, "head": detail, "command": shlex.join(cmd)})
    skipped = [{"key": i.get("key"), "branch": i.get("branch"), "status": i.get("status")}
               for i in items if i.get("status") != "ready"]
    return (0 if not refused else 2), {"ok": not refused, "push": push, "refused": refused, "skipped": skipped}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("result", help="the workflow result as JSON, or - for stdin")
    ap.add_argument("--repo", help="the checkout whose refs to check (default: the result's repo)")
    ap.add_argument("--remote", default="origin", help="the remote the push commands name")
    a = ap.parse_args(argv[1:])
    try:
        raw = sys.stdin.read() if a.result == "-" else open(a.result, encoding="utf-8").read()
        doc = json.loads(raw)
    except (OSError, ValueError) as e:
        code, out = 2, {"ok": False, "code": "bad_request", "message": f"cannot read the result: {e}"}
    else:
        code, out = run(doc, a.repo, a.remote)
    print(json.dumps(out, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
