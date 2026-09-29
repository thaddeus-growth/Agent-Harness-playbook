"""Every story's check, run on a client's data — read-only.

The story-check registry (`[ssot].story_checks`, a TSV with columns
`story, verb, expect, needs` and a note) holds one row per story:
`verb`, the read command that proves it as typed after the CLI word
(without `--json` / `--market`, which this adds; `{market}` in it = the
market as an SQL string literal, so an SQL check counts that market's
rows only); `expect`, what that command's --json must hold; `needs`, the
tables (`;`-separated) it needs rows in for the market. Each distinct verb
runs once, through the harness CLI (kit.runner.run_json), with `--json`,
`--market` where the verb takes one, and this run's `--assume` on a
compute.

    pass  the verb printed its document and every `expect` term holds
    fail  the verb failed (story_check_verb_failed, its own failure code
          nested as `error`), or a term does not hold
          (story_check_expect_failed, the terms that don't)
    skip  a `needs` table holds no row for the market yet — the client
          lacks the data, so the story is not judged (story_check_no_data);
          the verb failed for want of data (a `no_data` code, nested as
          `error`: story_check_verb_no_data); or the row has no verb: its
          proof is the test suite in CI (story_check_ci_only)

`expect` terms, space-separated, all must hold. A path is dot-separated
keys; `[]` takes every element of a list (a leading `[]`: the document is
a list), `*` every value of an object:

    path      some value at path is not null
    path>0    some value is a number > 0 or a non-empty list/object/string
    path=V    some value is V (compared as JSON text: true, 0, executed)
    !term     the term does not hold

Only read verbs run: a row whose verb is not a `read` verb of the verb
table (kit.verbs), or is a verb whose script may itself spawn verbs
(`[layers].spawn_allowed`: no recursion), refuses the whole run before
anything runs (story_check_verb_refused), as does a malformed `expect`
term (story_check_bad_term). So this writes nothing and calls no external
system. Output {meta, market, stories: [{id, status, verb, reason,
reason_code}], summary: {pass, fail, skip}}; exit 0 whatever the stories'
status: a failed story is a finding, not an error.

Deviations (SPEC §stories / the reference): a verb runs through the
harness CLI (`<cli>.py <words> -- <rest> --json …`, the dispatcher
strips the first `--`), not by importing the verb table's script; a
verb whose script is in `[layers].spawn_allowed` is refused (the
reference hard-coded "every compute but this one"); `--assume` goes to
the verbs whose script matches `[layers].compute`; the default meta is
an empty kit.contract.meta (window None) and the default has_rows reads
a `market` column: the reference's 30-day window over its own tables is
domain, so both are hooks; a refused run exits 2 (kit.contract.fail);
story_check_registry_missing / _bad are new codes.

A harness runs it as its `compute stories` verb:
`raise SystemExit(kit.stories.main(sys.argv[1:]))`; every step is a hook
(verbs, has_rows, resolve_market, run_verb, meta) it may replace.

Test: kit/tests/test_stories.py.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

from kit import contract, runner
from kit.config import config
from kit.contract import HarnessError
from kit.messages import Msg, coded, msg, relayed

NONE = "—"
COLUMNS = ("story", "verb", "expect", "needs")
TERM = re.compile(r"(!?)([^!=>]+?)(>0|=.+)?")
NO_DATA_CODES = ("no_data",)
STATUSES = ("pass", "fail", "skip")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Check(NamedTuple):
    story: str
    verb: str                   # as registered; "" = no data check (CI only)
    expect: tuple[str, ...]
    needs: tuple[str, ...]


def _none(cell: str) -> bool:
    return cell.strip() in ("", NONE)


def verb_table(verbs: Any = None) -> list:
    """The verb table as a list (objects with words, script, kind,
    takes_market): `verbs` as given, else kit.verbs.load(<scripts_dir>/
    verbs.py)."""
    if verbs is None:
        from kit import verbs as kit_verbs     # the one verb table (§verbs)
        cfg = config()
        verbs = kit_verbs.load(cfg.root / cfg.scripts_dir / "verbs.py")
    if isinstance(verbs, Mapping):
        verbs = verbs.values()
    return list(verbs)


def match(words: list[str], table: list) -> Any:
    """The verb whose words are the longest prefix of `words`, else None."""
    best = None
    for v in table:
        w = tuple(v.words)
        if w and tuple(words[:len(w)]) == w and (
                best is None or len(w) > len(best.words)):
            best = v
    return best


def _stem(script: Any) -> str:
    return Path(str(script)).stem


def _spawners() -> set[str]:
    return {Path(str(s)).name.removesuffix(".py")
            for s in config().layers.get("spawn_allowed", ())}


def rows(path: Path) -> list[dict[str, str]]:
    """The registry's rows (strict TSV: a cell is everything between two
    tabs), refused as a whole when the file or a column is missing."""
    if not path.is_file():
        raise HarnessError(msg(
            "story_check_registry_missing",
            f"No story-check registry at {path}: declare [ssot].story_checks "
            f"in harness.toml and create the file", path=str(path)), [])
    lines = [line.rstrip("\r").split("\t")
             for line in path.read_text(encoding="utf-8").split("\n")]
    lines = [r for r in lines if any(c.strip() for c in r)]
    head = [c.strip() for c in (lines[0] if lines else [])]
    missing = [c for c in COLUMNS if c not in head]
    if missing:
        detail = f"the header lacks {', '.join(missing)}"
        raise HarnessError(msg(
            "story_check_registry_bad",
            f"{path} is not a valid story-check registry: {detail}",
            path=str(path), detail=detail), [])
    out = []
    for n, r in enumerate(lines[1:], start=2):
        if len(r) != len(head):
            detail = f"line {n} has {len(r)} cells, the header {len(head)}"
            raise HarnessError(msg(
                "story_check_registry_bad",
                f"{path} is not a valid story-check registry: {detail}",
                path=str(path), detail=detail), [])
        out.append(dict(zip(head, (c.strip() for c in r))))
    return out


def load(path: Path | str | None = None, *, verbs: Any = None
         ) -> list[Check]:
    """The registry's checks; a verb that is not a read verb, or a
    malformed `expect` term, refuses them all (HarnessError)."""
    cfg = config()
    path = Path(path) if path else cfg.ssot_path("story_checks")
    if path is None:
        raise HarnessError(msg(
            "story_check_registry_missing",
            "No story-check registry: harness.toml declares no "
            "[ssot].story_checks", path=""), [])
    table = verb_table(verbs)
    spawners = _spawners()
    out = []
    for r in rows(path):
        story = r["story"]
        verb = "" if _none(r["verb"]) else r["verb"]
        if verb:
            try:
                words = shlex.split(verb)
            except ValueError:          # an unbalanced quote: not a verb
                words = []
            v = match(words, table) if words else None
            if v is None or v.kind != "read" or _stem(v.script) in spawners:
                raise HarnessError(msg(
                    "story_check_verb_refused",
                    f"{path.name} {story}: `{verb}` is not a read verb a "
                    f"story check may run; nothing was run",
                    story=story, verb=verb), [])
        expect = () if _none(r["expect"]) else tuple(r["expect"].split())
        for t in expect:
            if not TERM.fullmatch(t):
                raise HarnessError(msg(
                    "story_check_bad_term",
                    f"{path.name} {story}: `{t}` is not an expect term "
                    f"(path, path>0, path=V, !term); nothing was run",
                    story=story, term=t), [])
        needs = tuple(t.strip() for t in r["needs"].split(";")
                      if not _none(t))
        out.append(Check(story, verb, expect, needs))
    return out


# ---- expect: a key path and what its values must be -----------------------

def values(doc: Any, path: str) -> list:
    """Every value at `path` (keys by dot, `[]` each list element, `*` each
    object value); a missing key contributes nothing."""
    nodes = [doc]
    for part in re.findall(r"\[\]|[^.\[\]]+", path):
        nxt: list = []
        for n in nodes:
            if part == "[]":
                nxt += n if isinstance(n, list) else []
            elif part == "*":
                nxt += list(n.values()) if isinstance(n, dict) else []
            elif isinstance(n, dict) and part in n:
                nxt.append(n[part])
        nodes = nxt
    return nodes


def _positive(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v > 0
    return isinstance(v, (list, dict, str)) and len(v) > 0


def holds(doc: Any, term: str) -> bool:
    neg, path, test = TERM.fullmatch(term).groups()
    vs = values(doc, path)
    if test == ">0":
        ok = any(_positive(v) for v in vs)
    elif test:
        ok = any((v if isinstance(v, str) else json.dumps(v)) == test[1:]
                 for v in vs)
    else:
        ok = any(v is not None for v in vs)
    return ok != bool(neg)


# ---- the default hooks -----------------------------------------------------

def table_has_rows(con: sqlite3.Connection, table: str, market: str) -> bool:
    """`table` (or view) holds a row of `market`'s: rows whose `market`
    column is the market, or any row when it has no such column."""
    if not _IDENT.match(table) or con.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name = ?", (table,)).fetchone() is None:
        return False
    cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    if "market" in cols:
        sql, args = f'SELECT 1 FROM "{table}" WHERE market = ? LIMIT 1', \
            (market,)
    else:
        sql, args = f'SELECT 1 FROM "{table}" LIMIT 1', ()
    return con.execute(sql, args).fetchone() is not None


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def verb_argv(words: list[str], rest: list[str], flags: list[str]
              ) -> list[str]:
    """argv after the CLI word: the verb's words, `--` (the dispatcher strips
    the first literal `--` and forwards the rest verbatim) unless `rest`
    already has one, then `rest` and `flags`."""
    return [*words, *([] if "--" in rest else ["--"]), *rest, *flags]


def _is_compute(v: Any) -> bool:
    """The verb's script is a compute ([layers].compute, default compute_*):
    it takes --assume."""
    pats = config().layers.get("compute", "compute_*")
    pats = [pats] if isinstance(pats, str) else list(pats)
    return any(fnmatch(_stem(v.script), p) for p in pats)


def run_via_cli(verb: str, market: str, assume: list[str], table: list
                ) -> Any:
    """The verb's --json document, else the runner.ChildFailed its run
    raised. Run as typed through `<scripts_dir>/<cli>.py`, as the harness
    CLI forwards it; a document without `error` is the verb's output
    whatever its exit (run_json's any_exit: an exit code that flags a
    finding the document carries)."""
    cfg = config()
    words = [w.replace("{market}", sql_literal(market))
             for w in shlex.split(verb)]
    v = match(words, table)
    n = len(v.words)
    flags = ["--json"]
    if getattr(v, "takes_market", True) and cfg.markets:
        flags += ["--market", market]
    if _is_compute(v):
        flags += assume
    target = cfg.root / cfg.scripts_dir / f"{cfg.cli}.py"
    try:
        return runner.run_json(target, verb_argv(words[:n], words[n:], flags),
                               any_exit=True)
    except runner.ChildFailed as e:
        return e


def _resolve_market(con: sqlite3.Connection, explicit: str | None,
                    cmd: list[str]) -> str:
    from kit import market      # SPEC §market: resolve(con, explicit)
    return market.resolve(con, explicit)


# ---- one story -------------------------------------------------------------

def judge(c: Check, *, market: str, has_rows: Callable[[str], bool],
          run: Callable[[str], Any],
          no_data_codes: tuple[str, ...] = NO_DATA_CODES
          ) -> tuple[str, Msg | None]:
    """(status, reason) of one story; `run(verb)` = its (cached) document
    or the runner.ChildFailed its run raised."""
    cli = config().cli
    if not c.verb:
        return "skip", msg("story_check_ci_only", f"{c.story}: proved by the "
                           f"harness's tests in CI; no check on the client's "
                           f"data", story=c.story)
    for t in c.needs:
        if not has_rows(t):
            return "skip", msg(
                "story_check_no_data", f"{c.story}: no {t} rows for market "
                f"{market} yet, so `{cli} {c.verb}` was not checked",
                story=c.story, verb=c.verb, table=t, market=market)
    doc = run(c.verb)
    if isinstance(doc, runner.ChildFailed):
        error = doc.message if isinstance(doc.message, Msg) \
            else relayed(doc.code, str(doc))
        if (doc.code or {}).get("code") in no_data_codes:
            return "skip", msg(
                "story_check_verb_no_data", f"{c.story}: `{cli} {c.verb}` "
                f"found no data for market {market} yet ({doc}), so it was "
                f"not checked", story=c.story, verb=c.verb, market=market,
                error=error)
        return "fail", msg("story_check_verb_failed",
                           f"{c.story}: `{cli} {c.verb}` failed: {doc}",
                           story=c.story, verb=c.verb, error=error)
    bad = [t for t in c.expect if not holds(doc, t)]
    if bad:
        return "fail", msg(
            "story_check_expect_failed", f"{c.story}: `{cli} {c.verb}` ran, "
            f"but {' '.join(bad)} does not hold", story=c.story, verb=c.verb,
            expect=bad)
    return "pass", None


def run_all(checks: list[Check], *, market: str,
            has_rows: Callable[[str], bool], run_verb: Callable[[str], Any],
            no_data_codes: tuple[str, ...] = NO_DATA_CODES) -> dict:
    """{stories, summary}: each check judged, each distinct verb run once."""
    docs: dict[str, Any] = {}

    def run(verb: str) -> Any:
        if verb not in docs:
            docs[verb] = run_verb(verb)
        return docs[verb]

    stories = []
    for c in checks:
        status, reason = judge(c, market=market, has_rows=has_rows, run=run,
                               no_data_codes=no_data_codes)
        stories.append({"id": c.story, "status": status,
                        "verb": c.verb or None, **coded("reason", reason)})
    return {"stories": stories,
            "summary": {s: sum(r["status"] == s for r in stories)
                        for s in STATUSES}}


def render(doc: dict) -> str:
    s, cli = doc["summary"], config().cli
    lines = [f"story checks (market={doc['market']}): {s['pass']} pass, "
             f"{s['fail']} fail, {s['skip']} skip"]
    for r in doc["stories"]:
        lines.append(f"  {r['id']}  {r['status']:<4}  "
                     + (r["reason"] or f"{cli} {r['verb']}"))
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, verbs: Any = None,
         registry: Path | str | None = None,
         has_rows: Callable[[sqlite3.Connection, str, str], bool]
         | None = None,
         resolve_market: Callable[..., str] | None = None,
         run_verb: Callable[[str, str, list[str]], Any] | None = None,
         meta: Callable[[sqlite3.Connection, str], dict] | None = None,
         no_data_codes: tuple[str, ...] = NO_DATA_CODES) -> int:
    """`<cli> compute stories [--market M] [--assume N=V]… [--json]`.

    Hooks: `verbs` (the verb table), `registry` (the TSV path),
    `has_rows(con, table, market)`, `resolve_market(con, explicit, cmd)`
    (default kit.market.resolve), `run_verb(verb, market, assume)`
    (default: through the harness CLI), `meta(con, market)` (default: an
    empty kit.contract.meta)."""
    cfg = config()
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog=f"{cfg.cli} compute stories",
                                description=__doc__.splitlines()[0])
    p.add_argument("--market")
    p.add_argument("--assume", action="append", default=[],
                   metavar="NAME=VALUE",
                   help="passed on to every compute the checks run")
    contract.add_json_arg(p)
    args = p.parse_args(argv)
    cmd = [cfg.cli, "compute", "stories"]
    assume = [x for a in args.assume for x in ("--assume", a)]
    try:
        table = verb_table(verbs)
        checks = load(registry, verbs=table)
        err = contract.missing_db_error()
        if err is not None:
            raise err
        from kit import paths
        con = sqlite3.connect(paths.db_path().as_uri() + "?mode=ro", uri=True)
        try:
            market = (resolve_market or _resolve_market)(con, args.market, cmd)
            rows_of = has_rows or table_has_rows
            result = run_all(
                checks, market=market,
                has_rows=lambda t: rows_of(con, t, market),
                run_verb=lambda v: (run_verb(v, market, assume) if run_verb
                                    else run_via_cli(v, market, assume,
                                                     table)),
                no_data_codes=no_data_codes)
            m = meta(con, market) if meta else contract.meta(
                window=None, sources=[], stale=[])
        finally:
            con.close()
    except Exception as e:
        return contract.fail(e, cmd=cmd, as_json=args.json)
    doc = {"meta": m, "market": market, **result}
    if args.json:
        contract.emit(doc)
    else:
        if warning := contract.stale_warning(m):
            print(warning)
        print(render(doc))
    return 0


def check_registry(path: Path | str | None = None, *, verbs: Any = None,
                   known_tables: set[str] | None = None,
                   required: tuple[str, ...] = (),
                   note_column: str = "note") -> list[str]:
    """Problems of the registry itself (for the harness's ssot test): it
    loads (read verbs only, well-formed terms); one row per story; a row
    without a verb has no expect and no needs, one with a verb at least
    one expect term; every `needs` is a known table (when given); every
    `required` story has a row; the note column, when present, is set."""
    try:
        checks = load(path, verbs=verbs)
    except HarnessError as e:
        return [str(e)]
    cfg = config()
    raw = rows(Path(path) if path else cfg.ssot_path("story_checks"))
    out = []
    ids = [c.story for c in checks]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        out.append(f"more than one row for {dup}")
    for c in checks:
        if bool(c.verb) != bool(c.expect) or (not c.verb and c.needs):
            out.append(f"{c.story}: a row with a verb needs an expect term; "
                       f"one without a verb has no expect and no needs")
        if known_tables is not None and not set(c.needs) <= known_tables:
            out.append(f"{c.story}: needs unknown tables "
                       f"{sorted(set(c.needs) - known_tables)}")
    missing = sorted(set(required) - set(ids))
    if missing:
        out.append(f"stories without a check row: {missing}")
    if raw and note_column in raw[0]:
        blank = [r["story"] for r in raw if not r[note_column].strip()]
        if blank:
            out.append(f"rows without a {note_column}: {blank}")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
