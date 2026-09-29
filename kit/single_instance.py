"""Single-instance guard for scheduled runs.

A scheduler starts a run whether or not the last one finished; a long
drain can outlast its interval. `hold(path)` takes a non-blocking
exclusive `fcntl.flock` on `path` so the second run can skip instead of
racing the first. flock behaves the same on Linux and macOS, so no
external lock tool is needed. The kernel drops the lock when the
holder's file is closed or its process dies, however it dies, so a
crashed run never leaves a stale lock; the lock file itself is left in
place on purpose (deleting it would let two runs lock two different
inodes).

Stdlib-only, no config: pullers import it.

Test: kit/tests/test_atomic.py (the lock cases).
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def hold(path: str | os.PathLike) -> Iterator[bool]:
    """Yield True while this process holds `path`'s lock, False (without
    waiting) if another holds it. Creates the parent dir."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True  # closing f on exit releases the lock
