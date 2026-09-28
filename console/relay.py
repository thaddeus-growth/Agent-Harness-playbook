"""Runs a harness's own human gate for one click of the console.

A harness that wants a human in the loop answers a first call with a challenge
(a one-time code bound to exactly what it would write) and accepts the same call
again with that code. `run()` is the console's side of that exchange:

    1 ask     <cmd> <verb…> [--value=V] --reason=R --json     stdin /dev/null
    2 read    the harness's one JSON document (stdout only)
    3 check   the payload the code is bound to is what the human was shown
    4 answer  the same call + --code=C --relay-user=web:U --relay-at=T

The harness's challenge is `{"code": "confirm_code_required", "params":
{"confirm_code": C}, "subject": {…}}`; its answers carry text in `error`,
`message` or `result` (a string). A document that is not the challenge is the
final answer: the harness's exit code says whether it worked, which is also how
an idempotent "already confirmed" ends the exchange at step 1. After step 4 a
failure counts as a refusal only when the harness said so in a document with
`error` or `message`; a crash, garbage or silence is `gate_unsure`, because the
harness may have written before it stopped.

What this module guarantees, and the tests hold it to:

  * `run()` never raises for a harness problem: every failure is a dict with a
    `code` from `core.CODES` (`gate_unavailable`, `gate_refused`, `gate_changed`,
    `gate_unsure`, `gate_timeout`) and one plain line of text.
  * Nothing runs unless the ask has a well-formed gate whose verb starts with one
    of the operator's allowed verbs and the answer is one `core.check_value`
    accepts. The command is the operator's, never the ask's; a "no" never runs.
  * No shell. Every argument is its own list item, and everything the console
    adds is one `--name=value` element (`--value`, `--reason`, and on the second
    call `--code`, `--relay-user`, `--relay-at`), so nothing the human types and
    no code the harness issues, even one starting with `-`, can be read as
    another option or become another command.
  * The code is sent only when the subject matches what was shown
    (`core.matches`: everything the ask names is in it exactly, an extra item
    is not allowed, only the harness's own envelope may say more); a mismatch
    is `gate_changed` and the second call never happens. The code appears in no
    result, exception or log line (a harness that echoes it back has it
    scrubbed), and the harness never sees the console's own secret
    (`CONSOLE_SECRET`).
  * A harness that hangs is killed with everything it started, on either call.

Only this module in the console starts a process.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import unicodedata

import core

CHALLENGE = "confirm_code_required"
MAX_MESSAGE, MAX_REASON = 300, 200
_BIDI = frozenset("‎‏؜‪‫‬‭‮"
                  "⁦⁧⁨⁩")

log = logging.getLogger("console.relay")

UNAVAILABLE = "This answer cannot be sent to the harness from this console."
UNSENDABLE = "That answer is not one the harness can be given; nothing was written."
NO_NEVER = "A no is never sent to the harness; nothing was written."
WHO = "The console cannot tell the harness who is answering; nothing was written."
UNREADABLE = "The harness answered in a way the console could not read; nothing was written."
BAD_CHALLENGE = "The harness asked for a confirmation the console could not read; nothing was written."
ASKED_AGAIN = "The harness asked for a code again instead of answering; nothing was written."
LATE = "The harness did not answer in time. It may have written before it stopped; ask the agent to check."


class _Timeout(Exception):
    pass


def run(ask: dict, value: str, *, cmd: list[str], verbs: list[list[str]],
        user: str, reason: str, timeout: float = 60.0,
        env: dict | None = None) -> dict:
    """Send one answer through the harness's gate.

    -> {"ok": bool, "code": None | "gate_refused" | "gate_changed" |
        "gate_unsure" | "gate_timeout" | "gate_unavailable", "message": one line
        of at most 300 characters, "verb": the ask's verb}

    `cmd` is the harness command (argv), `verbs` the allowed verb prefixes (word
    lists), `user` the human's name (the harness is told `web:<user>`), `reason`
    is cleaned to one line of at most 200 characters, `timeout` applies to each
    of the two calls, `env` is the child's environment (default: ours).
    """
    verb = _verb(ask, verbs)
    if verb is None:
        return _out(False, "gate_unavailable", UNAVAILABLE, [])

    def fail(code, message):
        return _out(False, code, message, verb)

    if not (isinstance(cmd, list) and cmd
            and all(isinstance(w, str) and _sendable(w) for w in cmd)):
        return fail("gate_unavailable", UNAVAILABLE)
    norm, err = core.check_value(ask, value) if isinstance(value, str) else (None, True)
    if err or not _sendable(norm):
        return fail("gate_refused", UNSENDABLE)
    if not core.gate_runs(ask, norm):
        return fail("gate_refused", NO_NEVER)
    if not (isinstance(user, str) and core.TOKEN_RE.fullmatch(user)):
        return fail("gate_refused", WHO)

    argv = cmd + verb
    if ask["gate"].get("value_arg", False):
        argv.append(f"--value={norm}")
    argv += ["--reason=" + _plain(reason or "")[:MAX_REASON], "--json"]

    try:
        status, out = _exec(argv, timeout, env, 1)
    except _Timeout:
        return fail("gate_timeout", core.CODES["gate_timeout"])
    except OSError:
        return fail("gate_unavailable", "The harness command could not be started.")
    doc = _parse(out)
    if doc is None:
        return fail("gate_refused", UNREADABLE)
    if not _is_challenge(doc):
        return _answer(status, doc, verb, None)

    secret = _code_of(doc)
    if secret is None:
        return fail("gate_refused", BAD_CHALLENGE)
    if not core.matches(core.expect_for(ask, norm), doc.get("subject")):
        return fail("gate_changed", core.CODES["gate_changed"])

    argv += [f"--code={secret}", f"--relay-user=web:{user}",
             f"--relay-at={core.now()}"]
    try:
        status, out = _exec(argv, timeout, env, 2)
    except _Timeout:
        return fail("gate_timeout", LATE)
    except OSError:
        return fail("gate_unavailable", "The harness command could not be started.")
    doc = _parse(out)
    if _is_challenge(doc):
        return fail("gate_refused", ASKED_AGAIN)
    if doc is None or (status != 0 and not _says(doc)):
        return fail("gate_unsure", core.CODES["gate_unsure"])
    return _answer(status, doc, verb, secret)


# ------------------------------------------------------------ checking --

def _verb(ask, verbs) -> list[str] | None:
    """The gate's verb (a copy) if the ask carries a well-formed gate and the verb
    starts with an allowed prefix, else None."""
    gate = ask.get("gate") if isinstance(ask, dict) else None
    if not (isinstance(gate, dict) and ask.get("step") in core.STEPS):
        return None
    verb, expect = gate.get("verb"), gate.get("expect")
    if not (isinstance(verb, list) and 1 <= len(verb) <= core.LIMITS["verb"]
            and all(isinstance(w, str) and core.TOKEN_RE.fullmatch(w) for w in verb)
            and isinstance(expect, dict) and expect
            and isinstance(gate.get("value_arg", False), bool)):
        return None
    if not any(isinstance(p, list) and p and verb[:len(p)] == p
               for p in verbs or []):
        return None
    return list(verb)


def _sendable(text: str) -> bool:
    """Can this be one argument of a child process (no NUL, encodable)?"""
    try:
        os.fsencode(text)
    except UnicodeError:
        return False
    return "\x00" not in text


def _plain(text) -> str:
    """One line of visible characters: control characters, direction marks and
    every kind of line break become single spaces."""
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Cs") or c in _BIDI
                   else c for c in str(text))
    return " ".join(text.split())


def _is_challenge(doc) -> bool:
    return isinstance(doc, dict) and doc.get("code") == CHALLENGE


def _says(doc) -> bool:
    """Did the harness put words in its answer (`error` or `message`)? A crash,
    garbage or an empty stdout has none."""
    return isinstance(doc, dict) and any(
        isinstance(v := doc.get(k), str) and v.strip() for k in ("error", "message"))


def _code_of(doc: dict) -> str | None:
    """The one-time code of a challenge, if it is one that can be sent back."""
    params = doc.get("params")
    c = params.get("confirm_code") if isinstance(params, dict) else None
    ok = (isinstance(c, str) and 0 < len(c) <= 200 and c.isprintable()
          and not any(ch.isspace() for ch in c))
    return c if ok else None


# ------------------------------------------------------------- running --

def _exec(argv: list[str], timeout: float, env: dict | None, n: int) -> tuple[int, bytes]:
    """(exit status, stdout). The child gets no stdin; on a timeout it and
    everything it started are killed."""
    env = dict(os.environ if env is None else env)
    env.pop("CONSOLE_SECRET", None)
    log.debug("relay call %d: %s", n, _shown(argv))
    # start_new_session: no controlling terminal, so a harness gate that reads
    # /dev/tty can only be satisfied by a person at the operator's terminal,
    # never by the web click; and the child leads its own process group, which
    # a timeout kills whole
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        raise _Timeout from None
    except BaseException:
        _kill(proc)
        raise
    log.debug("relay call %d: exit %s, %d bytes out, %d bytes err",
              n, proc.returncode, len(out), len(err))
    return proc.returncode, out


def _kill(proc: subprocess.Popen) -> None:
    # a wrapper such as `uv run` has a child of its own: signal the whole group
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        proc.kill()
    proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        pipe.close()


def _shown(argv: list[str]) -> list[str]:
    return ["--code=***" if w.startswith("--code=") else w for w in argv]


# ------------------------------------------------------------- reading --

def _parse(raw: bytes):
    """The harness's document: all of stdout if it is one JSON document, else its
    last line (a harness may print a banner first). A dict or a list, or None."""
    text = raw.decode("utf-8", "replace").strip()
    for candidate in (text, text.rsplit("\n", 1)[-1].strip()):
        try:
            doc = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(doc, (dict, list)):
            return doc
    return None


def _answer(status: int, doc, verb: list[str], secret: str | None) -> dict:
    ok = status == 0
    keys = ("message", "result", "error") if ok else ("error", "message", "result")
    text = next((v for k in keys
                 if isinstance(doc, dict) and isinstance(v := doc.get(k), str)
                 and v.strip()), "")
    if secret:
        text = text.replace(secret, "***")
    text = _plain(text)
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE - 1].rstrip() + "…"
    if not ok:
        return _out(False, "gate_refused", text or core.CODES["gate_refused"], verb)
    return _out(True, None, text, verb)


def _out(ok: bool, code: str | None, message: str, verb: list[str]) -> dict:
    return {"ok": ok, "code": code, "message": message, "verb": list(verb)}
