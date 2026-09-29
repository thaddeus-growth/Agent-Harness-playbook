#!/usr/bin/env python3
"""kit.db + kit.schema_base: human data survives every path into the file.

Ported from the reference harness's test_db_minimal.py and
test_human_tables_survive.py, on a generic cache (orders by day, sales
by week, a stock snapshot):

  [1] connect() bootstraps tables, views, triggers, the version stamp and
      WAL; says on stderr when it created the file; a re-open changes no
      byte and takes no write lock
  [2] triggers refuse UPDATE/DELETE of history, DELETE of a fact, a
      decision, a queued action, any change to an effect, an UPDATE
      of a frozen queue column, and INSERT OR REPLACE over an existing
      row (REPLACE fires no DELETE trigger), on a raw connection; the allowed writes
      pass; a dropped or changed trigger is put back by the next write open
  [3] keep_human_rows refuses and rolls back any shrink (every human
      table), commits a non-shrinking write, rolls back on an exception,
      nests; connect()'s own bootstrap runs inside it
  [4] a stale cache table is tolerated and named; a rebuild that would
      drop periods raw cannot refill is refused (count equal, set
      different included; per period column; dry-run check too) and
      nothing is dropped; a superset rebuilds; a snapshot table without a
      period column rebuilds; a backup is written before a rebuild
  [5] a new shape that only adds nullable/defaulted columns keeps every
      row; an added NOT NULL column without default is not "only adds"
  [6] human-table drift (columns or pk) always raises, before anything
      changes, read-only too; a missing human table is refused on a
      read-only open unless declared human_added_later
  [7] a file stamped by a newer harness is refused on every open and the
      dry run, before any read: bytes unchanged, no backup; read-only
      never stamps, write mode stamps
  [8] a read-only open of a missing file is no_db and creates nothing
      (not even the directory); an unreadable file is db_unreadable
  [9] upsert is idempotent, drops unknown keys, skips rows without a pk,
      never blanks columns a row does not carry, is all-or-nothing
  [10] backup_human_tables keeps the newest N, copies rows verbatim,
      leaves no temp file, is unique under a frozen clock, skips empty
  [11] schema_hash is pinned and stable, and moves only with the shape
  [12] a malformed spec fails at construction
  [13] unknown_tables / human_row_counts
  [14] views: created, replaced when changed, TEMP on a read-only open
  [15] every db code is registered and emitted (closure, strict)
  [16] shrunk_since_backup: human rows lost since the newest backup are
      seen (a wiped or deleted file included); a killed backup is never
      taken for one
"""

import contextlib
import datetime
import io
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import db, messages, schema_base  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

ORDERS = {"columns": {"date": "TEXT", "sku": "TEXT", "units": "INTEGER",
                      "revenue": "REAL"},
          "pk": ("date", "sku"), "doc": "orders per day and sku",
          "grain": "date × sku"}
WEEKLY = {"columns": {"week_start": "TEXT", "sku": "TEXT",
                      "units": "INTEGER"},
          "pk": ("week_start", "sku"), "doc": "sales per week",
          "grain": "week × sku"}
STOCK = {"columns": {"sku": "TEXT", "units": "INTEGER", "pulled_on": "TEXT"},
         "pk": ("sku",), "not_null": ("units",), "doc": "latest stock"}
CACHE = {"orders_daily": ORDERS, "sales_weekly": WEEKLY, "stock": STOCK}
VIEWS = {"v_orders": "SELECT date, sku, units FROM orders_daily"}
DAYS = ("2026-09-01", "2026-09-02")
WEEKS = ("2026-08-24", "2026-08-31")


def spec(version: int = 1, cache: dict | None = None, **kw) -> db.SchemaSpec:
    kw.setdefault("views", VIEWS)
    kw.setdefault("period_columns", {"sales_weekly": "week_start"})
    return db.with_human(CACHE if cache is None else cache, version=version,
                         **kw)


def changed(table: dict, **over) -> dict:
    return {**table, **over}


def quiet(fn):
    """fn() with stderr captured: (result, stderr text)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn()
    return out, err.getvalue()


def refusal(fn, exc=HarnessError):
    """(the exception, its message code) when fn() raises `exc`; anything
    else propagates."""
    e = raises(fn, exc)
    return e, (getattr(e.message, "code", None) if e is not None else None)


def raw(path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def rows(path, table: str) -> list:
    with contextlib.closing(sqlite3.connect(path)) as c:
        return c.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()


def counts(path, s: db.SchemaSpec | None = None) -> dict:
    with contextlib.closing(sqlite3.connect(path)) as c:
        return db.human_row_counts(s or spec(), c)


def objects(path, kind: str) -> set:
    with contextlib.closing(sqlite3.connect(path)) as c:
        return {n for (n,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def stamp(path) -> int:
    with contextlib.closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


SEEDED = {"client_facts": 2, "client_facts_history": 3, "decisions": 1,
          "decisions_history": 2, "action_queue": 1, "action_effects": 2}


def seed(s: db.SchemaSpec, con: sqlite3.Connection) -> None:
    """The rows facts/decisions/queue/execute leave: SEEDED counts."""
    t = "2026-09-22T00:00:00Z"
    with db.keep_human_rows(s, con):
        for key, value in (("market_declared", "US"), ("unit_cost", "4.2")):
            con.execute("INSERT INTO client_facts (market, key, value, "
                        "source, updated_at, changed_by) VALUES "
                        "('US', ?, ?, 'owner', ?, 'owner@tty')",
                        (key, value, t))
        for key, action in (("market_declared", "init"),
                            ("unit_cost", "set"), ("unit_cost", "confirm")):
            con.execute("INSERT INTO client_facts_history (market, key, "
                        "action, new_value, reason, changed_by, at) VALUES "
                        "('US', ?, ?, 'v', 'seed', 'owner@tty', ?)",
                        (key, action, t))
        con.execute("INSERT INTO decisions (market, entity_type, entity_id, "
                    "key, value, source, updated_at, changed_by) VALUES "
                    "('US', 'sku', 'A1', 'tier', 'hero', 'agent', ?, "
                    "'agent@cli')", (t,))
        for action in ("set", "confirm"):
            con.execute("INSERT INTO decisions_history (market, entity_type, "
                        "entity_id, key, action, new_value, reason, "
                        "changed_by, at) VALUES ('US', 'sku', 'A1', 'tier', "
                        "?, 'hero', 'seed', 'owner@tty', ?)", (action, t))
        con.execute("INSERT INTO action_queue (market, action_id, kind, "
                    "target_ref, payload, basis, created_at) VALUES ('US', "
                    "'a1', 'price_set', 'sku:A1', '{\"price\": 9}', 'b1', ?)",
                    (t,))
        for state in ("pending", "sent"):
            con.execute("INSERT INTO action_effects (queue_id, market, "
                        "effect_id, state, at, changed_by) VALUES (1, 'US', "
                        "'a1', ?, ?, 'owner@cli')", (state, t))


def fresh(s: db.SchemaSpec | None = None, *, seeded: bool = True,
          orders: bool = True) -> Path:
    """A new file bootstrapped by `s` (default spec()), with human rows and
    2 days x 2 skus of orders + 2 weeks of sales."""
    s = s or spec()
    path = Path(tmp_dir("db-")) / "shop.db"
    con, _ = quiet(lambda: db.connect(s, path))
    if seeded:
        seed(s, con)
    if orders:
        db.upsert(s, con, "orders_daily", [
            {"date": d, "sku": k, "units": 1, "revenue": 2.5}
            for d in DAYS for k in ("A1", "B2")])
        db.upsert(s, con, "sales_weekly", [
            {"week_start": w, "sku": "A1", "units": 7} for w in WEEKS])
    con.close()
    return path


def backups(path: Path) -> list[Path]:
    return sorted((path.parent / "backups").glob("human-*.db"))


# ---------------------------------------------------------------------------

def t1_bootstrap() -> None:
    print("[1] connect() bootstraps; a re-open is silent and changes nothing")
    s = spec()
    path = Path(tmp_dir("db-")) / "not-yet" / "shop.db"
    con, err = quiet(lambda: db.connect(s, path))
    check("write open creates the file and its directory", path.is_file())
    check("… and says so in one stderr line naming the path",
          err.count("\n") == 1
          and f"created a NEW empty database at {path}" in err
          and "SHOP_DATA_DIR" in err and "SHOP_DB" in err, err)
    check("every spec table exists (human + cache)",
          set(s.tables) <= objects(path, "table"), objects(path, "table"))
    check("the kit's six human tables are in every spec",
          set(schema_base.HUMAN) <= set(s.human_tables)
          and len(s.human_tables) == 6, s.human_tables)
    check("views created", objects(path, "view") == set(VIEWS))
    check("triggers installed, named per spec",
          objects(path, "trigger") == set(db.triggers(s)),
          objects(path, "trigger"))
    check("user_version stamped", stamp(path) == 1, stamp(path))
    check("WAL + foreign keys",
          con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
          and con.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
    check("autocommit connection, tuples",
          con.isolation_level is None and con.row_factory is None)
    check("column order on disk is the spec's",
          [r[1] for r in con.execute("PRAGMA table_info(client_facts)")]
          == list(s.columns("client_facts")))
    con.close()

    before = path.read_bytes()
    (_, err) = quiet(lambda: db.connect(s, path).close())
    (_, err2) = quiet(lambda: db.connect(s, path, read_only=True).close())
    check("re-opening (write, read-only) is silent", err == err2 == "",
          (err, err2))
    check("… and changes no byte of the file", path.read_bytes() == before)

    holder = raw(path)
    holder.execute("BEGIN IMMEDIATE")
    t0 = time.monotonic()
    try:
        con = db.connect(s, path)
        ok = True
        con.close()
    except sqlite3.OperationalError as e:
        ok = e
    holder.execute("ROLLBACK")
    holder.close()
    check("a write open that changes nothing takes no write lock",
          ok is True and time.monotonic() - t0 < 2, ok)


def t2_triggers() -> None:
    print("\n[2] triggers refuse what bypasses every Python guard")
    s = spec()
    path = fresh(s)
    before = {t: rows(path, t) for t in s.human_tables}
    refused = [
        "UPDATE client_facts_history SET reason = 'rewritten'",
        "DELETE FROM client_facts_history",
        "DELETE FROM client_facts",
        "UPDATE decisions_history SET changed_by = 'someone else'",
        "DELETE FROM decisions_history",
        "DELETE FROM decisions",
        "DELETE FROM action_queue",
        "UPDATE action_queue SET payload = '{\"price\": 1}'",
        "UPDATE action_queue SET target_ref = 'sku:B2', status = 'approved'",
        "UPDATE action_effects SET state = 'confirmed'",
        "DELETE FROM action_effects",
    ]
    c = raw(path)
    for sql in refused:
        try:
            c.execute(sql)
            got = "no error"
        except sqlite3.IntegrityError as e:
            got = str(e)
        check(f"raw `{sql[:44]}…` refused", got.startswith("refused"), got)
    check("… every human row unchanged, who/when included",
          {t: rows(path, t) for t in s.human_tables} == before)
    replaced = [
        "INSERT OR REPLACE INTO client_facts_history (id, market, key, "
        "action, reason, changed_by, at) VALUES (1, 'US', 'x', 'set', "
        "'rewritten', 'someone else', 't')",
        "REPLACE INTO client_facts (market, key, value, is_assumption, "
        "source, updated_at, changed_by) VALUES ('US', 'unit_cost', '99', "
        "0, 'forged', 't', 'someone else')",
        "INSERT OR REPLACE INTO decisions_history (id, market, entity_type, "
        "entity_id, key, action, reason, changed_by, at) VALUES (1, 'US', "
        "'sku', 'A1', 'tier', 'set', 'rewritten', 'someone else', 't')",
        "INSERT OR REPLACE INTO action_effects (id, queue_id, market, "
        "effect_id, state, at, changed_by) VALUES (1, 1, 'US', 'a1', "
        "'confirmed', 't', 'someone else')",
        "INSERT OR REPLACE INTO action_queue (id, market, action_id, kind, "
        "target_ref, payload, basis, status, created_at) VALUES (1, 'US', "
        "'a1', 'price_set', 'sku:A1', '{\"price\": 1}', 'b1', 'approved', "
        "'t')",
        "INSERT INTO client_facts_history (id, market, key, action, reason, "
        "changed_by, at) VALUES (1, 'US', 'x', 'set', 'r', 'u', 't') "
        "ON CONFLICT (id) DO UPDATE SET reason = 'rewritten'"]
    for sql in replaced:
        try:
            c.execute(sql)
            got = "no error"
        except sqlite3.IntegrityError as e:
            got = str(e)
        check(f"raw `{sql[:44]}…` refused (REPLACE fires no DELETE "
              f"trigger)", got.startswith("refused: INSERT over an "
                                          "existing"), got)
    check("… no row rewritten, and the row counts did not move either",
          {t: rows(path, t) for t in s.human_tables} == before
          and counts(path) == SEEDED)
    c.execute("BEGIN")
    n = c.execute("INSERT OR REPLACE INTO client_facts (market, key, value, "
                  "source, updated_at, changed_by) VALUES ('US', 'new_key', "
                  "'1', 'owner', 't', 'u')").rowcount
    c.execute("UPDATE client_facts SET value = '4.5' WHERE key = "
              "'unit_cost'")
    check("a new key still inserts (OR REPLACE included); an existing fact "
          "changes by UPDATE",
          n == 1 and c.execute("SELECT count(*) FROM client_facts"
                               ).fetchone()[0] == SEEDED["client_facts"] + 1
          and c.execute("SELECT value FROM client_facts WHERE key = "
                        "'unit_cost'").fetchone() == ("4.5",))
    c.execute("ROLLBACK")
    c.execute("BEGIN")
    for sql in ("UPDATE client_facts SET value = '4.5', is_assumption = 1",
                "INSERT INTO client_facts_history (market, key, action, "
                "reason, changed_by, at) VALUES ('US', 'x', 'set', 'r', 'u', "
                "'t')",
                "UPDATE decisions SET status = 'confirmed', "
                "confirmed_value = value",
                "UPDATE action_queue SET status = 'approved', decided_by = "
                "'owner@tty', decided_at = 't', reason = 'ok', reason_code "
                "= 'fine'",
                "INSERT INTO action_effects (queue_id, market, effect_id, "
                "state, at, changed_by) VALUES (1, 'US', 'a1', 'confirmed', "
                "'t', 'u')"):
        c.execute(sql)
    c.execute("ROLLBACK")
    check("UPDATE of a fact / decision / queue decision and INSERT of "
          "history still allowed", True)
    c.execute("DROP TRIGGER IF EXISTS client_facts_history_no_delete")
    c.execute("DROP TRIGGER IF EXISTS client_facts_history_no_replace")
    c.close()
    quiet(lambda: db.connect(s, path).close())
    check("a dropped trigger is put back by the next write open",
          {"client_facts_history_no_delete",
           "client_facts_history_no_replace"} <= objects(path, "trigger"))

    bare = fresh(s)
    c = raw(bare)
    c.execute("DROP TRIGGER client_facts_history_no_replace")
    c.execute("INSERT OR REPLACE INTO client_facts_history (id, market, key, "
              "action, reason, changed_by, at) VALUES (1, 'US', 'x', 'set', "
              "'rewritten', 'someone else', 't')")
    c.close()
    check("without its trigger a REPLACE rewrites a history row and the "
          "count still holds: the trigger is what guards it",
          rows(bare, "client_facts_history")[0][8:10]
          == ("rewritten", "someone else")
          and counts(bare) == SEEDED, rows(bare, "client_facts_history")[0])

    notes = {"columns": {"market": "TEXT", "note_id": "TEXT",
                         "text": "TEXT"}, "pk": ("market", "note_id")}
    s2 = spec(extra_human={"notes": notes})
    quiet(lambda: db.connect(s2, path).close())
    check("an extra human table refuses DELETE by default",
          "notes_no_delete" in objects(path, "trigger")
          and "notes" in s2.human_tables)
    s3 = spec(extra_human={"notes": notes}, append_only={"notes": ("UPDATE",)})
    quiet(lambda: db.connect(s3, path).close())
    trig = objects(path, "trigger")
    check("a changed trigger spec: the old one dropped, the new one made",
          "notes_no_update" in trig and "notes_no_delete" not in trig, trig)
    s4 = spec(extra_human={"notes": notes},
              frozen_columns={"notes": ("text",)})
    quiet(lambda: db.connect(s4, path).close())
    c = raw(path)
    c.execute("INSERT INTO notes VALUES ('US', 'n1', 'hello')")
    c.execute("UPDATE notes SET text = 'edited'")
    e = raises(lambda: c.execute("UPDATE notes SET note_id = 'n2'"),
               sqlite3.IntegrityError)
    c.close()
    check("frozen_columns on an extra human table: only listed columns move",
          e is not None and "frozen" in str(e), e)
    c = raw(path)
    c.execute("CREATE TRIGGER stock_no_delete BEFORE DELETE ON stock "
              "BEGIN SELECT 1; END")    # the harness's own, on a cache table
    c.close()
    quiet(lambda: db.connect(s4, path).close())
    check("a trigger of the harness's own on a cache table is left alone",
          "stock_no_delete" in objects(path, "trigger"))


def t3_keep_human_rows() -> None:
    print("\n[3] keep_human_rows refuses any write that loses human rows")
    s = spec()
    path = fresh(s)
    con = quiet(lambda: db.connect(s, path))[0]
    for name in db.triggers(s):      # a file written before the triggers
        con.execute(f"DROP TRIGGER IF EXISTS {name}")
    for table in s.human_tables:
        def shrink(t=table):
            with db.keep_human_rows(s, con):
                con.execute(f"DELETE FROM {t}")
        e, code = refusal(shrink)
        check(f"DELETE FROM {table} inside the guard: human_rows_would_shrink",
              code == "human_rows_would_shrink"
              and f"{table} {SEEDED[table]} → 0" in e.message.params["lost"],
              e)
        check(f"… rolled back ({table})", counts(path) == SEEDED,
              counts(path))
    check("… and the connection is usable (no transaction left open)",
          not con.in_transaction)

    with db.keep_human_rows(s, con):
        con.execute("UPDATE client_facts SET source = 'x' "
                    "WHERE key = 'market_declared'")
    check("a non-shrinking write commits",
          [r[4] for r in rows(path, "client_facts")] == ["x", "owner"],
          rows(path, "client_facts"))

    def boom():
        with db.keep_human_rows(s, con):
            con.execute("INSERT INTO client_facts_history (market, key, "
                        "action, reason, changed_by, at) VALUES ('US', 'k', "
                        "'set', 'r', 'u', 't')")
            raise RuntimeError("the writer crashed")
    e = raises(boom, RuntimeError)
    check("an exception in the block rolls back and propagates",
          e is not None and counts(path) == SEEDED, counts(path))

    with db.keep_human_rows(s, con):
        con.execute("INSERT INTO client_facts_history (market, key, action, "
                    "reason, changed_by, at) VALUES ('US', 'k', 'set', 'r', "
                    "'u', 't')")
        e, code = refusal(lambda: _nested_shrink(s, con))
    check("nested: the inner shrink is refused and undone, the outer write "
          "commits",
          code == "human_rows_would_shrink"
          and counts(path) == {**SEEDED, "client_facts_history": 4},
          counts(path))
    con.close()

    quiet(lambda: db.connect(s, path).close())      # triggers back
    real = db._trigger_plan

    def lossy_plan(spec_, con_):
        return real(spec_, con_) + ["DROP TRIGGER IF EXISTS "
                                    "client_facts_no_delete",
                                    "DELETE FROM client_facts"]
    db._trigger_plan = lossy_plan
    try:
        e, code = refusal(lambda: quiet(lambda: db.connect(s, path)))
    finally:
        db._trigger_plan = real
    check("connect()'s bootstrap that would drop facts is refused",
          code == "human_rows_would_shrink", e)
    check("… and rolled back (facts and their trigger intact)",
          counts(path)["client_facts"] == 2
          and "client_facts_no_delete" in objects(path, "trigger"))


def _nested_shrink(s, con):
    with db.keep_human_rows(s, con):
        con.execute("DELETE FROM client_facts")


def t4_lossy() -> None:
    print("\n[4] stale cache tables: tolerated, named, rebuilt only when "
          "raw can refill every period")
    s1 = spec()
    path = fresh(s1)
    # v2: orders gain a channel in the pk; weekly sales and stock re-keyed.
    orders2 = changed(ORDERS, columns={**ORDERS["columns"], "channel": "TEXT"},
                      pk=("date", "sku", "channel"))
    weekly2 = changed(WEEKLY, pk=("sku", "week_start"))
    stock2 = changed(STOCK, pk=("sku", "pulled_on"), not_null=())
    s2 = spec(2, {"orders_daily": orders2, "sales_weekly": weekly2,
                  "stock": stock2})
    con = db.connect(s2, path, read_only=True)
    check("read-only open tolerates stale cache tables",
          db.stale_tables(s2, con) == ["orders_daily", "sales_weekly",
                                       "stock"], db.stale_tables(s2, con))
    con.close()
    quiet(lambda: db.connect(s2, path).close())
    check("a write open without rebuild leaves them (rows kept), stamps v2",
          len(rows(path, "orders_daily")) == 4 and stamp(path) == 2)
    check("… and takes no backup (only a rebuild does)",
          backups(path) == [], backups(path))

    e, code = refusal(lambda: quiet(lambda: db.connect(
        s2, path, rebuild=("orders_daily",))), db.LossyRebuild)
    check("no raw periods: refused (fail closed), names every held day",
          code == "lossy_rebuild"
          and e.losses == [("orders_daily", 2, list(DAYS))]
          and e.message.params == {"table": "orders_daily", "held": 2,
                                   "lacking": 2,
                                   "missing": "2026-09-01..2026-09-02"},
          e and (e.losses, e.message.params))
    check("… nothing dropped", len(rows(path, "orders_daily")) == 4)

    e, code = refusal(lambda: quiet(lambda: db.connect(
        s2, path, rebuild=("orders_daily",),
        raw_periods={"orders_daily": {"2026-09-02", "2026-09-03"}},
        backfill=lambda t, m: f"shop pull {t} --from {m[0]}")),
        db.LossyRebuild)
    check("same period count, different set: refused, names the lost day",
          code == "lossy_rebuild"
          and e.losses == [("orders_daily", 2, ["2026-09-01"])]
          and "raw lacks 1 of them (2026-09-01)" in str(e), e)
    check("… `next` = the backfill hook's command",
          e is not None
          and e.next == ["shop pull orders_daily --from 2026-09-01"],
          e and e.next)

    e, code = refusal(lambda: quiet(lambda: db.connect(
        s2, path, rebuild=("orders_daily", "sales_weekly"),
        raw_periods={"orders_daily": set(DAYS)})), db.LossyRebuild)
    check("the period column is per table (week_start for sales_weekly)",
          code == "lossy_rebuild"
          and e.losses == [("sales_weekly", 2, list(WEEKS))]
          and e.message.params["missing"] == "2026-08-24, 2026-08-31",
          e and (code, e.losses))
    e, code = refusal(lambda: quiet(lambda: db.connect(
        s2, path, rebuild=("orders_daily", "sales_weekly"))),
        db.LossyRebuild)
    check("two lossy tables: one joined message, a part per table",
          code == "joined" and [p.code for p in e.message.params["parts"]]
          == ["lossy_rebuild", "lossy_rebuild"] and len(e.losses) == 2,
          e and e.losses)

    before = path.read_bytes()
    e, code = refusal(lambda: db.check_rebuild(
        s2, path, rebuild=("orders_daily",), raw_periods={}),
        db.LossyRebuild)
    check("check_rebuild (dry run) raises the same, touching nothing",
          code == "lossy_rebuild" and path.read_bytes() == before, e)
    check("check_rebuild passes when raw covers",
          db.check_rebuild(s2, path, rebuild=("orders_daily",),
                           raw_periods={"orders_daily": DAYS}) is None)

    n_backups = len(backups(path))
    con, err = quiet(lambda: db.connect(
        s2, path, rebuild=("orders_daily", "stock", "client_facts"),
        raw_periods={"orders_daily": {"2026-08-31", *DAYS}}))
    check("superset raw: rebuilt in the new shape, emptied for the ingest",
          "orders_daily" not in db.stale_tables(s2, con)
          and rows(path, "orders_daily") == []
          and "[rebuild] orders_daily: old shape dropped" in err, err)
    check("a snapshot table without a period column rebuilds without raw",
          "stock" not in db.stale_tables(s2, con)
          and "[rebuild] stock" in err, err)
    check("a table not named stays stale", db.stale_tables(s2, con)
          == ["sales_weekly"], db.stale_tables(s2, con))
    check("naming a human table in rebuild= does nothing to it",
          counts(path, s2) == SEEDED, counts(path, s2))
    con.close()
    made = backups(path)
    check("a backup of the human tables was written before the rebuild",
          len(made) == n_backups + 1 and counts(made[-1], s2) == SEEDED,
          (len(made), n_backups))


def t5_only_adds() -> None:
    print("\n[5] a new shape that only adds columns keeps every row")
    path = fresh()
    s3 = spec(3, {**CACHE, "orders_daily": changed(
        ORDERS, columns={**ORDERS["columns"], "returns": "INTEGER"})})
    con, err = quiet(lambda: db.connect(s3, path,
                                        rebuild=("orders_daily",)))
    got = con.execute("SELECT date, sku, units, revenue, returns FROM "
                      "orders_daily ORDER BY 1, 2").fetchall()
    check("no raw given, yet not refused: 4 rows carried over, new column "
          "NULL", len(got) == 4 and all(r[2:] == (1, 2.5, None) for r in got),
          got)
    check("… no longer stale; stderr says rows were kept",
          db.stale_tables(s3, con) == []
          and "[rebuild] orders_daily: new columns added, 4 row(s) kept"
          in err, err)
    check("… no temp table left behind",
          con.execute("SELECT count(*) FROM sqlite_temp_master")
          .fetchone()[0] == 0)
    con.close()

    path = fresh()
    s4 = spec(4, {**CACHE, "orders_daily": changed(
        ORDERS, columns={**ORDERS["columns"], "channel": "TEXT"},
        not_null=("channel",))})
    e, code = refusal(lambda: quiet(lambda: db.connect(
        s4, path, rebuild=("orders_daily",))), db.LossyRebuild)
    check("an added NOT NULL column without default is not 'only adds'",
          code == "lossy_rebuild" and len(rows(path, "orders_daily")) == 4,
          e)
    s5 = spec(5, {**CACHE, "orders_daily": changed(
        ORDERS, columns={**ORDERS["columns"], "channel": "TEXT"},
        not_null=("channel",), defaults={"channel": "web"})})
    con = quiet(lambda: db.connect(s5, path, rebuild=("orders_daily",)))[0]
    check("… with a default it is: rows kept, the default filled in",
          [r[0] for r in con.execute("SELECT channel FROM orders_daily")]
          == ["web"] * 4)
    con.close()


def t6_human_drift() -> None:
    print("\n[6] human-table drift always raises, before anything changes")
    s1 = spec()
    path = fresh(s1)
    s2 = spec(2, {**CACHE, "orders_daily": changed(
        ORDERS, pk=("sku", "date"))})
    c = raw(path)
    c.execute("ALTER TABLE client_facts ADD COLUMN extra TEXT")
    c.close()
    for label, kw in (("write + rebuild", {
            "rebuild": ("orders_daily",),
            "raw_periods": {"orders_daily": DAYS}}),
            ("read-only", {"read_only": True})):
        e, code = refusal(lambda: quiet(lambda: db.connect(s2, path, **kw)),
                          db.HumanTableDrift)
        check(f"{label}: human_table_drift names the table and both shapes",
              code == "human_table_drift"
              and e.message.params["table"] == "client_facts"
              and "extra" in e.message.params["found"]
              and "extra" not in e.message.params["expected"]
              and "never dropped" in str(e) and e.next == ["shop doctor"], e)
    check("… nothing changed: the stale cache table was not rebuilt, the "
          "version not stamped",
          len(rows(path, "orders_daily")) == 4 and stamp(path) == 1)
    e, code = refusal(lambda: db.check_rebuild(
        s2, path, rebuild=("orders_daily",),
        raw_periods={"orders_daily": DAYS}), db.HumanTableDrift)
    check("… and the dry-run check says so too", code == "human_table_drift",
          e)

    path = fresh(s1)
    c = raw(path)
    c.execute("ALTER TABLE decisions RENAME TO decisions_old")
    c.execute("CREATE TABLE decisions AS SELECT * FROM decisions_old")
    c.close()
    e, code = refusal(lambda: quiet(lambda: db.connect(s1, path)),
                      db.HumanTableDrift)
    check("same columns, other primary key: drift too",
          code == "human_table_drift"
          and e.message.params["table"] == "decisions"
          and "primary key none" in e.message.params["found"], e)

    path = fresh(s1, seeded=False)
    c = raw(path)
    c.execute("DROP TABLE action_effects")
    c.close()
    e, code = refusal(lambda: db.connect(s1, path, read_only=True))
    check("read-only open, a human table missing: db_human_table_missing",
          code == "db_human_table_missing"
          and e.message.params == {"table": "action_effects"}, e)
    later = spec(human_added_later=("action_effects",))
    con = db.connect(later, path, read_only=True)
    check("… declared human_added_later: the read-only open proceeds",
          db.human_row_counts(later, con)["action_effects"] == 0)
    con.close()
    quiet(lambda: db.connect(later, path).close())
    check("… and the next write open creates it",
          "action_effects" in objects(path, "table"))


def t7_too_new() -> None:
    print("\n[7] a file stamped by a newer harness is refused before any "
          "read")
    s = spec()
    path = fresh(s)
    c = raw(path)
    c.execute("ALTER TABLE stock ADD COLUMN future TEXT")
    c.execute("PRAGMA user_version = 5")
    c.close()
    before = path.read_bytes()
    for label, fn in (
            ("read-only", lambda: db.connect(s, path, read_only=True)),
            ("write", lambda: db.connect(s, path)),
            ("rebuild", lambda: db.connect(s, path, rebuild=("stock",),
                                           raw_periods={})),
            ("dry-run check", lambda: db.check_rebuild(
                s, path, rebuild=("stock",)))):
        e, code = refusal(lambda: quiet(fn), db.SchemaTooNew)
        check(f"{label}: schema_too_new, names both versions",
              code == "schema_too_new"
              and e.message.params == {"path": str(path), "found": 5,
                                       "knows": 1}
              and "schema v5" in str(e) and "knows v1" in str(e)
              and e.next and e.next[0].startswith("git -C "), e)
    check("refusal changed nothing: bytes, the extra column, no backup",
          path.read_bytes() == before
          and not (path.parent / "backups").exists())

    path = Path(tmp_dir("db-")) / "old.db"
    c = raw(path)                           # a field file, never stamped
    c.execute("CREATE TABLE client_facts (x TEXT)")
    c.close()
    e, code = refusal(lambda: db.connect(s, path, read_only=True))
    check("read-only open of an unstamped file does not stamp it",
          code == "human_table_drift" and stamp(path) == 0, e)
    c = raw(path)
    c.execute("DROP TABLE client_facts")
    c.close()
    quiet(lambda: db.connect(s, path).close())
    check("an unstamped file opens in write mode and is stamped",
          stamp(path) == 1)


def t8_no_db() -> None:
    print("\n[8] a read-only open never creates anything")
    s = spec()
    where = Path(tmp_dir("db-")) / "nope"
    path = where / "shop.db"
    e, code = refusal(lambda: db.connect(s, path, read_only=True))
    check("missing file: no_db with the harness's next step",
          code == "no_db" and e.message.params == {"path": str(path)}
          and e.next == ["shop facts init"], e)
    check("… neither the file nor its directory was created",
          not path.exists() and not where.exists())
    check("check_rebuild of a missing file: nothing to lose, nothing made",
          db.check_rebuild(s, path, rebuild=("orders_daily",)) is None
          and not where.exists())

    data = _shop.data_dir()
    e, code = refusal(lambda: db.connect(s, read_only=True))
    check("default path = <SHOP_DATA_DIR>/shop.db, still no_db",
          code == "no_db"
          and e.message.params["path"] == str(data / "shop.db")
          and not (data / "shop.db").exists(), e)
    quiet(lambda: db.connect(s).close())
    check("a write open at the default path creates it",
          (data / "shop.db").is_file())

    junk = Path(tmp_dir("db-")) / "junk.db"
    junk.write_text("this is not a database, it is a note\n" * 40)
    before = junk.read_bytes()
    for label, kw in (("read-only", {"read_only": True}), ("write", {})):
        e, code = refusal(lambda: db.connect(s, junk, **kw))
        check(f"{label} open of a non-database: db_unreadable, untouched",
              code == "db_unreadable" and junk.read_bytes() == before, e)


def t9_upsert() -> None:
    print("\n[9] upsert: idempotent, only the columns a row carries")
    s = spec()
    path = fresh(s, seeded=False, orders=False)
    con = db.connect(s, path)
    row = {"date": "2026-09-01", "sku": "A1", "units": 3, "revenue": 7.5,
           "not_a_column": "dropped"}
    n1 = db.upsert(s, con, "orders_daily", [row])
    n2 = db.upsert(s, con, "orders_daily", [row])
    check("same row twice: 1 written each time, 1 row stored",
          (n1, n2) == (1, 1) and len(rows(path, "orders_daily")) == 1,
          (n1, n2))
    db.upsert(s, con, "orders_daily", [{**row, "revenue": 9.99}])
    db.upsert(s, con, "orders_daily", [{"date": "2026-09-01", "sku": "A1",
                                        "units": 4}])
    check("a re-upsert refreshes values; a partial row blanks nothing",
          rows(path, "orders_daily") == [("2026-09-01", "A1", 4, 9.99)],
          rows(path, "orders_daily"))
    n = db.upsert(s, con, "orders_daily", [
        {"date": None, "sku": "A1", "units": 1}, {"sku": "B2", "units": 1},
        {"date": "2026-09-02", "sku": "B2"}])
    check("rows missing a pk value are skipped and not counted; a pk-only "
          "row inserts", n == 1 and len(rows(path, "orders_daily")) == 2, n)
    check("empty rows: 0", db.upsert(s, con, "orders_daily", []) == 0)
    db.upsert(s, con, "stock", [
        {"sku": "A1", "units": 1, "pulled_on": "t1"},
        {"sku": "A1", "units": 2}])
    check("the same pk twice in one call: last wins, carried columns kept",
          rows(path, "stock") == [("A1", 2, "t1")], rows(path, "stock"))
    e = raises(lambda: db.upsert(s, con, "stock", [
        {"sku": "B2", "units": 5}, {"sku": "C3", "units": None}]),
        sqlite3.IntegrityError)
    check("all or nothing: a bad row rolls back the good one",
          e is not None and rows(path, "stock") == [("A1", 2, "t1")]
          and not con.in_transaction, e)
    check("an unknown table is a programming error",
          raises(lambda: db.upsert(s, con, "nope", [{"a": 1}]), ValueError)
          is not None)
    con.close()


def t10_backups() -> None:
    print("\n[10] backup_human_tables keeps the newest N snapshots")
    s = spec(backups_keep=2)
    path = fresh(s)
    made = [db.backup_human_tables(s, path) for _ in range(3)]
    kept = backups(path)
    check("only the newest 2 kept", kept == made[1:], (kept, made))
    snap = kept[-1]
    check("a snapshot holds every human row verbatim (who/when)",
          all(rows(snap, t) == rows(path, t) for t in s.human_tables))
    check("… only human tables, and the file's version stamp",
          objects(snap, "table") - {"sqlite_sequence"} == set(s.human_tables)
          and stamp(snap) == stamp(path))
    check("no temp file left behind",
          sorted(p.name for p in (path.parent / "backups").iterdir())
          == [p.name for p in kept])

    real = db.dates.now
    frozen = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
    db.dates.now = lambda: frozen
    try:
        a = db.backup_human_tables(s, path)
        b = db.backup_human_tables(s, path)
    finally:
        db.dates.now = real
    check("a frozen clock still gives distinct names, in creation order",
          a != b and a.name == "human-20990101T000000000000Z.db"
          and backups(path)[-2:] == sorted([a, b]) == [a, b], (a, b))

    empty = fresh(s, seeded=False)
    check("empty human tables: no snapshot, no backups dir",
          db.backup_human_tables(s, empty) is None
          and not (empty.parent / "backups").exists())
    check("missing file: no snapshot",
          db.backup_human_tables(s, empty.parent / "nope.db") is None)


def t11_hash() -> None:
    print("\n[11] schema_hash: pinned, stable, moves only with the shape")
    base = db.schema_hash(db.with_human({}, version=1))
    check("the kit's human tables are pinned (a change here means every "
          "harness bumps its schema version; then re-pin)",
          base == "5c8623e090b32a33", base)
    h = db.schema_hash(spec())
    check("stable across calls and cache insertion order",
          h == db.schema_hash(spec())
          == db.schema_hash(spec(cache=dict(reversed(CACHE.items())))))
    same = [spec(7), spec(backups_keep=3),
            spec(period_columns={}),
            spec(cache={**CACHE, "stock": changed(STOCK, doc="x",
                                                   grain="y")})]
    check("version, backups_keep, period columns, doc and grain do not "
          "move it", all(db.schema_hash(x) == h for x in same))
    moved = [
        spec(cache={**CACHE, "stock": changed(STOCK, columns={
            "units": "INTEGER", "sku": "TEXT", "pulled_on": "TEXT"})}),
        spec(cache={**CACHE, "stock": changed(STOCK, columns={
            **STOCK["columns"], "units": "REAL"})}),
        spec(cache={**CACHE, "stock": changed(STOCK, pk=("sku", "units"))}),
        spec(cache={**CACHE, "stock": changed(STOCK, not_null=())}),
        spec(views={}),
        spec(extra_human={"notes": {"columns": {"id": "INTEGER"},
                                    "pk": ("id",)}}),
    ]
    check("column order, a type, pk, not_null, views, a human table move "
          "it", len({db.schema_hash(x) for x in moved} | {h})
          == len(moved) + 1)


def t12_validation() -> None:
    print("\n[12] a malformed spec fails at construction")
    bad = {
        "a cache table named like a human one":
            lambda: db.with_human({"decisions": ORDERS}, version=1),
        "overriding the kit's triggers":
            lambda: spec(append_only={"client_facts": ()}),
        "a name that is not a plain SQL name":
            lambda: spec(cache={**CACHE, "bad name": ORDERS}),
        "a pk column that is not a column":
            lambda: spec(cache={**CACHE, "t": changed(ORDERS, pk=("nope",))}),
        "no pk": lambda: spec(cache={**CACHE, "t": changed(ORDERS, pk=())}),
        "an unknown type":
            lambda: spec(cache={**CACHE, "t": changed(ORDERS, columns={
                **ORDERS["columns"], "a": "DATE"})}),
        "a misspelt table key":
            lambda: spec(cache={**CACHE,
                                "t": {**ORDERS, "not_nul": ("units",)}}),
        "a frozen column that is not a column":
            lambda: spec(extra_human={"n": STOCK},
                         frozen_columns={"n": ("nope",)}),
        "an operation a trigger cannot refuse":
            lambda: spec(extra_human={"n": STOCK},
                         append_only={"n": ("INSERT",)}),
        "append_only on a cache table":
            lambda: db.SchemaSpec(tables=CACHE, human_tables=(), version=1,
                                  append_only={"stock": ("DELETE",)}),
        "version 0": lambda: spec(0),
        "a period column on a human table":
            lambda: spec(period_columns={"client_facts": "market"}),
        "a view named like a table": lambda: spec(views={"stock": "SELECT 1"}),
        "backups_keep 0": lambda: spec(backups_keep=0),
    }
    for label, fn in bad.items():
        check(f"refused: {label}", raises(fn, ValueError) is not None)
    s = spec(cache={**CACHE, "t": {"columns": {"id": int, "v": str,
                                               "x": float}, "pk": "id"}})
    check("python types and a bare pk string are accepted",
          s.tables["t"]["columns"] == {"id": "INTEGER", "v": "TEXT",
                                       "x": "REAL"} and s.pk("t") == ("id",))


def t13_listing() -> None:
    print("\n[13] unknown_tables and human_row_counts")
    s = spec()
    path = fresh(s, seeded=False)
    c = raw(path)
    c.execute("CREATE TABLE old_pull_log (id INTEGER PRIMARY KEY "
              "AUTOINCREMENT, ts TEXT)")
    c.execute("INSERT INTO old_pull_log (ts) VALUES ('t')")
    c.execute("CREATE TABLE _leftover (k TEXT)")
    c.close()
    con = db.connect(s, path, read_only=True)
    check("unknown_tables names leftovers, not sqlite_* nor views",
          db.unknown_tables(s, con) == ["_leftover", "old_pull_log"],
          db.unknown_tables(s, con))
    con.close()
    with contextlib.closing(sqlite3.connect(":memory:")) as mem:
        check("human_row_counts: a table not created yet counts 0",
              db.human_row_counts(s, mem) == dict.fromkeys(s.human_tables, 0))


def t14_views() -> None:
    print("\n[14] views: made, replaced when changed, TEMP when read-only")
    s = spec()
    path = fresh(s)
    s2 = spec(views={"v_orders": "SELECT date, sku FROM orders_daily"})
    quiet(lambda: db.connect(s2, path).close())
    con = db.connect(s2, path, read_only=True)
    check("a changed view is replaced by the next write open",
          len(con.execute("SELECT * FROM v_orders").fetchone()) == 2)
    con.close()
    s3 = spec(views={**VIEWS, "v_units": "SELECT sku, sum(units) AS units "
                     "FROM orders_daily GROUP BY sku"})
    con = db.connect(s3, path, read_only=True)
    check("read-only: a view the file lacks works for this connection",
          dict(con.execute("SELECT * FROM v_units").fetchall())
          == {"A1": 2, "B2": 2})
    con.close()
    check("… and the file did not gain it",
          "v_units" not in objects(path, "view"))


def t15_codes() -> None:
    print("\n[15] every db code is registered and emitted")
    reg = {c: r for c, r in messages.base_registry().items()
           if Path(r["file"]).name == "db.tsv" or c == "no_db"}
    check("db.tsv holds the six db codes",
          sorted(c for c in reg if c != "no_db") == [
              "db_human_table_missing", "db_unreadable",
              "human_rows_would_shrink", "human_table_drift",
              "lossy_rebuild", "schema_too_new"], sorted(reg))
    probs = messages.check_registry_closed(
        _shop.KIT, ["db.py", "schema_base.py"], reg, strict_kit=True)
    check("closed both ways (literal codes, exact params, each emitted)",
          probs == [], probs)
    words = ("amazon", "ppc", "spapi", "meta_", "kol", "lark",
             "zylos")
    text = "".join((_shop.KIT / f).read_text(encoding="utf-8").lower()
                   for f in ("db.py", "schema_base.py",
                             "message_codes.d/db.tsv"))
    check("no domain words in db.py, schema_base.py, db.tsv",
          not [w for w in words if w in text],
          [w for w in words if w in text])


KILL_CHILD = """
import os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import test_db
from kit import db
db.os.replace = lambda *a: os.kill(os.getpid(), signal.SIGKILL)
db.backup_human_tables(test_db.spec(), sys.argv[3])
"""


def t16_shrunk_since_backup() -> None:
    print("\n[16] shrunk_since_backup sees human rows lost since the newest "
          "backup")
    s = spec()
    path = fresh(s)
    check("no backup yet: nothing to compare, {} (and nothing created)",
          db.shrunk_since_backup(s, path) == {}
          and not (path.parent / "backups").exists())
    db.backup_human_tables(s, path)
    check("a backup and nothing lost: {}",
          db.shrunk_since_backup(s, path) == {})
    con = quiet(lambda: db.connect(s, path))[0]
    for name in db.triggers(s):
        con.execute(f"DROP TRIGGER IF EXISTS {name}")
    con.execute("DELETE FROM client_facts")
    con.close()
    check("after hand SQL emptied the facts: {table: [was, now]}",
          db.shrunk_since_backup(s, path)
          == {"client_facts": [SEEDED["client_facts"], 0]},
          db.shrunk_since_backup(s, path))
    before = path.read_bytes()
    path.unlink()
    check("a missing file counts as empty, and the check creates nothing",
          db.shrunk_since_backup(s, path)
          == {t: [n, 0] for t, n in SEEDED.items()}
          and not path.exists(), db.shrunk_since_backup(s, path))
    path.write_bytes(before)
    check("… a database that only grew reports nothing",
          db.shrunk_since_backup(spec(), fresh(spec(), orders=False)) == {})

    print("      a backup killed before it is whole is never taken for one")
    path = fresh(s)
    src = subprocess.run(
        [sys.executable, "-c", KILL_CHILD, str(Path(__file__).resolve()
                                               .parents[2]),
         str(Path(__file__).resolve().parent), str(path)],
        capture_output=True, text=True, timeout=60)
    left = sorted(p.name for p in (path.parent / "backups").iterdir())
    check("the child was killed mid-copy (SIGKILL), a temp file is left",
          src.returncode == -9 and len(left) == 1
          and left[0].endswith(".tmp"), (src.returncode, left, src.stderr))
    check("… it is no backup: newest_backup None, shrunk {} , none listed",
          db.newest_backup(path) is None
          and db.shrunk_since_backup(s, path) == {} and backups(path) == [])
    made = db.backup_human_tables(s, path)
    check("… and the next backup is whole",
          made is not None and db.newest_backup(path) == made
          and counts(made) == SEEDED, made)


def main() -> int:
    _shop.use()
    for t in (t1_bootstrap, t2_triggers, t3_keep_human_rows, t4_lossy,
              t5_only_adds, t6_human_drift, t7_too_new, t8_no_db, t9_upsert,
              t10_backups, t11_hash, t12_validation, t13_listing, t14_views,
              t15_codes, t16_shrunk_since_backup):
        t()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
