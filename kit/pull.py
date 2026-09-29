"""What a puller does with a pull: merge it into raw, plan a long one in
chunks, and keep a ledger of what could not be pulled. Stdlib-only, POSIX
(file locks); it imports no harness, config or database. Pull writes raw,
ingest reads raw into the database, and raw is the one thing a rebuild
refills from, so it only grows (kit.raw is the write-once store for
files; this is the merged file a pull re-reads every day).

    merge_rows, accumulate      each pull merged into raw by natural key
    chunk_windows, ChunkPlan    a long window in chunks, saved after each step
    record_gap, record_filled,  what could not be pulled and why, in an
    read_ledger, open_gaps      append-only ledger

Its neighbours: kit.atomic (the write), kit.single_instance (one run at a
time), kit.retry.call (waits floored at the quota's refill; `GaveUp`
carries the reason to `record_gap`). Every stamp made here is
`dates.utc_stamp()`, and so is the caller's `pulled_at`: one format that
compares as a string, one clock a test or the golden diff pins by patching
`dates.now`.

The rules, each learned from a bug in the source project:

  - A pull is merged, never saved over the last one: rows are keyed by
    their natural key, the newer pull wins per key, and days outside this
    pull's window stay. Each row carries its own `pulledAt`, so an old row
    is never relabelled by a new pull, and an older pull that lands late (a
    resumed report) never overwrites a newer row. A key this pull no longer
    returns stays: raw never shrinks. Two rows of one pull with the same
    key are refused: the key is wrong, and merging would silently drop one.
  - A raw file that cannot be read is moved aside to
    `<path>.unreadable-<stamp>` (never over an earlier one), never
    overwritten; the pull starts a new one.
  - A long window is cut into chunks the platform accepts, and the plan
    (each chunk's window, request id and status) is saved after every step,
    so a killed job resumes the chunks not yet saved. If a newer pull
    landed since, a resumed chunk is requested again, never polled: an old
    report would overwrite newer rows.
  - Every gap goes into an append-only ledger with its reason, and a later
    pull that gets the day appends that it was filled. `open_gaps` is what
    a gaps-only pull asks for again; no run clears the list the next one
    needs. A torn last line (a writer killed mid-line) was never a record
    and is cut off by the next write; any other broken line raises.

    with single_instance.hold(os.path.join(data_dir, "pull.lock")) as mine:
        if not mine:
            print("skipped: another run holds the lock")
            sys.exit(0)
        rows = pull(...)
        pull_mod.accumulate(raw_path, rows, key=("day", "entity_id"),
                            pulled_at=dates.utc_stamp())

Test: kit/tests/test_pull.py.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from kit import dates
from kit.atomic import write_json_atomic


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- merge rows --

def _field(row: Any, name: str) -> Any:
    """`row[name]`; a dotted name reaches into nested objects; None where a
    level is missing."""
    for part in name.split("."):
        row = row.get(part) if isinstance(row, dict) else None
    return row


def merge_rows(old: list[dict], new: list[dict], key: tuple[str, ...],
               pulled_at: str) -> list[dict]:
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
            raise ValueError(
                f"two rows of one pull share the key {dict(zip(key, kr))}: "
                f"that is not the natural key, and merging would drop one "
                f"of them")
        fresh[kr] = {**r, "pulledAt": pulled_at}
    newer = {k(r) for r in old
             if k(r) in fresh and str(r.get("pulledAt") or "") > pulled_at}
    kept = [r for r in old if k(r) not in fresh or k(r) in newer]
    return sorted(kept + [r for kr, r in fresh.items() if kr not in newer],
                  key=k)


def _move_aside(path: str, pulled_at: str, why: str) -> str:
    base = f"{path}.unreadable-{pulled_at.replace(':', '')}"
    aside, n = base, 1
    while os.path.exists(aside):                  # never over an earlier one
        n += 1
        aside = f"{base}.{n}"
    os.replace(path, aside)
    _log(f"[warn] {path}: unreadable ({why}); moved aside to {aside}, "
         f"starting a new file")
    return aside


def accumulate(path: str, rows: list[dict], key: tuple[str, ...],
               pulled_at: str, *, list_key: str = "rows",
               **meta: Any) -> list[dict]:
    """Merge this pull's `rows` into the JSON file at `path`
    (`{..meta, "pulledOn", "rowCount", list_key: [...]}`) with
    `merge_rows`, and write it back atomically. Returns the rows written.
    One writer per path (take kit.single_instance.hold first).

    A row already there without its own `pulledAt` (raw written before rows
    carried one) gets the file's old `pulledOn`, else its mtime, so this
    pull's stamp never relabels it. A file that cannot be read as that
    shape is moved aside, never overwritten."""
    old: list[dict] = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            old = payload[list_key]
            if (not isinstance(old, list)
                    or not all(isinstance(r, dict) for r in old)):
                raise ValueError(f"{list_key!r} is not a list of objects")
        except (OSError, ValueError, KeyError, TypeError) as e:
            _move_aside(path, pulled_at, repr(e))
            old = []
        else:
            was = payload.get("pulledOn") or dates.utc_stamp(
                os.path.getmtime(path))
            old = [r if r.get("pulledAt") else {**r, "pulledAt": was}
                   for r in old]
    merged = merge_rows(old, rows, key, pulled_at)
    write_json_atomic(path, {**meta, "pulledOn": pulled_at,
                             "rowCount": len(merged), list_key: merged})
    return merged


# ---------------------------------------------------------------- chunk plan --

DONE = ("done", "skipped")                  # a chunk with nothing left to request


def chunk_windows(start: str, end: str, max_days: int) -> list[list[str]]:
    """`start..end` (ISO days, inclusive) as consecutive windows of at most
    `max_days`, oldest first: what the platform accepts per request."""
    if max_days < 1:
        raise ValueError(f"max_days must be >= 1, got {max_days}")
    d, last = (datetime.date.fromisoformat(start),
               datetime.date.fromisoformat(end))
    out = []
    while d <= last:
        stop = min(last, d + datetime.timedelta(days=max_days - 1))
        out.append([d.isoformat(), stop.isoformat()])
        d = stop + datetime.timedelta(days=1)
    return out


@dataclass
class ChunkPlan:
    """One long pull as chunks `{"window", "request_id", "status"}`, saved
    to `path` on start and after every `update`, so a killed job leaves a
    plan the next run resumes. Merge a chunk's rows with the `pulled_at` of
    the plan, the time its requests were made."""
    path: str
    window: list[str]
    pulled_at: str
    chunks: list[dict]

    @classmethod
    def start(cls, path: str, start: str, end: str, max_days: int,
              pulled_at: str) -> "ChunkPlan":
        plan = cls(path, [start, end], pulled_at,
                   [{"window": w, "request_id": None, "status": None}
                    for w in chunk_windows(start, end, max_days)])
        plan.save()
        return plan

    @classmethod
    def resume(cls, path: str,
               raw_pulled_on: str | None = None) -> "ChunkPlan | None":
        """The saved plan when a chunk of it is not done, else None (also
        when the file is missing or unreadable: start a new plan). When raw
        was pulled after the plan (`raw_pulled_on` is later), the
        unfinished chunks lose their request ids: they are requested again,
        never polled."""
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            plan = cls(path, saved["window"], saved["pulled_at"],
                       saved["chunks"])
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
        return [i for i, c in enumerate(self.chunks)
                if c.get("status") not in DONE]

    def update(self, i: int, **fields: Any) -> None:
        self.chunks[i].update(fields)
        self.save()

    def save(self) -> None:
        write_json_atomic(self.path, {"window": self.window,
                                      "pulled_at": self.pulled_at,
                                      "chunks": self.chunks}, indent=1)


# ---------------------------------------------------------------- gap ledger --

def _append(path: str, event: dict) -> dict:
    """One JSON line at the end of `path`, flushed to disk, under a lock. A
    torn last line (a writer killed mid-line) is cut off first: it was
    never a record."""
    line = (json.dumps(event, ensure_ascii=False, sort_keys=True)
            + "\n").encode()
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
    "HTTP 403: no access"; a `retry.GaveUp` has it as `.reason`). `fatal`:
    the whole layer came back empty, so the run must exit non-zero rather
    than let an ingest treat it as complete."""
    return _append(path, {"event": "gap", "endpoint": endpoint,
                          "days": sorted(days), "reason": reason,
                          "fatal": fatal,
                          "seen_at": seen_at or dates.utc_stamp()})


def record_filled(path: str, *, endpoint: str, days: list[str],
                  seen_at: str | None = None) -> dict:
    """Append that a later pull of `endpoint` got `days` after all."""
    return _append(path, {"event": "filled", "endpoint": endpoint,
                          "days": sorted(days),
                          "seen_at": seen_at or dates.utc_stamp()})


def read_ledger(path: str) -> list[dict]:
    """Every event, oldest first. A torn last line is not an event; any
    other line that is not JSON raises ValueError: the ledger was edited."""
    try:
        with open(path, "rb") as f:
            lines = f.read().split(b"\n")
    except FileNotFoundError:
        return []
    lines.pop()               # b"" after the last newline, or a torn line
    out = []
    for n, ln in enumerate(lines, 1):
        try:
            out.append(json.loads(ln))
        except ValueError:
            raise ValueError(f"{path}:{n} is not a JSON event: the ledger "
                             f"is append-only") from None
    return out


def open_gaps(path: str) -> dict[str, dict[str, str]]:
    """{endpoint: {day: reason}}: each day whose last event is a gap. This
    is what a gaps-only pull asks for again."""
    out: dict[str, dict[str, str]] = {}
    for e in read_ledger(path):
        days = out.setdefault(e["endpoint"], {})
        for d in e["days"]:
            if e["event"] == "gap":
                days[d] = e["reason"]
            else:
                days.pop(d, None)
    return {ep: dict(sorted(days.items()))
            for ep, days in out.items() if days}
