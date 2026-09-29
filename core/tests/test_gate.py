#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""gate.py: a person, bound to exactly what they were shown, once.

Three parts. The subject and the code, in process with the clock pinned. Then
confirm() at and off a terminal, its two seams patched. Then the toy harness
run as a real process, the way an agent or the console runs a harness: a pipe
and a pty with the answer already typed cannot answer, a real terminal can; a
relayed code is refused for other ids, a subset, another entity type, value or
scope, and once its write has landed; and console/relay.py (not a stand-in)
drives the gate end to end. Every toy run gets no terminal and a closed stdin
unless the test gives it one, so the suite never waits on a person.
"""

from __future__ import annotations

import getpass
import inspect
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402
from core import gate  # noqa: E402

TOY = os.path.join(HERE, "toy_harness.py")
CONSOLE = os.path.join(_t.ROOT, "console")
SECRET = "a-test-secret-for-codes"
AT = "2026-01-05T10:00:00Z"
RELAY = ["--relay-user=web:alice", f"--relay-at={AT}"]
T0 = 6_000_000 * gate.SLOT_S                   # a pinned clock, at the start of a slot
USER = getpass.getuser()


# ------------------------------------------------------------- helpers --

def S(**kw) -> str:
    """A subject: two budgets approved in one scope, unless a test says otherwise."""
    base = {"verb": "approve", "scope": "north", "entity_type": "budget",
            "items": {"a": 10, "b": 20}, "version": 7}
    return gate.subject(**{**base, **kw})


@contextmanager
def secret(on: bool = True):
    with mock.patch.dict(os.environ):
        os.environ.pop(gate.CODE_SECRET_ENV, None)
        if on:
            os.environ[gate.CODE_SECRET_ENV] = SECRET
        yield


def off_terminal():
    return mock.patch.object(gate, "stdin_is_tty", return_value=False)


@contextmanager
def at_terminal(typed: str):
    prompts = []

    def read(prompt, what):
        prompts.append(prompt)
        return typed
    with mock.patch.object(gate, "stdin_is_tty", return_value=True), \
            mock.patch.object(gate, "read_tty", read):
        yield prompts


def toy_env(store: str, with_secret: bool = True) -> dict:
    env = {k: v for k, v in os.environ.items() if k != gate.CODE_SECRET_ENV}
    env["TOY_STORE"] = store
    if with_secret:
        env[gate.CODE_SECRET_ENV] = SECRET
    return env


def toy(store: str, *argv, with_secret: bool = True, stdin=subprocess.DEVNULL, text_in=None):
    """(exit, the one --json document): the toy harness with no terminal."""
    p = subprocess.run([sys.executable, "-B", TOY, *argv, "--json"], input=text_in,
                       stdin=None if text_in is not None else stdin, capture_output=True,
                       text=True, env=toy_env(store, with_secret), start_new_session=True,
                       timeout=60)
    return p.returncode, json.loads(p.stdout)


def new_store(d: str, scope: str, *sets) -> str:
    path = os.path.join(d, f"{scope}.json")
    assert toy(path, "init", scope)[0] == 0
    for typ, eid, value in sets:
        assert toy(path, "set", typ, eid, value, "--reason=proposed")[0] == 0
    return path


def items(store: str) -> dict:
    with open(store, encoding="utf-8") as f:
        return json.load(f)["items"]


def history(store: str) -> list:
    with open(store, encoding="utf-8") as f:
        return json.load(f)["history"]


def at_real_terminal(argv: list[str], env: dict, typed: str, timeout: float = 30.0):
    """(exit, what the terminal showed): python `argv` with a pty as its
    controlling terminal and its stdin, a person typing `typed` once asked."""
    pid, fd = pty.fork()
    if pid == 0:                                            # the child: exec at once
        try:
            os.execve(sys.executable, [sys.executable, "-B", *argv], env)
        finally:
            os._exit(127)
    shown, sent, end = b"", False, time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:                                 # the child closed the terminal
                break
            if not chunk:
                break
            shown += chunk
            if not sent and b"to confirm: " in shown:
                os.write(fd, typed.encode() + b"\n")
                sent = True
        else:
            os.kill(pid, signal.SIGKILL)
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), shown.decode(errors="replace")


# ----------------------------------------------- the subject and the code --

def test_the_subject_is_canonical_with_ids_sorted_and_their_values_kept():
    assert S(items={"b": 20, "a": 10}) == S()
    doc = json.loads(S())
    assert set(doc) == {"verb", "scope", "entity_type", "items", "version"}
    assert list(doc["items"]) == ["a", "b"] and doc["items"] == {"a": 10, "b": 20}
    assert json.loads(gate.subject("v", "s", "t", {3: {"stage": "launch"}}, None))["items"] == \
        {"3": {"stage": "launch"}}                          # ids are text; a value may be several fields


def test_a_code_confirms_exactly_the_subject_it_was_shown_for():
    with secret():
        code = gate.issue_code(S(), at=T0)
        assert re.fullmatch(r"[0-9]{6}", code)
        assert gate.check_code(S(), code, at=T0)
        assert gate.check_code(S(items={"b": 20, "a": 10}), code, at=T0)      # any order
        for label, other in (("other ids (A,B shown, A,C asked)", S(items={"a": 10, "c": 20})),
                             ("a subset", S(items={"a": 10})),
                             ("a superset", S(items={"a": 10, "b": 20, "c": 30})),
                             ("another value", S(items={"a": 10, "b": 21})),
                             ("another scope", S(scope="south")),
                             ("another entity type", S(entity_type="fact")),
                             ("another verb", S(verb="rollback")),
                             ("the version its own write moved", S(version=8))):
            assert not gate.check_code(other, code, at=T0), label


def test_a_code_lives_in_its_slot_and_the_two_after_it_never_before():
    with secret():
        code = gate.issue_code(S(), at=T0)
        for k in range(gate.SLOTS_BACK + 1):
            assert gate.check_code(S(), code, at=T0 + k * gate.SLOT_S + gate.SLOT_S - 1), k
        assert not gate.check_code(S(), code, at=T0 + (gate.SLOTS_BACK + 1) * gate.SLOT_S)   # expired
        assert not gate.check_code(S(), code, at=T0 - 1)   # checked before its slot: a future code is no code
    assert gate.CODE_TTL_MINUTES == 15


def test_anything_but_six_ascii_digits_is_no_code_and_never_a_crash():
    with secret():
        code = gate.issue_code(S(), at=T0)
        wide = "".join(chr(ord(c) + 0xFEE0) for c in code)   # full-width digits: isdigit() says yes
        for bad in (wide, code + "0", code[:5], "", " " * 6, "abcdef", None):
            assert not gate.check_code(S(), bad, at=T0), bad
        assert gate.check_code(S(), f" {code}\n", at=T0)                     # pasted with spaces
    with secret(False):
        assert not gate.check_code(S(), code, at=T0)                         # no secret, no code


# ----------------------------------------------------------- confirm() --

def test_off_a_terminal_without_the_secret_only_a_person_at_a_terminal_can():
    with secret(False), off_terminal(), _t.refused("confirm_needs_human") as c:
        gate.confirm("approve budget", "a = 10", "confirm", subj=S(), code="123456",
                     relay_user="web:alice", relay_at=AT)
    assert gate.CODE_SECRET_ENV in str(c.err)


def test_off_a_terminal_the_first_call_is_the_challenge_as_data():
    cmd = ["harness.py", "approve", "b", "a", "--reason=ok", "--json"]
    with secret(), off_terminal(), _t.refused("confirm_code_required") as c:
        gate.confirm("approve budget", "a = 10; b = 20", "confirm", subj=S())
    e = c.err
    assert isinstance(e, gate.CodeRequired)
    doc = gate.json_refusal(e, cmd)
    assert set(doc) == {"error", "next", "code", "params", "subject"}
    assert doc["params"] == {"what": "approve budget", "summary": "a = 10; b = 20",
                             "confirm_code": e.confirm_code, "ttl_minutes": 15}
    assert doc["subject"] == json.loads(S())
    assert doc["next"] == ["harness.py approve b a --reason=ok --json "
                           f"--code={e.confirm_code} --relay-user=<sender_id> --relay-at=<iso_time>"]
    with secret():
        assert gate.check_code(S(), e.confirm_code)


def test_the_code_passes_only_with_who_sent_it_back_and_when():
    with secret(), off_terminal():
        code = gate.issue_code(S())
        for user, at, want in ((None, AT, "confirm_relay_audit_missing"),
                               ("web:alice", None, "confirm_relay_audit_missing"),
                               ("", "", "confirm_relay_audit_missing"),
                               ("web alice", AT, "confirm_relay_audit_invalid"),
                               ("web:a] [relay user=x", AT, "confirm_relay_audit_invalid"),
                               ("web:alice", "yesterday", "confirm_relay_audit_invalid"),
                               ("web:alice", "2026-01-05T10:00:00", "confirm_relay_audit_invalid")):
            with _t.refused(want):
                gate.confirm("approve budget", "a = 10; b = 20", "confirm", subj=S(), code=code,
                             relay_user=user, relay_at=at)
        passed = gate.confirm("approve budget", "a = 10; b = 20", "confirm", subj=S(), code=code,
                              relay_user="web:alice", relay_at=AT)
    assert passed == gate.Passed("relay", f"{USER}@relay", f" [relay user=web:alice at={AT}]")


def test_a_code_for_another_payload_is_refused_like_a_wrong_one():
    with secret(), off_terminal():
        other = gate.issue_code(S(items={"a": 10, "c": 20}))
        for code in (other, "000000"):
            with _t.refused("confirm_code_mismatch"):
                gate.confirm("approve budget", "a = 10; b = 20", "confirm", subj=S(), code=code,
                             relay_user="web:alice", relay_at=AT)


def test_at_a_terminal_the_person_retypes_and_no_code_is_consulted():
    with secret(), at_terminal("12") as prompts:
        passed = gate.confirm("confirm fact", "unit_cost = 12", "12", subj=S(), code="000000")
    assert passed == gate.Passed("tty", f"{USER}@tty", "")
    assert "unit_cost = 12" in prompts[0] and prompts[0].endswith("Type 12 to confirm: ")
    with secret(), at_terminal("13"), _t.refused("confirm_retype_mismatch"):
        gate.confirm("confirm fact", "unit_cost = 12", "12", subj=S())


def test_a_reason_is_required_and_who_wrote_is_derived_never_typed():
    for blank in (None, "", "  \n"):
        with _t.refused("reason_required"):
            gate.why(blank)
    assert gate.why("  owner said so  ") == "owner said so"
    assert gate.changed_by("cli") == f"{USER}@cli"
    assert not {"changed_by", "user", "who"} & set(inspect.signature(gate.confirm).parameters)


def test_any_other_refusal_is_one_coded_document_and_a_crash_is_unclassified():
    with _t.refused("reason_required") as c:
        gate.why("")
    assert gate.json_refusal(c.err, ["h.py"]) == {"error": str(c.err), "next": [],
                                                  "code": "reason_required", "params": {}}
    doc = gate.json_refusal(KeyError("boom"), ["h.py"])
    assert doc["code"] == "unclassified_error" and doc["next"] == [] and "subject" not in doc


# ----------------------------------------- the toy harness, a real process --

def test_a_pipe_cannot_answer_even_with_the_right_value_waiting():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "12"))
        for with_secret, want in ((False, "confirm_needs_human"), (True, "confirm_code_required")):
            rc, doc = toy(s, "confirm", "fact", "unit_cost", "--reason=ok",
                          with_secret=with_secret, text_in="12\nconfirm\n12\n")
            assert rc == 1 and doc["code"] == want, doc
        assert items(s)["fact:unit_cost"]["value"] is None and len(history(s)) == 1


def test_a_terminal_on_stdin_is_not_enough_the_answer_is_read_at_dev_tty():
    """A pty on stdin with the right answer already typed (a `script` wrapper fed
    from a file), but no controlling terminal: refused, nothing written."""
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "12"))
        master, slave = pty.openpty()
        try:
            os.write(master, b"12\n")
            rc, doc = toy(s, "confirm", "fact", "unit_cost", "--reason=ok", stdin=slave)
        finally:
            os.close(master)
            os.close(slave)
        assert rc == 1 and doc["code"] == "confirm_no_terminal", doc
        assert items(s)["fact:unit_cost"]["value"] is None and len(history(s)) == 1


def test_a_person_at_a_real_terminal_confirms_by_retyping_the_value():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "12"))
        argv = [TOY, "confirm", "fact", "unit_cost", "--reason=owner at the desk"]
        rc, shown = at_real_terminal(argv, toy_env(s, with_secret=False), "13")
        assert rc == 1 and "typed '13', expected '12'" in shown, shown
        assert items(s)["fact:unit_cost"]["value"] is None
        rc, shown = at_real_terminal(argv, toy_env(s, with_secret=False), "12")
        assert rc == 0 and "in north: fact unit_cost = 12" in shown, shown
        assert items(s)["fact:unit_cost"]["value"] == "12"
        assert history(s)[-1]["changed_by"] == f"{USER}@tty"
        assert history(s)[-1]["reason"] == "owner at the desk"


def test_a_relayed_code_approves_exactly_the_ids_shown_in_any_order_and_once():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("budget", "a", "10"), ("budget", "b", "20"), ("budget", "c", "20"),
                      ("fact", "a", "10"), ("fact", "b", "20"))
        rc, ch = toy(s, "confirm", "budget", "b", "a", "--reason=ok")
        code = ch["params"]["confirm_code"]
        assert rc == 1 and ch["code"] == "confirm_code_required" and ch["subject"]["items"] == \
            {"a": "10", "b": "20"}
        for label, argv in (("other ids: A,B shown, A,C asked", ["budget", "a", "c"]),
                            ("a subset", ["budget", "a"]),
                            ("a superset", ["budget", "a", "b", "c"]),
                            ("the same ids and values, another entity type", ["fact", "a", "b"])):
            rc, doc = toy(s, "confirm", *argv, "--reason=ok", f"--code={code}", *RELAY)
            assert rc == 1 and doc["code"] == "confirm_code_mismatch", (label, doc)
        assert len(history(s)) == 5 and all(v["value"] is None for v in items(s).values())
        rc, doc = toy(s, "confirm", "budget", "a", "b", "--reason=client ok", f"--code={code}", *RELAY)
        assert rc == 0 and doc["confirmed"] == ["budget:a", "budget:b"], doc
        assert [(h["item"], h["changed_by"], h["reason"]) for h in history(s)[5:]] == [
            (k, f"{USER}@relay", f"client ok [relay user=web:alice at={AT}]")
            for k in ("budget:a", "budget:b")]
        for eid, value in (("a", "10"), ("b", "20")):          # the identical values proposed again
            toy(s, "set", "budget", eid, value, "--reason=again")
        rc, doc = toy(s, "confirm", "budget", "a", "b", "--reason=replay", f"--code={code}", *RELAY)
        assert rc == 1 and doc["code"] == "confirm_code_mismatch", doc
        assert not [h for h in history(s) if h["reason"].startswith("replay")]


def test_a_relayed_code_is_bound_to_the_value_and_the_scope():
    with _t.tmpdir() as d:
        north = new_store(d, "north", ("fact", "unit_cost", "12"))
        south = new_store(d, "south", ("fact", "unit_cost", "12"))   # same ids, values and revs
        rc, ch = toy(north, "confirm", "fact", "unit_cost", "--value=12", "--reason=ok")
        code = ch["params"]["confirm_code"]
        for store, value in ((north, "13"), (south, "12")):
            rc, doc = toy(store, "confirm", "fact", "unit_cost", f"--value={value}", "--reason=ok",
                          f"--code={code}", *RELAY)
            assert rc == 1 and doc["code"] == "confirm_code_mismatch", doc
        assert items(south)["fact:unit_cost"]["value"] is None
        rc, doc = toy(north, "confirm", "fact", "unit_cost", "--value=12", "--reason=ok",
                      f"--code={code}", *RELAY)
        assert rc == 0 and items(north)["fact:unit_cost"]["value"] == "12", doc


def test_a_bad_set_is_refused_before_any_code_is_issued():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("budget", "a", "10"))
        rc, doc = toy(s, "confirm", "budget", "a", "zz", "--reason=ok")
        assert rc == 1 and doc["code"] == "nothing_to_confirm" and "confirm_code" not in str(doc), doc
        assert doc["params"] == {"entity_type": "budget", "missing": ["zz"], "idle": []}


# ------------------------------------------ console/relay.py, the real one --

DRIVER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import core, relay
a = json.loads(sys.argv[2])
ask, errors = core.validate_ask(a["ask"])
if errors:
    print(json.dumps({"invalid": errors}))
    sys.exit(3)
print(json.dumps(relay.run(ask, a["value"], cmd=a["cmd"], verbs=a["verbs"], user=a["user"],
                           reason=a["reason"], env=a["env"])))
"""


def example(name: str) -> dict:
    with open(os.path.join(CONSOLE, "examples", name), encoding="utf-8") as f:
        return json.load(f)


def console_relay(ask: dict, value: str, store: str, with_secret: bool = True) -> dict:
    """console/relay.py's run(), in a process of its own (its `core` is not ours),
    against the toy harness: what one click of the console does."""
    args = {"ask": ask, "value": value, "cmd": [sys.executable, "-B", TOY], "verbs": [["confirm"]],
            "user": "alice", "reason": "owner clicked", "env": toy_env(store, with_secret)}
    p = subprocess.run([sys.executable, "-B", "-c", DRIVER, CONSOLE, json.dumps(args)],
                       capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, (p.stdout, p.stderr)
    return json.loads(p.stdout)


def provide_ask() -> dict:
    ask = example("provide-number.json")
    ask["gate"] = {"verb": ["confirm", "fact", "unit_cost"], "value_arg": True,
                   "expect": {"items": {"unit_cost": "$value"}}}
    return ask


def test_console_relay_confirms_through_the_real_gate_and_the_audit_is_kept():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "4.10"))
        got = console_relay(provide_ask(), "4.20", s)
        assert got == {"ok": True, "code": None, "message": "Confirmed fact unit_cost = 4.20.",
                       "verb": ["confirm", "fact", "unit_cost"]}, got
        assert items(s)["fact:unit_cost"]["value"] == "4.20"
        last = history(s)[-1]
        assert last["changed_by"] == f"{USER}@relay"
        assert re.fullmatch(r"owner clicked \[relay user=web:alice at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]",
                            last["reason"]), last


def test_console_relay_stops_when_the_value_moved_after_the_page_showed_it():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "4.10"))
        ask = example("confirm-word.json")
        ask["gate"] = {"verb": ["confirm", "fact", "unit_cost"],
                       "expect": {"items": {"unit_cost": "4.10"}}}
        toy(s, "set", "fact", "unit_cost", "4.30", "--reason=agent changed it")
        got = console_relay(ask, "yes", s)
        assert got["ok"] is False and got["code"] == "gate_changed", got
        assert items(s)["fact:unit_cost"]["value"] is None and len(history(s)) == 2


def test_console_relay_without_a_code_secret_is_refused_in_the_harness_words():
    with _t.tmpdir() as d:
        s = new_store(d, "north", ("fact", "unit_cost", "4.10"))
        got = console_relay(provide_ask(), "4.20", s, with_secret=False)
        assert got["ok"] is False and got["code"] == "gate_refused", got
        assert got["message"].startswith("confirm fact needs a person at an interactive terminal"), got
        assert items(s)["fact:unit_cost"]["value"] is None


if __name__ == "__main__":
    _t.main(globals())
