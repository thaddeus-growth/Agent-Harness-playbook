"""The one retry policy for READ calls to an external API.

`retry(fn, max_retries=N)` calls `fn()` (one HTTP read; it returns a
response object with `.status` and `.headers`) until the answer is not a
throttle, at most N + 1 times:

  * throttled = `is_throttled(resp)`; default: 429 or 5xx. An API that
    signals throttling some other way (a 400 with an error code in the
    body, a usage header) passes its own hook.
  * the wait = `wait_hint(resp, attempt, backoff)` when it names one
    (default: the `Retry-After` header, in seconds), else exponential
    backoff doubling from `backoff`; always capped at `max_backoff`.
    `rate_limit_hint(header)` is the hint for an API that publishes its
    bucket's refill rate (requests/s): Retry-After first, else the wait is
    floored at 1/rate, the bucket's steady-state refill period, so a slow
    bucket is not hammered until `max_retries` runs out.
  * a transport error (`transient`: OSError, http.client.HTTPException)
    is retried with exponential backoff, then re-raised. An HTTP error
    raised as an exception with a `.status` (urllib's HTTPError) is judged
    like a response: retried only when throttled, else re-raised at once.
  * retries exhausted: the final response is returned (a caller reads its
    status), never None, never a loop.

READS ONLY. A retried write can happen twice; the write path
(kit.write_guard) never imports this module (its test proves that).

Stdlib only, no harness needed. Test: kit/tests/test_retry.py.
"""

from __future__ import annotations

import http.client
import math
import time
from collections.abc import Mapping
from typing import Any, Callable

MAX_BACKOFF = 60.0
TRANSIENT: tuple[type[BaseException], ...] = (OSError,
                                              http.client.HTTPException)

WaitHint = Callable[[Any, int, float], "float | None"]


def status(resp: Any) -> int | None:
    """`.status` of a response (or of an HTTP error exception, whose older
    spelling is `.code`)."""
    s = getattr(resp, "status", None)
    if s is None:
        s = getattr(resp, "code", None)
    return s if isinstance(s, int) and not isinstance(s, bool) else None


def header(resp: Any, name: str) -> str | None:
    """A response header, case-insensitively, from `.headers` (a Mapping
    or an email.message.Message); None when absent."""
    h = getattr(resp, "headers", None)
    if h is None:
        return None
    v = h.get(name)
    if v is None and isinstance(h, Mapping):
        low = name.lower()
        v = next((val for k, val in h.items()
                  if isinstance(k, str) and k.lower() == low), None)
    return None if v is None else str(v)


def throttled(resp: Any) -> bool:
    """The default throttle test: HTTP 429 or any 5xx."""
    s = status(resp)
    return s is not None and (s == 429 or 500 <= s < 600)


def _seconds(v: str | None) -> float | None:
    try:
        w = float(v) if v is not None else None
    except ValueError:
        return None
    return w if w is not None and math.isfinite(w) and w >= 0 else None


def retry_after(resp: Any, attempt: int, backoff: float) -> float | None:
    """The default wait hint: `Retry-After` in seconds; None when absent
    or not a number (the HTTP-date form falls back to backoff)."""
    return _seconds(header(resp, "Retry-After"))


def rate_limit_hint(limit_header: str) -> WaitHint:
    """A wait hint for an API whose `limit_header` carries the requests/s
    its bucket allows: Retry-After wins; else max(exponential guess,
    1 / rate), so the wait is never shorter than the refill period and
    never shorter than the plain curve."""

    def hint(resp: Any, attempt: int, backoff: float) -> float | None:
        ra = retry_after(resp, attempt, backoff)
        if ra is not None:
            return ra
        rate = _seconds(header(resp, limit_header))
        if rate:
            return max(backoff * (2 ** attempt), 1.0 / rate)
        return None
    return hint


def retry(fn: Callable[[], Any], *, max_retries: int, backoff: float = 2.0,
          max_backoff: float = MAX_BACKOFF,
          is_throttled: Callable[[Any], bool] = throttled,
          wait_hint: WaitHint = retry_after,
          transient: tuple[type[BaseException], ...] = TRANSIENT,
          sleep: Callable[[float], None] | None = None) -> Any:
    """fn()'s first non-throttled response, or the last one once
    `max_retries` retries are spent (see the module docstring)."""
    if max_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {max_retries}")
    pause = sleep or time.sleep

    def wait(r: Any, attempt: int) -> float:
        w = wait_hint(r, attempt, backoff)
        if w is None or not math.isfinite(w) or w < 0:
            w = backoff * (2 ** attempt)
        return min(w, max_backoff)

    for attempt in range(max_retries + 1):
        last = attempt == max_retries
        try:
            resp = fn()
        except transient as e:
            if status(e) is not None and getattr(e, "headers", None) is not None:
                if last or not is_throttled(e):
                    raise
                pause(wait(e, attempt))
                continue
            if last:
                raise
            pause(min(backoff * (2 ** attempt), max_backoff))
            continue
        if last or not is_throttled(resp):
            return resp
        pause(wait(resp, attempt))
    raise AssertionError("unreachable")   # the loop always returns or raises
