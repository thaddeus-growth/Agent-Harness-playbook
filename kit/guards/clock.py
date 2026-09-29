"""One clock: kit/dates.py's now() is the only calendar read in a harness.

Every other module reads the time as `dates.now()`, `dates.today()`,
`dates.host_today()` or `dates.market_day()`, so one patch of
`kit.dates.now` (a test's, or a golden diff's one instant) reaches every
read. `check_reads(root)` returns every problem of these rules, by AST, on
every .py under the harness's code folders except the vendored kit:

  1. no `.now`, `.today` or `.utcnow` of the stdlib `datetime.datetime` or
     `datetime.date`, however they were imported (`import datetime as dt`,
     `from datetime import datetime as DT`), called or not;
  2. no `time.localtime()`, `gmtime()`, `ctime()`, `asctime()` or
     `strftime(fmt)` without a time value: those read the calendar too.
     `time.time()` and `time.monotonic()` measure durations and are
     allowed;
  3. no `from kit.dates import now | today | host_today | market_day`
     (or a relative form): a name bound at import keeps the function a
     later patch of the module no longer reaches.

The clock itself (`<scripts_dir>/kit/dates.py`) must read the stdlib clock
once, in `now()`; the vendored kit around it is held byte for byte by the
drift guard and, in the playbook, by kit/tests/test_clock.py.

harness.toml `[guards.clock]`, all optional:

  code     folders of code to lint (default: the scripts dir)
  skip     folders left out (default: the vendored kit)
  clock    the one module that reads the calendar
  allowed  reads not yet moved, "path::function" = count, for a harness
           that adopts the kit late. It must match exactly: a read that
           moved fails here until its row goes too, so the list only
           shrinks. A new harness starts with none.

`self_test()` scans a planted file with every forbidden form, so the rule
cannot pass by looking at nothing.

Test: kit/tests/test_guards.py, kit/tests/test_clock.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kit.config import HarnessConfig
from kit.guards import harness, report, section, strs

CLASSES = {"datetime.datetime", "datetime.date"}
READS = {"now", "today", "utcnow"}
TIME_READS = {"time.localtime": 1, "time.gmtime": 1, "time.ctime": 1,
              "time.asctime": 1, "time.strftime": 2}   # reads now with fewer args
BOUND = {"now", "today", "host_today", "market_day"}

Hit = tuple[str, int, str]         # (enclosing function, line, what)


class Scan(ast.NodeVisitor):
    """The rule's hits in one module."""

    def __init__(self) -> None:
        self.alias: dict[str, set[str]] = {}    # local name -> stdlib names
        self.stack: list[str] = []
        self.hits: list[Hit] = []

    def run(self, tree: ast.AST) -> list[Hit]:
        for node in ast.walk(tree):              # every binding, any scope
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in ("datetime", "time"):
                        self.alias.setdefault(a.asname or a.name,
                                              set()).add(a.name)
            elif (isinstance(node, ast.ImportFrom) and not node.level
                  and node.module in ("datetime", "time")):
                for a in node.names:
                    self.alias.setdefault(a.asname or a.name, set()).add(
                        f"{node.module}.{a.name}")
        self.visit(tree)
        return self.hits

    def qual(self, node: ast.AST) -> set[str]:
        """The stdlib names an expression like `dt.datetime.now` stands for."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return set()
        return {".".join([q, *parts]) for q in self.alias.get(node.id, ())}

    def _hit(self, node: ast.AST, what: str) -> None:
        self.hits.append((".".join(self.stack) or "<module>", node.lineno,
                          what))

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
        if any(len(node.args) < TIME_READS.get(q, 0)
               for q in self.qual(node.func)):
            self._hit(node, ast.unparse(node))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (node.module or "").rpartition(".")[2] == "dates":
            for a in node.names:
                if a.name in BOUND:
                    self._hit(node, f"from {'.' * node.level}{node.module} "
                                    f"import {a.name}")


def scan(source: str) -> list[Hit]:
    """The forbidden calendar reads in one module's source."""
    return Scan().run(ast.parse(source))


def settings(cfg: HarnessConfig) -> tuple[tuple[str, ...], tuple[str, ...],
                                          str, dict[str, int]]:
    """(code folders, skipped folders, the clock module, allowed reads)."""
    g = section(cfg, "guards", "clock")
    scripts = cfg.scripts_dir.strip("/")
    code = strs(g.get("code"), [scripts])
    skip = strs(g.get("skip"), [f"{scripts}/kit"])
    clock = str(g.get("clock", f"{scripts}/kit/dates.py"))
    allowed = {str(k): int(v)
               for k, v in section(cfg, "guards", "clock", "allowed").items()}
    return code, skip, clock, allowed


def modules(root: Path, code: tuple[str, ...], skip: tuple[str, ...]
            ) -> list[str]:
    """The .py files under `code` (harness-relative, sorted), `skip`
    folders left out."""
    out = []
    for d in code:
        for p in sorted((root / d).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if "__pycache__" in p.parts or any(
                    rel == s or rel.startswith(s.rstrip("/") + "/")
                    for s in skip):
                continue
            out.append(rel)
    return out


def check_reads(root: Path | str | None = None) -> list[str]:
    cfg = harness(root)
    base = Path(root) if root is not None else cfg.root
    code, skip, clock, allowed = settings(cfg)
    files = modules(base, code, skip)
    problems: list[str] = []
    if not files:
        problems.append(f"no code to read under {', '.join(code)}: a wrong "
                        f"path is never a pass")
    found: dict[tuple[str, str], list[str]] = {}
    for rel in files:
        for q, n, w in scan((base / rel).read_text(encoding="utf-8")):
            found.setdefault((rel, q), []).append(f"{rel}:{n} ({q}) {w}")
    for key, sites in sorted(found.items()):
        left = sites[allowed.get(f"{key[0]}::{key[1]}", 0):]
        problems += [f"{s}: read dates.now() / .today() / .host_today() "
                     f"instead" for s in left]
    for k, n in sorted(allowed.items()):
        f, _, q = k.partition("::")
        have = len(found.get((f, q), []))
        if have < n:
            problems.append(f"[guards.clock.allowed] {k}: lists {n}, "
                            f"{have} left ({n - have} moved): drop or lower "
                            f"the row")
    cpath = base / clock
    if not cpath.is_file():
        problems.append(f"the clock {clock} is missing")
    else:
        got = [(q, w) for q, _, w in scan(cpath.read_text(encoding="utf-8"))]
        if got != [("now", "datetime.datetime.now")]:
            problems.append(f"{clock} must read the stdlib clock once, in "
                            f"now(); it reads {got}")
    return problems


_PLANTED = ("import datetime\nimport datetime as dt\nimport time\n"
            "from datetime import datetime as DT, date\n"
            "from kit.dates import now\n"
            "def f():\n"
            "    a = datetime.datetime.now()\n"
            "    b = dt.date.today\n"
            "    c = DT.utcnow()\n"
            "    d = date.today()\n"
            "    e = time.strftime('%Y')\n"
            "    g = time.time() + time.monotonic()\n"
            "    h = time.strftime('%Y', time.gmtime(0))\n"
            "    i = DT.fromisoformat('2026-01-31')\n"
            "    return a\n")


def self_test() -> list[str]:
    """The rule tried on a planted module: [] when every forbidden form is
    caught and the allowed ones are not."""
    got = scan(_PLANTED)
    want = [("<module>", 5), ("f", 7), ("f", 8), ("f", 9), ("f", 10),
            ("f", 11)]
    out = []
    if [(q, n) for q, n, _ in got] != want:
        out.append(f"planted forms: expected lines {want}, the scan found "
                   f"{[(q, n) for q, n, _ in got]}")
    if scan("from .dates import today\n") == []:
        out.append("a relative `from .dates import today` is not caught")
    if any(w.startswith("time.time") or "gmtime(0)" in w
           or "fromisoformat" in w for _, _, w in got):
        out.append("time.time / time.monotonic / strftime of a given time / "
                   "fromisoformat were flagged")
    return out


def check_clock(root: Path | str | None = None) -> bool:
    """self_test() + check_reads() as kit.testing.check lines."""
    ok = report("clock: the rule catches each planted form", self_test())
    return report("clock: the calendar is read only through kit.dates",
                  check_reads(root)) and ok
