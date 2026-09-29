#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""messages.py and contract.py, the way a harness uses them.

First the contract itself, pointed at core's sources plus the toy harness, their
two registry files and the toy's verb table: the registry is well formed and
closed both ways, and every verb's --json documents keep it. A harness's own
contract test is the same three calls on its own sources, files and table.

Then each check is shown catching a broken input, so none can pass by looking at
nothing: uncoded prose, a code list of another length, an unregistered or
computed code, wrong params, a registered code nothing emits, a verb with no
case, an unclassified or incomplete failure, two documents on stdout, and bad
registry rows. Last, the Msg rules: a Msg is its text, coded() places codes,
failure() classifies any exception, relayed() keeps a child's code.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402
import toy_harness  # noqa: E402  (points messages at its own registry, as a harness would)
from core import contract, gate, messages  # noqa: E402
from core.messages import msg  # noqa: E402

CORE = os.path.dirname(HERE)
TOY = os.path.join(HERE, "toy_harness.py")
SOURCES = sorted(glob.glob(os.path.join(CORE, "*.py"))) + [TOY]
REGISTRIES = [messages.CORE_TSV, os.path.join(HERE, "toy_codes.tsv")]
RELAY = ["--relay-user=web:alice", "--relay-at=2026-01-05T10:00:00Z"]


def reg() -> dict:
    return messages.registry()


# ------------------------------------------------- the contract, applied --

def test_the_registry_files_are_well_formed():
    assert [str(p) for p in messages.registry_files()] == [str(p) for p in REGISTRIES]
    assert contract.lint_registry(REGISTRIES) == []


def test_the_harness_registry_is_read_and_owns_no_core_code():
    """The harness's own registry: `templates/ssot/message_codes.tsv` in the
    playbook, `ssot/message_codes.tsv` once core/ is copied into a harness. The
    same reader takes it, and it lints clean beside core's own file."""
    found = [p for p in (os.path.join(_t.ROOT, "templates", "ssot", "message_codes.tsv"),
                         os.path.join(_t.ROOT, "ssot", "message_codes.tsv")) if os.path.isfile(p)]
    assert found, "no ssot/message_codes.tsv beside core/: start it from the playbook's template"
    template = found[0]
    rows = messages.read_tsv(template)
    assert rows and all(r["code"] for r in rows)
    assert contract.lint_registry([template]) == []
    overlap = sorted({r["code"] for r in rows} & {r["code"] for r in messages.read_tsv(messages.CORE_TSV)})
    assert overlap == [], f"core/message_codes.tsv owns {overlap}: remove them from the template"


def test_the_registry_is_closed_both_ways_over_core_and_the_toy_harness():
    assert contract.closed(SOURCES, reg()) == []
    assert len(contract.scan(SOURCES)) >= len(reg())            # the scan saw every call


def test_every_verb_in_the_toy_table_keeps_the_contract():
    with _t.tmpdir() as d:
        env = {**os.environ, "TOY_STORE": os.path.join(d, "s.json"), gate.CODE_SECRET_ENV: "s3cret"}

        seen = []

        def run(verb, argv):
            p = subprocess.run([sys.executable, "-B", TOY, *argv], capture_output=True, text=True,
                               env=env, stdin=subprocess.DEVNULL, start_new_session=True, timeout=60)
            seen.append(p.stdout)
            return p.returncode, p.stdout

        J = "--json"
        cases = {
            "init": [["init", "north", J], ["init", "north", J], ["init", "not a token", J]],
            "set": [["set", "fact", "unit_cost", "12", "--reason=r", J], ["set", "fact", "x", "1", J],
                    ["set", "gizmo", "x", "1", "--reason=r", J]],
            "confirm": [["confirm", "fact", "unit_cost", "--reason=ok", J],
                        ["confirm", "fact", "unit_cost", "--reason=ok", "--code=000000", *RELAY, J],
                        ["confirm", "fact", "unit_cost", "--reason=ok", "--code=000000", J],
                        ["confirm", "fact", "unit_cost", "--relay-user=web:alice", "--relay-at=x",
                         "--reason=ok", "--code=1", J],
                        ["confirm", "fact", "unit_cost", "zz", "--reason=ok", J],
                        ["confirm", "fact", "a", "b", "--value=1", "--reason=ok", J]],
            "list": [["list", J]],
        }
        assert contract.check_verbs(toy_harness.VERBS, cases, run, reg()) == []
        codes = {json.loads(out).get("code") for out in seen}
        assert {"store_exists", "bad_request", "reason_required", "confirm_code_required",
                "confirm_code_mismatch", "confirm_relay_audit_missing", "confirm_relay_audit_invalid",
                "nothing_to_confirm", "value_needs_one_id"} <= codes, codes


# ------------------------------------- each check, caught on a broken input --

def test_prose_without_a_code_beside_it_is_found_and_machine_values_are_not():
    r = reg()
    ok = {"code": "confirmed", "params": {"summary": "x"}}
    assert contract.uncoded({"message": "Confirmed x.", "message_code": ok}, r) == []
    assert contract.uncoded({"rows": [{"reason": "prose here"}], "note": None, "note_code": None,
                             "evidence": {"reason": "own_target"}}, r) == [".rows[0].reason"]
    assert contract.uncoded({"message": "Confirmed x.", "message_code": {"code": "confirmed", "params": {}}},
                            r) == [".message"]                   # a code with the wrong params
    assert contract.uncoded({"warnings": ["a", "b"], "warning_codes": [ok]}, r) == [".warnings"]
    assert contract.uncoded({"warnings": ["a"], "warning_codes": [ok]}, r) == []
    assert contract.uncoded({"rows": [{"label": "Tea"}]}, r, data_paths=(".rows[].label",)) == []
    assert contract.uncoded({"error": "It broke.", "next": []}, r) == [".error"]   # a failure, no code
    assert contract.uncoded({"x_code": {"code": "confirmed", "params": {"summary": "a", "message": "b"}}},
                            r) == []                             # a code's params are data, not prose


def test_an_unregistered_code_wrong_params_and_a_computed_code_are_found():
    with _t.tmpdir() as d:
        src = os.path.join(d, "verb.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write("from core.messages import msg\n"
                    "a = msg('no_such_code', 'x')\n"
                    "b = msg('confirmed', 'x', summary='s', extra=1)\n"
                    "c = msg(name, 'x')\n"
                    "d = messages.msg('confirmed', 'x', **kw)\n")
        bad = contract.closed([src], {"confirmed": ("summary",)})
    assert bad == ["verb.py:2: no_such_code is not registered",
                   "verb.py:3: confirmed takes ['summary'], this call passes ['extra', 'summary']",
                   "verb.py:4: msg() names its code as a string literal",
                   "verb.py:5: confirmed passes its params as plain keywords, not **"], bad


def test_a_registered_code_that_nothing_emits_is_found():
    bad = contract.closed(SOURCES, {**reg(), "never_said": ()})
    assert bad == ["never_said: registered, but no msg() call emits it"], bad


def test_a_new_verb_fails_until_it_has_a_case():
    def run(verb, argv):
        return 0, '{"rows": []}'
    table = {"list": None, "export": None}
    assert contract.check_verbs(table, {"list": [["list"]]}, run, reg()) == \
        ["export: no --json case (a new verb fails here until it has one)"]
    assert contract.check_verbs({"list": None}, {"list": [["list"]], "gone": [["gone"]]}, run, reg()) \
        == ["gone: a case for a verb the table does not have"]


def test_an_unclassified_or_incomplete_failure_and_two_documents_are_found():
    r = reg()
    outs = {"crash": (1, json.dumps({"error": "it broke", "next": [], "code": "unclassified_error",
                                     "params": {"detail": "it broke"}})),
            "bare": (1, json.dumps({"error": "it broke"})),
            "two": (0, '{"a": 1}\n{"b": 2}\n'),
            "fine": (1, json.dumps({"error": "x", "next": ["h.py init north"], "code": "store_unset",
                                    "params": {}}))}
    bad = contract.check_verbs(list(outs), {v: [["--json"]] for v in outs}, lambda v, a: outs[v], r)
    assert bad == ["crash --json: unclassified_error: a refusal the harness can name gets its own code",
                   "bare --json: `next` is not a list of commands",
                   "bare --json: code None: not registered, or not exactly its params",
                   "bare --json: uncoded .error",
                   "two --json: stdout is not exactly one JSON document"], bad


def test_a_bad_registry_file_is_found():
    with _t.tmpdir() as d:
        good = os.path.join(d, "a.tsv")
        with open(good, "w", encoding="utf-8") as f:
            f.write("code\tmeaning_en\tmeaning_es\tparams\n"
                    "ok_code\tSaid {x}\tDicho {x}\tx\n"
                    "Bad-Code\tSaid\tDicho\t\n"
                    "no_es\tSaid {y}\t\ty\n"
                    "stray\tSaid {z}\tDicho\t\n")
        dup = os.path.join(d, "b.tsv")
        with open(dup, "w", encoding="utf-8") as f:
            f.write("code\tmeaning_en\tmeaning_es\tparams\nok_code\tSaid\tDicho\t\n")
        shape = os.path.join(d, "c.tsv")
        with open(shape, "w", encoding="utf-8") as f:
            f.write("code\ttext\tparams\n")
        bad = contract.lint_registry([good, dup, shape])
    assert bad[:4] == ["a.tsv: Bad-Code: a code is lowercase snake_case",
                       "a.tsv: no_es: meaning_es is empty",
                       "a.tsv: stray: meaning_en names ['z'], not among its params",
                       "b.tsv: ok_code: also in a.tsv"], bad
    assert len(bad) == 5 and "header must be code, meaning_en" in bad[4], bad


# --------------------------------------------------------- the Msg rules --

def test_a_msg_is_its_text_with_its_code_and_params_beside_it():
    m = msg("pending_written", "fact:x = 1 is pending.", item="fact:x", value="1")
    assert m == "fact:x = 1 is pending." and json.dumps(m) == '"fact:x = 1 is pending."'
    assert messages.code(m) == {"code": "pending_written", "params": {"item": "fact:x", "value": "1"}}
    inner = msg("store_unset", "unset")
    outer = msg("bad_request", "bad", detail=inner)
    assert messages.code(outer)["params"]["detail"] == {"code": "store_unset", "params": {}}
    assert messages.code(msg("store_declared", "d", scope=date(2026, 1, 5)))["params"] == \
        {"scope": "2026-01-05"}


def test_coded_places_k_code_beside_k_and_k_codes_beside_a_list():
    m = msg("store_unset", "unset")
    assert messages.coded("reason", m) == {"reason": m, "reason_code": {"code": "store_unset", "params": {}}}
    assert messages.coded("reason", None) == {"reason": None, "reason_code": None}
    assert messages.coded("warnings", [m, m]) == {"warnings": [m, m], "warning_codes": [
        {"code": "store_unset", "params": {}}] * 2}
    assert messages.coded("warnings", []) == {"warnings": [], "warning_codes": []}


def test_failure_classifies_any_exception_and_relayed_keeps_a_childs_code():
    e = gate.Refused(msg("no_store", "no store at /x", path="/x"))
    assert messages.failure_doc(e, ["h.py init north"]) == {
        "error": "no store at /x", "next": ["h.py init north"], "code": "no_store", "params": {"path": "/x"}}
    assert messages.failure(ValueError("disk full")) == {"code": "unclassified_error",
                                                         "params": {"detail": "disk full"}}
    kept = messages.relayed({"code": "no_store", "params": {"path": "/d"}}, "report failed: no store at /d")
    assert kept == "report failed: no store at /d" and messages.code(kept) == \
        {"code": "no_store", "params": {"path": "/d"}}
    for c in (None, {}, {"code": "not_ours", "params": {}}):
        assert messages.code(messages.relayed(c, "boom")) == {"code": "unclassified_error",
                                                               "params": {"detail": "boom"}}


def test_an_unregistered_code_a_plain_string_and_a_code_in_two_files_are_refused():
    for bad, exc in ((lambda: msg("no_such_code", "x"), ValueError),
                     (lambda: messages.code("plain text"), TypeError),
                     (lambda: messages.use_registry("/nonexistent/message_codes.tsv"), ValueError)):
        try:
            bad()
        except exc:
            continue
        raise AssertionError(f"not refused with {exc.__name__}")
    with _t.tmpdir() as d:
        twice = os.path.join(d, "codes.tsv")
        with open(twice, "w", encoding="utf-8") as f:
            f.write("code\tmeaning_en\tmeaning_es\tparams\nreason_required\tA reason\tUn motivo\t\n")
        messages.use_registry(twice)
        try:
            messages.registry()
            raise AssertionError("a code in core's file and the harness's was accepted")
        except ValueError as e:
            assert "reason_required" in str(e)
        finally:
            messages.use_registry(os.path.join(HERE, "toy_codes.tsv"))
    assert "confirmed" in reg()


if __name__ == "__main__":
    _t.main(globals())
