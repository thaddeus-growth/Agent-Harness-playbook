"""The append-only raw store: raw only grows.

Everything a harness pulls or imports lands here first, under
`<DATA_DIR>/raw/<source>/<name>`, and ingest rebuilds the DB from it. The
rule this module holds is the lesson of a rebuild that shrank 3,072 rows
to 329: a raw file is written once and never replaced, so the DB can
always be rebuilt from everything ever pulled.

  * write-once: saving the same name with identical bytes is a no-op;
    with different bytes it is HarnessError(raw_would_overwrite) and the
    file on disk is untouched;
  * atomic and race-safe: the bytes go to a temp sibling, then a hard link
    claims the name (it fails if the name exists, so two concurrent writers
    can never both win); the temp is removed either way. A filesystem
    without hard links falls back to an exists-check + os.replace;
  * `import_file` copies an external export (a CSV the owner downloaded…)
    in under a content-hash name `<stem>.<sha256[:12]><suffix>`, so
    importing the same file twice leaves one raw file;
  * names are single path components: no separator, no leading dot (temp
    files start with one and `listing()` never shows them).

Test: kit/tests/test_raw.py.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from kit import paths
from kit.contract import HarnessError
from kit.messages import msg

RAW = "raw"


def _plain(name: str) -> str:
    if (not isinstance(name, str) or not name or name in (".", "..")
            or name.startswith(".") or "/" in name or "\\" in name
            or "\0" in name):
        raise HarnessError(msg(
            "raw_bad_name",
            f"{name!r} is not a plain file or folder name (no path separator,"
            f" no leading dot, not empty)", name=str(name)))
    return name


def raw_dir(source: str) -> Path:
    """<DATA_DIR>/raw/<source>/ (created)."""
    d = paths.data_dir() / RAW / _plain(source)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _same(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _refuse(source: str, name: str) -> HarnessError:
    return HarnessError(msg(
        "raw_would_overwrite",
        f"raw/{source}/{name} already holds different bytes; a raw file is "
        f"written once and never replaced — save the new data under a new "
        f"name. Nothing was written", source=source, name=name))


def save(source: str, name: str, data: bytes) -> Path:
    """Write `data` as raw/<source>/<name> once; identical re-save = no-op,
    different bytes = raw_would_overwrite. Returns the path."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"raw.save takes bytes, got {type(data).__name__}")
    data = bytes(data)
    path = raw_dir(source) / _plain(name)
    if path.exists():
        if _same(path, data):
            return path
        raise _refuse(source, name)
    tmp = path.parent / f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)          # claims the name, or fails if taken
        except FileExistsError:
            if _same(path, data):
                return path
            raise _refuse(source, name) from None
        except OSError as e:
            if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EXDEV,
                               errno.EMLINK, errno.EOPNOTSUPP):
                raise
            if path.exists():           # no hard links here: best effort
                if _same(path, data):
                    return path
                raise _refuse(source, name) from None
            os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
    return path


def canonical_json(obj: Any) -> bytes:
    """The bytes save_json writes: sorted keys, compact, utf-8. Equal
    objects give equal bytes, so a re-save of the same data is a no-op."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def save_json(source: str, name: str, obj: Any) -> Path:
    """save() of canonical_json(obj)."""
    return save(source, name, canonical_json(obj))


def listing(source: str) -> list[Path]:
    """The raw files of `source`, sorted by name (temp files never shown)."""
    return sorted(p for p in raw_dir(source).iterdir()
                  if p.is_file() and not p.name.startswith("."))


def import_file(source: str, src: Path | str) -> Path:
    """Copy an external file in under `<stem>.<sha256[:12]><suffix>`;
    importing the same file twice leaves one raw file."""
    src = Path(src).expanduser()
    try:
        if not src.is_file():
            raise OSError
        data = src.read_bytes()
    except OSError:
        raise HarnessError(msg(
            "raw_import_missing",
            f"Nothing to import: {src} is not a readable file",
            path=str(src))) from None
    digest = hashlib.sha256(data).hexdigest()[:12]
    stem = src.stem.lstrip(".") or "import"
    return save(source, f"{stem}.{digest}{src.suffix}", data)
