"""Raw files and the pulls that write them. Stdlib-only, Python 3.11+, POSIX
(file locks). A puller imports this module and never the database: pull
writes raw, ingest reads raw into the database, and raw is the one thing a
rebuild refills from, so it only grows.

    open_atomic, write_json_atomic   a reader never sees half a file
    merge_rows, accumulate           each pull merged into raw by natural key
    hold                             one run at a time; an overlapping run skips
    RetryPolicy, call, GaveUp        waits floored at the quota's refill, a budget per endpoint
    ChunkPlan, chunk_windows         a long window in chunks, saved after each step
    record_gap, record_filled,       what could not be pulled and why, in an
    open_gaps                        append-only ledger

The rules, each learned from a bug in the source project:

  - A raw file is written to `<path>.tmp`, flushed to disk, then renamed over
    `<path>`: a crash or a full disk leaves the old file, never half a new one.
  - A pull is merged, never saved over the last one: rows are keyed by their
    natural key, the newer pull wins per key, and days outside this pull's
    window stay. Each row carries its own `pulledAt`, so an old row is never
    relabelled by a new pull, and an older pull that lands late (a resumed
    report) never overwrites a newer row. A key this pull no longer returns
    stays: raw never shrinks. Two rows of one pull with the same key are
    refused: the key is wrong, and merging would silently drop one.
  - A raw file that cannot be read is moved aside to
    `<path>.unreadable-<stamp>`, never overwritten; the pull starts a new one.
  - A scheduled run takes a non-blocking lock first. An overlapping run finds
    it taken, prints "skipped" and exits 0; the kernel drops the lock however
    the holder dies, and the lock file stays so two runs never lock two files.
  - A throttled call waits at least the platform's quota refill period
    (`RetryPolicy.refill_s`, or what `refill_from` reads from the reply's
    headers), even when the exponential guess is shorter or the cap lower;
    waiting less only spends the next try on another refusal. Each endpoint
    has its own budget of tries, and a run may share one deadline across
    calls. When the budget is spent, `GaveUp` carries the last reason
    ("HTTP 429"): record it as a gap, never as a day with no data.
  - A long window is cut into chunks the platform accepts, and the plan
    (each chunk's window, request id and status) is saved after every step,
    so a killed job resumes the chunks not yet saved. If a newer pull landed
    since, a resumed chunk is requested again, never polled: an old report
    would overwrite newer rows.
  - Every gap goes into an append-only ledger with its reason, and a later
    pull that gets the day appends that it was filled. `open_gaps` is what a
    gaps-only pull asks for again; no run clears the list the next one needs.

    with hold(os.path.join(data_dir, "pull.lock")) as mine:
        if not mine:
            print("skipped: another run holds the lock")
            sys.exit(0)
        rows = pull(...)
        accumulate(raw_path, rows, key=("day", "entity_id"), pulled_at=utc_stamp())
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, TextIO


def utc_stamp(t: float | None = None) -> str:
    """`2026-01-31T09:00:00Z`: UTC in one format, so stamps compare as strings."""
    when = time.time() if t is None else t
    return datetime.datetime.fromtimestamp(when, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


# -------------------------------------------------------------- atomic write --

@contextmanager
def open_atomic(path: str) -> Iterator[TextIO]:
    """Write text to `path` so no reader ever sees a partial file: the text
    goes to `<path>.tmp`, is flushed to disk, and replaces `path` only when
    the block ends without an error. On an error the temp file is removed and
    `path` is untouched. One writer per path (see `hold`)."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            yield f
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, path)


def write_json_atomic(path: str, obj: Any, *, indent: int | None = None) -> None:
    with open_atomic(path) as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------- merge rows --

def _field(row: Any, name: str) -> Any:
    """`row[name]`; a dotted name reaches into nested objects; None where a level is missing."""
    for part in name.split("."):
        row = row.get(part) if isinstance(row, dict) else None
    return row


def merge_rows(old: list[dict], new: list[dict], key: tuple[str, ...], pulled_at: str) -> list[dict]:
    """`old` rows plus this pull's `new` ones, merged on `key` (field names,
    dotted for a nested one) and sorted by it. Each new row is stamped
    `pulledAt` = `pulled_at`. A new row replaces the old row with its key
    unless that one was pulled later. Old rows whose key this pull did not
    return are kept. Raises ValueError when two new rows share a key."""
    def k(r: dict) -> tuple[str, ...]:
        return tuple(str(_field(r, f)) for f in key)
    fresh: dict[tuple[str, ...], dict] = {}
    for r in new:
        if (kr := k(r)) in fresh:
            raise ValueError(f"two rows of one pull share the key {dict(zip(key, kr))}: that is "
                             f"not the natural key, and merging would drop one of them")
        fresh[kr] = {**r, "pulledAt": pulled_at}
    newer = {k(r) for r in old if k(r) in fresh and str(r.get("pulledAt") or "") > pulled_at}
    kept = [r for r in old if k(r) not in fresh or k(r) in newer]
    return sorted(kept + [r for kr, r in fresh.items() if kr not in newer], key=k)


def _move_aside(path: str, pulled_at: str, why: str) -> str:
    base = f"{path}.unreadable-{pulled_at.replace(':', '')}"
    aside, n = base, 1
    while os.path.exists(aside):                  # never over an earlier one
        n += 1
        aside = f"{base}.{n}"
    os.replace(path, aside)
    _log(f"[warn] {path}: unreadable ({why}); moved aside to {aside}, starting a new file")
    return aside


def accumulate(path: str, rows: list[dict], key: tuple[str, ...], pulled_at: str,
               *, list_key: str = "rows", **meta: Any) -> list[dict]:
    """Merge this pull's `rows` into the JSON file at `path`
    (`{..meta, "pulledOn", "rowCount", list_key: [...]}`) with `merge_rows`,
    and write it back atomically. Returns the rows written.

    A row already there without its own `pulledAt` (raw written before rows
    carried one) gets the file's old `pulledOn`, else its mtime, so this
    pull's stamp never relabels it. A file that cannot be read as that shape
    is moved aside, never overwritten."""
    old: list[dict] = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            old = payload[list_key]
            if not isinstance(old, list) or not all(isinstance(r, dict) for r in old):
                raise ValueError(f"{list_key!r} is not a list of objects")
        except (OSError, ValueError, KeyError, TypeError) as e:
            _move_aside(path, pulled_at, repr(e))
            old = []
        else:
            was = payload.get("pulledOn") or utc_stamp(os.path.getmtime(path))
            old = [r if r.get("pulledAt") else {**r, "pulledAt": was} for r in old]
    merged = merge_rows(old, rows, key, pulled_at)
    write_json_atomic(path, {**meta, "pulledOn": pulled_at, "rowCount": len(merged), list_key: merged})
    return merged


# ------------------------------------------------------------ single instance --

@contextmanager
def hold(path: str) -> Iterator[bool]:
    """Yield True while this process holds the lock on `path`, or False at once
    (no waiting) when another process holds it. The kernel releases it when
    the holder's file closes or its process dies, however it dies. The lock
    file is left in place: deleting it would let two runs lock two files."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True                                   # closing f releases it


# --------------------------------------------------------------------- retry --

class GaveUp(Exception):
    """An endpoint's retry budget or the run's deadline ran out. `reason` is
    what the last try got ("HTTP 429", "TimeoutError: ..."): record it as a
    gap with `record_gap`."""

    def __init__(self, endpoint: str, reason: str, tries: int):
        super().__init__(f"{endpoint}: gave up after {tries} tr{'y' if tries == 1 else 'ies'}: {reason}")
        self.endpoint, self.reason, self.tries = endpoint, reason, tries


@dataclass(frozen=True)
class RetryPolicy:
    """How one endpoint is retried. Give each endpoint its own: a report
    create whose quota refills in minutes needs another budget than a list
    call. `refill_s` is the platform's refill period for this endpoint's quota
    (its docs; verify per platform); `refill_from` may read a longer one from
    a reply's headers. No wait is shorter than either, whatever `cap_s` says;
    only `Retry-After`, the server's own number, is taken as it is."""
    refill_s: float
    tries: int = 5                       # retries after the first call
    backoff_s: float = 2.0               # first exponential guess, doubled per retry
    cap_s: float = 300.0                 # the longest exponential guess
    retry_on: tuple[int, ...] = (429, 500, 502, 503, 504)
    transport: tuple[type[BaseException], ...] = (OSError,)
    refill_from: Callable[[Mapping[str, str]], float | None] | None = field(default=None, compare=False)


def _header(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    v = headers.get(name)
    if v is None and isinstance(headers, dict):
        v = next((val for k, val in headers.items() if k.lower() == name.lower()), None)
    return v


def wait_s(policy: RetryPolicy, retry: int, headers: Any) -> float:
    """Seconds to wait before retry number `retry` (0 first): `Retry-After`
    when the reply has one in seconds, else the exponential guess, capped,
    then raised to the refill period."""
    try:
        return max(0.0, float(_header(headers, "Retry-After")))
    except (TypeError, ValueError):
        pass
    floor = policy.refill_s
    if policy.refill_from is not None and headers:
        floor = max(floor, policy.refill_from(headers) or 0.0)
    return max(min(policy.backoff_s * 2 ** retry, policy.cap_s), floor)


def _status(resp: Any) -> int | None:
    status = getattr(resp, "status_code", None)
    return status if status is not None else getattr(resp, "status", None)


def call(fn: Callable[[], Any], policy: RetryPolicy, *, endpoint: str, deadline: float | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Any:
    """`fn()` until it answers with a status not in `policy.retry_on`, then
    that reply. A reply is anything with `status_code` or `status`, and
    `headers`; urllib's HTTPError counts as a reply. A transport error or a
    retryable status waits `wait_s` and tries again, at most `policy.tries`
    times. Raises `GaveUp` when the tries are spent, or before a wait that
    would pass `deadline` (a `clock()` value one run may share across calls)."""
    for retry in range(policy.tries + 1):
        try:
            resp = fn()
        except policy.transport as e:
            code = getattr(e, "code", None)
            if not isinstance(code, int):
                reason, headers = f"{type(e).__name__}: {e}", None
            elif code in policy.retry_on:        # urllib raises an HTTP answer
                reason, headers = f"HTTP {code}", getattr(e, "headers", None)
            else:
                raise
        else:
            if _status(resp) not in policy.retry_on:
                return resp
            reason, headers = f"HTTP {_status(resp)}", getattr(resp, "headers", None)
        if retry == policy.tries:
            raise GaveUp(endpoint, reason, retry + 1)
        wait = wait_s(policy, retry, headers)
        if deadline is not None and clock() + wait > deadline:
            raise GaveUp(endpoint, f"{reason}; waiting {wait:.0f}s more would pass the run's deadline",
                         retry + 1)
        _log(f"[wait] {endpoint} {reason}: waiting {wait:.0f}s")
        sleep(wait)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- chunk plan --

DONE = ("done", "skipped")                  # a chunk with nothing left to request


def chunk_windows(start: str, end: str, max_days: int) -> list[list[str]]:
    """`start..end` (ISO days, inclusive) as consecutive windows of at most
    `max_days`, oldest first: what the platform accepts per request."""
    d, last, out = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end), []
    while d <= last:
        stop = min(last, d + datetime.timedelta(days=max_days - 1))
        out.append([d.isoformat(), stop.isoformat()])
        d = stop + datetime.timedelta(days=1)
    return out


@dataclass
class ChunkPlan:
    """One long pull as chunks `{"window", "request_id", "status"}`, saved to
    `path` on start and after every `update`, so a killed job leaves a plan
    the next run resumes. Merge a chunk's rows with the `pulled_at` of the
    plan, the time its requests were made."""
    path: str
    window: list[str]
    pulled_at: str
    chunks: list[dict]

    @classmethod
    def start(cls, path: str, start: str, end: str, max_days: int, pulled_at: str) -> "ChunkPlan":
        plan = cls(path, [start, end], pulled_at,
                   [{"window": w, "request_id": None, "status": None}
                    for w in chunk_windows(start, end, max_days)])
        plan.save()
        return plan

    @classmethod
    def resume(cls, path: str, raw_pulled_on: str | None = None) -> "ChunkPlan | None":
        """The saved plan when a chunk of it is not done, else None. When raw
        was pulled after the plan (`raw_pulled_on` is later), the unfinished
        chunks lose their request ids: they are requested again, never polled."""
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            plan = cls(path, saved["window"], saved["pulled_at"], saved["chunks"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not plan.todo():
            return None
        if raw_pulled_on and raw_pulled_on > plan.pulled_at:
            for i in plan.todo():
                plan.chunks[i]["request_id"] = None
            plan.save()
        return plan

    def todo(self) -> list[int]:
        return [i for i, c in enumerate(self.chunks) if c.get("status") not in DONE]

    def update(self, i: int, **fields: Any) -> None:
        self.chunks[i].update(fields)
        self.save()

    def save(self) -> None:
        write_json_atomic(self.path, {"window": self.window, "pulled_at": self.pulled_at,
                                      "chunks": self.chunks}, indent=1)


# ---------------------------------------------------------------- gap ledger --

def _append(path: str, event: dict) -> dict:
    """One JSON line at the end of `path`, flushed to disk, under a lock. A
    torn last line (a writer killed mid-line) is cut off first: it was never
    a record."""
    line = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        if size:
            whole = os.pread(fd, size, 0).rfind(b"\n") + 1
            if whole < size:
                os.ftruncate(fd, whole)
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return event


def record_gap(path: str, *, endpoint: str, days: list[str], reason: str,
               fatal: bool = False, seen_at: str | None = None) -> dict:
    """Append what `endpoint` could not pull for `days` and why ("HTTP 429",
    "HTTP 403: no access"). `fatal`: the whole layer came back empty, so the
    run must exit non-zero rather than let an ingest treat it as complete."""
    return _append(path, {"event": "gap", "endpoint": endpoint, "days": sorted(days),
                          "reason": reason, "fatal": fatal, "seen_at": seen_at or utc_stamp()})


def record_filled(path: str, *, endpoint: str, days: list[str], seen_at: str | None = None) -> dict:
    """Append that a later pull of `endpoint` got `days` after all."""
    return _append(path, {"event": "filled", "endpoint": endpoint, "days": sorted(days),
                          "seen_at": seen_at or utc_stamp()})


def read_ledger(path: str) -> list[dict]:
    """Every event, oldest first. A torn last line is not an event; any other
    line that is not JSON raises ValueError: the ledger was edited."""
    try:
        with open(path, "rb") as f:
            lines = f.read().split(b"\n")
    except FileNotFoundError:
        return []
    lines.pop()                                 # b"" after the last newline, or a torn line
    out = []
    for n, ln in enumerate(lines, 1):
        try:
            out.append(json.loads(ln))
        except ValueError:
            raise ValueError(f"{path}:{n} is not a JSON event: the ledger is append-only") from None
    return out


def open_gaps(path: str) -> dict[str, dict[str, str]]:
    """{endpoint: {day: reason}}: each day whose last event is a gap. This is
    what a gaps-only pull asks for again."""
    out: dict[str, dict[str, str]] = {}
    for e in read_ledger(path):
        days = out.setdefault(e["endpoint"], {})
        for d in e["days"]:
            if e["event"] == "gap":
                days[d] = e["reason"]
            else:
                days.pop(d, None)
    return {ep: dict(sorted(days.items())) for ep, days in out.items() if days}
