"""The human gate: a confirm, an approval or a rollback needs a person, bound to
exactly what that person was shown. Every human verb calls `confirm()`.

    at a terminal   stdin is a TTY AND the person retypes the value (or the word
                    for a batch, e.g. `confirm`) at /dev/tty. Never from stdin, so
                    a pipe, `</dev/null` or a pty wrapper fed from a file cannot
                    answer: it would have to be the controlling terminal.
    off a terminal  refused, unless the operator opted in (`CODE_SECRET_ENV` set).
                    Then the first call is refused with a one-time code
                    (`CodeRequired`); the agent relays the code and the summary
                    to the person over any channel, the person sends the code
                    back, and the same call rerun with `--code` passes. The rerun
                    also needs `--relay-user` and `--relay-at`: who sent the code
                    back and when, kept in the written row's reason.

The code is HMAC-SHA256(secret, subject | time slot) cut to 6 digits, so nothing
is stored between issuing it and checking it. `subject()` is what it is bound to:
the verb, the scope, the entity type, every id (sorted) with the value confirmed
for it, and a version that the confirmed write itself moves (the last history id
of those rows, a row's decided_at). A code shown for one payload refuses any other,
and once its write lands the version has moved: single use, with no state of its
own. Slots are 5 minutes; a code is accepted in its own slot and the two after it,
never in a slot before it was issued, so it expires within 15 minutes.

`Passed` says how the gate was passed; its `changed_by` is `<OS user>@tty` or
`<OS user>@relay`, and `changed_by("cli")` names a write nothing gated. It is
derived, never typed. `why()` is the required, non-blank --reason.
`json_refusal()` is the one --json document of a refused gate verb; a challenge
carries its code and summary in `params`, the exact payload in `subject`, and
`next` = the same command rerun with the code, so a client (a chat agent, a
console button) relays it without parsing English.

This guards against an agent confirming by accident or by habit. It is NOT a
security boundary: an agent running as the same OS user can drive a pty, and an
agent that relays the code can send it back itself. The guard is the binding and
the audit, not secrecy. There is deliberately no bypass flag.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from .messages import failure_doc, msg

CODE_SECRET_ENV = "CONFIRM_CODE_SECRET"
SLOT_S = 300           # one code slot is 5 minutes
SLOTS_BACK = 2         # + the two slots after it: a code lives 10 to 15 minutes
CODE_TTL_MINUTES = (1 + SLOTS_BACK) * SLOT_S // 60
_RELAY_USER = re.compile(r"[^\s\[\]]{1,120}")    # one token: it is written inside [relay user=… at=…]


class Refused(Exception):
    """A gated write refused at the boundary: nothing was written. args[0] is a Msg."""


class CodeRequired(Refused):
    """Off a terminal, the secret set, no --code yet: the refusal, plus the
    challenge as data for json_refusal()."""

    def __init__(self, message, *, confirm_code: str, subject: str):
        super().__init__(message)
        self.confirm_code, self.subject = confirm_code, json.loads(subject)


@dataclass(frozen=True)
class Passed:
    channel: str       # "tty" | "relay"
    changed_by: str    # "<OS user>@<channel>"
    audit: str         # "" at a terminal; " [relay user=… at=…]" to append to the reason


def stdin_is_tty() -> bool:
    """True only when stdin is a terminal. A closed or detached stdin counts as piped."""
    try:
        return sys.stdin.isatty()
    except (ValueError, AttributeError):
        return False


def read_tty(prompt: str, what: str) -> str:
    """One line from the controlling terminal, never stdin: a pipe or `</dev/null`
    cannot answer it. The seam tests patch."""
    try:
        with open("/dev/tty", "w") as out, open("/dev/tty") as inp:
            out.write(prompt)
            out.flush()
            line = inp.readline()
    except OSError:
        raise Refused(msg("confirm_no_terminal", f"{what}: there is no controlling terminal "
                          f"(/dev/tty) to answer at. Nothing was written.", what=what)) from None
    except KeyboardInterrupt:
        raise Refused(msg("confirm_aborted", f"{what}: stopped at the keyboard. "
                          f"Nothing was written.", what=what)) from None
    if not line:
        raise Refused(msg("confirm_aborted", f"{what}: no answer at the terminal. "
                          f"Nothing was written.", what=what))
    return line.strip()


def subject(verb: str, scope: str, entity_type: str, items: dict, version: object) -> str:
    """The exact payload a code confirms, as canonical JSON, so the issuing run and
    the checking run derive the same string. `items` maps every entity id to the
    value(s) confirmed for it (sorted, so the order on the command line does not
    matter); `version` is whatever the confirmed write itself moves."""
    return json.dumps({"verb": verb, "scope": scope, "entity_type": entity_type,
                       "items": {str(k): v for k, v in items.items()}, "version": version},
                      sort_keys=True, separators=(",", ":"), default=str)


def _secret() -> bytes | None:
    v = os.environ.get(CODE_SECRET_ENV)
    return v.encode() if v else None


def _code_at(subj: str, slot: int, secret: bytes) -> str:
    mac = hmac.new(secret, f"{subj}|{slot}".encode(), hashlib.sha256).digest()
    offset = mac[-1] & 0x0F
    n = int.from_bytes(mac[offset:offset + 4], "big") & 0x7FFFFFFF
    return f"{n % 1_000_000:06d}"


def _slot(at: float | None) -> int:
    return int((time.time() if at is None else at) // SLOT_S)


def issue_code(subj: str, at: float | None = None) -> str:
    """The code valid now (or at `at`, epoch seconds) for `subj`. Needs the secret."""
    secret = _secret()
    if secret is None:
        raise RuntimeError(f"{CODE_SECRET_ENV} is not set")
    return _code_at(subj, _slot(at), secret)


def check_code(subj: str, code: str, at: float | None = None) -> bool:
    """Does `code` confirm `subj` now: issued in this slot or one of the
    SLOTS_BACK before it, never a later one? Anything but 6 ASCII digits is no."""
    secret, code = _secret(), (code or "").strip()
    if secret is None or not (len(code) == 6 and code.isascii() and code.isdigit()):
        return False
    now = _slot(at)
    return any(hmac.compare_digest(_code_at(subj, s, secret), code)
               for s in range(now - SLOTS_BACK, now + 1))


def confirm(what: str, summary: str, expected: str, *, subj: str, code: str | None = None,
            relay_user: str | None = None, relay_at: str | None = None) -> Passed:
    """The human challenge. `what` names the act ("confirm unit_cost"), `summary`
    says in plain words what will be written, `expected` is what a person at a
    terminal retypes, `subj` is subject(). Returns Passed, or raises Refused.

    At a terminal the code is never consulted. Off one: no secret, refused; no
    code, CodeRequired (the challenge); a code, then the relay audit is required
    and the code must match `subj` now."""
    if stdin_is_tty():
        typed = read_tty(f"{what}: {summary}\nType {expected} to confirm: ", what)
        if typed != expected:
            raise Refused(msg("confirm_retype_mismatch", f"{what}: typed {typed!r}, expected "
                              f"{expected!r}. Nothing was written.",
                              what=what, typed=typed, expected=expected))
        return Passed("tty", changed_by("tty"), "")
    if _secret() is None:
        raise Refused(msg("confirm_needs_human", f"{what} needs a person at an interactive "
                          f"terminal (stdin is not a TTY), or {CODE_SECRET_ENV} set for a relayed "
                          f"code. Agents and scripts may propose; only a person confirms. "
                          f"Nothing was written.", what=what))
    if code is None:
        issued = issue_code(subj)
        raise CodeRequired(msg(
            "confirm_code_required",
            f"{what}: not at a terminal. Show the person this, then rerun with "
            f"--code=<the code they send back>:\n  {summary}\n  code: {issued} (expires within "
            f"{CODE_TTL_MINUTES} minutes)\nNothing was written.",
            what=what, summary=summary, confirm_code=issued, ttl_minutes=CODE_TTL_MINUTES),
            confirm_code=issued, subject=subj)
    if not relay_user or not relay_at:
        raise Refused(msg("confirm_relay_audit_missing", "--code needs --relay-user and "
                          "--relay-at too: the history must show who sent the code back, and when. "
                          "Nothing was written."))
    for field, ok in (("relay_user", _RELAY_USER.fullmatch(relay_user)), ("relay_at", _iso(relay_at))):
        if not ok:
            raise Refused(msg("confirm_relay_audit_invalid", f"--{field.replace('_', '-')} is not "
                              f"valid (one token for the user, an ISO 8601 time with its zone). "
                              f"Nothing was written.", field=field))
    if not check_code(subj, code):
        raise Refused(msg("confirm_code_mismatch", f"{what}: that code does not match this exact "
                          f"request: issued for other ids, values or scope, already used, or "
                          f"expired (within {CODE_TTL_MINUTES} minutes). Nothing was written.",
                          what=what, ttl_minutes=CODE_TTL_MINUTES))
    return Passed("relay", changed_by("relay"), f" [relay user={relay_user} at={relay_at}]")


def _iso(text: str) -> bool:
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False


def changed_by(channel: str) -> str:
    """Who wrote a row, derived, never typed: the OS user and the channel that
    passed the gate (`tty`, `relay`), or `cli` when nothing gated the write."""
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = "unknown"
    return f"{user}@{channel}"


def why(text: str | None) -> str:
    """The stated --reason, stripped. An empty one is refused: it is the history row's why."""
    if not text or not text.strip():
        raise Refused(msg("reason_required", "--reason is required and must not be empty "
                          "(it is the history row's why). Nothing was written."))
    return text.strip()


def json_refusal(e: BaseException, cmd: list[str]) -> dict:
    """The one --json document of a refused gate verb: {error, next, code, params}.
    `cmd` is the command as run. A CodeRequired adds `subject`, and `next` is
    `cmd` rerun with the code and the relay flags to fill in."""
    doc = failure_doc(e)
    if isinstance(e, CodeRequired):
        doc["next"] = [f"{shlex.join(cmd)} --code={e.confirm_code} "
                       f"--relay-user=<sender_id> --relay-at=<iso_time>"]
        doc["subject"] = e.subject
    return doc
