#!/usr/bin/env python3
"""The human gate (kit/human.py) holds each of its guards.

  1. TTY retype through the KIT_TTY seam: the right value passes (channel
     tty), a wrong one or no answer is refused and nothing is written;
     answers are consumed one per line; at a terminal --code is ignored.
  2. A pipe cannot answer: stdin holding the value never confirms, in
     process or in a child (piped, /dev/null, or a pty as stdin with no
     controlling terminal); a real controlling terminal (pty.fork) does.
  3. A code is issued off a TTY with the secret set, checked on rerun, and
     single use (the write moves the version); a relayed confirm needs its
     audit, and the audit is well formed (one token for the user, an ISO
     time with its zone) so it cannot forge the suffix; full-width digits
     count; no secret = no code.
  4. A code is bound to its subject: another value, id (a subset, a
     superset, another id), market, entity type, verb or version refuses
     it; id order does not matter.
  5. An expired slot is refused (2 slots back is the limit), a future one
     too.
  6. relay_audit, why, changed_by, typed_source, now, stale_basis.
  7. json_refusal's shape is exactly what console/relay.py parses, and an
     end-to-end exchange through console/relay.py (run in a child: the kit
     never imports the console) confirms, refuses a changed subject before
     the second call, and relays a no-secret refusal.
  8. human.py's msg() calls are closed over its fragment.
"""

import argparse
import contextlib
import getpass
import io
import json
import os
import pty
import select
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import contract, dates, human, messages  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, raises, tmp_dir)
from kit.testing.sandbox import sandbox_env  # noqa: E402

CFG = _shop.use()
os.environ.pop("KIT_TTY", None)     # this process is never at the seam
SECRET = CFG.env("CONFIRM_CODE_SECRET")
WHAT = "`shop facts confirm`"
CONSOLE = _shop.PLAYBOOK / "console"
T0 = 1_800_000_000 - 1_800_000_000 % human.WINDOW_S   # a slot boundary

# ---- an in-process gated verb: `shop facts confirm KEY [--value V]` --------

STATE: dict[str, tuple[str, int]] = {}


def prompt(key: str, value: str) -> str:
    return f"{key} [US] = {value} — retype the value to confirm it: "


def gated(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="shop facts")
    p.add_argument("verb", choices=["confirm"])
    p.add_argument("key")
    p.add_argument("--value")
    human.add_gate_args(p)
    contract.add_json_arg(p)

    def fn(argv):
        a = p.parse_args(argv)
        value, version = STATE[a.key]
        value = value if a.value is None else a.value
        reason = human.why(a.reason)
        subj = human.subject("facts confirm", "US", "fact", {a.key: value},
                             version)
        channel = human.confirm(WHAT, prompt(a.key, value), value, subj=subj,
                                code=a.code)
        reason += human.relay_audit(channel, a.relay_user, a.relay_at)
        STATE[a.key] = (value, version + 1)
        return {"confirmed": {a.key: value},
                "changed_by": human.changed_by(channel), "reason": reason}
    return contract.run_main(fn, argv, cmd=["shop", "facts", *argv])


def fresh() -> None:
    STATE.clear()
    STATE["unit_cost"] = ("4.2", 7)


BASE = ["confirm", "unit_cost", "--reason", "invoice", "--json"]


def code_of(out: str) -> str | None:
    return ((one_doc(out) or {}).get("params") or {}).get("confirm_code")


# ---- a gated verb as a child process -----------------------------------------

GATE = r'''
import argparse, json, os, sys
sys.path.insert(0, os.environ["PLAYBOOK"])
from kit import contract, human

def main(argv):
    p = argparse.ArgumentParser(prog="shop facts")
    p.add_argument("verb", choices=["confirm"])
    p.add_argument("key")
    p.add_argument("--value")
    human.add_gate_args(p)
    contract.add_json_arg(p)
    a = p.parse_args(argv)
    path = os.environ["GATE_STATE"]
    try:
        with open(path) as f:
            st = json.load(f)
    except FileNotFoundError:
        st = {"version": 0, "writes": []}
    value = a.value if a.value is not None else "yes"
    reason = human.why(a.reason)
    bound = value + os.environ.get("GATE_SKEW", "")
    subj = human.subject("facts confirm", "US", "fact", {a.key: bound},
                         st["version"])
    channel = human.confirm("`shop facts confirm`",
                            f"{a.key} [US] = {value} — retype it: ", value,
                            subj=subj, code=a.code)
    reason += human.relay_audit(channel, a.relay_user, a.relay_at)
    st["version"] += 1
    st["writes"].append({"key": a.key, "value": value, "reason": reason,
                         "changed_by": human.changed_by(channel)})
    with open(path, "w") as f:
        json.dump(st, f)
    return {"ok": True, "message": f"Confirmed {a.key} = {value}.",
            "confirmed": {a.key: value}}

argv = sys.argv[1:]
argv = argv[1:] if argv[:1] == ["facts"] else argv   # the console sends the group word
raise SystemExit(contract.run_main(main, argv, cmd=["shop", "facts", *argv]))
'''

RELAY = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import core, relay
with open(sys.argv[2]) as f:
    ask, errs = core.validate_ask(json.load(f))
assert not errs, errs
cfg = json.loads(sys.argv[3])
print(json.dumps(relay.run(ask, cfg["value"], cmd=cfg["cmd"], verbs=cfg["verbs"],
                           user="alice", reason="matches the invoice",
                           timeout=30.0, env=cfg["env"])))
'''


class Child:
    """The GATE script in a sandbox: its state file says what it wrote."""

    def __init__(self, **extra: str | None):
        d = Path(tmp_dir("gate-"))
        self.script = d / "gate.py"
        self.script.write_text(GATE, encoding="utf-8")
        self.state = d / "state.json"
        self.env = sandbox_env(d, PLAYBOOK=str(_shop.PLAYBOOK),
                               GATE_STATE=str(self.state),
                               PYTHONDONTWRITEBYTECODE="1", **extra)
        self.cmd = [sys.executable, str(self.script)]

    def writes(self) -> list[dict]:
        if not self.state.exists():
            return []
        return json.loads(self.state.read_text(encoding="utf-8"))["writes"]

    def run(self, argv, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(self.cmd + argv, env=self.env,
                              capture_output=True, text=True, timeout=60, **kw)


ARGV = ["confirm", "unit_cost", "--value", "4.2", "--reason", "r", "--json"]


def test_tty_seam() -> None:
    print("[1] TTY retype through the KIT_TTY seam")
    fresh()
    rc, out, err = capture(gated, BASE, env=clean_env(), tty_answers=["4.2"])
    doc = one_doc(out)
    check("the right value retyped: exit 0, channel tty, the prompt shown",
          rc == 0 and doc["changed_by"].endswith("@tty")
          and doc["reason"] == "invoice"
          and prompt("unit_cost", "4.2") in err and STATE["unit_cost"][1] == 8,
          (rc, out, err))

    fresh()
    rc, out, err = capture(gated, BASE, env=clean_env(), tty_answers=["4.20"])
    doc = one_doc(out)
    check("a mistyped value: exit 2, confirm_typed_mismatch, nothing written",
          rc == 2 and doc["code"] == "confirm_typed_mismatch"
          and doc["params"] == {"what": WHAT, "typed": "4.20",
                                "expected": "4.2"}
          and doc["next"] == [] and STATE["unit_cost"][1] == 7,
          (rc, out, err))

    rc, out, _ = capture(gated, BASE, env=clean_env(), tty_answers=[])
    check("no answer at the terminal: confirm_tty_eof, nothing written",
          rc == 2 and one_doc(out)["code"] == "confirm_tty_eof"
          and STATE["unit_cost"][1] == 7, out)

    rc, out, _ = capture(gated, [*BASE, "--code", "000000"], env=clean_env(),
                         tty_answers=["4.2"])
    check("at a terminal --code is never consulted: the retype decides",
          rc == 0 and one_doc(out)["changed_by"].endswith("@tty"), out)

    fresh()
    rc, out, _ = capture(gated, BASE, env=clean_env(), stdin="nope\n",
                         tty_answers=["4.2"])
    check("the terminal answers, stdin never does", rc == 0, out)

    f = Path(tmp_dir("tty-")) / "answers"
    f.write_text("first\nsecond\n", encoding="utf-8")
    shown = io.StringIO()
    with mock.patch.dict(os.environ, {human.TTY_ENV: str(f)}), \
            contextlib.redirect_stderr(shown):
        got = [human.read_tty("1? "), human.read_tty("2? ")]
        eof = raises(lambda: human.read_tty("3? "), human.Refused)
    check("seam answers are consumed one per line, then EOF; each prompt "
          "shown on stderr", shown.getvalue() == "1? 2? 3? "
          and got == ["first", "second"] and messages.code(eof.message)["code"]
          == "confirm_tty_eof", (got, eof))
    with mock.patch.dict(os.environ, {human.TTY_ENV: str(f) + ".missing"}):
        e = raises(lambda: human.read_tty("? "), human.Refused)
    check("a seam path that cannot be read: confirm_no_terminal",
          e is not None and e.message.code == "confirm_no_terminal", e)


def test_pipe_cannot_answer() -> None:
    print("[2] a pipe cannot answer")
    fresh()
    rc, out, err = capture(gated, BASE, env=clean_env(), stdin="4.2\n")
    doc = one_doc(out)
    check("stdin holds the value, no secret: confirm_needs_human, exit 2, "
          "one doc, empty stderr, nothing written",
          rc == 2 and doc["code"] == "confirm_needs_human"
          and doc["params"] == {"what": WHAT} and err == ""
          and STATE["unit_cost"][1] == 7, (rc, out, err))
    rc, out, _ = capture(gated, BASE, env=clean_env(**{SECRET: "s3"}),
                         stdin="4.2\n")
    check("with the secret set the piped value still confirms nothing: a "
          "challenge", rc == 2 and one_doc(out)["code"]
          == "confirm_code_required" and STATE["unit_cost"][1] == 7, out)

    child = Child()
    for how, kw in (("piped", {"input": "4.2\n4.2\n"}),
                    ("/dev/null", {"stdin": subprocess.DEVNULL})):
        r = child.run(ARGV, **kw)
        check(f"a child with stdin {how}: confirm_needs_human, nothing "
              f"written", r.returncode == 2
              and one_doc(r.stdout)["code"] == "confirm_needs_human"
              and child.writes() == [], (r.returncode, r.stdout, r.stderr))

    master, slave = pty.openpty()
    try:
        p = subprocess.Popen(child.cmd + ARGV, stdin=slave,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=child.env, start_new_session=True, text=True)
        os.close(slave)
        os.write(master, b"4.2\n")
        out, err = p.communicate(timeout=60)
    finally:
        os.close(master)
    check("stdin is a terminal but there is no controlling one: the value "
          "typed into stdin is never read (confirm_no_terminal)",
          p.returncode == 2 and one_doc(out)["code"] == "confirm_no_terminal"
          and child.writes() == [], (p.returncode, out, err))

    for typed, ok in (("4.3", False), ("4.2", True)):
        rc = tty_child(child, typed)
        w = child.writes()
        if ok:
            check("a real controlling terminal (pty.fork): the retyped value "
                  "confirms, channel tty", rc == 0 and len(w) == 1
                  and w[0]["changed_by"].endswith("@tty"), (rc, w))
        else:
            check("… and a mistyped one is refused, nothing written",
                  rc == 2 and w == [], (rc, w))


def tty_child(child: Child, typed: str) -> int | None:
    """Run the gate with a pty as its controlling terminal; type `typed`."""
    pid, fd = pty.fork()
    if pid == 0:  # the child
        try:
            os.execve(sys.executable, [*child.cmd, *ARGV], child.env)
        finally:
            os._exit(127)
    deadline = time.monotonic() + 60
    try:
        os.write(fd, typed.encode() + b"\n")
        while True:
            ready, _, _ = select.select([fd], [], [],
                                        max(0.0, deadline - time.monotonic()))
            if not ready:
                os.kill(pid, signal.SIGKILL)
                break
            try:
                if not os.read(fd, 4096):
                    break
            except OSError:
                break
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def test_code_issued_and_checked() -> None:
    print("[3] a code off a TTY: issued, checked, single use, audited")
    env = clean_env(**{SECRET: "s3cret"})
    fresh()
    rc, out, err = capture(gated, BASE, env=env)
    doc, c = one_doc(out), code_of(out)
    subj = human.subject("facts confirm", "US", "fact", {"unit_cost": "4.2"}, 7)
    want = {
        "error": doc and doc["error"],
        "next": [f"{shlex.join(['shop', 'facts', *BASE])} --code {c} "
                 f"--relay-user <sender_id> --relay-at <iso_time>"],
        "code": "confirm_code_required",
        "params": {"what": WHAT, "summary": prompt("unit_cost", "4.2").strip(),
                   "confirm_code": c, "ttl_minutes": 15},
        "subject": json.loads(subj)}
    check("no --code yet: exit 2, the challenge document exactly, keys in "
          "order, empty stderr, nothing written",
          rc == 2 and doc == want and list(doc) == list(want) and err == ""
          and c and len(c) == 6 and c.isdigit() and STATE["unit_cost"][1] == 7
          and c in doc["error"] and "Nothing was written." in doc["error"],
          (rc, out, err))
    with mock.patch.dict(os.environ, {SECRET: "s3cret"}):
        ok = human.check_code(subj, c) if c else False
    check("the code is the one issue_code gives for the subject now", ok, c)

    rc, out, _ = capture(gated, [*BASE, "--code", c], env=env)
    check("--code without --relay-user/--relay-at: "
          "confirm_relay_audit_missing, nothing written",
          rc == 2 and one_doc(out)["code"] == "confirm_relay_audit_missing"
          and STATE["unit_cost"][1] == 7, out)

    relay = ["--code", c, "--relay-user", "test:owner",
             "--relay-at", "2026-09-28T00:00:00Z"]
    rc, out, _ = capture(gated, [*BASE, *relay], env=env)
    doc = one_doc(out)
    check("the code relayed back with its audit: confirmed, channel relay, "
          "the audit on the reason",
          rc == 0 and doc["changed_by"].endswith("@relay")
          and doc["reason"] == "invoice [relay user=test:owner "
                               "at=2026-09-28T00:00:00Z]"
          and STATE["unit_cost"][1] == 8, out)
    rc, out, _ = capture(gated, [*BASE, *relay], env=env)
    doc = one_doc(out)
    check("the same code again: confirm_code_mismatch (the write moved the "
          "version: single use)",
          rc == 2 and doc["code"] == "confirm_code_mismatch"
          and doc["params"] == {"what": WHAT, "confirm_code": c,
                                "ttl_minutes": 15}
          and STATE["unit_cost"][1] == 8, out)

    fresh()
    _, out, _ = capture(gated, BASE, env=env)
    c = code_of(out)
    rc, out, _ = capture(gated, [*BASE, f"--code={c}",
                                 "--relay-user=web:alice",
                                 "--relay-at=2026-09-28T01:02:03Z"], env=env)
    check("the console's --name=value form works", rc == 0
          and one_doc(out)["reason"].endswith("[relay user=web:alice "
                                              "at=2026-09-28T01:02:03Z]"), out)

    fresh()
    _, out, _ = capture(gated, BASE, env=env)
    c = code_of(out)
    at = "2026-09-28T01:02:03Z"
    for label, who, when in (
            ("a user with a space", "web alice", at),
            ("a user that closes the suffix and opens another",
             "web:a] [relay user=owner", at),
            ("a user of 121 characters", "u" * 121, at),
            ("a time that is not a time", "web:alice", "yesterday"),
            ("a time with no zone", "web:alice", "2026-09-28T01:02:03")):
        rc, out, _ = capture(gated, [*BASE, "--code", c, "--relay-user", who,
                                     "--relay-at", when], env=env)
        d = one_doc(out)
        check(f"{label}: confirm_relay_audit_invalid, nothing written, the "
              f"code still unused",
              rc == 2 and d["code"] == "confirm_relay_audit_invalid"
              and d["params"] == {"field": "relay_at" if who == "web:alice"
                                  else "relay_user"}
              and STATE["unit_cost"][1] == 7, out)
    rc, out, _ = capture(gated, [*BASE, "--code", c, "--relay-user",
                                 "web:alice", "--relay-at", at], env=env)
    check("… and the same code then passes with a valid audit",
          rc == 0 and one_doc(out)["reason"].endswith(
              f"[relay user=web:alice at={at}]"), out)

    fresh()
    _, out, _ = capture(gated, BASE, env=env)
    c = code_of(out)
    wide = "".join(chr(ord(ch) + 0xFEE0) for ch in c)
    rc, out, _ = capture(gated, [*BASE, "--code", wide, *relay[2:]], env=env)
    check("full-width digits typed on a phone count", rc == 0, out)
    fresh()
    rc, out, _ = capture(gated, [*BASE, "--code", "１２三四五六", *relay[2:]],
                         env=env)
    check("a non-ASCII code is refused, never a crash",
          rc == 2 and one_doc(out)["code"] == "confirm_code_mismatch", out)

    with mock.patch.dict(os.environ, {SECRET: ""}):
        e = raises(lambda: human.issue_code(subj), human.Refused)
    check("no secret: no code can be issued (confirm_code_unavailable)",
          e is not None and messages.code(e.message) == {
              "code": "confirm_code_unavailable",
              "params": {"var": SECRET}}, e)
    with mock.patch.dict(os.environ, {SECRET: "one"}):
        c1 = human.issue_code(subj, at=T0)
    with mock.patch.dict(os.environ, {SECRET: "two"}):
        other = human.check_code(subj, c1, at=T0)
    check("a code from another secret does not check", other is False)


def test_bound_to_subject() -> None:
    print("[4] a code is bound to its subject")
    base = ("facts confirm", "US", "fact", {"a": "1", "b": ["x", 2]}, 5)
    variants = {
        "one id fewer (a subset)": ("facts confirm", "US", "fact",
                                    {"a": "1"}, 5),
        "another value": ("facts confirm", "US", "fact",
                          {"a": "2", "b": ["x", 2]}, 5),
        "another id": ("facts confirm", "US", "fact",
                       {"a": "1", "c": ["x", 2]}, 5),
        "one more id": ("facts confirm", "US", "fact",
                        {"a": "1", "b": ["x", 2], "c": "1"}, 5),
        "another market": ("facts confirm", "CA", "fact",
                           {"a": "1", "b": ["x", 2]}, 5),
        "another entity type": ("facts confirm", "US", "decision",
                                {"a": "1", "b": ["x", 2]}, 5),
        "another verb": ("decisions confirm", "US", "fact",
                         {"a": "1", "b": ["x", 2]}, 5),
        "a moved version": ("facts confirm", "US", "fact",
                            {"a": "1", "b": ["x", 2]}, 6),
    }
    with mock.patch.dict(os.environ, {SECRET: "s3cret"}):
        subj = human.subject(*base)
        c = human.issue_code(subj, at=T0)
        check("the subject's own code checks", human.check_code(subj, c, at=T0))
        for label, v in variants.items():
            check(f"{label}: refused",
                  not human.check_code(human.subject(*v), c, at=T0))
    swapped = human.subject("facts confirm", "US", "fact",
                            {"b": ["x", 2], "a": "1"}, 5)
    check("id order does not matter; the subject is canonical JSON",
          swapped == subj and subj == json.dumps(
              json.loads(subj), sort_keys=True, separators=(",", ":")),
          (swapped, subj))


def test_expiry() -> None:
    print("[5] an expired slot is refused")
    with mock.patch.dict(os.environ, {SECRET: "s3cret"}):
        subj = human.subject("facts confirm", "US", "fact", {"k": "v"}, 1)
        c = human.issue_code(subj, at=T0)
        for back, ok in ((0, True), (1, True), (2, True), (3, False)):
            got = human.check_code(subj, c, at=T0 + back * human.WINDOW_S)
            check(f"{back} slot(s) later: {'accepted' if ok else 'expired'}",
                  got is ok)
        check("a code of a future slot is refused",
              not human.check_code(subj, human.issue_code(
                  subj, at=T0 + human.WINDOW_S), at=T0))
        check("TTL_MINUTES = 15 (300 s x 3)", human.TTL_MINUTES == 15)
    env = clean_env(**{SECRET: "s3cret"})
    fresh()
    with mock.patch.dict(os.environ, {SECRET: "s3cret"}):
        old = human.issue_code(human.subject(
            "facts confirm", "US", "fact", {"unit_cost": "4.2"}, 7),
            at=time.time() - 3 * human.WINDOW_S)
    rc, out, _ = capture(gated, [*BASE, "--code", old, "--relay-user", "u",
                                 "--relay-at", "t"], env=env)
    check("through the verb: a code issued 3 slots ago is refused, nothing "
          "written", rc == 2 and one_doc(out)["code"] == "confirm_code_mismatch"
          and STATE["unit_cost"][1] == 7, out)


def test_small_helpers() -> None:
    print("[6] relay_audit, why, changed_by, typed_source, now, stale_basis")
    for user, at in ((None, "t"), ("u", None), ("", "")):
        e = raises(lambda: human.relay_audit("relay", user, at), human.Refused)
        check(f"relay without user={user!r} at={at!r}: "
              f"confirm_relay_audit_missing",
              e is not None and e.message.code == "confirm_relay_audit_missing")
    check("relay_audit: '' off the relay, the suffix on it",
          human.relay_audit("tty", None, None) == ""
          and human.relay_audit("relay", "web:a", "2026-09-28T00:00:00+08:00")
          == " [relay user=web:a at=2026-09-28T00:00:00+08:00]")
    for user, at, field in (("a b", "2026-09-28T00:00:00Z", "relay_user"),
                            ("a]", "2026-09-28T00:00:00Z", "relay_user"),
                            ("[a", "2026-09-28T00:00:00Z", "relay_user"),
                            ("a\nb", "2026-09-28T00:00:00Z", "relay_user"),
                            ("a", "T", "relay_at"),
                            ("a", "2026-09-28", "relay_at"),
                            ("a", "2026-09-28T00:00:00", "relay_at")):
        e = raises(lambda: human.relay_audit("relay", user, at), human.Refused)
        check(f"relay_audit({user!r}, {at!r}): confirm_relay_audit_invalid "
              f"on {field}",
              e is not None and messages.code(e.message) == {
                  "code": "confirm_relay_audit_invalid",
                  "params": {"field": field}}, e)
    for bad in (None, "", "   "):
        e = raises(lambda: human.why(bad), human.Refused)
        check(f"why({bad!r}): reason_required",
              e is not None and messages.code(e.message) == {
                  "code": "reason_required", "params": {}})
    check("why() strips", human.why("  because  ") == "because")
    check("changed_by: <OS user>@<channel>, derived",
          human.changed_by("cli") == f"{getpass.getuser()}@cli"
          and human.changed_by("tty") == f"{getpass.getuser()}@tty")
    fixed = datetime(2026, 9, 28, 23, 59, 58, 123456, tzinfo=timezone.utc)
    with mock.patch.object(dates, "now", lambda: fixed):
        check("typed_source / now read kit.dates.now",
              human.typed_source() == "human_confirmed_2026-09-28"
              and human.now() == "2026-09-28T23:59:58.123456+00:00",
              (human.typed_source(), human.now()))
    check("stale_basis: equal = fresh, moved or missing = stale",
          not human.stale_basis("abc", "abc") and human.stale_basis("abc", "abd")
          and human.stale_basis(None, "abc") and human.stale_basis("abc", "")
          and human.stale_basis("", ""))
    p = argparse.ArgumentParser()
    human.add_gate_args(p)
    a = p.parse_args(["--code=-12345", "--relay-user=web:bo",
                      "--relay-at=T", "--reason=r"])
    check("add_gate_args: --code/--relay-user/--relay-at/--reason, the "
          "--name=value form keeps a leading dash",
          (a.code, a.relay_user, a.relay_at, a.reason)
          == ("-12345", "web:bo", "T", "r"), a)
    p = argparse.ArgumentParser()
    p.add_argument("--reason", required=True)
    human.add_gate_args(p, reason=False)
    check("add_gate_args(reason=False) leaves the verb's own --reason",
          p.parse_args(["--reason", "x"]).reason == "x")
    check("CodeRequired is a Refused is a HarnessError (exit 2)",
          issubclass(human.CodeRequired, human.Refused)
          and issubclass(human.Refused, contract.HarnessError))


def relay_parses(doc) -> str | None:
    """console/relay.py's reading of a challenge (_is_challenge, _code_of,
    subject a dict), restated: the code it would send, else None."""
    if not (isinstance(doc, dict) and doc.get("code") == "confirm_code_required"):
        return None
    c = (doc.get("params") or {}).get("confirm_code")
    ok = (isinstance(c, str) and 0 < len(c) <= 200 and c.isprintable()
          and not any(ch.isspace() for ch in c))
    return c if ok and isinstance(doc.get("subject"), dict) else None


def test_json_refusal_and_console() -> None:
    print("[7] json_refusal as console/relay.py parses it; an exchange "
          "through relay.py")
    cmd = ["shop", "facts", "confirm", "unit_cost", "--json"]
    with mock.patch.dict(os.environ, {SECRET: "s3cret"}), \
            mock.patch.object(sys, "stdin", io.StringIO()):
        subj = human.subject("facts confirm", "US", "fact", {"unit_cost": 4}, 1)
        e = raises(lambda: human.confirm(WHAT, "unit_cost = 4 ", "4", subj=subj),
                   human.CodeRequired)
    doc = human.json_refusal(e, cmd)
    line = json.dumps(doc, ensure_ascii=False)
    check("a challenge: {error, next, code, params, subject}, the code a "
          "string relay.py would send, subject the bound payload",
          list(doc) == ["error", "next", "code", "params", "subject"]
          and relay_parses(json.loads(line)) == e.confirm_code
          and doc["subject"] == json.loads(subj)
          and doc["next"] == [f"{shlex.join(cmd)} --code {e.confirm_code} "
                              "--relay-user <sender_id> --relay-at <iso_time>"]
          and doc == contract.failure_doc(e, cmd), doc)
    plain = human.json_refusal(human.Refused(messages.msg(
        "confirm_relay_audit_missing", "x")), cmd)
    check("a plain refusal: {error, next [], code, params}, no subject, not "
          "a challenge", plain == {"error": "x", "next": [],
                                   "code": "confirm_relay_audit_missing",
                                   "params": {}}
          and relay_parses(plain) is None, plain)
    crash = human.json_refusal(sqlite3.OperationalError("disk I/O"), cmd)
    check("anything else: unclassified_error with the raw text",
          crash == {"error": "disk I/O", "next": [],
                    "code": "unclassified_error",
                    "params": {"detail": "disk I/O"}}, crash)

    ask = CONSOLE / "examples" / "provide-number.json"
    if not (CONSOLE / "relay.py").is_file():
        check("console/relay.py is present in the playbook", False, CONSOLE)
        return

    def exchange(child: Child) -> dict:
        env = {**child.env, "PYTHONDONTWRITEBYTECODE": "1"}
        cfg = json.dumps({"value": "4.20", "cmd": child.cmd,
                          "verbs": [["facts", "confirm"]], "env": child.env})
        r = subprocess.run([sys.executable, "-c", RELAY, str(CONSOLE),
                            str(ask), cfg], env=env, capture_output=True,
                           text=True, timeout=120)
        return one_doc(r.stdout) or {"stdout": r.stdout, "stderr": r.stderr}

    child = Child(**{SECRET: "s3cret"})
    got = exchange(child)
    w = child.writes()
    check("relay.run through the kit gate: ok, one write, channel relay, "
          "the console's identity and UTC time in the audit",
          got.get("ok") is True and got.get("code") is None
          and len(w) == 1 and w[0]["changed_by"].endswith("@relay")
          and w[0]["value"] == "4.20"
          and w[0]["reason"].startswith("matches the invoice [relay "
                                        "user=web:alice at=20")
          and w[0]["reason"].endswith("Z]"), (got, w))

    child = Child(**{SECRET: "s3cret", "GATE_SKEW": "1"})
    got = exchange(child)
    check("a subject other than what the human was shown: gate_changed, the "
          "code never sent, nothing written",
          got.get("ok") is False and got.get("code") == "gate_changed"
          and child.writes() == [], (got, child.writes()))

    child = Child()
    got = exchange(child)
    check("no secret: the kit's refusal comes back as gate_refused with its "
          "text, nothing written",
          got.get("ok") is False and got.get("code") == "gate_refused"
          and "must be run by a human" in got.get("message", "")
          and child.writes() == [], got)


def test_closure() -> None:
    print("[8] human.py keeps its registry fragment closed")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "human.tsv"
            or c == "reason_required"}
    probs = messages.check_registry_closed(_shop.KIT, ["human.py"], mine,
                                           strict_kit=True)
    check("every msg() in human.py is literal, registered with exact "
          "params, and every human.tsv code is emitted", probs == [], probs)
    names = {"confirm_needs_human", "confirm_code_required",
             "confirm_code_mismatch", "confirm_relay_audit_missing",
             "confirm_relay_audit_invalid"}
    check("the reference gate code names are kept exactly",
          names <= set(mine), sorted(names - set(mine)))


def main() -> int:
    for fn in (test_tty_seam, test_pipe_cannot_answer,
               test_code_issued_and_checked, test_bound_to_subject,
               test_expiry, test_small_helpers,
               test_json_refusal_and_console, test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
