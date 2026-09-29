"""The sample harness's one clock: the only module that reads the calendar.

Everything else calls `dates.now()`, `dates.today()` or `dates.host_today()`
through the module, so one patch of `dates.now` pins every read.
"""

from __future__ import annotations

import datetime


def now() -> datetime.datetime:
    """This instant, aware UTC."""
    return datetime.datetime.now(datetime.timezone.utc)


def today() -> datetime.date:
    """Today's UTC date: now()'s day."""
    return now().date()


def host_today() -> datetime.date:
    """Today on this host: now()'s day in the local time zone."""
    return now().astimezone().date()


def utc_stamp(at: datetime.datetime | None = None) -> str:
    """`at` (default now()) as UTC ISO-8601 to the second; a naive `at` is refused."""
    at = now() if at is None else at
    if at.tzinfo is None:
        raise ValueError("utc_stamp needs an aware datetime")
    return at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
