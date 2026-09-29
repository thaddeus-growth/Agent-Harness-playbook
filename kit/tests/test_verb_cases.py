#!/usr/bin/env python3
"""kit.guards.json_contract.check_verbs: every verb of the table, of every
kind, has a `--json` case, and each case keeps the contract.

Ported from the playbook's core/ contract test ("a new verb fails until it
has a case"). The read verbs are run by check_read_verbs (test_guards.py);
this is the guard for the rest: a gate or write verb's refusals sat outside
that test once, so about 40 of them reached the owner as "unclassified".

  [1] a table whose every verb has a case, each a classified failure or a
      coded success, passes; `--json` is added, after the verb's words and
      a `--`, unless the case has it
  [2] a verb with no case fails (every kind: read, human, gated, external,
      dev unless skipped); a case for a verb the table lacks fails
  [3] a failure that is unclassified, uncoded, unregistered or prose, a
      traceback, a success with an uncoded message or two documents, is
      found
"""

import json
import sys
from collections import namedtuple
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import contract, human  # noqa: E402
from kit.guards import json_contract  # noqa: E402
from kit.messages import coded, msg  # noqa: E402
from kit.testing.check import check, finish  # noqa: E402

V = namedtuple("V", "words script kind takes_market needs_data_dir",
               defaults=(True, True))
VERBS = [V(("facts", "list"), "facts.py", "read"),
         V(("facts", "set"), "facts.py", "human"),
         V(("facts", "confirm"), "facts.py", "gated"),
         V(("pull", "orders"), "pull_orders.py", "external"),
         V(("test",), "run_tests.py", "dev", False, False)]


def doc(**kw) -> str:
    return json.dumps(kw)


def refusal(e: BaseException) -> str:
    return json.dumps(contract.failure_doc(e, ["shop", "x"]))


def runner(outputs: dict, calls: list | None = None):
    """run(argv) -> (rc, out, err) from {the verb's words: (rc, out, err)}."""
    def run(argv: list[str]):
        if calls is not None:
            calls.append(argv)
        words = " ".join(a for a in argv[:2] if not a.startswith("-"))
        return outputs[words]
    return run


def found(problems: list[str], *needles: str) -> bool:
    return all(any(n in p for p in problems) for n in needles)


def main() -> int:
    _shop.use()
    NEEDS_HUMAN = refusal(human.Refused(msg(
        "confirm_needs_human", "facts confirm needs a person.",
        what="facts confirm")))
    OK_ROWS = doc(rows=[], **coded("message", msg(
        "reason_required", "--reason is required.")))
    OKAY = doc(rows=[])
    CASES = {"facts list": [[]], "facts set": [["k", "1"]],
             "facts confirm": [["k"]], "pull orders": [["--from", "2026-01-01"]],
             "test": [[]]}

    print("[1] a table whose every verb has a case, each keeping the "
          "contract")
    calls: list[list[str]] = []
    outputs = {"facts list": (0, OK_ROWS, ""),
               "facts set": (2, NEEDS_HUMAN, ""),
               "facts confirm": (2, NEEDS_HUMAN, ""),
               "pull orders": (0, OKAY, ""),
               "test": (0, OKAY, "")}
    probs = json_contract.check_verbs(CASES, verbs=VERBS,
                                      run=runner(outputs, calls))
    check("every verb of every kind has a case, and passes", probs == [],
          probs)
    check("`--json` goes after the verb's words, a `--` and the case's "
          "arguments",
          ["facts", "confirm", "--", "k", "--json"] in calls
          and ["pull", "orders", "--", "--from", "2026-01-01", "--json"]
          in calls and ["test", "--", "--json"] in calls, calls)
    calls.clear()
    json_contract.check_verbs({**CASES, "facts list": [["--json"]]},
                              verbs=VERBS, run=runner(outputs, calls))
    check("… unless the case already has it (once)",
          ["facts", "list", "--", "--json"] in calls, calls)
    check("check_verb_cases reports through kit.testing.check",
          json_contract.check_verb_cases(CASES, verbs=VERBS,
                                         run=runner(outputs)))
    try:
        json_contract.check_verbs(CASES, verbs=VERBS)
        got = "no error"
    except ValueError as e:
        got = str(e)
    check("neither `run` nor a fixture data dir: a ValueError",
          "run" in got and "data_dir" in got, got)

    print("\n[2] a new verb fails until it has a case")
    for kind_verb in ("facts list", "facts set", "facts confirm",
                      "pull orders"):
        partial = {k: v for k, v in CASES.items() if k != kind_verb}
        check(f"no case for `{kind_verb}` "
              f"({next(v.kind for v in VERBS if ' '.join(v.words) == kind_verb)}"
              f"): found",
              found(json_contract.check_verbs(
                  partial, verbs=VERBS, run=runner(outputs)),
                  f"{kind_verb}: no --json case"))
    check("an empty list of cases is no case",
          found(json_contract.check_verbs(
              {**CASES, "facts set": []}, verbs=VERBS, run=runner(outputs)),
              "facts set: no --json case"))
    check("a dev verb named in `skip` needs none",
          json_contract.check_verbs(
              {k: v for k, v in CASES.items() if k != "test"}, verbs=VERBS,
              run=runner(outputs), skip=("test",)) == [])
    check("a case for a verb the table does not have",
          found(json_contract.check_verbs(
              {**CASES, "facts wipe": [[]]}, verbs=VERBS,
              run=runner(outputs)),
              "facts wipe: a case for a verb the table does not have"))
    check("the verb as typed: extra spaces in a case's name still match",
          json_contract.check_verbs(
              {**{k: v for k, v in CASES.items() if k != "facts confirm"},
               "facts   confirm": [["k"]]}, verbs=VERBS,
              run=runner(outputs)) == [])

    print("\n[3] a failure that is not classified, a message that is not "
          "coded")
    crash = refusal(ValueError("boom"))
    check("a gated verb whose refusal is unclassified_error",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "facts confirm": (1, crash, "")})),
              "facts confirm k: unclassified_error"))
    prose_only = doc(error="needs a person", next=[])
    check("a refusal that is prose only, no code and params",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "facts set": (2, prose_only, "")})),
              "facts set k 1: not {error, next, code, params"))
    check("a traceback on stderr, even on exit 0",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "facts set": (0, NEEDS_HUMAN,
                                            "Traceback (most recent call "
                                            "last):\n  boom")})),
              "facts set k 1: a traceback on stderr"))
    check("a failing verb whose stdout is prose, not one document",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "pull orders": (2, "error: boom\n", "")})),
              "pull orders --from 2026-01-01: stdout is not exactly one JSON "
              "document"))
    check("a success with an English message and no code beside it",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "pull orders": (0, doc(
                      message="pulled 3 days"), "")})),
              "pull orders --from 2026-01-01: uncoded message at .message"))
    check("a success that is two documents",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "test": (0, OKAY + OKAY, "")})),
              "test: stdout is not exactly one JSON document"))
    check("an unregistered code in a failure",
          found(json_contract.check_verbs(
              CASES, verbs=VERBS, run=runner(
                  {**outputs, "facts set": (2, doc(
                      error="x", next=[], code="not_a_code", params={}),
                      "")})), "facts set k 1: code 'not_a_code'"))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
