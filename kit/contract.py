"""The output contract every harness verb keeps.

  * One error base, `HarnessError(message, next)`: `message` is a coded
    Msg, `next` the command(s) that fix it.
  * One stdout document under `--json`, a failure included: `fail()`
    prints exactly {"error", "next", "code", "params"[, "subject"]} on
    stdout and nothing on stderr; without --json it prints `error:` /
    `next:` lines on stderr. Never a traceback. Exit 2 for a HarnessError
    (a refusal the harness names; the gate's Refused is one), 1 for
    anything else.
  * A challenge from the human gate (an exception carrying `confirm_code`
    and `subject`, kit.human.CodeRequired) is rendered here too, byte for
    byte what console/relay.py expects: code confirm_code_required,
    params.confirm_code, `subject`, and `next` = the same command rerun
    with `--code C --relay-user <sender_id> --relay-at <iso_time>`.
  * Read verbs never create the DB: `missing_db_error()`.
  * Every compute `--json` carries one `meta` built by `meta()`: its
    required keys are always present (None / [] / {} when n/a), so an
    orchestrator never guesses a shape.

Note: the reference harness exited 1 on every failure; the kit follows
the spec (2 = a named refusal, 1 = anything else). Both are non-zero,
which is all console/relay.py and runner.run_json look at.

Test: kit/tests/test_contract.py.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import date, timedelta
from typing import Any, Callable, Iterable

import kit
from kit import dates
from kit.config import config
from kit.messages import Msg, failure, joined, msg


class HarnessError(Exception):
    """A failure the harness names: a (coded) message + the command(s) that
    fix it."""

    def __init__(self, message: Msg | str, next: Iterable[str] = ()):
        super().__init__(message)
        self.next = list(next)

    @property
    def message(self) -> Msg | str:
        return self.args[0]


def emit(doc: Any) -> None:
    """The one JSON document on stdout (one line, key order kept)."""
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=None,
                                sort_keys=False, default=str) + "\n")
    sys.stdout.flush()


def _failure(e: BaseException) -> dict:
    """failure(e), even when the registry itself cannot load (a broken
    harness.toml is reported through fail() too)."""
    try:
        return failure(e)
    except Exception:
        return {"code": "unclassified_error", "params": {"detail": str(e)}}


def failure_doc(e: BaseException, cmd: list[str]) -> dict:
    """{error, next, code, params[, subject]} for `e`; `cmd` is the command
    as run (["shop", "facts", "confirm", …])."""
    doc = {"error": str(e), "next": list(getattr(e, "next", None) or []),
           **_failure(e)}
    confirm_code = getattr(e, "confirm_code", None)
    if confirm_code is not None:
        doc["next"] = [f"{shlex.join(cmd)} --code {confirm_code} "
                       f"--relay-user <sender_id> --relay-at <iso_time>"]
    subject = getattr(e, "subject", None)
    if subject is not None:
        doc["subject"] = subject
    return doc


def fail(e: BaseException, *, cmd: list[str], as_json: bool) -> int:
    """The one failure output of every verb; returns the exit code."""
    doc = failure_doc(e, cmd)
    if as_json:
        emit(doc)
    else:
        print(f"error: {doc['error']}", file=sys.stderr)
        for c in doc["next"]:
            print(f"next: {c}", file=sys.stderr)
    return 2 if isinstance(e, HarnessError) else 1


def missing_db_error() -> HarnessError | None:
    """The refusal of a read verb when there is no DB file; None when there
    is one. A read creates nothing: only a write verb makes the DB. `next`
    = `[contract].no_db_next` of harness.toml (commands after the CLI
    word), else `<cli> doctor` + `<cli> facts init`."""
    from kit import paths
    path = paths.db_path()
    if path.exists():
        return None
    cfg = config()
    hook = cfg.raw.get("contract", {}).get("no_db_next")
    steps = ([hook] if isinstance(hook, str) else list(hook)) if hook \
        else ["doctor", "facts init"]
    nxt = [s if s.startswith(f"{cfg.cli} ") else f"{cfg.cli} {s}"
           for s in steps]
    return HarnessError(msg("no_db", f"No database at {path}",
                            path=str(path)), nxt)


# ---- meta: the top-level object of every compute --json -------------------

WINDOW_KEYS = ("start", "end", "days_requested", "days_found")
SOURCE_KEYS = ("table", "pulled_on")
STALE_KEYS = ("table", "last_date", "lag_days", "max_lag_days")
REQUIRED_KEYS = ("window", "sources", "stale", "coverage",
                 "thresholds_overridden", "assumed_thresholds",
                 "evidence_level", "harness")


def _iso(d: date | str) -> str:
    return d if isinstance(d, str) else d.isoformat()


def span(start: date | str, end: date | str) -> list[str]:
    """Every ISO day of start..end, both included."""
    lo, hi = date.fromisoformat(_iso(start)), date.fromisoformat(_iso(end))
    return [(lo + timedelta(n)).isoformat() for n in range((hi - lo).days + 1)]


def window(start: date | str, end: date | str,
           present_days: Iterable[date | str]) -> dict:
    """{start, end, days_requested, days_found} of the REQUESTED window."""
    days = span(start, end)
    present = {_iso(d) for d in present_days}
    return {"start": days[0] if days else _iso(start),
            "end": days[-1] if days else _iso(end),
            "days_requested": len(days),
            "days_found": sum(1 for d in days if d in present)}


def missing_days(start: date | str, end: date | str,
                 present_days: Iterable[date | str]) -> list[str]:
    """The requested days not in `present_days` (coverage.missing_days)."""
    present = {_iso(d) for d in present_days}
    return [d for d in span(start, end) if d not in present]


def harness_version() -> str | None:
    """[harness].version of harness.toml, else <root>/VERSION, else None."""
    cfg = config()
    v = cfg.raw.get("harness", {}).get("version")
    if v:
        return str(v)
    f = cfg.root / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else None


def _rows(name: str, rows: list[dict] | None, keys: tuple[str, ...]
          ) -> list[dict]:
    rows = list(rows or [])
    for r in rows:
        if not (isinstance(r, dict) and set(keys) <= set(r)):
            raise ValueError(f"meta.{name}: every item needs {keys}, got {r!r}")
    return rows


def meta(*, window: dict | None, sources: list[dict], stale: list[dict],
         coverage: dict | None = None,
         thresholds_overridden: dict | None = None,
         assumed_thresholds: dict | None = None,
         evidence_level: str | None = None,
         extra: dict | None = None) -> dict:
    """The one `meta` object. Required keys always present:
    window {start,end,days_requested,days_found} | None,
    sources [{table, pulled_on}], stale [{table,last_date,lag_days,
    max_lag_days}], coverage {missing_days, next, …} | None,
    thresholds_overridden {name: …} ({} when none), assumed_thresholds
    ({} when none: anything else means the run is a what-if),
    evidence_level | None, harness {name, version, kit_version}.
    `extra` adds keys (a harness's own, e.g. `settled`); it may not
    replace a required one. A malformed part is a ValueError: a bug in
    the compute, not a runtime condition."""
    if window is not None and not (isinstance(window, dict)
                                   and set(WINDOW_KEYS) <= set(window)):
        raise ValueError(f"meta.window needs {WINDOW_KEYS}, got {window!r}")
    if coverage is not None and not (
            isinstance(coverage, dict)
            and isinstance(coverage.get("missing_days"), list)
            and isinstance(coverage.get("next"), list)):
        raise ValueError(f"meta.coverage needs missing_days and next lists, "
                         f"got {coverage!r}")
    cfg = config()
    doc = {
        "window": window,
        "sources": _rows("sources", sources, SOURCE_KEYS),
        "stale": _rows("stale", stale, STALE_KEYS),
        "coverage": coverage,
        "thresholds_overridden": dict(thresholds_overridden or {}),
        "assumed_thresholds": dict(assumed_thresholds or {}),
        "evidence_level": evidence_level,
        "harness": {"name": cfg.name, "version": harness_version(),
                    "kit_version": kit.__version__},
    }
    clash = set(extra or {}) & set(doc)
    if clash:
        raise ValueError(f"meta: extra may not replace {sorted(clash)}")
    doc.update(extra or {})
    return doc


def _ticks(cmds: list[str]) -> str:
    return " then ".join(f"`{c}`" for c in cmds)


def coverage_warning(m: dict) -> Msg | None:
    """One line when the requested window is not fully covered: fewer days
    found than requested (SHORT WINDOW), or a source with gaps of its own
    (PARTIAL SOURCE, from the optional coverage.missing_days_by_source),
    then the backfill that refills them (coverage.next). None when
    covered."""
    w, cov = m.get("window"), m.get("coverage") or {}
    gaps = {t: dates.ranges(d)
            for t, d in (cov.get("missing_days_by_source") or {}).items() if d}
    nxt = list(cov.get("next") or [])
    fix = f" — backfill: {_ticks(nxt)}" if nxt else ""
    what = "; ".join(f"{t} has no rows for {r}" for t, r in gaps.items())
    if w and w["days_found"] < w["days_requested"]:
        missing = dates.ranges(cov.get("missing_days") or [])
        return msg(
            "contract_short_window",
            f"⚠ SHORT WINDOW — only {w['days_found']} of "
            f"{w['days_requested']} requested days ({w['start']}..{w['end']}) "
            f"have data, so results rest on {w['days_found']} day(s) — "
            f"{what or 'no rows for ' + missing}{fix}",
            days_found=w["days_found"], days_requested=w["days_requested"],
            start=w["start"], end=w["end"], missing=missing, gaps=gaps,
            next=nxt)
    if gaps:
        return msg("contract_partial_source",
                   f"⚠ PARTIAL SOURCE — {what}{fix}", gaps=gaps, next=nxt)
    return None


def stale_warning(m: dict) -> Msg | None:
    """The top-of-output warning a text report prints from meta, None when
    there is none: the ASSUMED line when the run has what-if thresholds,
    the STALE DATA line when meta.stale is non-empty, then
    coverage_warning(). Several lines = one `joined` message. A compute
    warns, never refuses, on stale or partial data."""
    lines: list[Msg] = []
    if a := m.get("assumed_thresholds"):
        def was(v: dict) -> Any:
            cv = v.get("client_value")
            return v.get("default") if cv is None else cv
        lines.append(msg(
            "contract_assumed_thresholds",
            "⚠ ASSUMED (not stored): " + "; ".join(
                f"{n}={v.get('value')} (in effect was {was(v)})"
                for n, v in a.items()),
            assumed=a))
    if s := m.get("stale"):
        lines.append(msg(
            "contract_stale_data",
            "⚠ STALE DATA — " + "; ".join(
                f"{r['table']} ends {r['last_date']}, {r['lag_days']} days "
                f"behind (max {r['max_lag_days']})" for r in s)
            + " — refresh the data before acting on this",
            stale=s))
    if c := coverage_warning(m):
        lines.append(c)
    if not lines:
        return None
    return lines[0] if len(lines) == 1 else joined(lines, sep="\n")


# ---- argparse + main glue --------------------------------------------------

def add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="print one JSON document on stdout (errors "
                             "included) instead of text")


def run_main(fn: Callable[[list[str]], Any], argv: list[str], *,
             cmd: list[str], as_json_default: bool = False) -> int:
    """Run `fn(argv)`: a dict/list it returns is emitted as the one JSON
    document; an int is the exit code; None = 0 (it printed its own text).
    Any Exception goes through fail(); --json (before a literal `--`) or
    `as_json_default` picks the JSON failure. SystemExit (argparse) and
    KeyboardInterrupt pass through."""
    head = argv[:argv.index("--")] if "--" in argv else argv
    as_json = as_json_default or "--json" in head
    try:
        out = fn(argv)
    except Exception as e:
        return fail(e, cmd=cmd, as_json=as_json)
    if isinstance(out, bool):
        raise TypeError("run_main: fn returned a bool; return an exit code")
    if isinstance(out, int):
        return out
    if out is None:
        return 0
    emit(out)
    return 0
