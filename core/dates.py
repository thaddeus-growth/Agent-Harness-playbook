"""The one clock: the only module that reads the calendar.

Every other module calls `dates.now()`, `dates.today()` or `dates.host_today()`
through the module, so one patch of `dates.now` pins every read: a test's
`mock.patch.object(dates, "now", return_value=AT)`, or the golden diff's one
instant. `tests/test_clock.py` (the playbook's test kit) keeps every other
calendar read out of the code.

- `now()`: this instant, aware UTC.
- `today()`: now()'s day in UTC, the day reports and freshness count in.
- `host_today()`: now()'s day on this host (`TZ`, else the system's). Named
  apart on purpose: a host off UTC is a day off from `today()` for part of
  every day. Use it only where a platform counts days in the operator's zone,
  and say so where it is used.
- `utc_stamp()`: the one stamp format, UTC to the second (`2026-01-31T23:05:09Z`).

Call through the module: `from dates import now` binds the function at import,
and a later patch of the module no longer reaches that name.
`time.time()` and `time.monotonic()` measure durations, not the calendar.
"""

from __future__ import annotations

import datetime


def now() -> datetime.datetime:
    """This instant, aware UTC: the read every other one goes through."""
    return datetime.datetime.now(datetime.timezone.utc)


def today() -> datetime.date:
    """Today's UTC date: now()'s day."""
    return now().date()


def host_today() -> datetime.date:
    """Today on this host: now()'s day in the local time zone."""
    return now().astimezone().date()


def utc_stamp(at: datetime.datetime | None = None) -> str:
    """`at` (default now()) as UTC ISO-8601 to the second. A naive `at` is
    refused: its zone would be guessed from the host."""
    at = now() if at is None else at
    if at.tzinfo is None:
        raise ValueError("utc_stamp needs an aware datetime")
    return at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
