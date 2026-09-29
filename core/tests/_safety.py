"""The tiny harness the data-safety tests end with:

    if __name__ == "__main__": _safety.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0. A file that defines no test, or runs under `python -O` (asserts
would be skipped, so nothing could fail), does not pass either.

Importing this puts the folder that holds core/ on sys.path, so a test says
`from core import store`. Helpers: `tmpdir()` (a folder removed afterwards), `raises(cls, code)` (the
block must raise it), `stderr()` (what the block printed there), and
`schema()`, the sample tables every store test uses: a day-grained cache table
that keeps history, a latest-state cache table, and three human tables (facts,
their append-only history, and approvals whose decision alone may change).
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import traceback
from contextlib import contextmanager, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the folder that holds core/
sys.path.insert(0, ROOT)
from core import store  # noqa: E402


def main(g: dict) -> None:
    """Run the `test_*` functions defined in the module namespace `g`."""
    if not __debug__:
        print("FAILED: python -O skips every assert; run without -O")
        sys.exit(1)
    tests = [(n, f) for n, f in g.items()
             if n.startswith("test_") and callable(f) and getattr(f, "__module__", None) == g.get("__name__")]
    if not tests:
        print("FAILED: no test_* function found")
        sys.exit(1)
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except (Exception, SystemExit):
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
    if failed:
        print(f"FAILED: {failed} of {len(tests)}")
        sys.exit(1)
    print(f"RESULT: {len(tests)} passed")


@contextmanager
def tmpdir():
    """A fresh empty folder, removed on exit (even after a failure)."""
    d = tempfile.mkdtemp(prefix="core-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _Caught:
    err: BaseException | None = None


@contextmanager
def raises(cls: type[BaseException], code: str | None = None):
    """The block must raise `cls` (with this `.code` if given); the exception
    is on the yielded object's `.err`. Anything else passes through, so a
    crash is never mistaken for a refusal."""
    caught = _Caught()
    try:
        yield caught
    except cls as e:
        assert code is None or getattr(e, "code", None) == code, f"expected {code!r}, got {e!r}"
        caught.err = e
        return
    raise AssertionError(f"expected {cls.__name__}; nothing was raised")


class _Text:
    text = ""


@contextmanager
def stderr():
    """Capture what the block prints to stderr on `.text`."""
    out, buf = _Text(), io.StringIO()
    try:
        with redirect_stderr(buf):
            yield out
    finally:
        out.text = buf.getvalue()


def schema(version: int = 1, *, daily: dict | None = None, facts: dict | None = None) -> store.Schema:
    """The sample schema. `daily` / `facts` replace those tables' columns (a
    new shape for a rebuild or a drift test)."""
    T = store.Table
    return store.Schema(version, {
        "daily": T(daily or {"day": "TEXT", "entity": "TEXT", "cost": "REAL"}, ("day", "entity"), day="day"),
        "latest": T({"entity": "TEXT", "state": "TEXT"}, ("entity",)),
        "facts": T(facts or {"scope": "TEXT", "key": "TEXT", "value": "TEXT", "state": "TEXT"},
                   ("scope", "key"), human=True, refuse=("DELETE",)),
        "facts_history": T({"id": "INTEGER", "scope": "TEXT", "key": "TEXT", "value": "TEXT",
                            "changed_by": "TEXT", "changed_at": "TEXT"}, ("id",), human=True,
                           refuse=("UPDATE", "DELETE")),
        "approvals": T({"id": "TEXT", "action": "TEXT", "status": "TEXT", "decided_by": "TEXT"}, ("id",),
                       human=True, refuse=("DELETE",), changes_only=("status", "decided_by")),
    })


def seed(conn, s: store.Schema) -> dict[str, int]:
    """Human rows as the human verbs leave them, plus cache rows on three days;
    returns the human row counts."""
    with store.keep_human_rows(conn, s):
        conn.executemany("INSERT INTO facts VALUES (?, ?, ?, ?)",
                         [("s1", "unit_cost", "4", "confirmed"), ("s1", "monthly_cap", "900", "pending")])
        conn.executemany("INSERT INTO facts_history (scope, key, value, changed_by, changed_at) "
                         "VALUES (?, ?, ?, ?, ?)",
                         [("s1", "unit_cost", "4", "agent", "2026-01-01T00:00:00Z"),
                          ("s1", "unit_cost", "4", "owner", "2026-01-02T00:00:00Z"),
                          ("s1", "monthly_cap", "900", "agent", "2026-01-02T00:00:00Z")])
        conn.execute("INSERT INTO approvals VALUES ('a1', 'raise cap on entity e1', 'open', NULL)")
        conn.executemany("INSERT INTO daily (day, entity, cost) VALUES (?, ?, ?)",
                         [(d, e, 1.0) for d in ("2026-01-01", "2026-01-02", "2026-01-03") for e in ("e1", "e2")])
        conn.execute("INSERT INTO latest VALUES ('e1', 'on')")
    return store.human_counts(conn, s)
