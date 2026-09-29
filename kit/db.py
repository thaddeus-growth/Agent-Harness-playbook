"""The SQLite layer (stdlib sqlite3): one file per client holding the
harness's cache tables (rebuildable from raw) beside the kit's human
tables (kit.schema_base: never rebuildable), and the guards that keep the
human data alive.

What it guards:

  * a file stamped by a newer harness (user_version > spec.version) is
    refused before anything is read or changed, read-only opens and the
    dry-run check included (SchemaTooNew, code schema_too_new);
  * a read-only open never creates anything: a missing file is no_db,
    and neither the file nor its directory appear;
  * human tables are never dropped and never migrated: their drift (other
    columns, other column order, other primary key) always raises
    (HumanTableDrift) before anything is changed, read-only opens too;
  * triggers in the file itself, generated from spec.append_only and
    spec.frozen_columns, refuse what they forbid whatever code path
    writes (a raw sqlite3 connection included); a write-mode open
    re-installs a missing or changed one. A third one per such table,
    <t>_no_replace, refuses an INSERT over an existing key: REPLACE
    deletes the old row without firing a DELETE trigger and is no UPDATE,
    so without it a history row is rewritten and the row count stays.
    A human row changes only by UPDATE (so upsert() on a human table
    refuses an existing key too);
  * keep_human_rows(): one transaction that rolls back any write leaving
    a human table with fewer rows than it had (human_rows_would_shrink),
    the second line, for what a trigger cannot see;
  * a cache table in an older shape is tolerated (stale_tables() names
    it) and rebuilt only when the caller names it in `rebuild=`. Before
    that, the human tables are backed up (newest spec.backups_keep kept),
    and a table that accumulates history by its period column
    (spec.period_columns, default "date") is refused (LossyRebuild)
    unless raw can refill every period it holds: the set, not the count,
    and a table the caller gave no periods for can refill none. A new
    shape that only adds nullable columns carries every row over;
  * a write-mode open changes nothing when nothing differs (no DDL, no
    user_version stamp), and says on stderr when it created the file;
  * shrunk_since_backup() is the check for "this database held human data
    and lost it": {table: [rows in the newest backup, rows now]} for each
    human table with fewer rows now (a missing file counts as empty).

Connections are in autocommit mode (isolation_level=None): every
statement commits itself. Group writes with `transaction(con)`, or with
`keep_human_rows(spec, con)` for anything that touches a human table.
Rows come back as tuples (set con.row_factory for more).

Deviations (SPEC §db), each additive:
  * SchemaSpec also takes human_added_later (reference parity: a
    read-only open of a file older than such a human table proceeds; the
    next write-mode open creates it). A table dict may carry "unique"
    and "defaults" besides columns/pk/not_null/doc/grain; column types
    are "TEXT", "INTEGER", "REAL", "BLOB" (or str, int, float, bytes).
  * with_human() also takes views, period_columns, append_only,
    frozen_columns (for extra human tables only; they default to no
    DELETE), human_added_later and backups_keep.
  * connect() and check_rebuild() take backfill(table, missing) -> a
    command or None, which becomes LossyRebuild's `next`.
  * Extra helpers: transaction(), check_rebuild() (the --dry-run view of
    a rebuild, reference parity), table_sql(), triggers(),
    HumanTableDrift. SchemaTooNew / LossyRebuild are HarnessErrors (the
    reference used SystemExit / RuntimeError), so fail() codes them.
  * Human-table drift is checked before a write-mode open changes
    anything (the reference checked after its bootstrap), and an added
    NOT NULL column without a default is not "only adds" (its rows could
    not be carried over).
  * A single INTEGER primary key is `INTEGER PRIMARY KEY AUTOINCREMENT`:
    ids are never reused.

Test: kit/tests/test_db.py.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import shlex
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping

from kit import dates, paths, schema_base
from kit.config import config
from kit.contract import HarnessError
from kit.messages import joined, msg

TYPES = {"TEXT": "TEXT", "INTEGER": "INTEGER", "REAL": "REAL",
         "BLOB": "BLOB", str: "TEXT", int: "INTEGER", float: "REAL",
         bytes: "BLOB"}
OPS = ("UPDATE", "DELETE")
BACKUPS_DIR = "backups"
TABLE_KEYS = {"columns", "pk", "not_null", "unique", "defaults", "doc",
              "grain"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Backfill = Callable[[str, list[str]], "str | None"]


# ---- the schema as data ---------------------------------------------------

def _names(v, where: str) -> tuple[str, ...]:
    return (v,) if isinstance(v, str) else tuple(v or ())


def _ident(name: object, where: str) -> str:
    if not (isinstance(name, str) and _IDENT.match(name)
            and not name.lower().startswith("sqlite_")):
        raise ValueError(f"{where}: {name!r} is not a plain SQL name")
    return name


def _subset(names: Iterable[str], of: Iterable[str], where: str) -> None:
    extra = [n for n in names if n not in set(of)]
    if extra:
        raise ValueError(f"{where}: {extra} not among {list(of)}")


def _norm_table(name: str, t: Mapping) -> dict:
    where = f"table {name!r}"
    _ident(name, "table name")
    if not isinstance(t, Mapping) or not t.get("columns"):
        raise ValueError(f"{where}: needs a non-empty 'columns' mapping")
    _subset(t, TABLE_KEYS, f"{where}: keys")
    cols = {}
    for c, ty in t["columns"].items():
        _ident(c, where)
        key = ty.upper() if isinstance(ty, str) else ty
        if key not in TYPES:
            raise ValueError(f"{where}: column {c!r} has type {ty!r}, not "
                             f"one of TEXT, INTEGER, REAL, BLOB")
        cols[c] = TYPES[key]
    pk = _names(t.get("pk"), where)
    if not pk:
        raise ValueError(f"{where}: needs a primary key ('pk')")
    not_null = _names(t.get("not_null"), where)
    unique = tuple(_names(u, where) for u in t.get("unique", ()))
    defaults = dict(t.get("defaults") or {})
    for group in (pk, not_null, *unique, defaults):
        _subset(group, cols, where)
    for c, v in defaults.items():
        if not (v is None or isinstance(v, (str, int))
                or isinstance(v, float) and math.isfinite(v)):
            raise ValueError(f"{where}: default of {c!r} must be a string, "
                             f"a number or None, got {v!r}")
    return {"columns": cols, "pk": pk, "not_null": not_null,
            "unique": unique, "defaults": defaults,
            "doc": str(t.get("doc", "")), "grain": str(t.get("grain", ""))}


@dataclass(frozen=True)
class SchemaSpec:
    """Every table of the file, which of them hold human data, and the
    version stamped into the file. Normalized and checked at construction
    (ValueError), so a malformed spec fails where it is written."""

    tables: dict[str, dict]
    human_tables: tuple[str, ...]
    version: int
    append_only: dict[str, tuple[str, ...]] = field(default_factory=dict)
    frozen_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    period_columns: dict[str, str] = field(default_factory=dict)
    views: dict[str, str] = field(default_factory=dict)
    backups_keep: int = 30
    human_added_later: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        put = object.__setattr__
        put(self, "tables", {n: _norm_table(n, t)
                             for n, t in dict(self.tables).items()})
        put(self, "human_tables", _names(self.human_tables, "human_tables"))
        put(self, "human_added_later",
            _names(self.human_added_later, "human_added_later"))
        put(self, "append_only", {t: tuple(o.upper() for o in _names(ops, t))
                                  for t, ops in self.append_only.items()})
        put(self, "frozen_columns", {t: _names(c, t) for t, c
                                     in self.frozen_columns.items()})
        put(self, "period_columns", dict(self.period_columns))
        put(self, "views", {v: s.strip() if isinstance(s, str) else s
                            for v, s in self.views.items()})
        if not (isinstance(self.version, int) and not isinstance(
                self.version, bool) and self.version >= 1):
            raise ValueError(f"version must be an int >= 1, got "
                             f"{self.version!r}")
        if not (isinstance(self.backups_keep, int)
                and self.backups_keep >= 1):
            raise ValueError(f"backups_keep must be an int >= 1, got "
                             f"{self.backups_keep!r}")
        if len(set(self.human_tables)) != len(self.human_tables):
            raise ValueError(f"human_tables lists a table twice: "
                             f"{self.human_tables}")
        _subset(self.human_tables, self.tables, "human_tables")
        _subset(self.human_added_later, self.human_tables,
                "human_added_later")
        _subset(self.append_only, self.human_tables, "append_only")
        for t, ops in self.append_only.items():
            _subset(ops, OPS, f"append_only[{t!r}]")
        _subset(self.frozen_columns, self.human_tables, "frozen_columns")
        for t, cols in self.frozen_columns.items():
            _subset(cols, self.columns(t), f"frozen_columns[{t!r}]")
        _subset(self.period_columns, self.cache_tables(), "period_columns")
        for t, col in self.period_columns.items():
            _subset([col], self.columns(t), f"period_columns[{t!r}]")
        for v, sql in self.views.items():
            _ident(v, "view name")
            if v in self.tables:
                raise ValueError(f"view {v!r} has a table's name")
            if not (isinstance(sql, str) and sql):
                raise ValueError(f"view {v!r} needs its SELECT as a string")

    def __hash__(self) -> int:                 # the dict fields are not
        return hash((self.version, tuple(self.tables), self.human_tables))

    def columns(self, table: str) -> tuple[str, ...]:
        return tuple(self.tables[table]["columns"])

    def pk(self, table: str) -> tuple[str, ...]:
        return self.tables[table]["pk"]

    def cache_tables(self) -> tuple[str, ...]:
        return tuple(t for t in self.tables if t not in self.human_tables)

    def period_column(self, table: str) -> str:
        return self.period_columns.get(table, "date")


def with_human(cache: Mapping[str, dict], *, version: int,
               extra_human: Mapping[str, dict] | None = None,
               views: Mapping[str, str] | None = None,
               period_columns: Mapping[str, str] | None = None,
               append_only: Mapping[str, Iterable[str]] | None = None,
               frozen_columns: Mapping[str, Iterable[str]] | None = None,
               human_added_later: Iterable[str] = (),
               backups_keep: int = 30) -> SchemaSpec:
    """The harness's cache tables + the kit's human tables (schema_base)
    + the harness's own extra human tables. The kit's human tables keep
    the kit's triggers; `append_only` / `frozen_columns` name extra human
    tables only, which default to refusing DELETE."""
    extra = dict(extra_human or {})
    base = schema_base.HUMAN
    clash = sorted((set(base) & set(extra)) | (set(base) | set(extra))
                   & set(cache))
    if clash:
        raise ValueError(f"table name(s) used twice: {clash}")
    append_only, frozen = dict(append_only or {}), dict(frozen_columns or {})
    ours = sorted((set(append_only) | set(frozen)) & set(base))
    if ours:
        raise ValueError(f"the kit's human tables keep the kit's triggers: "
                         f"{ours}")
    return SchemaSpec(
        tables={**base, **extra, **cache},
        human_tables=(*base, *extra), version=version,
        append_only={**schema_base.APPEND_ONLY,
                     **{t: ("DELETE",) for t in extra}, **append_only},
        frozen_columns={**schema_base.FROZEN_COLUMNS, **frozen},
        period_columns=dict(period_columns or {}), views=dict(views or {}),
        backups_keep=backups_keep, human_added_later=tuple(human_added_later))


def schema_hash(spec: SchemaSpec) -> str:
    """16 hex chars over the file's shape: every table's columns (in
    order) and types, pk, not_null, unique, defaults, which tables are
    human, the triggers and the views. Not docs, grains, period columns,
    backups_keep nor the version: a harness pins (version, hash) in a
    test, so changing the shape without bumping the version fails."""
    shape = {
        "tables": {n: {"columns": [[c, ty] for c, ty
                                   in t["columns"].items()],
                       "pk": list(t["pk"]), "not_null": sorted(t["not_null"]),
                       "unique": sorted(list(u) for u in t["unique"]),
                       "defaults": t["defaults"]}
                   for n, t in spec.tables.items()},
        "human_tables": sorted(spec.human_tables),
        "triggers": triggers(spec),
        "views": spec.views,
    }
    text = json.dumps(shape, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---- SQL built from the spec ----------------------------------------------

def _q(name: str) -> str:
    return f'"{name}"'


def _literal(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(int(v) if isinstance(v, bool) else v)
    return "'" + str(v).replace("'", "''") + "'"


def table_sql(spec: SchemaSpec, table: str) -> str:
    """The CREATE TABLE statement of `table` (pk and not_null columns NOT
    NULL; a single INTEGER pk is the AUTOINCREMENT rowid)."""
    t = spec.tables[table]
    pk = t["pk"]
    rowid = len(pk) == 1 and t["columns"][pk[0]] == "INTEGER"
    parts = []
    for c, ty in t["columns"].items():
        s = f"{_q(c)} {ty}"
        if rowid and c == pk[0]:
            s += " PRIMARY KEY AUTOINCREMENT"
        if c in pk or c in t["not_null"]:
            s += " NOT NULL"
        if c in t["defaults"]:
            s += f" DEFAULT {_literal(t['defaults'][c])}"
        parts.append(s)
    if not rowid:
        parts.append(f"PRIMARY KEY ({', '.join(map(_q, pk))})")
    parts += [f"UNIQUE ({', '.join(map(_q, u))})" for u in t["unique"]]
    return f"CREATE TABLE {_q(table)} ({', '.join(parts)})"


def triggers(spec: SchemaSpec) -> dict[str, str]:
    """{trigger name: CREATE TRIGGER} from append_only (<t>_no_update,
    <t>_no_delete) and frozen_columns (<t>_frozen: BEFORE UPDATE OF every
    column the table does not list as mutable), plus <t>_no_replace for
    each table with either: BEFORE INSERT, aborting when the key exists
    (INSERT OR REPLACE deletes the old row without firing DELETE triggers
    and is no UPDATE)."""
    out = {}
    for table in dict.fromkeys((*spec.append_only, *spec.frozen_columns)):
        name, pk = f"{table}_no_replace", spec.pk(table)
        out[name] = (f"CREATE TRIGGER {_q(name)} BEFORE INSERT ON "
                     f"{_q(table)} WHEN EXISTS (SELECT 1 FROM {_q(table)} "
                     f"WHERE ({', '.join(map(_q, pk))}) = "
                     f"({', '.join('NEW.' + _q(c) for c in pk)})) BEGIN "
                     f"SELECT RAISE(ABORT, 'refused: INSERT over an "
                     f"existing {table} row: human rows change only by "
                     f"UPDATE'); END")
    for table, ops in spec.append_only.items():
        for op in ops:
            name = f"{table}_no_{op.lower()}"
            out[name] = (f"CREATE TRIGGER {_q(name)} BEFORE {op} ON "
                         f"{_q(table)} BEGIN SELECT RAISE(ABORT, 'refused: "
                         f"{op} on {table}: human data is append-only'); END")
    for table, mutable in spec.frozen_columns.items():
        frozen = [c for c in spec.columns(table) if c not in mutable]
        if frozen:
            name = f"{table}_frozen"
            out[name] = (f"CREATE TRIGGER {_q(name)} BEFORE UPDATE OF "
                         f"{', '.join(map(_q, frozen))} ON {_q(table)} BEGIN "
                         f"SELECT RAISE(ABORT, 'refused: a {table} row is "
                         f"frozen; an UPDATE may change only "
                         f"{', '.join(mutable)}'); END")
    return out


def _view_sql(name: str, sql: str) -> str:
    return f"CREATE VIEW {_q(name)} AS {sql}"


# ---- what the file holds --------------------------------------------------

def _objects(con: sqlite3.Connection, kind: str) -> dict[str, str | None]:
    return dict(con.execute("SELECT name, sql FROM main.sqlite_master "
                            "WHERE type = ?", (kind,)).fetchall())


def _shape(con: sqlite3.Connection, table: str
           ) -> tuple[list[str], list[str]]:
    """(column names in order, pk columns in pk order) on disk."""
    info = con.execute(f"PRAGMA main.table_info({_q(table)})").fetchall()
    pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    return [r[1] for r in info], pk


def _drifted(spec: SchemaSpec, con: sqlite3.Connection, table: str) -> bool:
    cols, pk = _shape(con, table)
    return cols != list(spec.columns(table)) or pk != list(spec.pk(table))


def stale_tables(spec: SchemaSpec, con: sqlite3.Connection) -> list[str]:
    """Cache tables on disk whose columns (or their order) or primary key
    differ from the spec: written by an older version, so their rows may
    follow old semantics; readers must not trust them until the ingest
    that fills them rebuilds them."""
    have = _objects(con, "table")
    return [t for t in spec.cache_tables()
            if t in have and _drifted(spec, con, t)]


def unknown_tables(spec: SchemaSpec, con: sqlite3.Connection) -> list[str]:
    """Tables the spec does not know (left by an older harness and never
    refreshed); SQLite's own sqlite_* tables are not reported."""
    return sorted(t for t in _objects(con, "table")
                  if t not in spec.tables and not t.startswith("sqlite_"))


def human_row_counts(spec: SchemaSpec, con: sqlite3.Connection
                     ) -> dict[str, int]:
    """Rows per human table; a table not created yet counts 0."""
    have = _objects(con, "table")
    return {t: (con.execute(f"SELECT count(*) FROM main.{_q(t)}")
                .fetchone()[0] if t in have else 0)
            for t in spec.human_tables}


# ---- refusals -------------------------------------------------------------

def _ranges(values: list[str]) -> str:
    """ISO days as consecutive runs (a..b, c); other periods listed."""
    try:
        return dates.ranges(values)
    except (TypeError, ValueError):
        return ", ".join(values)


def _desc(cols: Iterable[str], pk: Iterable[str]) -> str:
    return f"{', '.join(cols)} (primary key {', '.join(pk) or 'none'})"


class SchemaTooNew(HarnessError):
    """The file was stamped by a newer harness: refused before anything is
    read or changed."""

    def __init__(self, path: Path | str, found: int, knows: int):
        self.path, self.found, self.knows = str(path), found, knows
        super().__init__(
            msg("schema_too_new",
                f"{path} was written by schema v{found}; this harness knows "
                f"v{knows} — update the harness checkout. Nothing was read "
                f"or changed", path=str(path), found=found, knows=knows),
            [f"git -C {shlex.quote(str(config().root))} pull"])


class LossyRebuild(HarnessError):
    """A stale-table rebuild would drop periods raw cannot refill. Raised
    before anything is dropped. `losses` = [(table, periods held, the
    held periods raw lacks, sorted)]; `next` = backfill(table, lacking)
    for each table where the hook names a command."""

    def __init__(self, losses: list[tuple[str, int, list[str]]],
                 backfill: Backfill | None = None):
        self.losses = losses
        parts = [msg("lossy_rebuild",
                     f"REFUSED: stale {t} holds {held} period(s) but raw "
                     f"lacks {len(m)} of them ({_ranges(m)}) — rebuilding "
                     f"would lose history, so it stays stale and nothing "
                     f"was dropped; refill raw for those periods first, or "
                     f"restore a copy of the raw that held them",
                     table=t, held=held, lacking=len(m), missing=_ranges(m))
                 for t, held, m in losses]
        nxt = [c for t, _h, m in losses if backfill and (c := backfill(t, m))]
        super().__init__(parts[0] if len(parts) == 1 else joined(parts), nxt)


class HumanTableDrift(HarnessError):
    """A human table on disk differs from the spec: never dropped, never
    migrated automatically."""

    def __init__(self, table: str, expected: str, found: str):
        self.table = table
        super().__init__(
            msg("human_table_drift",
                f"Schema drift on human table {table}: expected {expected}, "
                f"found {found}. This is human data — back it up and "
                f"migrate it deliberately; it is never dropped or migrated "
                f"automatically", table=table, expected=expected,
                found=found),
            [f"{config().cli} doctor"])


def _no_db(path: Path) -> HarnessError:
    """contract.missing_db_error()'s refusal, for any path."""
    cfg = config()
    hook = cfg.raw.get("contract", {}).get("no_db_next")
    steps = (([hook] if isinstance(hook, str) else list(hook)) if hook
             else ["doctor", "facts init"])
    return HarnessError(
        msg("no_db", f"No database at {path}", path=str(path)),
        [s if s.startswith(f"{cfg.cli} ") else f"{cfg.cli} {s}"
         for s in steps])


def _ro_uri(path: Path) -> str:
    return Path(path).absolute().as_uri() + "?mode=ro"


def _refuse_newer(spec: SchemaSpec, path: Path) -> None:
    """SchemaTooNew when the file is stamped above spec.version; a file
    SQLite cannot read is db_unreadable. Reads nothing but the stamp."""
    if not path.exists():
        return
    try:
        con = sqlite3.connect(_ro_uri(path), uri=True)
        try:
            found = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise HarnessError(
            msg("db_unreadable", f"{path} cannot be read as a database: "
                f"{e}. Nothing was changed", path=str(path), detail=str(e)),
            [f"{config().cli} doctor"]) from None
    if found > spec.version:
        raise SchemaTooNew(path, found, spec.version)


def _check_human(spec: SchemaSpec, con: sqlite3.Connection, *,
                 read_only: bool) -> None:
    have = _objects(con, "table")
    for t in spec.human_tables:
        if t not in have:
            if read_only and t not in spec.human_added_later:
                raise HarnessError(
                    msg("db_human_table_missing",
                        f"The database has no human table {t} — open it "
                        f"once with a verb that writes to create it; "
                        f"nothing was changed", table=t),
                    [f"{config().cli} doctor"])
            continue
        if _drifted(spec, con, t):
            raise HumanTableDrift(t, _desc(spec.columns(t), spec.pk(t)),
                                  _desc(*_shape(con, t)))


# ---- transactions and the human-row guard ---------------------------------

@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All or nothing. Outermost: BEGIN IMMEDIATE (the write lock first, so
    what the block reads is what it writes over); nested: a savepoint."""
    nested = con.in_transaction
    con.execute("SAVEPOINT kit_tx" if nested else "BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK TO kit_tx" if nested else "ROLLBACK")
            if nested:
                con.execute("RELEASE kit_tx")
        raise
    con.execute("RELEASE kit_tx" if nested else "COMMIT")


@contextmanager
def keep_human_rows(spec: SchemaSpec, con: sqlite3.Connection
                    ) -> Iterator[sqlite3.Connection]:
    """One transaction that refuses to commit if it leaves any human table
    with fewer rows than it started with: rolled back, HarnessError
    human_rows_would_shrink. No sanctioned write deletes a human row, so
    a shrink is always a bug; every writer of a human table (and the
    bootstrap in connect()) runs inside this."""
    with transaction(con):
        before = human_row_counts(spec, con)
        yield con
        after = human_row_counts(spec, con)
        lost = "; ".join(f"{t} {before[t]} → {after[t]}"
                         for t in spec.human_tables if after[t] < before[t])
        if lost:
            raise HarnessError(msg(
                "human_rows_would_shrink",
                f"Refused: this write would remove human rows ({lost}); "
                f"rolled back, nothing changed. Human tables are never "
                f"emptied and their history is append-only", lost=lost))


# ---- rebuilds -------------------------------------------------------------

def _only_adds(spec: SchemaSpec, con: sqlite3.Connection, table: str
               ) -> bool:
    """The stale table's new shape keeps its pk and every column it has,
    only adding nullable (or defaulted) ones: its rows can be carried
    over, the new columns NULL until raw refills them."""
    cols, pk = _shape(con, table)
    t = spec.tables[table]
    added = [c for c in t["columns"] if c not in cols]
    return (pk == list(t["pk"]) and set(cols) <= set(t["columns"])
            and all(c not in t["not_null"] or c in t["defaults"]
                    for c in added))


def _to_rebuild(spec: SchemaSpec, con: sqlite3.Connection,
                rebuild: Iterable[str]) -> list[str]:
    """The named tables that are stale (human tables never are)."""
    return sorted(set(rebuild) & set(stale_tables(spec, con)))


def _lossy(spec: SchemaSpec, con: sqlite3.Connection, tables: list[str],
           raw_periods: Mapping[str, Iterable]
           ) -> list[tuple[str, int, list[str]]]:
    """Tables with a period column on disk whose rebuild would drop a
    period raw cannot refill (a table carried over loses nothing). NULL
    periods cannot be keyed to raw and are not counted."""
    out = []
    for t in tables:
        col = spec.period_column(t)
        if col not in _shape(con, t)[0] or _only_adds(spec, con, t):
            continue
        held = list(dict.fromkeys(str(v) for (v,) in con.execute(
            f"SELECT DISTINCT {_q(col)} FROM main.{_q(t)} WHERE {_q(col)} "
            f"IS NOT NULL ORDER BY 1")))
        refill = {str(p) for p in raw_periods.get(t, ())}
        missing = [p for p in held if p not in refill]
        if missing:
            out.append((t, len(held), missing))
    return out


def _trigger_plan(spec: SchemaSpec, con: sqlite3.Connection) -> list[str]:
    """Statements that make the file's triggers the spec's: a missing or
    changed one (re)created, one of ours (a human table's <t>_no_update,
    <t>_no_delete, <t>_frozen, <t>_no_replace) the spec no longer has
    dropped."""
    want, have = triggers(spec), _objects(con, "trigger")
    ours = {f"{t}_{s}" for t in spec.human_tables
            for s in ("no_update", "no_delete", "frozen", "no_replace")}
    out = [f"DROP TRIGGER {_q(n)}" for n in sorted(have)
           if n in ours and n not in want]
    for n, sql in want.items():
        if have.get(n) != sql:
            out += [f"DROP TRIGGER {_q(n)}"] * (n in have) + [sql]
    return out


def _view_plan(spec: SchemaSpec, con: sqlite3.Connection) -> list[str]:
    have = _objects(con, "view")
    out = []
    for v, sql in spec.views.items():
        if have.get(v) != _view_sql(v, sql):
            out += [f"DROP VIEW {_q(v)}"] * (v in have) + [_view_sql(v, sql)]
    return out


def _stamp(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def _has_work(spec: SchemaSpec, con: sqlite3.Connection,
              rebuild: Iterable[str]) -> bool:
    have = _objects(con, "table")
    return bool(_to_rebuild(spec, con, rebuild)
                or any(t not in have for t in spec.tables)
                or _trigger_plan(spec, con) or _view_plan(spec, con)
                or _stamp(con) != spec.version)


def _bootstrap(spec: SchemaSpec, con: sqlite3.Connection,
               rebuild: Iterable[str], raw_periods: Mapping[str, Iterable],
               backfill: Backfill | None) -> list[str]:
    """Inside keep_human_rows: rebuild the named stale tables, create the
    missing ones, install triggers and views, stamp the version. Returns
    the [rebuild] notes for stderr."""
    stale = _to_rebuild(spec, con, rebuild)
    if lossy := _lossy(spec, con, stale, raw_periods):
        raise LossyRebuild(lossy, backfill)
    notes, carried = [], {}
    for t in stale:
        if _only_adds(spec, con, t):
            carried[t] = _shape(con, t)[0]
            con.execute(f"CREATE TEMP TABLE {_q('_carry_' + t)} AS "
                        f"SELECT * FROM main.{_q(t)}")
        else:
            notes.append(f"[rebuild] {t}: old shape dropped and recreated "
                         f"(cache: refilled from raw)")
        con.execute(f"DROP TABLE main.{_q(t)}")
    have = _objects(con, "table")
    for t in spec.tables:
        if t not in have:
            con.execute(table_sql(spec, t))
    for t, cols in carried.items():
        names = ", ".join(map(_q, cols))
        n = con.execute(f"INSERT INTO main.{_q(t)} ({names}) SELECT {names} "
                        f"FROM temp.{_q('_carry_' + t)}").rowcount
        con.execute(f"DROP TABLE temp.{_q('_carry_' + t)}")
        notes.append(f"[rebuild] {t}: new columns added, {n} row(s) kept "
                     f"(new columns NULL until raw refills them)")
    for sql in _trigger_plan(spec, con) + _view_plan(spec, con):
        con.execute(sql)
    if _stamp(con) != spec.version:
        con.execute(f"PRAGMA user_version = {int(spec.version)}")
    return notes


# ---- opening the file -----------------------------------------------------

def _open(path: Path, *, read_only: bool) -> sqlite3.Connection:
    """Autocommit, foreign_keys on. Write mode switches to WAL only after
    the human tables passed their check (connect)."""
    con = (sqlite3.connect(_ro_uri(path), uri=True, isolation_level=None)
           if read_only else sqlite3.connect(path, isolation_level=None))
    con.execute("PRAGMA foreign_keys = ON")
    return con


def connect(spec: SchemaSpec, path: Path | str | None = None, *,
            read_only: bool = False, rebuild: Iterable[str] = (),
            raw_periods: Mapping[str, Iterable] | None = None,
            backfill: Backfill | None = None) -> sqlite3.Connection:
    """Open the file (default paths.db_path()) and make it the spec's.

    Always first: SchemaTooNew for a file stamped above spec.version.
    Read-only: `file:…?mode=ro`; a missing file is no_db (nothing is
    created); human-table drift raises; a view the file lacks is a TEMP
    view for this connection; `rebuild` is ignored.
    Write mode: human-table drift raises before anything changes; when
    `rebuild` is given, the human tables are backed up first; the named
    stale cache tables are rebuilt (LossyRebuild unless raw_periods
    {table: periods raw can refill} covers every period each holds;
    carried over when the new shape only adds columns); missing tables,
    triggers and views are created; user_version stamped when it
    differs. All of it in one keep_human_rows transaction, and only when
    something differs. WAL, foreign_keys on, autocommit."""
    path = Path(path) if path is not None else paths.db_path()
    _refuse_newer(spec, path)
    if read_only:
        if not path.exists():
            raise _no_db(path)
        con = _open(path, read_only=True)
        try:
            _check_human(spec, con, read_only=True)
            have = _objects(con, "view")
            for v, sql in spec.views.items():
                if v not in have:
                    con.execute(f"CREATE TEMP VIEW {_q(v)} AS {sql}")
        except BaseException:
            con.close()
            raise
        return con
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    rebuild = tuple(rebuild)
    if rebuild:
        backup_human_tables(spec, path)
    con = _open(path, read_only=False)
    notes: list[str] = []
    try:
        _check_human(spec, con, read_only=False)
        con.execute("PRAGMA journal_mode = WAL")
        if _has_work(spec, con, rebuild):
            with keep_human_rows(spec, con):
                notes = _bootstrap(spec, con, rebuild, raw_periods or {},
                                   backfill)
    except BaseException:
        con.close()
        raise
    for line in notes:
        print(line, file=sys.stderr)
    if created:
        cfg = config()
        print(f"[db] created a NEW empty database at {path} — if you meant "
              f"an existing one, check {cfg.env('DATA_DIR')} / "
              f"{cfg.env('DB')}", file=sys.stderr)
    return con


def check_rebuild(spec: SchemaSpec, path: Path | str | None = None, *,
                  rebuild: Iterable[str],
                  raw_periods: Mapping[str, Iterable] | None = None,
                  backfill: Backfill | None = None) -> None:
    """A --dry-run's view of connect(rebuild=, raw_periods=): raise the
    SchemaTooNew, HumanTableDrift or LossyRebuild that the write open
    would hit, touching nothing. A file that does not exist yet has
    nothing to lose."""
    path = Path(path) if path is not None else paths.db_path()
    if not path.exists():
        return
    _refuse_newer(spec, path)
    con = _open(path, read_only=True)
    try:
        _check_human(spec, con, read_only=False)
        if lossy := _lossy(spec, con, _to_rebuild(spec, con, rebuild),
                           raw_periods or {}):
            raise LossyRebuild(lossy, backfill)
    finally:
        con.close()


# ---- writers --------------------------------------------------------------

def upsert(spec: SchemaSpec, con: sqlite3.Connection, table: str,
           rows: Iterable[Mapping]) -> int:
    """INSERT … ON CONFLICT(pk) DO UPDATE, all rows in one transaction.
    Returns the rows written. Keys that are not columns are dropped; a
    row missing a pk value is skipped (not counted: the caller sees the
    shortfall); on conflict only the columns a row carries are updated,
    so a partial row never blanks the others. Idempotent. For cache
    tables: on a human table an existing key is refused by its
    <t>_no_replace trigger (a human row changes only by UPDATE)."""
    if table not in spec.tables:
        raise ValueError(f"upsert: {table!r} is not a table of the spec")
    cols, pk = spec.columns(table), spec.pk(table)
    keep = [{c: r[c] for c in cols if c in r} for r in rows
            if all(r.get(c) is not None for c in pk)]
    if not keep:
        return 0
    conflict = ", ".join(map(_q, pk))
    with transaction(con):
        for keys, group in itertools.groupby(keep, key=lambda r: tuple(r)):
            sets = [f"{_q(c)} = excluded.{_q(c)}" for c in keys
                    if c not in pk]
            action = f"DO UPDATE SET {', '.join(sets)}" if sets \
                else "DO NOTHING"
            con.executemany(
                f"INSERT INTO main.{_q(table)} ({', '.join(map(_q, keys))}) "
                f"VALUES ({', '.join('?' * len(keys))}) "
                f"ON CONFLICT ({conflict}) {action}",
                [tuple(r[c] for c in keys) for r in group])
    return len(keep)


def backup_human_tables(spec: SchemaSpec, path: Path | str | None = None
                        ) -> Path | None:
    """Snapshot every human table the file holds (their DDL, rows and the
    user_version) into `<db dir>/backups/human-<UTC µs>.db`, written to a
    temp file and renamed, one consistent read of the live file; keep the
    newest spec.backups_keep. None (nothing written) when the file does
    not exist or every human table is empty."""
    path = Path(path) if path is not None else paths.db_path()
    if not path.exists():
        return None
    src = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        ddl = {n: s for n, s in _objects(src, "table").items()
               if n in spec.human_tables}
        if not any(src.execute(f"SELECT 1 FROM main.{_q(t)} LIMIT 1")
                   .fetchone() for t in ddl):
            return None
        version = _stamp(src)
    finally:
        src.close()
    out = path.parent / BACKUPS_DIR
    out.mkdir(exist_ok=True)
    stamp = dates.now()
    while (dst := out / f"human-{stamp:%Y%m%dT%H%M%S%fZ}.db").exists():
        stamp += timedelta(microseconds=1)      # a frozen clock: still unique
    tmp = out / f".{dst.name}.{os.getpid()}.tmp"
    snap = sqlite3.connect(str(tmp), uri=True, isolation_level=None)
    try:
        snap.execute("ATTACH DATABASE ? AS live", (_ro_uri(path),))
        snap.execute("BEGIN")
        for t, sql in ddl.items():
            snap.execute(sql)
            snap.execute(f"INSERT INTO main.{_q(t)} "
                         f"SELECT * FROM live.{_q(t)}")
        snap.execute(f"PRAGMA user_version = {int(version)}")
        snap.execute("COMMIT")
        snap.execute("DETACH DATABASE live")
    except BaseException:
        snap.close()
        tmp.unlink(missing_ok=True)
        raise
    snap.close()
    os.replace(tmp, dst)
    for old in sorted(out.glob("human-*.db"))[:-spec.backups_keep]:
        old.unlink()
    return dst


def newest_backup(path: Path | str | None = None) -> Path | None:
    """The newest `<db dir>/backups/human-*.db`, or None. A half-written
    snapshot is a dot-file `.tmp` (renamed only when whole), never listed."""
    path = Path(path) if path is not None else paths.db_path()
    found = sorted((path.parent / BACKUPS_DIR).glob("human-*.db"))
    return found[-1] if found else None


def shrunk_since_backup(spec: SchemaSpec, path: Path | str | None = None
                        ) -> dict[str, list[int]]:
    """{human table: [rows in the newest backup, rows now]} for each one
    with fewer rows now: the file lost human data since. Human rows only
    grow, so any entry is a loss; "not set up yet" (no backup: {}) and
    "set up, then wiped" look the same in the live tables, the backup tells
    them apart. A missing file counts as empty. Read-only: creates nothing."""
    path = Path(path) if path is not None else paths.db_path()
    snap = newest_backup(path)
    if snap is None:
        return {}
    con = sqlite3.connect(_ro_uri(snap), uri=True)
    try:
        then = human_row_counts(spec, con)
    finally:
        con.close()
    now = dict.fromkeys(then, 0)
    if path.exists():
        con = sqlite3.connect(_ro_uri(path), uri=True)
        try:
            now = human_row_counts(spec, con)
        finally:
            con.close()
    return {t: [then[t], now[t]] for t in then if now[t] < then[t]}
