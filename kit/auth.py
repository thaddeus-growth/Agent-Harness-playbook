"""Where an API client's bearer token comes from: one setting, `<P>_TOKEN_SOURCE`.

    env      (default) the harness's own env-mode fetcher for that API (a
             hook: `fetch(prefix, now) -> {"token", "expires_at"[, "origin"]}`),
             e.g. `static_env_token()` = `<prefix>_ACCESS_TOKEN` (+ optional
             `<prefix>_TOKEN_EXPIRES_AT`). The kit holds no network code: an
             exchange of a refresh token, if an API needs one, is the
             harness's fetcher.
    command  for a host that keeps secrets out of the client's env: it
             provides `<prefix>_TOKEN_COMMAND`. The kit runs it (shlex split,
             NO shell, stdin closed, `command_timeout` seconds) and reads ONE
             JSON object from its stdout:

                 {"access_token": "...", "expires_at": <epoch s | epoch ms |
                  ISO 8601 | null>, "<field>": "..."}

             `access_token` is required; unknown keys are ignored. Keys named
             in `command_fields` (per-account, non-secret config such as an
             account id or endpoint) win over `<prefix>_<FIELD>` (`setting()`).
             A non-zero exit, bad JSON, no token, an expired token, a bad
             expiry or a timeout is an AuthError naming the var; stderr may be
             quoted, the command's stdout never is (it may hold a token).

What this module guards:

  * One token per prefix per process (one prefix = one account = one
    fetcher), thread-safe (concurrent callers wait for one fetch), reused
    until `expiry_skew` (60 s) before its expiry, then fetched again once.
    A failed fetch is never cached.
  * `require_expiry` is decided per call (per API) and has no default:
    an API whose tokens lapse passes True, and a token with no known
    expiry is then refused every time (it would be reused until the API
    refuses it); a static key (`api_key()`, or an API whose tokens never
    lapse) passes False.
  * A token that already expired is refused, never served.
  * Env files are read through kit.env (parsed, never sourced); a token
    never appears in a message or in repr().

`source()` is the bound harness's TokenSource: `[auth] command_fields`
and `command_timeout` of harness.toml configure it.

Deviations from the reference token source (none change its guards):
AuthError is a coded HarnessError; the refresh-token exchange became the
env-mode `fetch` hook; the expiry skew applies to env-mode tokens too
(the fetcher returns the real expiry); a non-finite expires_at is refused
as a bad expiry; env mode with no fetcher is refused (auth_no_env_source)
for an API whose tokens only a host command provides; time is read as
`kit.dates.now()` so a test moves it with one patch.

Test: kit/tests/test_auth.py.
"""

from __future__ import annotations

import json
import math
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable, Iterable

from kit import config as _config
from kit import dates
from kit import env as _env
from kit.contract import HarnessError
from kit.messages import msg

MODES = ("env", "command")
COMMAND_TIMEOUT = 30.0   # seconds a token command may run
EXPIRY_SKEW = 60.0       # fetch again this many seconds before expiry
_NO_EXPIRY = math.inf

# fetch(prefix, now_epoch_s) -> {"token": str, "expires_at": epoch s | ms |
# ISO | None, "origin"?: the var the token came from (for messages)}
Fetcher = Callable[[str, float], dict]


class AuthError(HarnessError):
    """No usable token: the message names the var to fix."""


def _next() -> list[str]:
    return [f"{_config.config().cli} doctor"]


def parse_expiry(value, var: str) -> float | None:
    """`expires_at` as epoch seconds: epoch s or ms (a number or a digit
    string; > 1e11 = ms) or ISO 8601 (naive = UTC); None = no known
    expiry. Anything else (a bool, NaN, a word) is an AuthError."""
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, str):
            try:
                ts = float(value)
            except ValueError:
                dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        else:
            ts = float(value)
        if not math.isfinite(ts):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        shown = repr(value)[:60]
        raise AuthError(
            msg("auth_bad_expiry",
                f"{var}: expires_at {shown} is neither epoch seconds nor "
                f"ISO 8601", source=var, value=shown), _next()) from None
    return ts / 1000 if ts > 1e11 else ts


class TokenSource:
    """The token-source engine for every API client of one harness.

    `chain` (default: kit.env.chain(), resolved at call time) is where the
    source var, the command vars and `<prefix>_<FIELD>` settings are read.
    `source_var` default: `<P>_TOKEN_SOURCE` of the bound harness."""

    def __init__(self, *, chain: _env.EnvChain | None = None,
                 source_var: str | None = None,
                 command_suffix: str = "TOKEN_COMMAND",
                 command_fields: Iterable[str] = (),
                 command_timeout: float = COMMAND_TIMEOUT,
                 expiry_skew: float = EXPIRY_SKEW):
        self._chain = chain
        self._source_var = source_var
        self.command_suffix = command_suffix
        self.command_fields = tuple(command_fields)
        self.command_timeout = command_timeout
        self.expiry_skew = expiry_skew
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}   # prefix -> entry (never printed)

    def __repr__(self) -> str:
        return (f"TokenSource(source_var={self.source_var!r}, "
                f"command_fields={self.command_fields!r})")

    @property
    def chain(self) -> _env.EnvChain:
        return self._chain or _env.chain()

    @property
    def source_var(self) -> str:
        return self._source_var or _config.config().env("TOKEN_SOURCE")

    def mode(self) -> str:
        """"env" (default) or "command"; any other value is an AuthError."""
        var = self.source_var
        val = (self.chain.get(var) or "env").strip().lower()
        if val not in MODES:
            raise AuthError(
                msg("auth_bad_token_source",
                    f"{var}={val!r} is not one of {', '.join(MODES)}",
                    var=var, value=val, allowed=", ".join(MODES)), _next())
        return val

    def command_var(self, prefix: str) -> str:
        """The var holding `prefix`'s token command (command mode)."""
        return f"{prefix}_{self.command_suffix}"

    def token(self, prefix: str, *, fetch: Fetcher | None = None,
              require_expiry: bool, force: bool = False) -> str:
        """The bearer token for `prefix` (cached; `force` fetches again).
        `require_expiry`: this API's tokens lapse, so one with no known
        expiry is refused, on this call and every later one."""
        entry = self._entry(prefix, fetch=fetch, force=force)
        if require_expiry and entry["expires_at"] == _NO_EXPIRY:
            src = entry["origin"]
            raise AuthError(
                msg("auth_token_no_expiry",
                    f"{src} gave a token with no expires_at, and this API "
                    f"needs one: its tokens lapse, and a token with no known "
                    f"expiry would be reused until the API refuses it",
                    source=src), _next())
        return entry["token"]

    def api_key(self, prefix: str, env_var: str) -> str | None:
        """A static key (never lapses): `env_var` from the chain in env
        mode, the token command's access_token in command mode (which may
        carry no expiry)."""
        if self.mode() == "command":
            return self._entry(prefix)["token"]
        return self.chain.get(env_var)

    def setting(self, prefix: str, field: str, *,
                env_var: str | None = None) -> str | None:
        """Per-account non-secret config: in command mode the command's
        value when its JSON carries `field` (one of command_fields), else
        `env_var` (default `<prefix>_<FIELD>`) from the chain. Env mode
        never runs anything."""
        return self.setting_source(prefix, field, env_var=env_var)[0]

    def setting_source(self, prefix: str, field: str, *,
                       env_var: str | None = None
                       ) -> tuple[str | None, str | None]:
        """setting() and where it came from: the command var, the config
        var, or None (the client's default applies, or nothing does)."""
        if self.mode() == "command":
            val = self._entry(prefix)["fields"].get(field)
            if val is not None:
                return str(val), self.command_var(prefix)
        name = env_var or f"{prefix}_{field.upper()}"
        val = self.chain.get(name)
        return val, (name if val else None)

    def clear_cache(self, prefix: str | None = None) -> None:
        """Drop cached tokens (a test seam; not normally needed)."""
        with self._lock:
            if prefix is None:
                self._cache.clear()
            else:
                self._cache.pop(prefix, None)

    # ---- the engine -----------------------------------------------------

    def _entry(self, prefix: str, *, fetch: Fetcher | None = None,
               force: bool = False) -> dict:
        mode = self.mode()
        now = dates.now().timestamp()
        with self._lock:
            cached = self._cache.get(prefix)
            if (not force and cached and cached["mode"] == mode
                    and now < cached["expires_at"]):
                return cached
            entry = (self._from_command(prefix, now) if mode == "command"
                     else self._from_fetch(prefix, now, fetch))
            entry["mode"] = mode
            self._cache[prefix] = entry
            return entry

    def _checked(self, token, expires_at, origin: str, now: float,
                 fields: dict) -> dict:
        """The cache entry for a fetched token, or AuthError."""
        if not isinstance(token, str) or not token:
            raise AuthError(
                msg("auth_no_access_token",
                    f"{origin} output has no access_token", source=origin),
                _next())
        exp = parse_expiry(expires_at, origin)
        if exp is not None and exp <= now:
            raise AuthError(
                msg("auth_token_expired",
                    f"{origin} returned a token that already expired",
                    source=origin), _next())
        return {"token": token, "origin": origin, "fields": fields,
                "expires_at": (_NO_EXPIRY if exp is None
                               else exp - self.expiry_skew)}

    def _from_fetch(self, prefix: str, now: float,
                    fetch: Fetcher | None) -> dict:
        if fetch is None:
            var = self.source_var
            cvar = self.command_var(prefix)
            raise AuthError(
                msg("auth_no_env_source",
                    f"{prefix} tokens come only from a host command: set "
                    f"{var}=command and provide {cvar}",
                    prefix=prefix, var=var, command_var=cvar), _next())
        got = fetch(prefix, now)
        if not isinstance(got, dict):
            got = {}
        origin = str(got.get("origin") or f"{prefix} ({self.source_var}=env)")
        return self._checked(got.get("token"), got.get("expires_at"),
                             origin, now, {})

    def _from_command(self, prefix: str, now: float) -> dict:
        var = self.command_var(prefix)
        cmd = self.chain.get(var)
        if not cmd:
            searched = ", ".join([_env.PROCESS, *self.chain.default_paths()])
            raise AuthError(
                msg("auth_command_missing",
                    f"{var} is required when {self.source_var}=command (a "
                    f"command that prints one JSON object with access_token "
                    f"and expires_at). Searched: {searched}",
                    var=var, source_var=self.source_var, searched=searched),
                _next())
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            argv, detail = [], str(e)
        else:
            detail = "empty"
        if not argv:
            raise AuthError(
                msg("auth_command_invalid",
                    f"{var} is not a valid command line: {detail}",
                    var=var, detail=detail), _next())
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL,
                                  timeout=self.command_timeout)
        except subprocess.TimeoutExpired:
            raise AuthError(
                msg("auth_command_timeout",
                    f"{var} timed out after {self.command_timeout:g}s",
                    var=var, seconds=self.command_timeout), _next()) from None
        except OSError as e:
            detail = e.strerror or str(e)
            raise AuthError(
                msg("auth_command_not_started",
                    f"{var} could not start {argv[0]!r}: {detail}",
                    var=var, program=argv[0], detail=detail),
                _next()) from None
        if proc.returncode != 0:
            err = proc.stderr.strip()[-300:]   # stderr only: stdout may hold a token
            raise AuthError(
                msg("auth_command_exit",
                    f"{var} exited {proc.returncode}"
                    + (f": {err}" if err else ""),
                    var=var, status=proc.returncode, stderr=err), _next())
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise AuthError(
                msg("auth_command_not_json",
                    f"{var} did not print one JSON object on stdout", var=var),
                _next())
        fields = {k: payload[k] for k in self.command_fields
                  if payload.get(k) not in (None, "")}
        return self._checked(payload.get("access_token"),
                             payload.get("expires_at"), var, now, fields)


def static_env_token(chain: _env.EnvChain | None = None, *,
                     var_suffix: str = "ACCESS_TOKEN",
                     expires_suffix: str | None = "TOKEN_EXPIRES_AT"
                     ) -> Fetcher:
    """An env-mode fetcher for a long-lived token kept in the env chain:
    `<prefix>_<var_suffix>` (required: EnvError env_required when unset)
    and, when `expires_suffix` is given, the optional
    `<prefix>_<expires_suffix>` (epoch s / ms / ISO 8601)."""

    def fetch(prefix: str, now: float) -> dict:
        ch = chain or _env.chain()
        var = f"{prefix}_{var_suffix}"
        token = ch.require(var)
        exp = None
        if expires_suffix:
            evar = f"{prefix}_{expires_suffix}"
            raw = ch.get(evar)
            exp = parse_expiry(raw, evar) if raw not in (None, "") else None
        return {"token": token, "expires_at": exp, "origin": var}
    return fetch


_source: TokenSource | None = None


def source() -> TokenSource:
    """The bound harness's TokenSource (cached until config.use):
    `<P>_TOKEN_SOURCE`, and `[auth] command_fields` / `command_timeout`
    of harness.toml when set."""
    global _source
    if _source is None:
        spec = _config.config().raw.get("auth", {})
        _source = TokenSource(
            command_fields=tuple(spec.get("command_fields", ())),
            command_timeout=float(spec.get("command_timeout",
                                           COMMAND_TIMEOUT)))
    return _source


@_config.on_reset
def _clear() -> None:
    global _source
    _source = None
