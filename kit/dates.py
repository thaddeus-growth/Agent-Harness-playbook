"""The one clock and the small date helpers every harness layer shares.

`now()` is the one stdlib calendar read in the kit (kit/tests/
test_clock.py holds that rule on every kit module); `today()` is its UTC
day, `host_today()` its day on this host, `market_day(tz)` its day in a
market's own time zone. Patching `kit.dates.now` (a test, a golden run)
pins every read, the write-side stamps included. Call them through the
module (`dates.now()`): a `from kit.dates import now` binding keeps the
unpatched one. A confirm code's time slots (the human gate) count on
time.time(), not the calendar, so no patch here moves them.

Stdlib-only, no config: pullers import it.

Test: kit/tests/test_dates.py.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo


def now() -> datetime.datetime:
    """This instant, aware UTC: the clock every other read goes through."""
    return datetime.datetime.now(datetime.timezone.utc)


def today() -> datetime.date:
    """Today's UTC date: now()'s day."""
    return now().date()


def host_today() -> datetime.date:
    """Today on this host: now()'s day in the local time zone (TZ, else the
    system's)."""
    return now().astimezone().date()


def market_day(tz: str) -> datetime.date:
    """Today in a market's own time zone (an IANA name, e.g.
    "America/Toronto"): a market's "yesterday" is not the host's or UTC's
    when they sit hours apart."""
    return now().astimezone(ZoneInfo(tz)).date()


def _day(d: datetime.date | str) -> datetime.date:
    return d if isinstance(d, datetime.date) else datetime.date.fromisoformat(d)


def ranges(days) -> str:
    """[Sep1..Sep5, Sep9] -> '2026-09-01..2026-09-05, 2026-09-09'. Days may
    be dates or ISO strings, in any order; duplicates count once."""
    out: list[list[datetime.date]] = []
    for d in sorted({_day(x) for x in days}):
        if out and (d - out[-1][1]).days == 1:
            out[-1][1] = d
        else:
            out.append([d, d])
    return ", ".join(a.isoformat() if a == b else f"{a}..{b}" for a, b in out)


def iso_week(d: datetime.date | str) -> str:
    """The ISO-week key `2026-W36` of a day (week_monday's inverse)."""
    y, w, _ = _day(d).isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(week: str) -> datetime.date:
    """The Monday of an ISO-week key `2026-W36`."""
    y, w = week.split("-W")
    return datetime.date.fromisocalendar(int(y), int(w), 1)


def consecutive_weeks(weeks: list[str]) -> bool:
    """True when ISO-week keys are calendar-adjacent, in order: "N weeks in
    a row" means no week skipped between them."""
    mondays = [week_monday(k) for k in weeks]
    return all((b - a).days == 7 for a, b in zip(mondays, mondays[1:]))


def month_add(month: str, n: int) -> str:
    """The calendar month `2026-09` shifted by n months."""
    i = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_days(month: str) -> int:
    """How many days the calendar month `2026-09` has."""
    return (datetime.date.fromisoformat(month_add(month, 1) + "-01")
            - datetime.date.fromisoformat(month + "-01")).days


def month_end(first: datetime.date) -> datetime.date:
    """The last day of `first`'s calendar month."""
    return ((first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            - datetime.timedelta(days=1))


def utc_stamp(epoch: float | None = None) -> str:
    """The one pull-time format: UTC ISO-8601 to the second,
    `2026-09-22T03:16:23Z`. `epoch` (e.g. a file's mtime) defaults to now."""
    t = (now() if epoch is None else
         datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc))
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
