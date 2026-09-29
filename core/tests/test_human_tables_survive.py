#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Human rows survive every path that writes the database. In the source
project a live database was found with its client's confirmed facts gone and
every test green; nothing below may let that happen again:

  1. every open, read-only included, and every rebuild of a stale cache table
     leaves each human table's rows as they were;
  2. `keep_human_rows` rolls back any write that leaves a human table shorter,
     on its own (a file written before the triggers existed) and when the
     shrink hides inside the open itself;
  3. the triggers in the file refuse what bypasses every Python guard: a
     plain sqlite3 connection, as a person with a SQL shell would use;
  4. every rebuild snapshots the human tables first, newest N kept, and the
     doctor's check sees rows lost since the newest snapshot.
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _safety  # noqa: E402  (puts the folder that holds core/ on sys.path)
from core import dates, store  # noqa: E402
from _safety import raises, schema, seed, stderr, tmpdir  # noqa: E402

DAYS = {"2026-01-01", "2026-01-02", "2026-01-03"}


def seeded(d: str) -> tuple[str, dict]:
    """A seeded database and its human row counts."""
    path = os.path.join(d, "store.db")
    with stderr():
        conn = store.open_write(path, schema())
    try:
        return path, seed(conn, schema())
    finally:
        conn.close()


def counts(path: str) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return store.human_counts(conn, schema())
    finally:
        conn.close()


def history(path: str) -> list[tuple]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT * FROM facts_history ORDER BY id").fetchall()
    finally:
        conn.close()


def raw_sql(path: str, *sql: str) -> None:
    """What a person at a SQL shell would run: no store.py in the way."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        for s in sql:
            conn.execute(s)
    finally:
        conn.close()


def drop_triggers(path: str) -> None:
    """The file as it was before the triggers existed."""
    raw_sql(path, *(f"DROP TRIGGER IF EXISTS {n}" for n in store.triggers(schema())))


def snapshots(d: str) -> list[str]:
    b = os.path.join(d, "backups")
    return sorted(os.listdir(b)) if os.path.isdir(b) else []


def test_every_open_and_every_rebuild_leaves_the_human_rows():
    with tmpdir() as d:
        path, n = seeded(d)
        assert n == {"facts": 2, "facts_history": 3, "approvals": 1}
        store.open_write(path, schema()).close()
        assert counts(path) == n
        store.open_read(path, schema()).close()
        assert counts(path) == n
        raw_sql(path, "ALTER TABLE daily ADD COLUMN zz_stale TEXT", "ALTER TABLE latest ADD COLUMN zz_stale TEXT")
        conn = store.open_read(path, schema())
        assert store.stale_tables(conn, schema()) == ["daily", "latest"]
        conn.close()
        with stderr() as err:
            conn = store.open_write(path, schema(), rebuild=("daily", "latest"), raw_days={"daily": DAYS})
        assert store.stale_tables(conn, schema()) == []
        conn.close()
        assert err.text.count("[rebuild]") == 2
        assert counts(path) == n


def test_a_write_that_shrinks_a_human_table_is_rolled_back():
    """The guard on its own: triggers dropped, a plain connection, any table."""
    with tmpdir() as d:
        path, n = seeded(d)
        drop_triggers(path)
        for table in schema().human:
            conn = sqlite3.connect(path, isolation_level=None)
            try:
                with raises(store.HumanRowsLost, "human_rows_lost") as c:
                    with store.keep_human_rows(conn, schema()):
                        conn.execute("INSERT INTO latest VALUES ('e7', 'on')")     # rolled back with it
                        conn.execute(f"DELETE FROM {table}")
                assert c.err.params["lost"] == {table: [n[table], 0]}
            finally:
                conn.close()
            assert counts(path) == n
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT count(*) FROM latest WHERE entity='e7'").fetchone() == (0,)
        conn.close()


def test_a_write_that_keeps_every_human_row_commits():
    with tmpdir() as d:
        path, n = seeded(d)
        conn = store.open_write(path, schema())
        try:
            with store.keep_human_rows(conn, schema()):
                conn.execute("UPDATE facts SET value='5', state='pending' WHERE key='unit_cost'")
                conn.execute("INSERT INTO facts_history (scope, key, value, changed_by, changed_at) "
                             "VALUES ('s1', 'unit_cost', '5', 'agent', '2026-01-03T00:00:00Z')")
        finally:
            conn.close()
        assert counts(path) == {**n, "facts_history": n["facts_history"] + 1}
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT value FROM facts WHERE key='unit_cost'").fetchone() == ("5",)
        conn.close()


def test_an_inner_rollback_leaves_the_outer_write_whole():
    """The guard nests: a refused step inside a larger write undoes only itself."""
    with tmpdir() as d:
        path, n = seeded(d)
        drop_triggers(path)
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            with store.keep_human_rows(conn, schema()):
                conn.execute("INSERT INTO latest VALUES ('e8', 'on')")
                with raises(store.HumanRowsLost), store.keep_human_rows(conn, schema()):
                    conn.execute("DELETE FROM approvals")
        finally:
            conn.close()
        assert counts(path) == n
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT count(*) FROM latest WHERE entity='e8'").fetchone() == (1,)
        conn.close()


def test_a_shrink_hidden_inside_the_open_itself_is_rolled_back():
    """Whatever step of an open loses a human row, the whole open is undone:
    here one that drops a trigger and then deletes the facts."""
    with tmpdir() as d:
        path, n = seeded(d)
        real = store._install_triggers

        def lossy(conn, s):
            conn.execute("DROP TRIGGER facts_no_delete")
            conn.execute("DELETE FROM facts")
            real(conn, s)
        store._install_triggers = lossy
        try:
            with raises(store.HumanRowsLost):
                store.open_write(path, schema()).close()
        finally:
            store._install_triggers = real
        assert counts(path) == n
        with raises(sqlite3.IntegrityError):                              # the trigger came back with the rollback
            raw_sql(path, "DELETE FROM facts")


def test_triggers_refuse_what_bypasses_every_python_guard():
    with tmpdir() as d:
        path, n = seeded(d)
        before = history(path)
        for sql in ("UPDATE facts_history SET changed_by='someone else', changed_at='1970-01-01'",
                    "DELETE FROM facts_history",
                    "DELETE FROM facts",
                    "DELETE FROM approvals",
                    "UPDATE approvals SET action='raise cap on entity e2'",
                    "UPDATE approvals SET id='a2', status='approved'",
                    # REPLACE deletes without firing DELETE triggers and is no UPDATE: its own trigger stops it
                    "INSERT OR REPLACE INTO facts_history VALUES (1, 's1', 'unit_cost', '9', 'forged', '1970-01-01')",
                    "INSERT OR REPLACE INTO approvals VALUES ('a1', 'raise cap on entity e2', 'open', NULL)",
                    "INSERT INTO facts VALUES ('s1', 'unit_cost', '9', 'confirmed') "
                    "ON CONFLICT (scope, key) DO UPDATE SET value = excluded.value"):
            with raises(sqlite3.IntegrityError) as c:
                raw_sql(path, sql)
            assert "refused" in str(c.err), (sql, c.err)
        assert history(path) == before and counts(path) == n
        raw_sql(path, "UPDATE facts SET value='6' WHERE key='unit_cost'",
                "INSERT INTO facts_history (scope, key, value, changed_by, changed_at) "
                "VALUES ('s1', 'unit_cost', '6', 'owner', '2026-01-04T00:00:00Z')",
                "UPDATE approvals SET status='approved', decided_by='owner' WHERE id='a1'")
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT status, decided_by FROM approvals").fetchall() == [("approved", "owner")]
        conn.close()


def test_the_triggers_are_generated_from_each_tables_rules():
    t = store.triggers(schema())
    assert sorted(t) == ["approvals_frozen", "approvals_no_delete", "approvals_no_replace",
                         "facts_history_no_delete", "facts_history_no_replace", "facts_history_no_update",
                         "facts_no_delete", "facts_no_replace"]
    assert "BEFORE UPDATE OF id, action ON approvals" in t["approvals_frozen"]


def test_every_rebuild_snapshots_the_human_tables_first():
    with tmpdir() as d:
        path, n = seeded(d)
        assert snapshots(d) == []
        store.open_write(path, schema()).close()                          # no rebuild asked: no snapshot
        assert snapshots(d) == []
        with stderr():
            store.open_write(path, schema(), rebuild=("daily",), raw_days={"daily": DAYS}).close()
        [snap] = snapshots(d)
        assert snap.startswith("store-human-") and snap.endswith(".db")
        full = os.path.join(d, "backups", snap)
        assert counts(full) == n and history(full) == history(path)       # who and when kept verbatim
        conn = sqlite3.connect(full)
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='daily'").fetchone() == (0,)
        conn.close()


def test_only_the_newest_snapshots_are_kept():
    with tmpdir() as d:
        path, _ = seeded(d)
        made = [store.snapshot_human(path, schema(), keep=2) for _ in range(3)]
        assert [os.path.join(d, "backups", s) for s in snapshots(d)] == made[1:]
        assert not [s for s in os.listdir(os.path.join(d, "backups")) if s.endswith(".tmp")]
        with raises(ValueError):
            store.snapshot_human(path, schema(), keep=0)


def test_a_snapshot_is_named_by_the_one_clock_and_a_pinned_clock_replaces_none():
    """A snapshot named off `dates.now` escaped the pinned clock; pinned, two
    snapshots of one run must still both be kept, in the order they were made."""
    at = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    with tmpdir() as d, mock.patch.object(dates, "now", return_value=at):
        path, _ = seeded(d)
        made = [store.snapshot_human(path, schema(), keep=3) for _ in range(3)]
        names = snapshots(d)
        assert [os.path.join(d, "backups", s) for s in names] == made and len(set(made)) == 3
        assert all(s.startswith("store-human-20300506T070809") for s in names), names


def test_a_snapshot_killed_before_it_is_whole_is_never_taken_for_one():
    """A restore must never start from half a copy."""
    with tmpdir() as d:
        path, _ = seeded(d)
        code = ("import os, signal, sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; import _safety; from core import store\n"
                "store.os.replace = lambda *a: os.kill(os.getpid(), signal.SIGKILL)\n"
                "store.snapshot_human(sys.argv[3], _safety.schema())")
        p = subprocess.run([sys.executable, "-c", code, _safety.ROOT, HERE, path],
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == -9, p.stderr
        assert [s for s in snapshots(d) if s.endswith(".tmp")] and store.newest_snapshot(path) is None
        assert store.shrunk_since_snapshot(path, schema()) == {}
        assert store.snapshot_human(path, schema()) == store.newest_snapshot(path)     # the next one is whole


def test_nothing_to_snapshot_writes_nothing():
    with tmpdir() as d:
        assert store.snapshot_human(os.path.join(d, "none.db"), schema()) is None
        path = os.path.join(d, "empty.db")
        with stderr():
            store.open_write(path, schema()).close()
        assert store.snapshot_human(path, schema()) is None
        assert snapshots(d) == [] and store.shrunk_since_snapshot(path, schema()) == {}


def test_the_doctor_sees_human_rows_lost_since_the_newest_snapshot():
    """'Not set up yet' and 'set up, then wiped' look the same in the live
    tables; the snapshot tells them apart."""
    with tmpdir() as d:
        path, n = seeded(d)
        store.snapshot_human(path, schema())
        assert store.shrunk_since_snapshot(path, schema()) == {}
        drop_triggers(path)
        raw_sql(path, "DELETE FROM facts")
        assert store.shrunk_since_snapshot(path, schema()) == {"facts": [n["facts"], 0]}
        os.remove(path)
        assert store.shrunk_since_snapshot(path, schema()) == {t: [c, 0] for t, c in n.items()}
        assert not os.path.exists(path)                                    # the check created nothing


if __name__ == "__main__":
    _safety.main(globals())
