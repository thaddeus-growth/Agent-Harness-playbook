#!/usr/bin/env python3
"""kit.dates: one clock, and the calendar helpers.

  * patching kit.dates.now pins today(), host_today(), market_day() and
    utc_stamp();
  * market_day() is the market's own day (a pinned instant that is
    already tomorrow in Tokyo and still today in Los Angeles);
  * ranges() folds dates or ISO strings, any order, duplicates once;
  * ISO weeks, calendar months, the one UTC stamp format.
"""

import datetime as dt
import os
import sys
import time
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit import dates  # noqa: E402
from kit.testing.check import check, finish  # noqa: E402

D = dt.date


def main() -> int:
    print("[1] one clock")
    n = dates.now()
    check("now() is aware UTC", n.tzinfo is not None
          and n.utcoffset() == dt.timedelta(0))
    pin = dt.datetime(2026, 9, 28, 16, 30, 5, tzinfo=dt.timezone.utc)
    with mock.patch("kit.dates.now", return_value=pin):
        check("today() follows the patched now", dates.today() == D(2026, 9, 28))
        check("utc_stamp() follows it", dates.utc_stamp() == "2026-09-28T16:30:05Z")
        check("market_day: already tomorrow in Tokyo (UTC+9)",
              dates.market_day("Asia/Tokyo") == D(2026, 9, 29))
        check("market_day: still today in Los Angeles",
              dates.market_day("America/Los_Angeles") == D(2026, 9, 28))
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        try:
            check("host_today() is the host's day (TZ=Asia/Tokyo)",
                  dates.host_today() == D(2026, 9, 29))
        finally:
            if saved is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = saved
            time.tzset()
    check("utc_stamp(epoch)", dates.utc_stamp(0) == "1970-01-01T00:00:00Z")

    print("\n[2] ranges()")
    check("runs fold, singles stay",
          dates.ranges([D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3),
                        D(2026, 9, 9)]) == "2026-09-01..2026-09-03, 2026-09-09")
    check("ISO strings, any order, duplicates once",
          dates.ranges(["2026-09-09", "2026-09-02", "2026-09-01",
                        "2026-09-02"]) == "2026-09-01..2026-09-02, 2026-09-09")
    check("empty -> ''", dates.ranges([]) == "")
    check("across a month end",
          dates.ranges(["2026-08-31", "2026-09-01"]) == "2026-08-31..2026-09-01")

    print("\n[3] weeks and months")
    check("iso_week of a date and of a str",
          dates.iso_week(D(2026, 9, 1)) == dates.iso_week("2026-09-01")
          == "2026-W36")
    check("iso_week at a year boundary", dates.iso_week("2027-01-01")
          == "2026-W53")
    check("week_monday inverts iso_week",
          dates.week_monday("2026-W36") == D(2026, 8, 31))
    check("consecutive_weeks", dates.consecutive_weeks(
        ["2026-W52", "2026-W53", "2027-W01"])
          and not dates.consecutive_weeks(["2026-W36", "2026-W38"]))
    check("month_add across years, both ways",
          dates.month_add("2026-12", 1) == "2027-01"
          and dates.month_add("2026-01", -1) == "2025-12"
          and dates.month_add("2026-09", 0) == "2026-09")
    check("month_days (leap February)",
          dates.month_days("2028-02") == 29 and dates.month_days("2026-02")
          == 28 and dates.month_days("2026-09") == 30)
    check("month_end", dates.month_end(D(2026, 2, 1)) == D(2026, 2, 28)
          and dates.month_end(D(2026, 12, 1)) == D(2026, 12, 31))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
