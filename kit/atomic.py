"""Atomic writes for source-of-truth files.

A raw puller output IS the source of truth (ingest reads it back), so a
half-written file must never be observable: a crash, a full disk or a
SIGKILL mid-write would otherwise leave a truncated file that the next
ingest reads as real data. Write a temp sibling, then `os.replace` it
over the target (atomic on one filesystem). On any error the temp file
is removed and the target is untouched.

The temp name is unique per call (pid + random), so two writers of the
same path never share a temp file; it is created with the process umask
like a plain open() would.

Stdlib-only, no config: pullers import it.

Test: kit/tests/test_atomic.py.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Iterator


def _tmp_for(path: str | os.PathLike) -> str:
    return f"{os.fspath(path)}.{os.getpid()}.{secrets.token_hex(4)}.tmp"


@contextmanager
def _open_tmp(path: str | os.PathLike, mode: str) -> Iterator[IO]:
    tmp = _tmp_for(path)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with (open(fd, mode, encoding="utf-8") if "b" not in mode
              else open(fd, mode)) as f:
            yield f
    except BaseException:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, path)


@contextmanager
def open_atomic(path: str | os.PathLike) -> Iterator[IO[str]]:
    """Text-write `path` (utf-8) so a reader never sees a partial file."""
    with _open_tmp(path, "w") as f:
        yield f


def write_json_atomic(path: str | os.PathLike, obj: Any, *,
                      indent: int | None = None) -> None:
    """Serialize `obj` to `path` (ensure_ascii=False) atomically."""
    with open_atomic(path) as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def write_bytes_atomic(path: str | os.PathLike, data: bytes) -> None:
    """Write `data` to `path` atomically."""
    with _open_tmp(path, "wb") as f:
        f.write(data)


def is_temp(path: str | os.PathLike) -> bool:
    """True for a temp sibling this module writes (a crash can leave one)."""
    return Path(path).name.endswith(".tmp")
