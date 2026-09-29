"""The human gate, and the attribution every human-table write shares.

Ported from the reference harness's `_lib/human.py`; its protocol is
byte-compatible with the playbook console (console/relay.py). What this
module guards:

  * require_human()  a person is at a terminal AND retypes `expected` at
                     /dev/tty, never stdin, so `</dev/null` or a pipe
                     cannot pre-supply it. A guard against an agent
                     confirming by accident or by habit, NOT a security
                     boundary: an agent running as the same OS user can
                     drive a pty. There is deliberately no bypass flag.
  * confirm()        require_human()'s remote-relay equivalent: off a TTY,
                     with `<P>_CONFIRM_CODE_SECRET` set, a short-lived code
                     stands in for the retyped value. The agent relays the
                     code and a plain-language summary to the human over
                     their chat channel (or the console shows it), the
                     human relays the code back, and `--code` closes the
                     loop. The same non-security-boundary property holds.
                     Secret unset = the plain TTY refusal: the relay is
                     opt-in.
  * subject()        what a code is bound to: verb, market, entity type,
                     every entity id with the value(s) confirmed for it,
                     and a version every write to those rows moves. A code
                     issued for one payload refuses any other, and because
                     the write it unlocks moves the version it is single
                     use, with no state of its own.
  * issue_code() / check_code()  HMAC-SHA256(secret, "subject|slot"),
                     dynamic truncation, 6 digits; 300 s slots, the current
                     one and 2 back (a code lives 5-15 min), never a future
                     one. Slots count on time.time(), not the calendar.
  * relay_audit()    a relayed confirm must name who relayed it and when
                     (`--relay-user`, `--relay-at`); the suffix
                     ` [relay user=… at=…]` goes on the history reason.
                     The user is one token (no space, no bracket) and the
                     time is ISO 8601 with its zone, so what a caller
                     types cannot close the suffix and forge another one.
  * changed_by()     `<OS user>@tty|relay|cli`: derived, never typed.
  * why()            the required, non-blank --reason (reason_required).
  * json_refusal()   the one --json document of a refused gate verb:
                     {error, next, code, params[, subject]}; a challenge is
                     code confirm_code_required, params.confirm_code, the
                     `subject`, and next = the same command rerun with
                     `--code C --relay-user <sender_id> --relay-at
                     <iso_time>` (kit.contract.failure_doc renders it).
  * stale_basis()    a proposal approved on one basis (a hash of the rows
                     its rule read) is stale once the basis moved: queue
                     and execute refuse it (prior art #10).

The test seam `KIT_TTY` (kit.testing.check.capture(tty_answers=…)): when
set, it names the file that stands in for the controlling terminal, and
a person counts as present. A regular file is read one answer per line,
each answer consumed (the prompt goes to stderr); anything else (a
device) is opened like /dev/tty. It exists so tests can drive the TTY
path in-process and in a child; sandbox_env()/clean_env() strip it, and
doctor should warn when it is set outside a test.

Additions to the reference (principle 5, every message coded): the
refusals it raised as plain text are coded too (confirm_typed_mismatch,
confirm_no_terminal, confirm_tty_eof, confirm_aborted,
confirm_code_unavailable), same wording. A relayed code is NFKC-normalised
and compared as bytes, so full-width digits typed on a phone count and a
non-ASCII code is refused instead of crashing hmac.compare_digest.
issue_code()/check_code() take an optional `at=` (epoch seconds) for tests.

Test: kit/tests/test_human.py.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import time
import unicodedata
from datetime import datetime
from typing import Literal

from kit import dates
from kit.config import config
from kit.contract import HarnessError, failure_doc
from kit.messages import Msg, msg

TTY_DEVICE = "/dev/tty"
TTY_ENV = "KIT_TTY"          # the test seam: a file standing in for the terminal
WINDOW_S = 300               # one code slot is 5 minutes
WINDOWS_BACK = 2             # + up to 2 slots old: a code is good 5-15 min
TTL_MINUTES = (1 + WINDOWS_BACK) * WINDOW_S // 60
# one token: it is written inside ` [relay user=… at=…]` on the history row
RELAY_USER = re.compile(r"[^\s\[\]]{1,120}")


class Refused(HarnessError):
    """A human-data write refused at the boundary. Nothing was written."""


class CodeRequired(Refused):
    """confirm() off a TTY with the secret set and no --code yet: the
    refusal text, plus the challenge as data (`confirm_code`, `subject`)
    for json_refusal()."""

    def __init__(self, message: Msg | str, *, confirm_code: str, subj: str):
        super().__init__(message)
        self.confirm_code, self.subject = confirm_code, json.loads(subj)


def now() -> str:
    """Microsecond ISO stamp of dates.now(): two rapid writes that really
    changed a value never share a stamp."""
    return dates.now().isoformat(timespec="microseconds")


def secret_env() -> str:
    """The bound harness's secret variable: `<P>_CONFIRM_CODE_SECRET`."""
    return config().env("CONFIRM_CODE_SECRET")


# ---- the terminal ----------------------------------------------------------

def stdin_is_tty() -> bool:
    """True only when a person is at a terminal (or the KIT_TTY seam stands
    in for one). Closed or detached stdin counts as piped."""
    if os.environ.get(TTY_ENV):
        return True
    try:
        return sys.stdin.isatty()
    except (ValueError, AttributeError):
        return False


def _no_terminal() -> Refused:
    return Refused(msg("confirm_no_terminal",
                       "no controlling terminal (/dev/tty). Nothing was "
                       "written."))


def _eof() -> Refused:
    return Refused(msg("confirm_tty_eof",
                       "no answer at the terminal (EOF). Nothing was "
                       "written."))


def _read_seam_file(path: str, prompt: str) -> str:
    """One answer from the KIT_TTY file: its first line, consumed (the rest
    is written back), so successive prompts get successive answers."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
    except OSError:
        raise _no_terminal() from None
    print(prompt, end="", file=sys.stderr, flush=True)
    if not lines:
        raise _eof()
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines[1:])
    return lines[0]


def read_tty(prompt: str) -> str:
    """One line from the controlling terminal, bypassing stdin, so a pipe
    or `</dev/null` cannot answer it. Stripped."""
    path = os.environ.get(TTY_ENV) or TTY_DEVICE
    try:
        if path != TTY_DEVICE and stat.S_ISREG(os.stat(path).st_mode):
            line = _read_seam_file(path, prompt)
        else:
            with open(path, "w") as out, open(path) as inp:
                out.write(prompt)
                out.flush()
                line = inp.readline()
    except OSError:
        raise _no_terminal() from None
    except KeyboardInterrupt:
        raise Refused(msg("confirm_aborted",
                          "aborted at the keyboard. Nothing was written.")
                      ) from None
    if not line:
        raise _eof()
    return line.strip()


def require_tty(what: str) -> None:
    """The first check alone: refused unless a person is at a terminal."""
    if not stdin_is_tty():
        raise Refused(msg(
            "confirm_needs_human",
            f"{what} must be run by a human at an interactive terminal "
            f"(stdin is not a TTY). Agents and scripts may propose; only "
            f"the owner confirms. Nothing was written.", what=what))


def require_human(what: str, prompt: str, expected: str) -> None:
    """A confirmation is a human act: a person at a terminal retypes
    `expected` at /dev/tty. TTY only: no relayed code (e.g. a restore)."""
    require_tty(what)
    answer = read_tty(prompt)
    if answer != expected:
        raise Refused(msg(
            "confirm_typed_mismatch",
            f"{what}: typed {answer!r}, expected {expected!r}. "
            f"Nothing was written.", what=what, typed=answer,
            expected=expected))


# ---- the relayed code ------------------------------------------------------

def _code_secret() -> bytes:
    var = secret_env()
    v = os.environ.get(var)
    if not v:
        raise Refused(msg(
            "confirm_code_unavailable",
            f"{var} is not set — the relayed --code path needs it. A human "
            f"at an interactive terminal can still confirm directly; "
            f"nothing else is affected.", var=var))
    return v.encode()


def subject(verb: str, market: str, entity_type: str, items: dict,
            version: object) -> str:
    """The exact payload a relayed --code confirms, canonical so the
    issuing run and the checking run derive the same string: `items` maps
    every entity id to the value(s) confirmed for it (ids sorted, so their
    order on the command line does not matter); `version` is whatever the
    confirmed write itself moves (the last history id, a row's
    decided_at), which makes a used code stale."""
    return json.dumps({"verb": verb, "market": market,
                       "entity_type": entity_type,
                       "items": {str(k): v for k, v in items.items()},
                       "version": version},
                      sort_keys=True, separators=(",", ":"), default=str)


def _slot(at: float | None) -> int:
    return int(time.time() if at is None else at) // WINDOW_S


def _code_at(subj: str, window: int) -> str:
    """A 6-digit code, deterministic from (subject, time slot) and the
    shared secret. Nothing is stored between issuing and checking."""
    mac = hmac.new(_code_secret(), f"{subj}|{window}".encode(),
                   hashlib.sha256).digest()
    offset = mac[-1] & 0x0F
    n = int.from_bytes(mac[offset:offset + 4], "big") & 0x7FFFFFFF
    return f"{n % 1_000_000:06d}"


def issue_code(subj: str, *, at: float | None = None) -> str:
    """The code valid now (or at epoch `at`) for confirming `subj`."""
    return _code_at(subj, _slot(at))


def check_code(subj: str, code: str, *, at: float | None = None) -> bool:
    """True if `code` matches the current time slot or one of the
    WINDOWS_BACK slots before it — never a future one, so a code works
    for at most TTL_MINUTES after it was issued."""
    typed = unicodedata.normalize("NFKC", code).strip().encode()
    cur = _slot(at)
    return any(hmac.compare_digest(_code_at(subj, w).encode(), typed)
               for w in range(cur - WINDOWS_BACK, cur + 1))


def confirm(what: str, prompt: str, expected: str, *, subj: str,
            code: str | None = None) -> Literal["tty", "relay"]:
    """The human challenge, at a TTY or relayed. Returns the channel to
    record via changed_by(): "tty" or "relay".

    At a terminal the person retypes `expected` (require_human); `code` is
    never consulted there. Off a TTY: secret unset = the confirm_needs_human
    refusal; set, with no `code` yet = CodeRequired carrying the current
    code and `prompt` as the summary to relay; the caller reruns with
    --code once the human answers. A code issued for another payload, one
    already used, or an expired one is refused like a mistyped value."""
    if stdin_is_tty():
        require_human(what, prompt, expected)
        return "tty"
    if not os.environ.get(secret_env()):
        require_tty(what)
    if code is None:
        issued, summary = issue_code(subj), prompt.strip()
        raise CodeRequired(msg(
            "confirm_code_required",
            f"{what}: not at an interactive terminal. Relay this to the "
            f"client over their chat channel, then rerun with --code <code>:\n"
            f"  {summary}\n"
            f"  code: {issued}  (good for ~{TTL_MINUTES} more minutes)\n"
            f"Nothing was written.",
            what=what, summary=summary, confirm_code=issued,
            ttl_minutes=TTL_MINUTES), confirm_code=issued, subj=subj)
    if not check_code(subj, code):
        raise Refused(msg(
            "confirm_code_mismatch",
            f"{what}: code {code!r} does not match this exact request — "
            f"issued for other ids, values or market, already used, or "
            f"expired (~{TTL_MINUTES} min). Nothing was written.",
            what=what, confirm_code=code, ttl_minutes=TTL_MINUTES))
    return "relay"


def relay_audit(channel: str, relay_user: str | None,
                relay_at: str | None) -> str:
    """"" unless confirm() returned "relay"; then --relay-user and
    --relay-at are required and this is the history-reason suffix that
    records them. The chat app rides in the sender id by convention
    (`<channel>:<sender id>`, the console sends `web:<user>`)."""
    if channel != "relay":
        return ""
    if not relay_user or not relay_at:
        raise Refused(msg(
            "confirm_relay_audit_missing",
            "--code needs --relay-user and --relay-at too (history must "
            "show who relayed the confirmation and when)"))
    for field, ok in (("relay_user", RELAY_USER.fullmatch(relay_user)),
                      ("relay_at", _iso_with_zone(relay_at))):
        if not ok:
            raise Refused(msg(
                "confirm_relay_audit_invalid",
                f"--{field.replace('_', '-')} is not valid (one token for "
                f"the user, an ISO 8601 time with its zone). Nothing was "
                f"written.", field=field))
    return f" [relay user={relay_user} at={relay_at}]"


def _iso_with_zone(text: str) -> bool:
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False


# ---- attribution -----------------------------------------------------------

def changed_by(channel: str) -> str:
    """Who wrote a history row, derived, never typed: the OS user plus the
    channel that passed the challenge (`tty`, `relay`) or `cli` when
    nothing gated the write."""
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = "unknown"
    return f"{user}@{channel}"


def typed_source() -> str:
    """The `source` of a value a human typed at a confirm (`--value`)."""
    return f"human_confirmed_{now()[:10]}"


def why(text: str | None) -> str:
    """The stated --reason, stripped. Rejects an empty one."""
    if not text or not text.strip():
        raise Refused(msg("reason_required",
                          "--reason is required and must not be empty "
                          "(it is the history row's why)"))
    return text.strip()


def json_refusal(e: BaseException, cmd: list[str]) -> dict:
    """The one --json document of a refused gate verb: {error, next, code,
    params} like every failure; a CodeRequired adds `subject` and `next` =
    `cmd` rerun with --code and the relay audit flags to fill in. `cmd` is
    the command as run (["shop", "facts", *argv])."""
    return failure_doc(e, cmd)


def add_gate_args(parser: argparse.ArgumentParser, *,
                  reason: bool = True) -> None:
    """--code, --relay-user, --relay-at (and --reason unless the verb
    declares its own). argparse accepts the `--name=value` form the
    console sends, so a code starting with `-` stays one argument."""
    parser.add_argument(
        "--code", help="off a TTY, instead of retyping: the one-time code "
        "the human relayed back, bound to exactly this request")
    parser.add_argument(
        "--relay-user", help="sender id of whoever answered, namespaced by "
        "channel (<channel>:<sender id>) — required with --code")
    parser.add_argument(
        "--relay-at", help="ISO 8601 time the human's reply arrived — "
        "required with --code")
    if reason:
        parser.add_argument("--reason", help="why (required; kept in history)")


# ---- basis staleness (prior art #10) ---------------------------------------

def stale_basis(saved: str | None, current: str | None) -> bool:
    """True when a proposal's saved basis (a hash of the rows its rule read
    plus the ingest version) no longer matches the basis recomputed now.
    A missing basis on either side is stale: it can never be proven
    current."""
    return not saved or not current or saved != current
