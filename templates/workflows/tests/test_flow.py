"""The two workflow scripts' control flow, run under node by sim.js with scripted agent replies.

No agent starts: every agent() call returns the reply its label has in the test (none means the
agent died). The tests pin what the scripts promise: who runs after whom, which prompt carries
which rule, that a stop or a red step ends its stack, that one repair round is the limit, that a
gate saying ok is not enough when the branch points elsewhere, and that only confirmed
candidates reach the planner. They need node on PATH (the runtime's language is JavaScript).
"""

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402

NODE = shutil.which("node")
SIM = os.path.join(HERE, "sim.js")
BUILD = os.path.join(_t.WORKFLOWS, "build-review-fix-verify.js")
SWEEP = os.path.join(_t.WORKFLOWS, "sweep-skeptic-plan.js")
BASE = "ba5e" * 10
H1, H2, H3, H4 = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)


def sim(script, args, replies, error=None):
    """Run `script` with `args`; returns the sim's document. The run must end as `error` says."""
    if not NODE:
        raise AssertionError("node is not on PATH: these tests run the workflow scripts under node")
    p = subprocess.run([NODE, SIM, script], input=json.dumps({"args": args, "replies": replies}),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert not doc["problems"], doc["problems"]
    if error is None:
        assert doc["error"] is None, doc["error"]
    else:
        assert error in (doc["error"] or ""), doc["error"]
    return doc


def labels(doc):
    return [c["label"] for c in doc["calls"] if "label" in c]


def prompt(doc, label):
    (p,) = [c["prompt"] for c in doc["calls"] if c.get("label") == label]
    return p


def by_key(doc):
    return {r["key"]: r for r in doc["result"]["items"]}


# ---------------------------------------------------------------- build-review-fix-verify --

def item(key, **over):
    it = {"key": key, "branch": f"b/{key}", "story": "S1", "kind": "fix", "spec": f"do {key}"}
    it.update(over)
    return it


def built(key, head, **over):
    r = {"branch": f"b/{key}", "worktree": f"/wt/{key}", "head_sha": head, "stopped_for_owner": False,
         "owner_question": "", "summary": "done", "tests_red_green": ["red on base, green here"],
         "suite": "RESULT: 9 passed", "golden": "0 differ", "mr_title": f"S1: {key}",
         "mr_description": "what and why", "owner_merge": False, "owner_merge_why": "",
         "schema_change": "none", "host_steps": "none", "queue_questions": [], "deviations": ""}
    r.update(over)
    return r


def finding(problem, severity="major"):
    return {"severity": severity, "file": "a.py:1", "problem": problem, "repro": "ran it", "fix": "fix it"}


def review(*findings, owner_merge_correct=True):
    return {"verdict": "fix" if findings else "ship", "findings": list(findings),
            "checked_and_fine": ["probed"], "owner_merge_correct": owner_merge_correct}


def fixed(head, **over):
    r = {"head_sha": head, "fixed": ["fixed it"], "declined": [], "suite": "RESULT: 9 passed",
         "golden": "0 differ", "stopped_for_owner": False, "owner_question": ""}
    r.update(over)
    return r


def gate(head, ok=True, *problems):
    return {"ok": ok, "branch_head": head, "suite": "RESULT: 9 passed", "golden": "0 differ",
            "problems": list(problems)}


def clean(key, head):
    """Replies for an item that builds, gets no finding and passes the gate."""
    return {f"build:{key}": built(key, head), f"review:{key}:correctness": review(),
            f"review:{key}:invariants": review(), f"verify:{key}:1": gate(head)}


def run_build(items, replies, error=None, **config):
    return sim(BUILD, {"config": {"base": BASE, "repo": "/repo", "scratch": "/s", **config},
                       "items": items}, replies, error)


def test_a_clean_item_is_built_reviewed_twice_and_gated_once():
    doc = run_build([item("a")], clean("a", H1))
    assert labels(doc) == ["build:a", "review:a:correctness", "review:a:invariants", "verify:a:1"]
    iso = {c["label"]: c["isolation"] for c in doc["calls"] if "label" in c}
    assert iso["build:a"] == "worktree" and [v for k, v in iso.items() if k != "build:a"] == [None] * 3
    r = by_key(doc)["a"]
    assert r["status"] == "ready" and r["head_sha"] == H1 and r["base"] == BASE, r
    assert r["mr_title"] == "S1: a" and r["owner_merge"] is False, r
    assert f"git checkout -b b/a {BASE}" in prompt(doc, "build:a")


def test_findings_go_to_one_fixer_and_the_gate_expects_the_fixers_head():
    rep = clean("a", H1)
    rep["review:a:correctness"] = review(finding("wrong total"))
    rep["review:a:invariants"] = review(finding("stray file", "minor"))
    rep["fix:a"] = fixed(H2)
    rep["verify:a:1"] = gate(H2)
    doc = run_build([item("a")], rep)
    assert labels(doc).count("fix:a") == 1
    fp = prompt(doc, "fix:a")
    assert "wrong total" in fp and "stray file" in fp and '"lens": "correctness"' in fp
    assert f"must print {H2}" in prompt(doc, "verify:a:1")
    r = by_key(doc)["a"]
    assert r["status"] == "ready" and r["head_sha"] == H2 and r["fixed"] == ["fixed it"], r


def test_no_finding_means_no_fix_pass():
    doc = run_build([item("a")], clean("a", H1))
    assert "fix:a" not in labels(doc)


def test_a_gate_that_says_ok_while_the_branch_points_elsewhere_is_not_a_pass():
    """The fixer reported H2 but the branch still holds the builder's H1 (a fix left on a
    detached HEAD). The gate's ok is not enough; the repair is told both heads."""
    rep = clean("a", H1)
    rep["review:a:correctness"] = review(finding("wrong total"))
    rep["fix:a"] = fixed(H2)
    rep["verify:a:1"] = gate(H1)                      # ok, but at the unreviewed commit
    doc = run_build([item("a")], rep)                 # no repair reply: the repair died
    r = by_key(doc)["a"]
    assert r["status"] == "not_ready" and r["problems"] == ["the repair pass returned nothing"], r
    rp = prompt(doc, "repair:a")
    assert H1 in rp and H2 in rp and "ref_mismatch" in rp and "R8." in rp
    rep["repair:a"] = fixed(H3)                       # the repair moves the branch
    rep["verify:a:2"] = gate(H3)
    r = by_key(run_build([item("a")], rep))["a"]
    assert r["status"] == "ready" and r["head_sha"] == H3, r


def test_at_most_one_repair_round():
    rep = clean("a", H1)
    rep["verify:a:1"] = gate(H1, False, "suite red")
    rep["repair:a"] = fixed(H2)
    rep["verify:a:2"] = gate(H2, False, "still red")
    rep["verify:a:3"] = gate(H2)                      # never asked for
    doc = run_build([item("a")], rep)
    assert labels(doc).count("repair:a") == 1 and "verify:a:3" not in labels(doc)
    r = by_key(doc)["a"]
    assert r["status"] == "not_ready" and r["problems"] == ["still red"], r
    rep["verify:a:2"] = gate(H2)
    assert by_key(run_build([item("a")], rep))["a"]["status"] == "ready"


def test_a_builder_that_stops_for_the_owner_commits_nothing_and_ends_its_stack():
    rep = {"build:a": built("a", "", stopped_for_owner=True, owner_question="Is a refund revenue? I recommend no.")}
    rep.update(clean("b", H2))
    doc = run_build([item("a"), item("b", stack_on="a"), item("c", stack_on="b")], rep)
    assert labels(doc) == ["build:a"]
    got = by_key(doc)
    assert got["a"]["status"] == "stopped_for_owner" and "refund" in got["a"]["owner_question"]
    assert got["b"]["status"] == "skipped" and got["c"]["status"] == "skipped", got
    rep["build:a"] = built("a", H1)
    rep.update({k: v for k, v in clean("a", H1).items() if k != "build:a"})
    rep.update(clean("c", H3))
    got = by_key(run_build([item("a"), item("b", stack_on="a"), item("c", stack_on="b")], rep))
    assert [got[k]["status"] for k in "abc"] == ["ready"] * 3, got


def test_a_stack_starts_from_the_verified_head_and_stops_at_its_first_red_step():
    rep = clean("a", H1)
    rep.update(clean("b", H2))
    rep["verify:b:1"] = gate(H2, False, "red")
    rep["repair:b"] = fixed(H3)
    rep["verify:b:2"] = gate(H3, False, "still red")
    rep.update(clean("c", H4))
    doc = run_build([item("a"), item("b", stack_on="a"), item("c", stack_on="b")], rep)
    assert f"git checkout -b b/b {H1}" in prompt(doc, "build:b")
    assert "build:c" not in labels(doc)
    got = by_key(doc)
    assert got["b"]["base"] == H1 and got["b"]["stack_on"] == "a", got["b"]
    assert [got[k]["status"] for k in "abc"] == ["ready", "not_ready", "skipped"], got
    rep["verify:b:2"] = gate(H3)
    doc = run_build([item("a"), item("b", stack_on="a"), item("c", stack_on="b")], rep)
    assert f"git checkout -b b/c {H3}" in prompt(doc, "build:c") and by_key(doc)["c"]["status"] == "ready"


def test_a_dead_builder_or_an_empty_branch_is_reported_not_dropped():
    doc = run_build([item("a"), item("b"), item("c", stack_on="a")],
                    {"build:b": built("b", BASE)})
    got = by_key(doc)
    assert [got[k]["status"] for k in "abc"] == ["no_result", "no_commit", "skipped"], got
    assert labels(doc) == ["build:a", "build:b"]
    assert [r["key"] for r in doc["result"]["items"]] == ["a", "b", "c"]


def test_bad_items_or_a_moving_base_stop_the_run_before_any_agent():
    cases = [([item("a"), item("a", branch="b/other")], {}, "share the key"),
             ([item("a"), item("b", branch="b/a")], {}, "share the branch"),
             ([item("a", stack_on="zz")], {}, "not an item"),
             ([item("a", story="")], {}, "needs key, branch, story and spec"),
             ([item("a", kind="refactr")], {}, "kind is fix, feature or refactor"),
             ([item("a")], {"base": "main"}, "must be a commit SHA")]
    for items, config, message in cases:
        doc = run_build(items, {}, error=message, **config)
        assert labels(doc) == [], message
    assert run_build([item("a")], clean("a", H1))["error"] is None


def test_a_stack_that_loops_is_reported_as_not_run():
    doc = run_build([item("a"), item("x", stack_on="y"), item("y", stack_on="x")], clean("a", H1))
    got = by_key(doc)
    assert got["a"]["status"] == "ready" and got["x"]["status"] == got["y"]["status"] == "not_run", got


def everything():
    """Three items whose runs touch every agent slot: build, reviews, fix, two gates, repair."""
    rep = {}
    for key, head in (("a", H1), ("b", H2), ("c", H3)):
        rep.update(clean(key, head))
        rep[f"review:{key}:correctness"] = review(finding("x"))
        rep[f"fix:{key}"] = fixed(head)
        rep[f"verify:{key}:1"] = gate(head, False, "red")
        rep[f"repair:{key}"] = fixed(head)
        rep[f"verify:{key}:2"] = gate(head)
    return run_build([item("a"), item("b"), item("c")], rep)


def test_every_agent_gets_its_own_ports_and_temp_dir():
    doc = everything()
    prompts = [c["prompt"] for c in doc["calls"] if "label" in c]
    assert len(prompts) == 21
    ports = [re.findall(r"ports (\d+-\d+)", p) for p in prompts]
    tmps = [set(re.findall(r"TMPDIR=(\S+?)[ ,.(]", p)) for p in prompts]
    assert all(len(p) == 1 for p in ports) and len({p[0] for p in ports}) == 21, ports
    assert all(len(t) == 1 for t in tmps) and len({t.pop() for t in tmps}) == 21


def test_each_role_gets_its_rules_and_only_those():
    doc = everything()
    want = {"build": {1, 2, 3, 4, 5, 6, 7, 9}, "review": {1, 2, 3, 4, 5, 6, 10},
            "fix": {1, 2, 3, 4, 5, 6, 7, 8, 9}, "repair": {1, 2, 3, 4, 5, 6, 7, 8, 9},
            "verify": {4, 5, 6, 10}}
    for c in doc["calls"]:
        if "label" in c:
            got = {int(n) for n in re.findall(r"^R(\d+)\. ", c["prompt"], re.M)}
            assert got == want[c["label"].split(":")[0]], (c["label"], got)


def test_a_reviewer_can_raise_owner_merge_and_nothing_lowers_it():
    rep = clean("a", H1)
    rep["review:a:invariants"] = review(owner_merge_correct=False)
    r = by_key(run_build([item("a")], rep))["a"]
    assert r["owner_merge"] is True and "risky" in r["owner_merge_why"], r
    rep = clean("a", H1)                              # planned owner merge, builder says no
    assert by_key(run_build([item("a", owner_merge=True)], rep))["a"]["owner_merge"] is True
    assert by_key(run_build([item("a")], clean("a", H1)))["a"]["owner_merge"] is False


def test_a_resumed_builder_finishes_in_its_existing_worktree():
    doc = run_build([item("a", resume="/wt/cut-off")], clean("a", H1))
    (b,) = [c for c in doc["calls"] if c.get("label") == "build:a"]
    assert b["isolation"] is None and b["prompt"].startswith("RESUMING"), b
    assert "EXISTING worktree /wt/cut-off" in b["prompt"] and "git checkout -b" not in b["prompt"]
    fresh = [c for c in run_build([item("a")], clean("a", H1))["calls"] if c.get("label") == "build:a"]
    assert fresh[0]["isolation"] == "worktree"


def test_an_item_whose_brief_needs_the_owner_is_not_built():
    doc = run_build([item("a", needs_owner=True, owner_question="Which total counts?"), item("b")],
                    clean("b", H2))
    assert "build:a" not in labels(doc)
    got = by_key(doc)
    assert got["a"]["status"] == "stopped_for_owner" and got["a"]["owner_question"] == "Which total counts?"
    assert got["b"]["status"] == "ready"


def test_a_refactor_golden_is_strict_and_a_fix_names_what_may_differ():
    doc = run_build([item("r", kind="refactor"), item("f", named_change="totals become null")],
                    {**clean("r", H1), **clean("f", H2)}, golden="tool compare {base} HEAD")
    assert f"`tool compare {BASE} HEAD --strict`, expecting no difference at all" in prompt(doc, "build:r")
    fp = prompt(doc, "build:f")
    assert "--strict" not in fp and "only the named change (totals become null)" in fp
    doc = run_build([item("f")], clean("f", H2), golden="")
    assert "there is no golden tool yet" in prompt(doc, "build:f")


# ---------------------------------------------------------------- sweep-skeptic-plan --

def cand(cid, kind="dead"):
    return {"id": cid, "kind": kind, "title": f"title {cid}", "files": ["a.py"], "evidence": "3 call sites",
            "change": "delete it", "risk": "low", "proof": "suite + golden", "waits_for": []}


def verdict(v):
    return {"verdict": v, "evidence": "ran it", "consumers_checked": ["pages"], "corrected_change": "", "risk": "low"}


def planned(key, **over):
    it = {"key": key, "branch": f"refactor/{key}", "story": "S09", "kind": "refactor", "owner_merge": False,
          "owner_merge_why": "", "stack_on": "", "waits_for": [], "candidate_ids": ["c1"],
          "spec": f"do {key}", "proof": "suite + golden --strict"}
    it.update(over)
    return it


def run_sweep(dims, replies, **config):
    return sim(SWEEP, {"config": {"base": BASE, "repo": "/repo", "scratch": "/s", **config},
                       "dimensions": [{"key": k, "prompt": k.upper()} for k in dims]}, replies)


def test_only_confirmed_candidates_reach_the_planner():
    rep = {"sweep:dead": {"candidates": [cand("c1"), cand("c2"), cand("c3"), cand("c4"), cand("c5", "out_of_scope")],
                          "overview": "o", "checked_and_fine": []},
           "skeptic:dead:c1": verdict("CONFIRMED"), "skeptic:dead:c2": verdict("REFUTED"),
           "skeptic:dead:c3": verdict("DOCUMENTED_LIMIT"),                     # c4's skeptic died
           "plan": {"verdict": "go", "items": [planned("k1")], "not_now": []},
           "brief:k1": {"key": "k1", "path": "/s/briefs/k1.md", "summary": "s", "needs_owner": False, "owner_question": ""}}
    doc = run_sweep(["dead"], rep)
    assert "skeptic:dead:c5" not in labels(doc)
    confirmed = prompt(doc, "plan").split("Confirmed: ")[1].split("\n")[0]
    assert '"id":"c1"' in confirmed and all(f'"id":"{c}"' not in confirmed for c in ("c2", "c3", "c4"))
    res = doc["result"]
    assert [c["id"] for c in res["confirmed"]] == ["c1"]
    assert [[c["id"] for c in res[k]] for k in ("refuted", "limits", "unverified")] == [["c2"], ["c3"], ["c4"]]
    assert res["items"][0]["brief"] == "/s/briefs/k1.md"


def test_nothing_confirmed_means_no_planner():
    rep = {"sweep:dead": {"candidates": [cand("c1")], "overview": "o", "checked_and_fine": []},
           "skeptic:dead:c1": verdict("REFUTED"), "plan": {"verdict": "go", "items": [planned("k1")], "not_now": []}}
    doc = run_sweep(["dead"], rep)
    assert "plan" not in labels(doc) and doc["result"]["items"] == []
    rep["skeptic:dead:c1"] = verdict("CONFIRMED")
    assert "plan" in labels(run_sweep(["dead"], rep))


def test_capped_candidates_and_a_dead_finder_are_logged():
    rep = {"sweep:dead": {"candidates": [cand("c1"), cand("c2"), cand("c3")], "overview": "o", "checked_and_fine": []}}
    doc = run_sweep(["dead", "docs"], rep, max_candidates=2)
    assert [lbl for lbl in labels(doc) if lbl.startswith("skeptic")] == ["skeptic:dead:c1", "skeptic:dead:c2"]
    assert "dead: 1 candidates past max_candidates were not checked" in doc["logs"], doc["logs"]
    assert "docs: the finder returned nothing" in doc["logs"], doc["logs"]


def test_sweep_prompts_carry_their_rules_and_their_own_ports():
    rep = {f"sweep:{d}": {"candidates": [cand(f"{d}1"), cand(f"{d}2")], "overview": "o", "checked_and_fine": []}
           for d in ("dead", "tests")}
    rep.update({f"skeptic:{d}:{d}{n}": verdict("CONFIRMED") for d in ("dead", "tests") for n in (1, 2)})
    rep["plan"] = {"verdict": "go", "items": [planned("k1"), planned("k2")], "not_now": []}
    doc = run_sweep(["dead", "tests"], rep)
    want = {"sweep": {1, 4, 5, 10, 11, 13}, "skeptic": {1, 4, 5, 10, 11, 12}, "plan": {1}, "brief": {1, 4, 10}}
    for c in doc["calls"]:
        got = {int(n) for n in re.findall(r"^R(\d+)\. ", c["prompt"], re.M)}
        assert got == want[c["label"].split(":")[0]], (c["label"], got)
    ports = [re.findall(r"ports (\d+-\d+)", c["prompt"]) for c in doc["calls"]]
    ports = [p[0] for p in ports if p]
    assert len(ports) == 6 and len(set(ports)) == 6, ports


def test_the_plan_feeds_the_build_script_and_a_brief_can_hold_an_item_back():
    rep = {"sweep:dead": {"candidates": [cand("c1")], "overview": "o", "checked_and_fine": []},
           "skeptic:dead:c1": verdict("CONFIRMED"),
           "plan": {"verdict": "go", "items": [planned("k1"), planned("k2", stack_on="k1")], "not_now": []},
           "brief:k1": {"key": "k1", "path": "/s/briefs/k1.md", "summary": "s", "needs_owner": False, "owner_question": ""},
           "brief:k2": {"key": "k2", "path": "/s/briefs/k2.md", "summary": "s", "needs_owner": True,
                        "owner_question": "Keep the old name as an alias?"}}
    items = run_sweep(["dead"], rep)["result"]["items"]
    assert items[1]["needs_owner"] is True
    doc = run_build(items, clean("k1", H1))
    assert "Read the design brief /s/briefs/k1.md" in prompt(doc, "build:k1")
    got = by_key(doc)
    assert got["k1"]["status"] == "ready" and got["k2"]["status"] == "stopped_for_owner", got
    assert "build:k2" not in labels(doc)


if __name__ == "__main__":
    if not NODE:
        print("FAILED: node is not on PATH; these tests run the workflow scripts under node")
        sys.exit(1)
    _t.main(globals())
