#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One clock: CLOCK's `now()` is the only calendar read in the code.

Every other module reads the time as `dates.now()`, `dates.today()` or
`dates.host_today()`, so one patch of `dates.now` (a test's, or the golden
diff's one instant) reaches every read. The rule, by AST, on every .py under
CODE except CLOCK:

  1. no `.now`, `.today` or `.utcnow` of `datetime.datetime` or `datetime.date`,
     however they were imported (`import datetime as dt`, `from datetime import
     datetime as DT`), called or not;
  2. no `time.localtime()`, `gmtime()`, `ctime()`, `asctime()` or
     `strftime(fmt)` without a time value: those read the calendar too.
     `time.time()` and `time.monotonic()` measure durations and are allowed;
  3. no `from ...dates import now | today | host_today`: a name bound at import
     keeps the function a later patch of the module no longer reaches.

ALLOWED holds reads not yet moved, as (file, enclosing function) -> count, and
must match exactly: a read that moved fails here until its row goes too, so
the list only shrinks. Start a new harness with it empty.
"""

import ast
import datetime
import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest import mock

from _check import check, finish

ROOT = Path(__file__).resolve().parent.parent
CODE = ("src", "core")                   # folders of code to lint (core/tests too: its toy harness is an example)
CLOCK = "core/dates.py"                  # the one module that reads the calendar
ALLOWED: dict[tuple[str, str], int] = {
    # ("src/pull_ads.py", "window"): 1,  a read still to move to dates.now()
}
CLASSES = {"datetime.datetime", "datetime.date"}
READS = {"now", "today", "utcnow"}
TIME_READS = {"time.localtime": 1, "time.gmtime": 1, "time.ctime": 1,
              "time.asctime": 1, "time.strftime": 2}   # reads now with fewer args
BOUND = {"now", "today", "host_today"}


class Scan(ast.NodeVisitor):
    """The rule's hits in one module: (enclosing function, line, what)."""

    def __init__(self):
        self.alias: dict[str, set[str]] = {}   # local name -> stdlib names
        self.stack: list[str] = []
        self.hits: list[tuple[str, int, str]] = []

    def run(self, tree: ast.AST) -> list[tuple[str, int, str]]:
        for node in ast.walk(tree):             # every binding, any scope
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in ("datetime", "time"):
                        self.alias.setdefault(a.asname or a.name, set()).add(a.name)
            elif (isinstance(node, ast.ImportFrom) and not node.level
                  and node.module in ("datetime", "time")):
                for a in node.names:
                    self.alias.setdefault(a.asname or a.name, set()).add(
                        f"{node.module}.{a.name}")
        self.visit(tree)
        return self.hits

    def qual(self, node: ast.AST) -> set[str]:
        """The stdlib names an expression like `dt.datetime.now` stands for."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return set()
        return {".".join([q, *parts]) for q in self.alias.get(node.id, ())}

    def _hit(self, node: ast.AST, what: str) -> None:
        self.hits.append((".".join(self.stack) or "<module>", node.lineno, what))

    def _scope(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in READS and any(q.rpartition(".")[0] in CLASSES
                                      for q in self.qual(node)):
            self._hit(node, ast.unparse(node))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if any(len(node.args) < TIME_READS.get(q, 0) for q in self.qual(node.func)):
            self._hit(node, ast.unparse(node))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (node.module or "").rpartition(".")[2] == "dates":
            for a in node.names:
                if a.name in BOUND:
                    self._hit(node, f"from {'.' * node.level}{node.module} import {a.name}")


def scan(source: str) -> list[tuple[str, int, str]]:
    return Scan().run(ast.parse(source))


def modules() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for d in CODE
                  for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts)


SAMPLE = '''\
import datetime as dt
import time
from datetime import date, datetime as DT
from core import dates
from core.dates import now, utc_stamp
def f():
    a = dt.datetime.now(dt.timezone.utc)
    b = date.today()
    c = DT.utcnow()
    d = DT.now
    e = time.time() + time.monotonic()
    g = dates.now(), dates.today(), utc_stamp()
    h = DT.fromisoformat("2026-01-31"), time.strftime("%Y", time.gmtime(0))
    i = time.strftime("%Y-%m-%d")
class C:
    def m(self):
        return dt.date.today()
'''


def main() -> int:
    print("the rule")
    got = scan(SAMPLE)
    check("catches every datetime/date now/today/utcnow, called or not, a time "
          "read with no time value, and a `from core.dates import now` binding",
          [(q, n) for q, n, _ in got] == [("<module>", 5), ("f", 7), ("f", 8), ("f", 9),
                                          ("f", 10), ("f", 14), ("C.m", 17)], got)
    check("leaves time.time, time.monotonic, dates.now/today, utc_stamp, "
          "fromisoformat and strftime of a given time alone",
          not any(w.startswith(("time.time", "dates.", "utc_stamp"))
                  or "fromisoformat" in w or "gmtime(0)" in w for _, _, w in got), got)
    check("catches a relative `from .dates import today`",
          len(scan("from .dates import today, utc_stamp\n")) == 1)

    print(f"{', '.join(CODE)} read the calendar only through {CLOCK}")
    files = modules()
    check(f"{', '.join(CODE)} hold code to read, {CLOCK} among it",
          CLOCK in files and len(files) > 1, files)
    found: dict[tuple[str, str], list[str]] = {}
    for rel in files:
        hits = scan((ROOT / rel).read_text(encoding="utf-8"))
        if rel == CLOCK:
            check(f"{CLOCK} reads the stdlib clock once, in now()",
                  [(q, w) for q, _, w in hits] == [("now", "datetime.datetime.now")], hits)
            continue
        for q, n, w in hits:
            found.setdefault((rel, q), []).append(f"{rel}:{n} ({q}) {w}")
    extra = [s for key, sites in sorted(found.items()) for s in sites[ALLOWED.get(key, 0):]]
    check("no calendar read outside the clock but ALLOWED's "
          "(read dates.now() / .today() / .host_today() instead)", not extra, "; ".join(extra))
    gone = [f"{f}:{q} ({n - len(found.get((f, q), []))} moved)"
            for (f, q), n in sorted(ALLOWED.items()) if len(found.get((f, q), [])) < n]
    check("ALLOWED lists only reads still there (drop a moved read's row)",
          not gone, "; ".join(gone))

    print("the clock: one patch of now() pins every day")
    spec = importlib.util.spec_from_file_location("clock_under_test", ROOT / CLOCK)
    clock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clock)
    at = clock.now()
    check("now() is aware UTC", at.utcoffset() == datetime.timedelta(0), at)
    # 18:20 UTC on 01-30 is 02:20 on 01-31 in Shanghai, 10:20 on 01-30 in Los Angeles
    pin = datetime.datetime(2026, 1, 30, 18, 20, tzinfo=datetime.timezone.utc)
    saved = os.environ.get("TZ")
    try:
        for tz, day in (("Asia/Shanghai", 31), ("America/Los_Angeles", 30)):
            os.environ["TZ"] = tz
            time.tzset()
            with mock.patch.object(clock, "now", return_value=pin):
                got = (clock.host_today(), clock.today(), clock.utc_stamp())
            check(f"{tz}: host_today() is the pinned instant's local day (01-{day}); "
                  "today() and utc_stamp() stay UTC",
                  got == (datetime.date(2026, 1, day), datetime.date(2026, 1, 30),
                          "2026-01-30T18:20:00Z"), got)
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()
    return finish()


if __name__ == "__main__":
    sys.exit(main())
