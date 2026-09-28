#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""A stand-in for a harness's human gate, for the tests of relay.py and serve.py.

It speaks the console's relay protocol and nothing else:

    fake_harness.py <verb…> [--value=V] [--reason=R] [--json]
                    [--code=C --relay-user=U --relay-at=T]

Every option that takes a value takes it in the `--name=value` form, as the
console sends it; `--code C` is an unknown option (exit 2), so a console that
went back to two argv elements fails every exchange, not just one assertion.

Without `--code` it answers with a challenge: a fresh one-time code and the
`subject` the code is bound to, `{"verb": "<first two words>", "items":
{<word>: <value>}}` for every word after the second (the value is `--value`,
else "yes"; a numeric string becomes a number, so a match has to be by value).
With `--code` it accepts a code it issued, once, for the same verb and value,
and only with the relay's identity (`--relay-user=web:<name>`, `--relay-at=` in
UTC seconds); any other code is refused. State (the codes) is in the file named
by `FAKE_HARNESS_STATE`.

`FAKE_HARNESS_MODE` (default `ok`):

    ok         the whole exchange works
    no_secret  no confirm-code secret configured: no challenge, an error, exit 2
    changed    the challenge's subject is not what the ask asked for
    extra      the challenge's subject holds one more item than the verb names
               (`EXTRA`), and a run with the code would write it too
    slow       hangs (heartbeat files, see below) instead of answering
    fail       the challenge is fine, but the run with the code is refused
    noisy      ok, plus a banner before each document on stdout and a JSON
               line on stderr after it (only stdout is the answer)

Other knobs: `FAKE_HARNESS_LOG` (a file; one JSON line per call with its argv
with the code shown as `--code=***`, cwd, pid and how it was started), `FAKE_HARNESS_SLOW_RUN`
(1 or 2: which call hangs, default 1), `FAKE_HARNESS_SLOW_SECS` (how long,
default 30), `FAKE_HARNESS_GRANDCHILD=1` (a hanging call also forks a hanging
child of its own, like a wrapper that starts the real program),
`FAKE_HARNESS_CHALLENGE_EXIT` (the challenge's exit code, default 2).
A hanging process appends a byte to `<LOG>.beat.child` (or `.grandchild`) every
50 ms: a test sees that it was killed when the file stops growing.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import tempfile
import time

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

CHALLENGE = "confirm_code_required"
MODES = ("ok", "no_secret", "changed", "extra", "slow", "fail", "noisy")
EXTRA = "unrequested"
NO_SECRET = "Confirm codes are not enabled here; confirm at a terminal instead."
REFUSED = "The harness refused this value: it is outside what the policy allows."
BAD_CODE = "That confirm code is not valid."
NUMBER = re.compile(r"-?\d+(\.\d+)?")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
VALUED = {"--value=": "value", "--reason=": "reason", "--code=": "code",
          "--relay-user=": "user", "--relay-at=": "at"}


class Usage(Exception):
    pass


def parse(argv: list[str]) -> dict:
    a = {"words": [], "value": None, "reason": None, "json": False,
         "code": None, "user": None, "at": None}
    for x in argv:
        flag = next((f for f in VALUED if x.startswith(f)), None)
        if flag:
            a[VALUED[flag]] = x[len(flag):]
        elif x == "--json":
            a["json"] = True
        elif x.startswith("-"):
            raise Usage(f"unknown option {x[:40]!r}")
        else:
            a["words"].append(x)
    if len(a["words"]) < 2:
        raise Usage("usage: <group> <verb> [KEY…] [--value=V] [--reason=R] --json")
    if a["code"] is None and (a["user"] is not None or a["at"] is not None):
        raise Usage("--relay-user and --relay-at go with --code")
    return a


def subject(a: dict, *, changed: bool = False, extra: bool = False) -> dict:
    v = a["value"] if a["value"] is not None else "yes"
    x = (int(v) if re.fullmatch(r"-?\d+", v) else float(v)) if NUMBER.fullmatch(v) else v
    if changed:
        x = x + 1 if isinstance(x, (int, float)) else v + "!"
    items = {w: x for w in a["words"][2:]}
    if extra:
        items[EXTRA] = x
    return {"verb": " ".join(a["words"][:2]), "items": items}


def bind(a: dict) -> str:
    """What a code is issued for: the verb and the value, nothing else."""
    return json.dumps([a["words"], a["value"]])


@contextlib.contextmanager
def state():
    path = (os.environ.get("FAKE_HARNESS_STATE")
            or os.path.join(tempfile.gettempdir(), "fake_harness_state.json"))
    with open(path + ".lock", "a") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                st = json.load(f)
        except FileNotFoundError:
            st = {"codes": {}}
        yield st
        with open(path, "w") as f:
            json.dump(st, f)


def record(argv: list[str], a: dict | None, mode: str) -> None:
    path = os.environ.get("FAKE_HARNESS_LOG")
    if not path:
        return
    shown = ["--code=***" if x.startswith("--code=") else x for x in argv]

    def tty() -> bool:
        try:
            open("/dev/tty").close()
            return True
        except OSError:
            return False
    try:
        devnull = os.path.samestat(os.fstat(0), os.stat(os.devnull))
    except OSError:
        devnull = False
    row = {"argv": shown, "cwd": os.getcwd(), "pid": os.getpid(), "mode": mode,
           "run": 2 if a and a["code"] is not None else 1,
           "stdin_devnull": devnull, "tty": tty(),
           "session_leader": hasattr(os, "getsid") and os.getsid(0) == os.getpid(),
           "console_secret": "CONSOLE_SECRET" in os.environ}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def emit(doc: dict, code: int) -> int:
    print(json.dumps(doc))
    sys.stdout.flush()
    if os.environ.get("FAKE_HARNESS_MODE") == "noisy":
        print(json.dumps({"ok": False, "error": "noise on stderr"}), file=sys.stderr)
    return code


def beat(role: str, secs: float) -> None:
    path = os.environ.get("FAKE_HARNESS_LOG")
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if path:
            with open(f"{path}.beat.{role}", "a") as f:
                f.write("x")
        time.sleep(0.05)


def linger() -> None:
    secs = float(os.environ.get("FAKE_HARNESS_SLOW_SECS", "30"))
    if os.environ.get("FAKE_HARNESS_GRANDCHILD") == "1" and os.fork() == 0:
        beat("grandchild", secs)
        os._exit(0)
    beat("child", secs)


def first(a: dict, mode: str) -> int:
    if mode == "no_secret":
        return emit({"error": NO_SECRET, "code": "confirm_code_unavailable",
                     "params": {}}, 2)
    code = secrets.token_hex(6)
    with state() as st:
        st["codes"][code] = {"bind": bind(a), "used": False}
    return emit({"error": "A confirm code is required.", "code": CHALLENGE,
                 "params": {"confirm_code": code},
                 "subject": subject(a, changed=mode == "changed",
                                    extra=mode == "extra")},
                int(os.environ.get("FAKE_HARNESS_CHALLENGE_EXIT", "2")))


def second(a: dict, mode: str) -> int:
    if not (a["user"] and a["user"].startswith("web:") and STAMP.fullmatch(a["at"] or "")):
        return emit({"error": "--code needs --relay-user=web:<name> and --relay-at=<UTC time>",
                     "code": "bad_request"}, 2)
    with state() as st:
        entry = st["codes"].get(a["code"])
        if not entry or entry["used"] or entry["bind"] != bind(a):
            return emit({"error": BAD_CODE, "code": "confirm_code_invalid"}, 1)
        entry["used"] = True
    if mode == "fail":
        return emit({"error": REFUSED, "code": "refused"}, 1)
    items = subject(a, extra=mode == "extra")["items"]
    return emit({"ok": True, "message": "Confirmed " + ", ".join(items) + ".",
                 "result": {"confirmed": items, "by": a["user"], "at": a["at"],
                            "reason": a["reason"]}}, 0)


def main(argv: list[str]) -> int:
    mode = os.environ.get("FAKE_HARNESS_MODE", "ok")
    try:
        a = parse(argv)
    except Usage as e:
        record(argv, None, mode)
        return emit({"error": str(e), "code": "bad_request"}, 2)
    record(argv, a, mode)
    if mode not in MODES:
        return emit({"error": f"unknown FAKE_HARNESS_MODE {mode!r}",
                     "code": "bad_request"}, 2)
    run = 1 if a["code"] is None else 2
    if mode == "slow" and run == int(os.environ.get("FAKE_HARNESS_SLOW_RUN", "1")):
        linger()
    if not a["json"]:
        print("fake harness: pass --json")
        return 0
    if mode == "noisy":
        print("fake-harness 0.1 starting")
        print("reading the gate configuration")
    return first(a, mode) if run == 1 else second(a, mode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
