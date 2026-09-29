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

`call(fn, RetryPolicy(...), endpoint=...)` is the puller's variant, for a
quota that refills on a clock (a report-create call, a rate bucket): it
raises `GaveUp` instead of returning the last throttle, so a throttled day
is a recorded gap (kit.pull.record_gap), never a day with no data.

  * no wait is shorter than the quota's refill period (`refill_s`, or what
    `refill_from` reads from the reply's headers), even when the exponential
    guess is shorter or `cap_s` lower: waiting less only spends the next
    try on another refusal. Only `Retry-After`, the server's own number,
    is taken as it is;
  * each endpoint has its own `RetryPolicy` (its own budget of tries and
    refill period), and a run may share one `deadline` across calls: a wait
    that would pass it gives up at once;
  * `GaveUp` carries the endpoint, the LAST reason ("HTTP 429",
    "TimeoutError: ...") and the number of calls made; an HTTP status not
    in `retry_on` (a 400) comes back as the reply, or is raised again when
    it was an exception (urllib's HTTPError).

READS ONLY. A retried write can happen twice; the write path
(kit.write_guard) never imports this module (its test proves that).

Stdlib only, no harness needed, no other kit module. Test:
kit/tests/test_retry.py.
"""

from __future__ import annotations

import http.client
import math
import sys
import time
import types
from collections.abc import Mapping
from dataclasses import dataclass, field
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


# ---------------------------------------------------- call: a policy per endpoint --

class GaveUp(Exception):
    """An endpoint's retry budget or the run's deadline ran out. `reason` is
    what the last try got ("HTTP 429", "TimeoutError: ..."), `tries` how
    many calls were made: record it as a gap, never as a day with no data."""

    def __init__(self, endpoint: str, reason: str, tries: int):
        super().__init__(f"{endpoint}: gave up after {tries} "
                         f"tr{'y' if tries == 1 else 'ies'}: {reason}")
        self.endpoint, self.reason, self.tries = endpoint, reason, tries


@dataclass(frozen=True)
class RetryPolicy:
    """How one endpoint is retried. Give each endpoint its own: a report
    create whose quota refills in minutes needs another budget than a list
    call. `refill_s` is the platform's refill period for this endpoint's
    quota (its docs; verify per platform); `refill_from` may read a longer
    one from a reply's headers. No wait is shorter than either, whatever
    `cap_s` says; only `Retry-After`, the server's own number, is taken as
    it is. `tries` counts retries after the first call."""
    refill_s: float
    tries: int = 5
    backoff_s: float = 2.0               # first exponential guess, doubled per retry
    cap_s: float = 300.0                 # the longest exponential guess
    retry_on: tuple[int, ...] = (429, 500, 502, 503, 504)
    transport: tuple[type[BaseException], ...] = TRANSIENT
    refill_from: Callable[[Mapping[str, str]], float | None] | None = \
        field(default=None, compare=False)


def wait_s(policy: RetryPolicy, retry: int, headers: Any) -> float:
    """Seconds to wait before retry number `retry` (0 first): `Retry-After`
    when the reply has a usable one in seconds (a date, a negative number
    or NaN is not usable), else the exponential guess, capped, then raised
    to the refill period."""
    ra = _seconds(header(types.SimpleNamespace(headers=headers),
                         "Retry-After"))
    if ra is not None:
        return ra
    floor = policy.refill_s
    if policy.refill_from is not None and headers:
        floor = max(floor, policy.refill_from(headers) or 0.0)
    return max(min(policy.backoff_s * 2 ** retry, policy.cap_s), floor)


def _reply_status(resp: Any) -> int | None:
    s = getattr(resp, "status_code", None)     # requests-style, else .status
    return s if isinstance(s, int) and not isinstance(s, bool) else status(resp)


def call(fn: Callable[[], Any], policy: RetryPolicy, *, endpoint: str,
         deadline: float | None = None,
         sleep: Callable[[float], None] | None = None,
         clock: Callable[[], float] = time.monotonic) -> Any:
    """`fn()` until it answers with a status not in `policy.retry_on`, then
    that reply. A reply is anything with `status_code` or `status`, and
    `headers`; urllib's HTTPError counts as a reply. A transport error or a
    retryable status waits `wait_s` and tries again, at most `policy.tries`
    times. Raises `GaveUp` when the tries are spent, or before a wait that
    would pass `deadline` (a `clock()` value one run may share across
    calls)."""
    pause = sleep or time.sleep
    for n in range(policy.tries + 1):
        try:
            resp = fn()
        except policy.transport as e:
            code = status(e)
            if code is None:
                reason, headers = f"{type(e).__name__}: {e}", None
            elif code in policy.retry_on:        # urllib raises an HTTP answer
                reason, headers = f"HTTP {code}", getattr(e, "headers", None)
            else:
                raise
        else:
            code = _reply_status(resp)
            if code not in policy.retry_on:
                return resp
            reason, headers = f"HTTP {code}", getattr(resp, "headers", None)
        if n == policy.tries:
            raise GaveUp(endpoint, reason, n + 1)
        wait = wait_s(policy, n, headers)
        if deadline is not None and clock() + wait > deadline:
            raise GaveUp(endpoint, f"{reason}; waiting {wait:.0f}s more "
                         f"would pass the run's deadline", n + 1)
        print(f"[wait] {endpoint} {reason}: waiting {wait:.0f}s",
              file=sys.stderr, flush=True)
        pause(wait)
    raise AssertionError("unreachable")   # the loop always returns or raises
