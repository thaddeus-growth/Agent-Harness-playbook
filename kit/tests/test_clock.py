#!/usr/bin/env python3
"""One clock: kit/dates.py's now() is the only calendar read in the kit.

Every other kit module reads the time as `dates.now()` / `dates.today()`
/ `dates.host_today()` / `dates.market_day()`, so one patch of
`kit.dates.now` reaches every read. The rule, by AST, on every
kit/**/*.py except kit/dates.py and kit/tests/:

  1. no `.now`, `.today` or `.utcnow` of the stdlib `datetime.datetime`
     or `datetime.date` class, however it was imported;
  2. no `time.localtime()`, `gmtime()`, `ctime()`, `asctime()` or
     `strftime(fmt)` without a time value;
  3. no `from kit.dates import now | today | host_today | market_day`:
     a name bound at import keeps the function a patch no longer reaches.

The rule is kit.guards.clock, the one a generated harness's
tests/test_clock.py runs on its own code.

`time.time()` / `time.monotonic()` measure durations and code lifetimes
(the gate's code slots), not the calendar: not flagged. The planted
file proves the scan finds each form.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.guards import clock  # noqa: E402
from kit.testing.check import check, finish  # noqa: E402


def hits(path: Path) -> list[str]:
    return [f"{path.name}:{n}: {w}" for _, n, w in
            clock.scan(path.read_text(encoding="utf-8"))]


def main() -> int:
    problems = clock.self_test()
    check("the scan finds every planted form (and not time.time)",
          not problems, problems)
    check("the guard is the same rule a generated harness runs "
          "(kit.guards.clock: datetime, time.strftime/localtime, bound names)",
          len(clock.scan("from kit.dates import now\n")) == 1
          and len(clock.scan("import time\ntime.localtime()\n")) == 1)
    d = _shop.KIT / "dates.py"
    check("kit/dates.py reads the stdlib clock once, in now()",
          [(q, w) for q, _, w in clock.scan(d.read_text(encoding="utf-8"))]
          == [("now", "datetime.datetime.now")])

    files = sorted(p for p in _shop.KIT.rglob("*.py")
                   if "tests" not in p.relative_to(_shop.KIT).parts
                   and p != d)
    bad = [h for f in files for h in hits(f)]
    check(f"no calendar read outside kit/dates.py ({len(files)} modules)",
          files and not bad, bad)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
