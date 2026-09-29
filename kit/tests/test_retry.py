#!/usr/bin/env python3
"""kit.retry: the read-only retry policy (no call really waits: `sleep`
is recorded).

  * a non-throttled response returns at once, no sleep;
  * Retry-After wins (any header case; a Mapping or an email Message);
    an HTTP-date, negative or NaN Retry-After falls back to backoff;
  * rate_limit_hint(header) floors the wait at 1/rate, never below the
    exponential guess;
  * plain exponential backoff 2, 4, 8 …; max_backoff caps every wait and
    is a parameter;
  * retries exhausted: the final response is returned (no raise, no None);
  * a transport error (OSError, http.client.HTTPException) is retried,
    then re-raised; an HTTP error raised as an exception (urllib's
    HTTPError) is retried only when throttled, else re-raised at once;
    any other exception propagates at once;
  * the is_throttled / wait_hint hooks replace the defaults (an API that
    throttles with a 400 and an error code);
  * default throttled = 429 or 5xx; max_retries 0 = one call, < 0 refused;
  * the module binds no harness (stdlib only, no kit import).
"""

import ast
import email.message
import http.client
import io
import sys
import urllib.error
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit import retry as retry_mod  # noqa: E402
from kit.retry import MAX_BACKOFF, rate_limit_hint, retry  # noqa: E402
from kit.testing.check import check, finish, raises  # noqa: E402


class Resp:
    def __init__(self, status, headers=None, body=None):
        self.status = status
        self.headers = headers or {}
        self.body = body


def seq(*items):
    """fn() returning (or raising) each item in turn; .calls counts."""
    it = iter(items)

    def fn():
        fn.calls += 1
        x = next(it)
        if isinstance(x, BaseException):
            raise x
        return x
    fn.calls = 0
    return fn


def run(fn, **kw):
    """(result or raised exception, sleeps)."""
    sleeps = []
    try:
        out = retry(fn, sleep=sleeps.append, **kw)
    except Exception as e:           # noqa: BLE001 — the test inspects it
        out = e
    return out, sleeps


def http_error(code, headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    return urllib.error.HTTPError("http://x.example.com/", code, "err", h, io.BytesIO(b""))


def main() -> int:
    print("[1] no throttle: one call, no sleep")
    fn = seq(Resp(200))
    out, sleeps = run(fn, max_retries=5)
    check("200 returned as is", out.status == 200 and fn.calls == 1)
    check("no sleep", sleeps == [], sleeps)

    print("\n[2] Retry-After wins")
    out, sleeps = run(seq(Resp(429, {"Retry-After": "7"}), Resp(200)),
                      max_retries=3, backoff=2.0)
    check("slept exactly Retry-After (7 s), then 200",
          out.status == 200 and sleeps == [7.0], sleeps)
    out, sleeps = run(seq(Resp(503, {"retry-after": "3"}), Resp(200)),
                      max_retries=3)
    check("header name in any case (dict headers)", sleeps == [3.0], sleeps)
    msg_h = email.message.Message()
    msg_h["Retry-After"] = "4"
    out, sleeps = run(seq(Resp(429, msg_h), Resp(200)), max_retries=3)
    check("email.message.Message headers (urllib)", sleeps == [4.0], sleeps)
    for label, v in (("an HTTP-date", "Wed, 21 Oct 2026 07:28:00 GMT"),
                     ("a negative number", "-5"), ("NaN", "nan")):
        out, sleeps = run(seq(Resp(429, {"Retry-After": v}), Resp(200)),
                          max_retries=3, backoff=2.0)
        check(f"Retry-After {label}: exponential backoff instead",
              sleeps == [2.0], sleeps)

    print("\n[3] rate_limit_hint floors the wait at 1/rate")
    hint = rate_limit_hint("X-RateLimit-Limit")
    out, sleeps = run(seq(Resp(429, {"X-RateLimit-Limit": "0.05"}),
                          Resp(200)), max_retries=3, backoff=2.0,
                      wait_hint=hint)
    check("waited the refill period (20 s), not the 2 s guess",
          out.status == 200 and sleeps == [20.0], sleeps)
    out, sleeps = run(seq(Resp(429, {"X-RateLimit-Limit": "100"}),
                          Resp(200)), max_retries=3, backoff=2.0,
                      wait_hint=hint)
    check("a fast rate never shrinks the wait below the guess",
          sleeps == [2.0], sleeps)
    out, sleeps = run(seq(Resp(429, {"X-RateLimit-Limit": "0.05",
                                     "Retry-After": "1"}), Resp(200)),
                      max_retries=3, wait_hint=hint)
    check("Retry-After still wins over the rate", sleeps == [1.0], sleeps)
    out, sleeps = run(seq(Resp(429, {"X-RateLimit-Limit": "zero"}),
                          Resp(200)), max_retries=3, wait_hint=hint)
    check("an unparsable rate: plain backoff", sleeps == [2.0], sleeps)

    print("\n[4] exponential backoff, max_backoff")
    fn = seq(Resp(429), Resp(500), Resp(599), Resp(200))
    out, sleeps = run(fn, max_retries=5, backoff=2.0)
    check("doubles each attempt: 2, 4, 8", sleeps == [2.0, 4.0, 8.0]
          and out.status == 200 and fn.calls == 4, sleeps)
    slow = {"X-RateLimit-Limit": "0.00167"}
    out, sleeps = run(seq(Resp(429, slow), Resp(200)), max_retries=2,
                      wait_hint=hint, max_backoff=700.0)
    check("max_backoff raised: waits past 60 s", sleeps and sleeps[0] > 60,
          sleeps)
    out, sleeps = run(seq(Resp(429, slow), Resp(200)), max_retries=2,
                      wait_hint=hint)
    check("default max_backoff caps at 60 s", sleeps == [MAX_BACKOFF], sleeps)
    out, sleeps = run(seq(Resp(429, {"Retry-After": "3600"}), Resp(200)),
                      max_retries=2)
    check("a huge Retry-After is capped too", sleeps == [MAX_BACKOFF], sleeps)

    print("\n[5] exhausted: the final response")
    fn = seq(Resp(429), Resp(429), Resp(429), Resp(200))
    out, sleeps = run(fn, max_retries=2, backoff=0.01)
    check("the final 429 is returned, after max_retries + 1 calls",
          isinstance(out, Resp) and out.status == 429 and fn.calls == 3
          and len(sleeps) == 2, (out, fn.calls))
    fn = seq(Resp(503))
    out, sleeps = run(fn, max_retries=0)
    check("max_retries=0: one call, no sleep", out.status == 503
          and fn.calls == 1 and sleeps == [])
    e = raises(lambda: retry(seq(Resp(200)), max_retries=-1), ValueError)
    check("max_retries < 0 refused", e is not None)

    print("\n[6] transport errors")
    fn = seq(ConnectionError("boom"), TimeoutError("slow"),
             urllib.error.URLError("dns"))
    out, sleeps = run(fn, max_retries=2, backoff=1.0)
    check("OSError family retried with backoff, then re-raised",
          isinstance(out, urllib.error.URLError) and fn.calls == 3
          and sleeps == [1.0, 2.0], (out, sleeps))
    fn = seq(http.client.RemoteDisconnected("gone"), Resp(200))
    out, sleeps = run(fn, max_retries=2)
    check("http.client.HTTPException retried", out.status == 200
          and fn.calls == 2, out)
    fn = seq(ValueError("a bug"), Resp(200))
    out, sleeps = run(fn, max_retries=5)
    check("any other exception propagates at once",
          isinstance(out, ValueError) and fn.calls == 1 and sleeps == [])

    print("\n[7] an HTTP error raised as an exception (urllib)")
    fn = seq(http_error(429, {"Retry-After": "5"}), Resp(200))
    out, sleeps = run(fn, max_retries=3)
    check("HTTPError 429: throttled, Retry-After honoured, retried",
          out.status == 200 and sleeps == [5.0], sleeps)
    fn = seq(http_error(404), Resp(200))
    out, sleeps = run(fn, max_retries=3)
    check("HTTPError 404: re-raised at once, never retried",
          isinstance(out, urllib.error.HTTPError) and out.code == 404
          and fn.calls == 1 and sleeps == [], (out, fn.calls))
    fn = seq(http_error(503), http_error(503))
    out, sleeps = run(fn, max_retries=1)
    check("HTTPError 503 at exhaustion: re-raised",
          isinstance(out, urllib.error.HTTPError) and fn.calls == 2)

    print("\n[8] hooks")

    def graph_throttled(r):
        return r.status == 400 and (r.body or {}).get("code") in (4, 17)

    def usage_hint(r, attempt, backoff):
        return float(r.headers.get("x-regain-seconds", "nan"))
    fn = seq(Resp(400, {"x-regain-seconds": "12"}, {"code": 17}), Resp(200))
    out, sleeps = run(fn, max_retries=3, is_throttled=graph_throttled,
                      wait_hint=usage_hint)
    check("custom is_throttled + wait_hint: a 400 with code 17 waits 12 s",
          out.status == 200 and sleeps == [12.0], sleeps)
    fn = seq(Resp(400, {}, {"code": 17}), Resp(200))
    out, sleeps = run(fn, max_retries=3, is_throttled=graph_throttled,
                      wait_hint=usage_hint, backoff=3.0)
    check("a hint of NaN / None falls back to backoff", sleeps == [3.0],
          sleeps)
    fn = seq(Resp(400, {}, {"code": 17}), Resp(200))
    out, sleeps = run(fn, max_retries=3)
    check("the default hook does not retry a 400", out.status == 400
          and fn.calls == 1)
    t = retry_mod.throttled
    check("default throttled: 429, 500, 503, 599",
          all(t(Resp(s)) for s in (429, 500, 503, 599)))
    check("not throttled: 200, 400, 404, 600, no status",
          not any(t(Resp(s)) for s in (200, 400, 404, 600, None, True)))

    print("\n[9] the default sleep is time.sleep; no harness bound")
    slept = []
    with mock.patch.object(retry_mod.time, "sleep", slept.append):
        retry(seq(Resp(429, {"Retry-After": "2"}), Resp(200)), max_retries=1)
    check("sleep=None uses time.sleep", slept == [2.0], slept)
    tree = ast.parse(Path(retry_mod.__file__).read_text(encoding="utf-8"))
    kit_imports = [n.module for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom)
                   and (n.module or "").startswith("kit")] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
        for a in n.names if a.name.startswith("kit")]
    check("kit.retry imports nothing from the kit (stdlib only)",
          kit_imports == [], kit_imports)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
