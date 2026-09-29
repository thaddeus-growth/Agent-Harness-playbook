#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""store.py: the opens and the rebuild rules. Each test's docstring names the
bug it guards; the test fails when store.py lets that bug through. That human
rows survive every path has its own file, test_human_tables_survive.py.
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _safety  # noqa: E402  (puts the folder that holds core/ on sys.path)
from core import store  # noqa: E402
from _safety import raises, schema, seed, stderr, tmpdir  # noqa: E402

DAYS = {"2026-01-01", "2026-01-02", "2026-01-03"}          # the days seed() puts in `daily`
OLD_DAILY = {"day": "TEXT", "entity": "TEXT", "cost": "REAL", "old_col": "TEXT"}   # a column the new shape dropped
WIDER_DAILY = {"day": "TEXT", "entity": "TEXT", "cost": "REAL", "clicks": "INTEGER"}


def db_in(d: str) -> str:
    return os.path.join(d, "data", "store.db")


def made(path: str, s: store.Schema) -> dict:
    """A database written by `s` and seeded; returns the human row counts."""
    with stderr():
        conn = store.open_write(path, s)
    try:
        return seed(conn, s)
    finally:
        conn.close()


def shape(path: str, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def rows(path: str, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    finally:
        conn.close()


def version(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_a_schema_with_an_unsafe_or_contradictory_table_is_refused():
    """Table and column names go into SQL text; a bad schema must not load."""
    T = store.Table
    for bad in ({"t; DROP TABLE x": T({"a": "TEXT"}, ("a",))},
                {"t": T({"a": "TEXT"}, ("b",))},                                   # key is not a column
                {"t": T({"a": "TEXT"}, ("a",), refuse=("DELETE",))},               # a trigger on a cache table
                {"t": T({"a": "TEXT"}, ("a",), human=True, day="a")},              # history rules on a human table
                {"t": T({"a": "TEXT", "b": "TEXT"}, ("a",), human=True, refuse=("UPDATE",), changes_only=("b",))},
                {"t": T({"a": "TEXT", "b": "TEXT"}, ("a",), human=True, refuse=("TRUNCATE",))},
                {"t": T({"a": "TEXT); DROP TABLE x; --"}, ("a",))}):
        with raises(ValueError):
            store.Schema(1, bad)
    with raises(ValueError):
        store.Schema(0, {"t": T({"a": "TEXT"}, ("a",))})
    assert schema().human == ("facts", "facts_history", "approvals")


def test_a_table_change_without_a_version_bump_fails_a_pinned_fingerprint():
    """Switching between an older and a newer checkout rebuilt the same tables
    each way, cutting history to the last pull every time: the file carried no
    stamp. A stamp helps only if every table change moves it; a harness pins
    (version, fingerprint) in its tests, and this is what makes the pin bite."""
    base = store.fingerprint(schema())
    assert base == store.fingerprint(schema()) and len(base) == 12
    assert base == store.fingerprint(schema(7))                          # the version is pinned beside it, not in it
    changed = [schema(daily=WIDER_DAILY),
               schema(daily={"day": "TEXT", "entity": "TEXT", "cost": "TEXT"}),        # a type
               schema(daily={"entity": "TEXT", "day": "TEXT", "cost": "REAL"}),        # the order
               schema(facts={"scope": "TEXT", "key": "TEXT", "value": "TEXT", "status": "TEXT"})]
    s = schema()
    changed.append(store.Schema(1, {**s.tables, "facts": store.Table(s.tables["facts"].columns, ("scope", "key"),
                                                                       human=True, refuse=("UPDATE", "DELETE"))}))
    assert len({base, *map(store.fingerprint, changed)}) == len(changed) + 1


def test_a_read_never_creates_the_database():
    """Read verbs once created an empty database wherever the path pointed."""
    with tmpdir() as d:
        path = db_in(d)
        with raises(store.NoDatabase, "no_db") as c:
            store.open_read(path, schema())
        assert path in str(c.err) and c.err.params == {"path": path}
        assert os.listdir(d) == []                                   # not even the folder


def test_a_write_open_that_creates_the_file_says_so():
    """A mistyped database path passed silently as a new, empty store."""
    with tmpdir() as d:
        path = db_in(d)
        with stderr() as err:
            store.open_write(path, schema()).close()
        assert "NEW empty database" in err.text and path in err.text
        with stderr() as err:
            store.open_write(path, schema()).close()
        assert err.text == ""                                         # an existing file opens quietly


def test_a_write_open_stamps_the_version_and_a_read_open_does_not():
    """The stamp is how the next open knows which code wrote the file."""
    with tmpdir() as d:
        path = os.path.join(d, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE latest (entity TEXT NOT NULL, state TEXT, PRIMARY KEY (entity))")
        conn.close()
        store.open_read(path, schema(3)).close()
        assert version(path) == 0
        store.open_write(path, schema(3)).close()
        assert version(path) == 3


def test_a_newer_stamp_is_refused_on_every_open_and_nothing_changes():
    """An older checkout read, and could rewrite, a file a newer one had written."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1))
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA user_version = 2")
        conn.close()
        before = open(path, "rb").read()
        opens = (lambda: store.open_read(path, schema(1)),
                 lambda: store.open_write(path, schema(1)),
                 lambda: store.open_write(path, schema(1), rebuild=("daily",), raw_days={"daily": DAYS}),
                 lambda: store.check_rebuild(path, schema(1), ("daily",), {"daily": DAYS}))
        for fn in opens:
            with raises(store.SchemaTooNew, "schema_too_new") as c:
                fn()
            assert c.err.params["found"] == 2 and c.err.params["known"] == 1
        assert open(path, "rb").read() == before
        assert not os.path.exists(os.path.join(d, "data", "backups"))     # refused before the snapshot


def test_a_read_open_cannot_write():
    """A read verb must not be able to change what it reads."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema())
        conn = store.open_read(path, schema())
        try:
            with raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO latest VALUES ('e9', 'on')")
        finally:
            conn.close()


def test_a_rebuild_that_would_lose_days_is_refused_and_drops_nothing():
    """A schema change dropped a history table that only the database still held."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        before = rows(path, "daily")
        with stderr(), raises(store.LossyRebuild, "lossy_rebuild") as c:
            store.open_write(path, schema(2), rebuild=("daily",), raw_days={"daily": DAYS - {"2026-01-01"}})
        assert c.err.losses == [("daily", 3, ["2026-01-01"])]
        assert shape(path, "daily") == list(OLD_DAILY) and rows(path, "daily") == before
        assert version(path) == 1


def test_the_same_number_of_days_shifted_is_still_refused():
    """An equal day count hid dropped days: a rolling window moved between a
    schema change and the ingest, so raw held as many days, but later ones."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        shifted = {"2026-01-02", "2026-01-03", "2026-01-04"}
        assert len(shifted) == len(DAYS)
        with stderr(), raises(store.LossyRebuild) as c:
            store.open_write(path, schema(2), rebuild=("daily",), raw_days={"daily": shifted})
        assert c.err.losses == [("daily", 3, ["2026-01-01"])]
        assert "2026-01-01" in str(c.err)


def test_no_raw_days_refill_nothing():
    """A caller that passes no days for a table must not be read as 'nothing to lose'."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        with stderr(), raises(store.LossyRebuild) as c:
            store.open_write(path, schema(2), rebuild=("daily",))
        assert c.err.losses == [("daily", 3, sorted(DAYS))]


def test_raw_holding_every_day_rebuilds_in_the_new_shape():
    """The refusal must not block the rebuild it exists to make safe."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        with stderr() as err:
            conn = store.open_write(path, schema(2), rebuild=("daily",), raw_days={"daily": DAYS | {"2026-01-04"}})
        try:
            assert store.stale_tables(conn, schema(2)) == []
        finally:
            conn.close()
        assert "[rebuild] daily: old shape dropped" in err.text
        assert shape(path, "daily") == ["day", "entity", "cost"] and rows(path, "daily") == []   # the ingest refills it
        assert version(path) == 2


def test_a_new_shape_that_only_adds_columns_keeps_every_row():
    """A column added to a report cost the days only the database still held."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1))
        before = rows(path, "daily")
        with stderr() as err:
            store.open_write(path, schema(2, daily=WIDER_DAILY), rebuild=("daily",)).close()   # no raw days at all
        assert shape(path, "daily") == list(WIDER_DAILY)
        assert rows(path, "daily") == [r + (None,) for r in before]
        assert "6 row(s) kept" in err.text


def test_a_stale_table_the_ingest_does_not_name_is_left_as_it_is():
    """Only the ingest that refills a table may rebuild it; readers are told it is stale."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        before = rows(path, "daily")
        store.open_write(path, schema(2)).close()
        assert shape(path, "daily") == list(OLD_DAILY) and rows(path, "daily") == before
        conn = store.open_read(path, schema(2))
        try:
            assert store.stale_tables(conn, schema(2)) == ["daily"]
        finally:
            conn.close()


def test_the_rebuild_names_only_cache_tables():
    """A rebuild list must never reach a human table."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema())
        for bad in (("facts",), ("approvals",), ("nope",)):
            with raises(ValueError):
                store.open_write(path, schema(), rebuild=bad)


def test_a_changed_human_table_is_refused_never_dropped():
    """Human data cannot be pulled again, so its shape never changes by code."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1))
        before = rows(path, "facts")
        wider = schema(2, facts={"scope": "TEXT", "key": "TEXT", "value": "TEXT", "state": "TEXT", "note": "TEXT"})
        for fn in (lambda: store.open_write(path, wider),
                   lambda: store.open_write(path, wider, rebuild=("daily",), raw_days={"daily": DAYS}),
                   lambda: store.open_read(path, wider)):
            with stderr(), raises(store.HumanDrift, "human_drift") as c:
                fn()
            assert c.err.params == {"table": "facts"}
        assert shape(path, "facts") == ["scope", "key", "value", "state"] and rows(path, "facts") == before
        assert version(path) == 1


def test_unknown_tables_are_named():
    """Tables left by older code were read and gave out-of-date answers."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema())
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE pull_log (x TEXT)")
        conn.close()
        conn = store.open_read(path, schema())
        try:
            assert store.unknown_tables(conn, schema()) == ["pull_log"]
        finally:
            conn.close()


def test_the_refusal_names_the_missing_days_as_ranges_and_the_backfill():
    """A refusal that does not say what to run next gets worked around."""
    days = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-07", "week-1"]
    assert store.day_ranges(days) == "2026-01-01..2026-01-03, 2026-01-07, week-1"
    err = store.LossyRebuild([("daily", 9, days[:4])])
    note = err.note(lambda t, missing: f"pull {t} --start {missing[0]} --end {missing[-1]}")
    assert "raw lacks 4 of them (2026-01-01..2026-01-03, 2026-01-07)" in note
    assert "Backfill first: `pull daily --start 2026-01-01 --end 2026-01-07`" in note
    assert "No pull can refill them" in err.note(lambda t, missing: None)


def test_the_dry_run_raises_the_same_refusal_and_touches_nothing():
    """A dry run that reports nothing lets the real run fail later, or worse."""
    with tmpdir() as d:
        path = db_in(d)
        made(path, schema(1, daily=OLD_DAILY))
        before = open(path, "rb").read()
        with raises(store.LossyRebuild) as c:
            store.check_rebuild(path, schema(2), ("daily",), {"daily": {"2026-01-03"}})
        assert c.err.losses == [("daily", 3, ["2026-01-01", "2026-01-02"])]
        store.check_rebuild(path, schema(2), ("daily",), {"daily": DAYS})             # would pass: no raise
        assert open(path, "rb").read() == before
        assert not os.path.exists(os.path.join(d, "data", "backups"))
        assert store.check_rebuild(os.path.join(d, "none.db"), schema(), ("daily",), {}) is None
        assert not os.path.exists(os.path.join(d, "none.db"))


if __name__ == "__main__":
    _safety.main(globals())
