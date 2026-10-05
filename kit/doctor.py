"""`<cli> doctor [--live] [--strict] [--json]`: is this install healthy, and
where did each setting come from.

The built-in checks, in order (every line is a coded message; each warning
names its fix in `next`):

  harness        name, version, kit version, root
  env_chain      the env files read (later wins) or "process env only", and
                 every harness variable with its source; a secret's value is
                 never shown, only set/unset
  token_source   `<P>_TOKEN_SOURCE` is env or command
  home_secrets   a secret or the data dir that came from the HOME-level file
                 (shared by every client of this user) warns
  env_files      a chain file readable by group or other warns (chmod 600)
  data_dir       `<P>_DATA_DIR` set, absolute, outside the harness checkout;
                 the DB path too
  db             no DB yet (info); a DB stamped by a newer harness, human
                 table drift, an unreadable file, stale or unknown tables warn
  markets        a declared market (confirmed); pending-only or none warns;
                 `markets = []` needs none
  writes         "writes: off" is the healthy default (ok); the kill switch or
                 STOP file is ok too; writes ON is info; a
                 `<P>_ALLOW_WRITES` value that is not 1 warns (it enables
                 nothing, and was probably meant to)
  platform_lock  a harness hook (prior art #11: a rule set in the platform
                 itself, below the gate): "set" ok, "unknown" warns; no hook,
                 no line
  verbs          every script of the verb table exists; the non-dev verbs
                 without `examples` (their --help teaches the agent nothing
                 by example) are one info line, never a warning, so
                 `--strict` stays green on a harness that has not written
                 them yet
  kit_tty        KIT_TTY (the gate's test seam) set outside a test warns

then the harness's own checks: `check(ctx)` callables that call
`ctx.ok/info/warn/fail(check, message, next)` with coded messages. `--live`
is theirs to use (`ctx.live`): the kit makes no network call. A check that
raises is reported as a warning (doctor_check_crashed), never a traceback.

Exit: 1 when any check failed (a `--live` probe that was rejected); with
`--strict` any warning is fatal too (so `<cli> doctor --strict && <cli>
ingest …` is a safe gate); else 0. Under --json: one document
{harness, checks: [{check, level, message, message_code, next}], summary,
healthy, live, strict, message, message_code}.

Reads only: the DB is opened read-only and never created.

Test: kit/tests/test_doctor.py.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from kit import config as _config
from kit import contract
from kit.contract import HarnessError
from kit.messages import Msg, coded, msg

LEVELS = ("ok", "info", "warn", "fail")
SECRET_WORDS = ("SECRET", "TOKEN", "PASSWORD", "KEY", "CREDENTIAL")
SHOWN = ("DATA_DIR", "DB", "AUTH_ENV_PATHS", "TOKEN_SOURCE", "ALLOW_WRITES",
         "KILL", "CONFIRM_CODE_SECRET")


@dataclass
class Finding:
    check: str
    level: str
    message: Msg
    next: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"check": self.check, "level": self.level,
                **coded("message", self.message), "next": list(self.next)}


@dataclass
class Context:
    """What a doctor check sees, and where it reports."""
    live: bool = False
    strict: bool = False
    spec: Any = None                   # the harness's kit.db.SchemaSpec
    verbs: list = field(default_factory=list)
    scripts_dir: Path | None = None
    findings: list[Finding] = field(default_factory=list)
    _con: Any = None
    _con_tried: bool = False

    @property
    def cfg(self) -> _config.HarnessConfig:
        return _config.config()

    def add(self, level: str, check: str, message: Msg,
            next: Iterable[str] = ()) -> None:
        if level not in LEVELS:
            raise ValueError(f"doctor level {level!r} is not one of {LEVELS}")
        if not isinstance(message, Msg):
            raise TypeError("a doctor finding is a coded Msg (kit.messages."
                            "msg), not a plain str")
        self.findings.append(Finding(check, level, message, list(next)))

    def ok(self, check: str, message: Msg, next: Iterable[str] = ()) -> None:
        self.add("ok", check, message, next)

    def info(self, check: str, message: Msg, next: Iterable[str] = ()) -> None:
        self.add("info", check, message, next)

    def warn(self, check: str, message: Msg, next: Iterable[str] = ()) -> None:
        self.add("warn", check, message, next)

    def fail(self, check: str, message: Msg, next: Iterable[str] = ()) -> None:
        self.add("fail", check, message, next)

    def con(self):
        """A read-only connection to the harness DB, or None (no spec, no
        file, or a file the db check already reported). Closed by run()."""
        if not self._con_tried:
            self._con_tried = True
            self._con = _open_db(self.spec) if self.spec is not None else None
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


Check = Callable[[Context], None]
PlatformLock = Callable[[Context], Any]


def _open_db(spec: Any):
    from kit import db, paths
    try:
        path = paths.db_path()
    except HarnessError:
        return None
    if not path.exists():
        return None
    try:
        return db.connect(spec, path, read_only=True)
    except Exception:
        return None


def _cli() -> str:
    return _config.config().cli


def _secretish(name: str) -> bool:
    return any(w in name.upper() for w in SECRET_WORDS)


# ---- built-in checks --------------------------------------------------------

def check_harness(ctx: Context) -> None:
    cfg = ctx.cfg
    ver = contract.harness_version() or "unversioned"
    from kit import __version__ as kit_version
    ctx.ok("harness", msg("doctor_harness", f"{cfg.name} {ver} (kit "
                          f"{kit_version}) at {cfg.root}", name=cfg.name,
                          version=ver, kit_version=kit_version,
                          root=str(cfg.root)))


def check_env_chain(ctx: Context) -> None:
    from kit import env
    ch = env.chain()
    cfg = ctx.cfg
    files = ch.read or ch.default_paths()
    if files:
        ctx.info("env_chain", msg("doctor_env_chain", "env chain (later "
                                  f"wins, process env first): "
                                  f"{' -> '.join(files)}",
                                  files=list(files)))
    else:
        var = ch.paths_var
        ctx.info("env_chain", msg("doctor_env_process_only", "env chain: none"
                                  f" — process env only ({var}=none or "
                                  f"empty: the cloud / multi-client setting)",
                                  var=var))
    for name in SHOWN:
        var = cfg.env(name)
        value, source = ch.source(var)
        if value is None:
            shown, src = "unset", "unset"
        else:
            shown = "set" if _secretish(name) else (value or '""')
            src = source or "process env"
        ctx.info("env_chain", msg("doctor_env_var", f"{var} = {shown} [{src}]",
                                  name=var, value=shown, source=src))


def check_token_source(ctx: Context) -> None:
    from kit import auth
    try:
        mode = auth.source().mode()
    except HarnessError as e:
        ctx.warn("token_source", e.message, e.next)
        return
    ctx.ok("token_source", msg("doctor_token_source", f"token source (every "
                               f"API): {mode}", mode=mode))


def check_home_secrets(ctx: Context) -> None:
    from kit import env
    ch = env.chain()
    home = os.path.expanduser(ch.home_file) if ch.home_file else None
    if not home:
        return
    data_var = ctx.cfg.env("DATA_DIR")
    names = sorted(n for n, src in ch.loaded.items()
                   if src == home and (_secretish(n) or n == data_var))
    if names:
        ctx.warn("home_secrets", msg(
            "doctor_home_secrets", f"{', '.join(names)} came from {home} "
            f"(HOME-level: shared by every client of this user) — export them "
            f"in the process env instead and set {ch.paths_var}=none",
            names=names, file=home, var=ch.paths_var),
            [f"export {ch.paths_var}=none"])


def check_env_files(ctx: Context) -> None:
    from kit import env
    for p in env.chain().default_paths():
        try:
            mode = os.stat(p).st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode) and mode & 0o077:
            ctx.warn("env_files", msg("doctor_env_file_loose", f"{p} is "
                                      f"readable by other users", path=p),
                     [f"chmod 600 {p}"])


def check_data_dir(ctx: Context) -> None:
    from kit import env, paths
    cfg = ctx.cfg
    var = cfg.env("DATA_DIR")
    try:
        d = paths.data_dir()
    except HarnessError as e:
        ctx.warn("data_dir", e.message, e.next)
        return
    ctx.ok("data_dir", msg("doctor_path", f"{var} -> {d}", var=var,
                           path=str(d)))
    ch = env.chain()
    home = os.path.expanduser(ch.home_file) if ch.home_file else None
    if home and ch.loaded.get(var) == home:
        ctx.warn("data_dir", msg(
            "doctor_data_dir_from_home", f"{var} came from {home} (HOME-level: "
            f"any client that leaves it unset lands in this data dir)",
            var=var, file=home), [f"export {var}=/absolute/path/to/client-data",
                                  f"export {ch.paths_var}=none"])
    for v, p in ((var, d), (cfg.env("DB"), paths.db_path())):
        if v != var:
            ctx.ok("data_dir", msg("doctor_path", f"{v} -> {p}", var=v,
                                   path=str(p)))
        if paths.inside_repo(p):
            ctx.warn("data_dir", msg(
                "doctor_inside_repo", f"{v} -> {p} is inside the harness "
                f"checkout: client data would ship in a release or leak into "
                f"a commit", var=v, path=str(p)),
                [f"export {var}=/absolute/path/outside/the/checkout"])


def check_db(ctx: Context) -> None:
    from kit import db, paths
    try:
        path = paths.db_path()
    except HarnessError:
        return                                   # data_dir said why
    if not path.exists():
        ctx.info("db", msg("doctor_no_db", f"no database yet at {path} (a "
                           f"write verb creates it)", path=str(path)),
                 [f"{_cli()} facts init"])
        return
    if ctx.spec is None:
        ctx.info("db", msg("doctor_db_present", f"database at {path} (no "
                           f"schema given to doctor: shape not checked)",
                           path=str(path)))
        return
    try:
        con = db.connect(ctx.spec, path, read_only=True)
    except HarnessError as e:
        dump = [f"sqlite3 {path} .dump > {path}.dump.sql"]
        unreadable = getattr(e.message, "code", None) == "db_unreadable"
        ctx.warn("db", e.message, dump if unreadable else e.next)
        ctx._con_tried = True
        return
    except Exception as e:                       # a lock, an OS error …
        ctx.warn("db", msg("doctor_db_unreadable", f"the database at {path} "
                           f"cannot be read: {e}", path=str(path),
                           detail=str(e)),
                 [f"sqlite3 {path} .dump > {path}.dump.sql"])
        ctx._con_tried = True
        return
    ctx._con, ctx._con_tried = con, True
    stale = db.stale_tables(ctx.spec, con)
    unknown = db.unknown_tables(ctx.spec, con)
    if stale:
        ctx.warn("db", msg("doctor_db_stale", f"cache table(s) in an older "
                           f"shape: {', '.join(stale)} — the next ingest "
                           f"rebuilds them", tables=stale))
    if unknown:
        ctx.warn("db", msg("doctor_db_unknown", f"table(s) no longer in the "
                           f"schema (left by an older harness; do not read "
                           f"them): {', '.join(unknown)}", tables=unknown))
    if not stale and not unknown:
        ctx.ok("db", msg("doctor_db_ok", f"database at {path}: schema "
                         f"v{ctx.spec.version}, current", path=str(path),
                         version=ctx.spec.version))


def check_markets(ctx: Context) -> None:
    from kit import market
    if not ctx.cfg.markets:
        ctx.ok("markets", msg("doctor_no_partition", "no market partition "
                              "(markets = []): nothing to declare"))
        return
    con = ctx.con()
    confirmed = market.declared(con) if con is not None else []
    pending = ([m for m in market.declared(con, include_pending=True)
                if m not in confirmed] if con is not None else [])
    if confirmed:
        ctx.ok("markets", msg("doctor_markets_declared", f"declared "
                              f"market(s): {', '.join(confirmed)}",
                              markets=confirmed))
    if pending:
        ctx.warn("markets", msg(
            "doctor_markets_pending", f"market(s) {', '.join(pending)} "
            f"declared but not confirmed: a person confirms the declaration",
            markets=pending),
            [f"{_cli()} facts confirm market_declared --market {m} "
             f"--reason <why>" for m in pending])
    if not confirmed and not pending:
        ctx.warn("markets", msg("doctor_not_onboarded", "no market declared "
                                "— this harness is not onboarded yet"),
                 [f"{_cli()} facts init"])


def check_writes(ctx: Context) -> None:
    from kit import write_guard
    guard = write_guard.Guard({})
    why = guard.refusal()
    opt = guard.opt_in_env
    val = os.environ.get(opt)
    if why is None:
        ctx.info("writes", msg("doctor_writes_on", f"writes: ON ({opt}=1) — "
                               f"only the harness allowlist's calls can go "
                               f"out", var=opt),
                 [f"unset {opt}"])
        return
    if why.code == "write_off" and val not in (None, ""):
        ctx.warn("writes", msg("doctor_writes_bad_value", f"{opt}={val!r} does "
                               f"not turn writes on (only 1 does): writes are "
                               f"off", var=opt, value=val),
                 [f"unset {opt}"])
        return
    ctx.ok("writes", msg("doctor_writes_off", f"writes: off ({why}) — the "
                         f"healthy default", reason=why))


def check_platform_lock(ctx: Context, hook: PlatformLock | None) -> None:
    if hook is None:
        return
    got = hook(ctx)
    state, detail = (got if isinstance(got, tuple) else (got, ""))
    detail = str(detail or "")
    if state == "set":
        ctx.ok("platform_lock", msg("doctor_platform_lock_set", "platform "
                                    f"rule: set {detail}".rstrip(),
                                    detail=detail))
    else:
        ctx.warn("platform_lock", msg(
            "doctor_platform_lock_unknown", f"platform rule: unknown — ask the "
            f"client's admin to set the platform's own limit on what agents "
            f"may do, then record it {detail}".rstrip(), detail=detail))


def check_verbs(ctx: Context) -> None:
    if not ctx.verbs or ctx.scripts_dir is None:
        return
    missing = [v for v in ctx.verbs
               if not (ctx.scripts_dir / v.script).is_file()]
    for v in missing:
        ctx.warn("verbs", msg("doctor_verb_script_missing", f"`{_cli()} "
                              f"{' '.join(v.words)}` has no script at "
                              f"{v.script}", verb=" ".join(v.words),
                              script=v.script))
    if not missing:
        ctx.ok("verbs", msg("doctor_verbs_ok", f"{len(ctx.verbs)} verb(s), "
                            f"every script present", count=len(ctx.verbs)))
    bare = [" ".join(v.words) for v in ctx.verbs
            if v.kind != "dev" and not getattr(v, "examples", ())]
    if bare:
        ctx.info("verbs", msg(
            "doctor_verbs_no_examples", f"{len(bare)} verb(s) list no "
            f"examples, so their --help ends with none: {', '.join(bare)}",
            count=len(bare), verbs=", ".join(bare)))


def check_kit_tty(ctx: Context) -> None:
    if os.environ.get("KIT_TTY"):
        ctx.warn("kit_tty", msg("doctor_kit_tty", "KIT_TTY is set: the human "
                                "gate's test seam stands in for the terminal "
                                "(tests only)", var="KIT_TTY"),
                 ["unset KIT_TTY"])


BUILTIN: tuple[Check, ...] = (check_harness, check_env_chain,
                              check_token_source, check_home_secrets,
                              check_env_files, check_data_dir, check_db,
                              check_markets, check_writes)


# ---- run --------------------------------------------------------------------

def _crashed(ctx: Context, fn: Any, e: BaseException) -> None:
    name = getattr(fn, "__name__", repr(fn))
    ctx.warn(name, msg("doctor_check_crashed", f"the check {name} crashed: "
                       f"{type(e).__name__}: {e}", check=name,
                       detail=f"{type(e).__name__}: {e}"))


def collect(ctx_checks: Iterable[Check] = (), *, live: bool = False,
            strict: bool = False, spec: Any = None, verbs: Iterable = (),
            scripts_dir: Path | str | None = None,
            platform_lock: PlatformLock | None = None) -> Context:
    """Run every built-in check, then the harness's; the Context holds the
    findings."""
    ctx = Context(live=live, strict=strict, spec=spec, verbs=list(verbs),
                  scripts_dir=Path(scripts_dir) if scripts_dir else None)
    steps: list[Callable[[Context], None]] = [
        *BUILTIN, lambda c: check_platform_lock(c, platform_lock),
        check_verbs, check_kit_tty, *ctx_checks]
    try:
        for fn in steps:
            try:
                fn(ctx)
            except Exception as e:
                _crashed(ctx, fn, e)
    finally:
        ctx.close()
    return ctx


def verdict(ctx: Context) -> int:
    levels = [f.level for f in ctx.findings]
    if "fail" in levels or (ctx.strict and "warn" in levels):
        return 1
    return 0


def summary(ctx: Context) -> dict:
    return {lvl: sum(1 for f in ctx.findings if f.level == lvl)
            for lvl in LEVELS}


def _summary_msg(s: dict) -> Msg:
    if s["warn"] or s["fail"]:
        return msg("doctor_summary_warnings", f"{s['warn']} warning(s), "
                   f"{s['fail']} failure(s) — each names its fix above",
                   warnings=s["warn"], failures=s["fail"])
    return msg("doctor_summary_ok", "OK — no warnings")


def document(ctx: Context) -> dict:
    cfg = _config.config()
    from kit import __version__ as kit_version
    s = summary(ctx)
    return {"harness": {"name": cfg.name, "cli": cfg.cli,
                        "version": contract.harness_version(),
                        "kit_version": kit_version, "root": str(cfg.root)},
            "live": ctx.live, "strict": ctx.strict,
            "checks": [f.as_dict() for f in ctx.findings],
            "summary": s, "healthy": verdict(ctx) == 0,
            **coded("message", _summary_msg(s))}


MARK = {"ok": "ok  ", "info": "    ", "warn": "WARN", "fail": "FAIL"}


def render(ctx: Context) -> str:
    cfg = _config.config()
    lines = [f"=== {cfg.cli} doctor ==="]
    for f in ctx.findings:
        lines.append(f"  {MARK[f.level]}  {f.message}")
        lines += [f"          next: {n}" for n in f.next]
    lines.append("")
    lines.append(str(_summary_msg(summary(ctx))))
    return "\n".join(lines)


def run(ctx_checks: Iterable[Check] = (), *, live: bool = False,
        strict: bool = False, spec: Any = None, verbs: Iterable = (),
        scripts_dir: Path | str | None = None,
        platform_lock: PlatformLock | None = None,
        as_json: bool = False) -> int:
    """Every check; prints the report (text, or one JSON document); returns
    the exit code (see the module docstring)."""
    ctx = collect(ctx_checks, live=live, strict=strict, spec=spec,
                  verbs=verbs, scripts_dir=scripts_dir,
                  platform_lock=platform_lock)
    if as_json:
        contract.emit(document(ctx))
    else:
        print(render(ctx))
    return verdict(ctx)


def parser(cli: str) -> argparse.ArgumentParser:
    p = contract.Parser(
        prog=f"{cli} doctor",
        description="Is this install healthy: env and where each value came "
                    "from, the data dir, the database, the markets, the write "
                    "switches.")
    p.add_argument("--live", action="store_true",
                   help="also run the harness's live probes (network); exit 1 "
                        "if one is rejected")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 on ANY warning, so `doctor --strict && …` is "
                        "a safe gate")
    contract.add_json_arg(p)
    return p


def main(argv: list[str] | None = None, *, checks: Iterable[Check] = (),
         spec: Any = None, verbs: Iterable = (),
         scripts_dir: Path | str | None = None,
         platform_lock: PlatformLock | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    def go(argv: list[str]) -> int:
        a = parser(_cli()).parse_args(argv)
        return run(checks, live=a.live, strict=a.strict, spec=spec,
                   verbs=verbs, scripts_dir=scripts_dir,
                   platform_lock=platform_lock, as_json=a.json)
    return contract.run_main(go, argv, cmd=[_cli(), "doctor", *argv])
