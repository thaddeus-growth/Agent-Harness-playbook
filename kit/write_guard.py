"""The one write path to an external API, behind hard guards.

A credential that can read an account can usually also write to it, so
this engine, not agent discipline, is what keeps a write from happening by
accident. The kit ships the engine and NO allowlist: a harness that writes
passes its own `allowed` table (one file, reviewed like code); with none,
every call is refused.

Checked before EVERY call, reads included (`Guard.refusal()` -> the coded
reason or None; `Guard.ensure_on()` raises it), in order:

  1. kill switch: `<P>_KILL` set to anything but "" or "0";
  2. kill switch: an entry named STOP in `<P>_DATA_DIR` (even a dangling
     link; a data dir that is unset or relative cannot be checked, so
     that refuses too);
  3. opt-in: `<P>_ALLOW_WRITES` exactly "1" (credentials alone never
     enable a call).

Then `Guard.check(method, path, body, created)`, the allowlist:

  * (METHOD, path) must match an `allowed` key; a `{name}` segment in a
    key's path matches one plain id segment (letters, digits,
    `_ . ~ : @ -`; never `.`/`..`, `/`, `?`, `%`, `#`, `{`); two keys that
    could match one path are refused when the Guard is built, so a path
    names at most one endpoint;
  * `read=True` endpoints pass any body (filters, not an item);
  * a `by_id` endpoint (an archive/delete of what the harness itself
    made) needs exactly {by_id: {"include": [one id in `created`]}};
  * a write body is exactly {key: [one item]} (or, with key None, the body
    is the one item), and the item's field set must EQUAL one Shape's
    fields and hold every fixed value of that shape (type and value);
  * an OWN shape (`own=<id field>`) needs that id in `created`: the ids
    the caller proved the harness created (its own action log);
  * `validators` {field: fn(value) -> bool} run last, on every item that
    carries that field (e.g. a bid must be a positive number); a
    validator that raises refuses.

`Writer(guard, transport).call(...)` = refusal -> check -> send. The
transport `(method, path, body, media) -> (status, payload)` is the seam
a test fakes. Any exception from the transport becomes WriteFailed and is
never re-sent: a retried create can write twice (this module never
imports kit.retry).

Deviations from the reference write path, all stricter: the kill env
counts any value but ""/"0" (the reference: exactly "1"); an unset or
relative data dir refuses (the reference skipped the STOP check); a read
endpoint is declared with `read=True` (the reference read `shapes == ()`
as a read, so a write endpoint missing its shapes passed any body); fixed
values compare type and value (True no longer equals 1); `key=None` (a
flat body) and `{name}` path segments are new, for APIs that put the id
in the path. A malformed allowlist raises ValueError at Guard() time.

Test: kit/tests/test_write_guard.py.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Mapping, NamedTuple

from kit import config as _config
from kit import paths
from kit.contract import HarnessError
from kit.messages import Msg, msg

KILL_FILE = "STOP"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_PARAM = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_ID_SEGMENT = re.compile(r"^[A-Za-z0-9_.~:@-]+$")

# transport(method, path, body, media) -> (http status, json payload)
Transport = Callable[[str, str, Any, str], tuple[int, Any]]


class WriteRefused(HarnessError):
    """A guard said no: kill switch, no opt-in, or not on the allowlist."""


class WriteFailed(HarnessError):
    """The call did not complete (a transport error); never re-sent."""


class Shape(NamedTuple):
    """One item a write may carry: exactly these fields, these fixed."""
    fields: frozenset
    fixed: dict                  # field -> the only value allowed
    own: str | None = None       # OWN only: this id field must be in `created`


class Endpoint(NamedTuple):
    media: str                   # the content type the transport sends
    key: str | None              # body list key; None = the body is the item
    shapes: tuple[Shape, ...]    # the items a write may carry
    by_id: str | None = None     # {by_id: {include: [one id in created]}}
    read: bool = False           # a read: any body (filters, not an item)


def shape(*fields: str, own: str | None = None, **fixed: Any) -> Shape:
    """Shape("id", "bid") = exactly {id, bid}; shape("id", state="PAUSED")
    = exactly {id, state} with state PAUSED; own="id" = id in `created`."""
    return Shape(frozenset(fields) | frozenset(fixed), dict(fixed), own)


def _passes(ok: Callable[[Any], bool], value: Any) -> bool:
    """A validator's verdict; one that raises refuses."""
    try:
        return bool(ok(value))
    except Exception:
        return False


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _segments(path: str) -> list[str]:
    return path.split("/")[1:]


def _overlap(a: str, b: str) -> bool:
    sa, sb = _segments(a), _segments(b)
    return len(sa) == len(sb) and all(
        x == y or _PARAM.match(x) or _PARAM.match(y) for x, y in zip(sa, sb))


def _validate(allowed: Mapping[tuple[str, str], Endpoint]) -> None:
    """A malformed allowlist is a bug in the harness: ValueError."""
    keys = list(allowed)
    for key in keys:
        ep = allowed[key]
        if not (isinstance(key, tuple) and len(key) == 2
                and key[0] in METHODS and isinstance(key[1], str)
                and key[1].startswith("/")):
            raise ValueError(f"allowlist key {key!r}: want (METHOD, '/path')")
        if not isinstance(ep, Endpoint):
            raise ValueError(f"{key}: not an Endpoint")
        forms = [bool(ep.read), bool(ep.by_id), bool(ep.shapes)]
        if sum(forms) != 1:
            raise ValueError(f"{key}: an endpoint is exactly one of read=True, "
                             f"by_id=… or non-empty shapes")
        for s in ep.shapes:
            if not isinstance(s, Shape) or not set(s.fixed) <= set(s.fields):
                raise ValueError(f"{key}: bad shape {s!r}")
            if s.own is not None and s.own not in s.fields:
                raise ValueError(f"{key}: own field {s.own!r} not in fields")
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[0] == b[0] and _overlap(a[1], b[1]):
                raise ValueError(f"allowlist keys {a} and {b} match the same "
                                 f"paths; make them distinct")


class Guard:
    """The switches and the allowlist; `refusal()` then `check()`."""

    def __init__(self, allowed: Mapping[tuple[str, str], Endpoint], *,
                 validators: Mapping[str, Callable[[Any], bool]] | None = None,
                 kill_env: str | None = None, opt_in_env: str | None = None,
                 kill_file: str = KILL_FILE):
        _validate(allowed)
        self.allowed = dict(allowed)
        self.validators = dict(validators or {})
        self._kill_env = kill_env
        self._opt_in_env = opt_in_env
        self.kill_file = kill_file

    @property
    def kill_env(self) -> str:
        return self._kill_env or _config.config().env("KILL")

    @property
    def opt_in_env(self) -> str:
        return self._opt_in_env or _config.config().env("ALLOW_WRITES")

    def refusal(self) -> Msg | None:
        """Why no call may be made right now (a coded message), or None.
        Kill switch first."""
        kill = self.kill_env
        if os.environ.get(kill, "").strip() not in ("", "0"):
            return msg("write_killed", f"kill switch: {kill} is set — no call "
                       f"goes out while it is", var=kill)
        try:
            stop = paths.data_dir() / self.kill_file
        except HarnessError:
            var = _config.config().env("DATA_DIR")
            return msg("write_stop_unchecked", f"writes refused: {var} is not "
                       f"an absolute path, so the {self.kill_file} file cannot "
                       f"be checked", var=var, file=self.kill_file)
        if os.path.lexists(stop):
            return msg("write_stop_file", f"kill switch: {stop} exists — "
                       f"remove it to allow calls again", path=str(stop))
        opt = self.opt_in_env
        if os.environ.get(opt) != "1":
            return msg("write_off", f"writes are off: {opt}=1 enables them "
                       f"(credentials alone never do)", var=opt)
        return None

    def ensure_on(self) -> None:
        """Raise WriteRefused when refusal() names a reason (an executor's
        pre-check before it plans any call)."""
        why = self.refusal()
        if why is not None:
            raise WriteRefused(why, [f"{_config.config().cli} doctor"])

    def match(self, method: str, path: str) -> Endpoint | None:
        """The one endpoint (method, path) matches, or None. A `{name}`
        segment matches one plain id segment; every other segment matches
        itself only."""
        if not path.startswith("/"):
            return None
        segs = _segments(path)
        for (m, pattern), ep in self.allowed.items():
            pat = _segments(pattern)
            if m == method and len(pat) == len(segs) and all(
                    (_ID_SEGMENT.match(s) and s not in (".", ".."))
                    if _PARAM.match(p) else p == s
                    for p, s in zip(pat, segs)):
                return ep
        return None

    def check(self, method: str, path: str, body: Any,
              created: frozenset = frozenset()) -> Endpoint:
        """The allowlist: the endpoint, or WriteRefused. `created`: the ids
        the caller proved the harness created — the only ones an OWN shape
        or a by_id endpoint may name."""
        where = f"{method} {path}"

        def refused(m) -> WriteRefused:
            return WriteRefused(m, [])

        ep = self.match(method, path)
        if ep is None:
            raise refused(msg("write_not_allowed",
                              f"{where} is not on the write allowlist",
                              method=method, path=path))
        if ep.read:
            return ep
        if ep.by_id:
            f = body.get(ep.by_id) if isinstance(body, dict) \
                and set(body) == {ep.by_id} else None
            ids = f.get("include") if isinstance(f, dict) \
                and set(f) == {"include"} else None
            if not isinstance(ids, list) or len(ids) != 1 \
                    or not isinstance(ids[0], str):
                want = f"{{{ep.by_id}: {{include: [one id]}}}}"
                raise refused(msg("write_bad_body",
                                  f"{where}: the body must be exactly {want}",
                                  method=method, path=path, expected=want))
            if ids[0] not in created:
                raise refused(self._not_own(method, path, ids[0]))
            return ep
        if ep.key is None:
            items = [body]
        else:
            items = body.get(ep.key) if isinstance(body, dict) \
                and set(body) == {ep.key} else None
        if not isinstance(items, list) or len(items) != 1 \
                or not isinstance(items[0], dict):
            want = ("one object" if ep.key is None
                    else f"{{{ep.key}: [one item]}}")
            raise refused(msg("write_bad_body",
                              f"{where}: the body must be exactly {want}",
                              method=method, path=path, expected=want))
        item = items[0]
        same = [s for s in ep.shapes if set(item) == s.fields]
        if not same:
            allowed = " or ".join(str(sorted(s.fields)) for s in ep.shapes)
            raise refused(msg("write_fields_not_allowed",
                              f"{where}: fields {sorted(map(str, item))} ≠ "
                              f"allowed {allowed}", method=method, path=path,
                              fields=sorted(map(str, item)), allowed=allowed))
        fit = [s for s in same
               if all(_same(item[f], v) for f, v in s.fixed.items())]
        if not fit:
            want = " or ".join(", ".join(f"{f}={v!r}" for f, v in
                                         sorted(s.fixed.items()))
                               for s in same)
            raise refused(msg("write_fixed_mismatch",
                              f"{where}: must be {want}", method=method,
                              path=path, expected=want))
        own = fit[0].own
        if own and item[own] not in created:
            raise refused(self._not_own(method, path, item[own]))
        for field, ok in self.validators.items():
            if field in item and not _passes(ok, item[field]):
                raise refused(msg("write_value_refused",
                                  f"{where}: {field} value is not allowed",
                                  method=method, path=path, field=field))
        return ep

    @staticmethod
    def _not_own(method: str, path: str, i: Any):
        return msg("write_not_own",
                   f"{method} {path}: {i!r} is not an entity the harness "
                   f"created — only those may be changed this way",
                   method=method, path=path, id=str(i))


class Writer:
    """The executor's only handle on the account."""

    def __init__(self, guard: Guard, transport: Transport):
        self.guard = guard
        self._send = transport

    def call(self, method: str, path: str, body: Any,
             created: frozenset = frozenset()) -> tuple[int, Any]:
        """refusal -> check -> send, once. (status, payload), WriteRefused,
        or WriteFailed (any transport exception; never re-sent)."""
        self.guard.ensure_on()
        ep = self.guard.check(method, path, body, created)
        try:
            return self._send(method, path, body, ep.media)
        except Exception as e:   # network: never retried, reported
            detail = f"{type(e).__name__}: {e}"
            raise WriteFailed(msg("write_failed",
                                  f"{method} {path}: {detail}", method=method,
                                  path=path, detail=detail)) from None
