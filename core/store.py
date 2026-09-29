"""The database guards: what keeps a harness from losing data while its tests
stay green. Stdlib-only (sqlite3), Python 3.11+.

A harness describes its tables once, as a `Schema`, and opens its SQLite file
only through `open_read` (read verbs) and `open_write` (ingest and the human
verbs). There are two kinds of table, kept apart:

    cache table   filled from raw files by an ingest; raw can always refill it
    human table   typed, confirmed or approved by a person; no pull refills it

The rules, each learned from a bug that passed the source project's tests:

  - Every open reads `PRAGMA user_version` first, read-only opens included,
    and refuses a file stamped by a newer schema (`SchemaTooNew`): nothing is
    read or changed. A write open stamps `Schema.version`, and only when the
    stamp differs. Pin `fingerprint(schema)` in a test beside the version, so
    no table changes without a bump.
  - `open_read` never creates the file (`NoDatabase`): only a write verb makes
    it. A write open that creates it says so on stderr, so a mistyped path
    never passes as a new, empty store.
  - A cache table whose shape changed is "stale" (`stale_tables`). It is
    rebuilt only when the ingest that fills it names it (`rebuild=`); readers
    that trust numbers refuse while any table is stale. A table that keeps
    history (`Table.day`) is rebuilt only if raw holds every day the table
    holds, compared as a set and not a count: a rolling window with as many
    days, shifted, still loses its oldest ones. Otherwise `LossyRebuild` is
    raised before anything is dropped, and `LossyRebuild.note()` names the
    backfill pull. A new shape that only adds columns keeps every row, the new
    columns NULL.
  - A human table is never dropped or migrated by code: a changed shape raises
    `HumanDrift` (back it up and migrate it by hand). Every write runs inside
    `keep_human_rows`, one transaction that rolls back if any human table has
    fewer rows at the end than at the start (`HumanRowsLost`). Triggers
    written into the file refuse what `Table.refuse` names (UPDATE, DELETE),
    freeze every column but the decision (`Table.changes_only`), and refuse
    an INSERT over an existing key, whatever connection writes: a hand-typed
    SQL session included. (REPLACE deletes the old row without firing DELETE
    triggers and is no UPDATE, so without that last trigger it rewrites a
    history row and keeps the row count. Change a human row with UPDATE.)
  - Before every rebuild, the human tables are copied to
    `<db dir>/backups/<db stem>-human-<UTC>.db` (`snapshot_human`), newest
    `KEEP` kept. `shrunk_since_snapshot` is the doctor's "this database held
    human data and lost it" check.

Connections come back in autocommit mode (`isolation_level=None`): wrap each
write in `keep_human_rows`, which is its transaction. Every refusal is a
`Refused` with a stable `code` and `params`, so an agent can word it.

    schema = Schema(1, {"daily": Table({"day": "TEXT", "entity": "TEXT",
                                        "cost": "REAL"}, ("day", "entity"),
                                       day="day"), ...})
    conn = open_write(path, schema, rebuild=("daily",),
                      raw_days={"daily": days_in_raw})
    with keep_human_rows(conn, schema):
        conn.executemany("INSERT OR REPLACE INTO daily VALUES (?, ?, ?)", rows)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import dates

KEEP = 30                                   # human-table snapshots kept per database
OPS = ("UPDATE", "DELETE")                  # what a trigger may refuse on a human table
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_TYPE = re.compile(r"^[A-Za-z ]*$")


# ------------------------------------------------------------------ refusals --

class Refused(Exception):
    """A guard said no, and nothing was changed. `code` is stable; the text is
    for an operator; `params` carries the values the text names."""
    code = "refused"

    def __init__(self, text: str, **params):
        super().__init__(text)
        self.params = params


class NoDatabase(Refused):
    code = "no_db"


class SchemaTooNew(Refused):
    code = "schema_too_new"


class HumanDrift(Refused):
    code = "human_drift"


class HumanRowsLost(Refused):
    code = "human_rows_lost"


class LossyRebuild(Refused):
    """A stale table's rebuild would drop days raw cannot refill. Raised
    before anything is dropped. `losses` is [(table, days_held,
    days_raw_lacks)], the lacking days sorted."""
    code = "lossy_rebuild"

    def __init__(self, losses: list[tuple[str, int, list[str]]]):
        self.losses = losses
        super().__init__("; ".join(f"{t}: raw lacks {day_ranges(m)}" for t, _, m in losses),
                         tables=[t for t, _, _ in losses])

    def note(self, backfill: Callable[[str, list[str]], str | None]) -> str:
        """One line per table. `backfill(table, missing_days)` returns the pull
        command that restores those days (the ingest knows its puller), or None
        when no pull can (the platform keeps no past state of it)."""
        lines = []
        for t, held, missing in self.losses:
            cmd = backfill(t, missing)
            fix = (f"Backfill first: `{cmd}`, then run this ingest again." if cmd else
                   "No pull can refill them: the table stays stale until a copy of the raw "
                   "that held them is restored.")
            lines.append(f"REFUSED: stale {t} holds {held} day(s) but raw lacks {len(missing)} "
                         f"of them ({day_ranges(missing)}); rebuilding would lose them, so "
                         f"nothing was dropped. {fix}")
        return "\n".join(lines)


def day_ranges(days: list[str]) -> str:
    """Sorted ISO days as consecutive runs: `2026-01-01..2026-01-03, 2026-01-07`.
    A value that is not an ISO day stands alone."""
    runs: list[list[str]] = []
    for d in days:
        try:
            follows = bool(runs) and (datetime.date.fromisoformat(d)
                                      - datetime.date.fromisoformat(runs[-1][1])).days == 1
        except ValueError:
            follows = False
        if follows:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    return ", ".join(a if a == b else f"{a}..{b}" for a, b in runs)


# -------------------------------------------------------------------- schema --

@dataclass(frozen=True)
class Table:
    """One table. `columns` maps each name to its SQLite type, in order; `pk`
    names the natural key. A cache table may name the `day` column it keeps
    history by (an ISO day, or a week's first day). A human table may `refuse`
    UPDATE and/or DELETE, or allow UPDATE of `changes_only` (its decision
    columns) and freeze the rest."""
    columns: dict[str, str]
    pk: tuple[str, ...]
    human: bool = False
    day: str | None = None
    refuse: tuple[str, ...] = ()
    changes_only: tuple[str, ...] = ()

    def problems(self, name: str) -> list[str]:
        out = [f"{name}: {what} is not a plain name" for what in (name, *self.columns)
               if not NAME.match(what)]
        out += [f"{name}.{c}: type {t!r} is not a plain SQLite type"
                for c, t in self.columns.items() if not SQL_TYPE.match(t)]
        if not self.pk or not set(self.pk) <= set(self.columns):
            out.append(f"{name}: pk {self.pk} must name its columns")
        if self.day is not None and (self.human or self.day not in self.columns):
            out.append(f"{name}: day must be a column of a cache table")
        if (self.refuse or self.changes_only) and not self.human:
            out.append(f"{name}: refuse and changes_only are for human tables")
        if not set(self.refuse) <= set(OPS):
            out.append(f"{name}: refuse takes only {OPS}")
        if self.changes_only and ("UPDATE" in self.refuse
                                  or not set(self.changes_only) < set(self.columns)):
            out.append(f"{name}: changes_only names some columns, and UPDATE is not refused")
        return out


@dataclass(frozen=True)
class Schema:
    """Every table the harness keeps, and the version stamped into the file.
    Bump `version` with every change to a table."""
    version: int
    tables: dict[str, Table]

    def __post_init__(self):
        bad = [p for n, t in self.tables.items() for p in t.problems(n)]
        if not isinstance(self.version, int) or self.version < 1:
            bad.append("version must be an int >= 1")
        if bad:
            raise ValueError("; ".join(bad))

    @property
    def human(self) -> tuple[str, ...]:
        return tuple(n for n, t in self.tables.items() if t.human)


def fingerprint(schema: Schema) -> str:
    """A short hash of every table's definition. Pin it in a test beside
    `Schema.version`; a table change without a version bump then fails CI,
    so the stamp in a file always tells which shape wrote it."""
    text = json.dumps({n: [list(t.columns.items()), list(t.pk), t.human, t.day, list(t.refuse),
                           list(t.changes_only)] for n, t in sorted(schema.tables.items())})
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def triggers(schema: Schema) -> dict[str, str]:
    """name -> CREATE TRIGGER, generated from each human table's `refuse` and
    `changes_only`, plus `<table>_no_replace` for any table with either. They
    live in the file, so they hold for any connection."""
    out = {}
    for name, t in schema.tables.items():
        if t.refuse or t.changes_only:
            tn, key = f"{name}_no_replace", ", ".join(t.pk)
            out[tn] = (f"CREATE TRIGGER {tn} BEFORE INSERT ON {name} WHEN EXISTS (SELECT 1 FROM {name} "
                       f"WHERE ({key}) = ({', '.join('NEW.' + c for c in t.pk)})) BEGIN SELECT "
                       f"RAISE(ABORT, 'refused: INSERT over an existing {name} row; human rows change "
                       f"only by UPDATE'); END")
        for op in t.refuse:
            tn = f"{name}_no_{op.lower()}"
            out[tn] = (f"CREATE TRIGGER {tn} BEFORE {op} ON {name} BEGIN SELECT RAISE(ABORT, "
                       f"'refused: {op} on {name}: human data is append-only'); END")
        if t.changes_only:
            frozen = [c for c in t.columns if c not in t.changes_only]
            tn = f"{name}_frozen"
            out[tn] = (f"CREATE TRIGGER {tn} BEFORE UPDATE OF {', '.join(frozen)} ON {name} "
                       f"BEGIN SELECT RAISE(ABORT, 'refused: a {name} row is frozen; only "
                       f"{', '.join(t.changes_only)} may change'); END")
    return out


def _create_sql(name: str, t: Table) -> str:
    # A single INTEGER key is SQLite's rowid: it numbers itself, so no NOT NULL.
    rowid = len(t.pk) == 1 and t.columns[t.pk[0]].upper() == "INTEGER"
    cols = [f"{c} {typ}".rstrip() + (" NOT NULL" if c in t.pk and not rowid else "")
            for c, typ in t.columns.items()]
    return f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(cols)}, PRIMARY KEY ({', '.join(t.pk)}))"


# ---------------------------------------------------------------- inspection --

def _ro_uri(path: str) -> str:
    return "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"


def _connect_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(_ro_uri(path), uri=True, isolation_level=None)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}


def _shape(conn: sqlite3.Connection, name: str) -> tuple[list[str], list[str]] | None:
    """(columns in order, pk in order) of `name` on disk; None when absent."""
    rows = conn.execute(f"PRAGMA main.table_info({name})").fetchall()
    if not rows:
        return None
    return [r[1] for r in rows], [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]


def _drifted(conn: sqlite3.Connection, name: str, t: Table) -> bool:
    shape = _shape(conn, name)
    return shape is not None and shape != (list(t.columns), list(t.pk))


def _only_adds(conn: sqlite3.Connection, name: str, t: Table) -> bool:
    """The new shape keeps the key and every column on disk, adding some: a
    rebuild then carries every row over instead of refilling from raw alone."""
    cols, pk = _shape(conn, name)
    return pk == list(t.pk) and set(cols) <= set(t.columns)


def stale_tables(conn: sqlite3.Connection, schema: Schema) -> list[str]:
    """Cache tables whose columns or key on disk differ from the schema. Do not
    trust their rows until the ingest that fills them has rebuilt them."""
    return [n for n, t in schema.tables.items() if not t.human and _drifted(conn, n, t)]


def unknown_tables(conn: sqlite3.Connection, schema: Schema) -> list[str]:
    """Tables the schema does not know: left by older code and never refreshed,
    or a sign the path points at some other database."""
    return sorted(n for n in _tables(conn) if n not in schema.tables and not n.startswith("sqlite_"))


def human_counts(conn: sqlite3.Connection, schema: Schema) -> dict[str, int]:
    """Rows per human table; a table not created yet counts 0."""
    have = _tables(conn)
    return {n: conn.execute(f"SELECT count(*) FROM main.{n}").fetchone()[0] if n in have else 0
            for n in schema.human}


def _refuse_newer(path: str, schema: Schema) -> None:
    if not os.path.exists(path):
        return
    conn = _connect_ro(path)
    try:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if found > schema.version:
        raise SchemaTooNew(f"{path} was written by schema v{found}; this code knows "
                           f"v{schema.version}. Update the code; nothing was read or changed.",
                           path=path, found=found, known=schema.version)


def _check_human(conn: sqlite3.Connection, schema: Schema) -> None:
    for n in schema.human:
        if _drifted(conn, n, schema.tables[n]):
            raise HumanDrift(f"human table {n} on disk has columns {_shape(conn, n)[0]}, the schema "
                             f"{list(schema.tables[n].columns)}. Human data is never dropped by code: "
                             f"back it up and migrate it by hand.", table=n)


def _lossy(conn: sqlite3.Connection, schema: Schema, stale: set[str],
           raw_days: dict[str, set[str]]) -> list[tuple[str, int, list[str]]]:
    """Stale history tables holding any day raw cannot refill. A table the
    caller gave no days for refills none: it fails closed."""
    out = []
    for n in sorted(stale):
        t = schema.tables[n]
        if t.day is None or t.day not in _shape(conn, n)[0] or _only_adds(conn, n, t):
            continue
        held = [str(d) for (d,) in conn.execute(
            f"SELECT DISTINCT {t.day} FROM main.{n} WHERE {t.day} IS NOT NULL ORDER BY 1")]
        can = {str(d) for d in raw_days.get(n, ())}
        if missing := [d for d in held if d not in can]:
            out.append((n, len(held), missing))
    return out


# --------------------------------------------------------------------- opens --

def open_read(path: str, schema: Schema) -> sqlite3.Connection:
    """A read-only connection. Creates nothing: a missing file is `NoDatabase`.
    A newer stamp is `SchemaTooNew`; a changed human table `HumanDrift`. A
    human table not created yet is fine (read it as empty)."""
    if not os.path.exists(path):
        raise NoDatabase(f"no database at {path}: a read creates nothing. Check the data root, "
                         f"or run the verb that writes it first.", path=path)
    _refuse_newer(path, schema)
    conn = _connect_ro(path)
    try:
        _check_human(conn, schema)
    except BaseException:
        conn.close()
        raise
    return conn


def check_rebuild(path: str, schema: Schema, rebuild: tuple[str, ...],
                  raw_days: dict[str, set[str]]) -> None:
    """The dry run of `open_write(rebuild=, raw_days=)`: raise the
    `LossyRebuild` it would raise, touching nothing."""
    if not os.path.exists(path):
        return
    _refuse_newer(path, schema)
    conn = _connect_ro(path)
    try:
        if lossy := _lossy(conn, schema, set(rebuild) & set(stale_tables(conn, schema)), raw_days):
            raise LossyRebuild(lossy)
    finally:
        conn.close()


def open_write(path: str, schema: Schema, *, rebuild: tuple[str, ...] = (),
               raw_days: dict[str, set[str]] | None = None) -> sqlite3.Connection:
    """Open for writing, creating the file and any missing table.

    `rebuild` names the cache tables the caller is about to refill (an
    ingest); `raw_days` maps each to the days its raw holds. Before anything
    else a rebuild snapshots the human tables. A named table that is stale is
    then dropped and recreated, unless it keeps history and holds a day raw
    lacks (`LossyRebuild`, nothing dropped); one whose new shape only adds
    columns keeps its rows. Stale tables not named are left as they are.
    Triggers are (re)installed and the version stamped, all in one
    `keep_human_rows` transaction."""
    if bad := [n for n in rebuild if n not in schema.tables or schema.tables[n].human]:
        raise ValueError(f"rebuild names no cache table: {bad}")
    _refuse_newer(path, schema)
    created = not os.path.exists(path)
    if rebuild and not created:
        snapshot_human(path, schema)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        _check_human(conn, schema)
        stale = set(rebuild) & set(stale_tables(conn, schema))
        if lossy := _lossy(conn, schema, stale, raw_days or {}):
            raise LossyRebuild(lossy)
        with keep_human_rows(conn, schema):
            for n in sorted(stale):
                _rebuild(conn, n, schema.tables[n])
            for n, t in schema.tables.items():
                conn.execute(_create_sql(n, t))
            _install_triggers(conn, schema)
            if conn.execute("PRAGMA user_version").fetchone()[0] != schema.version:
                conn.execute(f"PRAGMA user_version = {schema.version}")
    except BaseException:
        conn.close()
        raise
    if created:
        print(f"[store] created a NEW empty database at {path}; if you meant an existing one, "
              f"check the path", file=sys.stderr)
    return conn


def _rebuild(conn: sqlite3.Connection, n: str, t: Table) -> None:
    carry = _only_adds(conn, n, t)
    cols = ", ".join(_shape(conn, n)[0])
    if carry:
        conn.execute(f"CREATE TEMP TABLE _carry_{n} AS SELECT * FROM main.{n}")
    conn.execute(f"DROP TABLE main.{n}")
    conn.execute(_create_sql(n, t))
    if carry:
        kept = conn.execute(f"INSERT INTO main.{n} ({cols}) SELECT {cols} FROM temp._carry_{n}").rowcount
        conn.execute(f"DROP TABLE temp._carry_{n}")
        print(f"[rebuild] {n}: new columns added, {kept} row(s) kept (new columns NULL until "
              f"raw refills them)", file=sys.stderr)
    else:
        print(f"[rebuild] {n}: old shape dropped and recreated; this ingest refills it from raw",
              file=sys.stderr)


def _install_triggers(conn: sqlite3.Connection, schema: Schema) -> None:
    have = dict(conn.execute("SELECT name, sql FROM main.sqlite_master WHERE type='trigger'"))
    for name, sql in triggers(schema).items():
        if have.get(name) != sql:
            conn.execute(f"DROP TRIGGER IF EXISTS main.{name}")
            conn.execute(sql)


# ----------------------------------------------------------------- the guard --

@contextmanager
def keep_human_rows(conn: sqlite3.Connection, schema: Schema) -> Iterator[sqlite3.Connection]:
    """One transaction (a savepoint, so it nests) that rolls back and raises
    `HumanRowsLost` if it leaves any human table with fewer rows than it
    started with. No sanctioned verb deletes a human row, so a shrink is always
    a bug, whatever caused it. The triggers are the first line; this is the
    second, for what a trigger cannot see (a DROP, a file written before the
    triggers existed)."""
    conn.execute("SAVEPOINT keep_human_rows")
    try:
        before = human_counts(conn, schema)
        yield conn
        after = human_counts(conn, schema)
        if lost := {n: [before[n], after[n]] for n in before if after[n] < before[n]}:
            raise HumanRowsLost("refused: this write would remove human rows ("
                                + ", ".join(f"{n}: {a} -> {b}" for n, (a, b) in lost.items())
                                + "); rolled back, nothing changed", lost=lost)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK TO keep_human_rows")
            conn.execute("RELEASE keep_human_rows")
        raise
    conn.execute("RELEASE keep_human_rows")


# ----------------------------------------------------------------- snapshots --

def _backups(path: str) -> tuple[Path, str]:
    return Path(path).parent / "backups", f"{Path(path).stem}-human-"


def snapshot_human(path: str, schema: Schema, keep: int = KEEP) -> str | None:
    """Copy every human table the file holds into
    `<db dir>/backups/<stem>-human-<UTC>.db` and keep the newest `keep`. The
    name reads `dates.now()`; under a pinned clock the next snapshot takes the
    next free microsecond, so none replaces another.
    The live file is attached read-only; the copy is written to `.tmp` and
    then renamed, so a snapshot on disk is always whole. Returns its path, or
    None (nothing written) when the file or its human rows do not exist yet."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not os.path.exists(path):
        return None
    src = _connect_ro(path)
    try:
        ddl = dict(src.execute("SELECT name, sql FROM main.sqlite_master WHERE type='table' AND name IN "
                               f"({','.join('?' * len(schema.human))})", schema.human))
        version = src.execute("PRAGMA user_version").fetchone()[0]
        if not any(src.execute(f"SELECT 1 FROM main.{n} LIMIT 1").fetchone() for n in ddl):
            return None
    finally:
        src.close()
    out, prefix = _backups(path)
    out.mkdir(exist_ok=True)
    at = dates.now()
    dst = out / f"{prefix}{at:%Y%m%dT%H%M%S%fZ}.db"
    while dst.exists():                     # a pinned clock: the next free microsecond, still in order
        at += datetime.timedelta(microseconds=1)
        dst = out / f"{prefix}{at:%Y%m%dT%H%M%S%fZ}.db"
    tmp = Path(f"{dst}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        snap = sqlite3.connect(str(tmp), uri=True, isolation_level=None)
        try:
            snap.execute("ATTACH ? AS live", (_ro_uri(path),))
            snap.execute("BEGIN")
            for n, sql in ddl.items():
                snap.execute(sql)
                snap.execute(f"INSERT INTO main.{n} SELECT * FROM live.{n}")
            snap.execute(f"PRAGMA user_version = {version}")
            snap.execute("COMMIT")
        finally:
            snap.close()
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for old in sorted(out.glob(f"{prefix}*.db"))[:-keep]:
        old.unlink()
    return str(dst)


def newest_snapshot(path: str) -> str | None:
    out, prefix = _backups(path)
    found = sorted(out.glob(f"{prefix}*.db"))
    return str(found[-1]) if found else None


def shrunk_since_snapshot(path: str, schema: Schema) -> dict[str, list[int]]:
    """{table: [rows in the newest snapshot, rows now]} for each human table
    that has fewer rows now: the file lost human data since (a missing file
    counts as empty). Human rows only grow, so any entry is a loss. Read-only."""
    snap = newest_snapshot(path)
    if snap is None:
        return {}
    conn = _connect_ro(snap)
    try:
        then = human_counts(conn, schema)
    finally:
        conn.close()
    now = dict.fromkeys(then, 0)
    if os.path.exists(path):
        conn = _connect_ro(path)
        try:
            now = human_counts(conn, schema)
        finally:
            conn.close()
    return {n: [then[n], now[n]] for n in then if now[n] < then[n]}
