#!/usr/bin/env python3
"""One clock: kit/dates.py's now() is the only calendar read in the kit.

Every other kit module reads the time as `dates.now()` / `dates.today()`
/ `dates.host_today()` / `dates.market_day()`, so one patch of
`kit.dates.now` reaches every read. The rule, by AST, on every
kit/**/*.py except kit/dates.py and kit/tests/:

  1. no `.now`, `.today` or `.utcnow` of the stdlib `datetime.datetime`
     or `datetime.date` class, however it was imported;
  2. no `from kit.dates import now | today | host_today | market_day`:
     a name bound at import keeps the function a patch no longer reaches.

`time.time()` / `time.monotonic()` measure durations and code lifetimes
(the gate's code slots), not the calendar: not flagged. The planted
file proves the scan finds each form.
"""

import ast
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402

READS = {"now", "today", "utcnow"}
BOUND = {"now", "today", "host_today", "market_day"}


def hits(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    mod_alias: set[str] = set()      # names bound to the datetime module
    cls_alias: set[str] = set()      # names bound to datetime.datetime/date
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "datetime":
                    mod_alias.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and not n.level:
            if n.module == "datetime":
                for a in n.names:
                    if a.name in ("datetime", "date"):
                        cls_alias.add(a.asname or a.name)
            if n.module == "kit.dates":
                for a in n.names:
                    if a.name in BOUND:
                        out.append(f"{path.name}:{n.lineno}: from kit.dates "
                                   f"import {a.name}")
        elif isinstance(n, ast.ImportFrom) and n.level and n.module == "dates":
            for a in n.names:
                if a.name in BOUND:
                    out.append(f"{path.name}:{n.lineno}: from .dates import "
                               f"{a.name}")
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Attribute) and n.attr in READS):
            continue
        v = n.value
        if isinstance(v, ast.Name) and v.id in cls_alias:
            out.append(f"{path.name}:{n.lineno}: {v.id}.{n.attr}")
        elif (isinstance(v, ast.Attribute) and v.attr in ("datetime", "date")
              and isinstance(v.value, ast.Name) and v.value.id in mod_alias):
            out.append(f"{path.name}:{n.lineno}: {v.value.id}.{v.attr}.{n.attr}")
    return out


def main() -> int:
    planted = Path(tmp_dir("clock-")) / "planted.py"
    planted.write_text(
        "import datetime\nimport datetime as dt\n"
        "from datetime import datetime as DT, date\n"
        "from kit.dates import now\n"
        "a = datetime.datetime.now()\n"
        "b = dt.date.today\n"
        "c = DT.utcnow()\n"
        "d = date.today()\n"
        "import time; e = time.time()\n", encoding="utf-8")
    found = hits(planted)
    check("the scan finds every planted form (and not time.time)",
          len(found) == 5 and not any("time.time" in f for f in found), found)

    files = sorted(p for p in _shop.KIT.rglob("*.py")
                   if "tests" not in p.relative_to(_shop.KIT).parts
                   and p != _shop.KIT / "dates.py")
    bad = [h for f in files for h in hits(f)]
    check(f"no calendar read outside kit/dates.py ({len(files)} modules)",
          files and not bad, bad)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
