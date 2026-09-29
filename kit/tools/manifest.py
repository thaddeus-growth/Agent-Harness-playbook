#!/usr/bin/env python3
"""MANIFEST.sha256: the fingerprint of a vendored kit/ or console/.

    python3 kit/tools/manifest.py DIR           write DIR/MANIFEST.sha256
    python3 kit/tools/manifest.py --check DIR   exit 1 on any drift

One line per file, `<sha256>  <relpath>` (two spaces, the sha256sum
format), sorted by path, covering every file under DIR except the
top-level tests/ dir, __pycache__ dirs, *.pyc, .DS_Store and the manifest
itself. A vendored copy that anyone edited locally (a changed, added or
removed file, VERSION included) no longer matches: `check()` names each
difference, and the harness's drift guard fails on it.

Stdlib only; runs without a bound harness.

Test: kit/tests/test_manifest.py.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

NAME = "MANIFEST.sha256"
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {NAME, ".DS_Store"}
SKIP_TOP = {"tests"}


def files(root: Path) -> list[str]:
    """The relative paths (posix) the manifest covers, sorted."""
    root = Path(root)
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if (rel.parts[0] in SKIP_TOP or SKIP_DIRS & set(rel.parts)
                or p.name in SKIP_FILES or p.suffix == ".pyc"
                or not p.is_file()):
            continue
        out.append(rel.as_posix())
    return sorted(out)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def compute(root: Path) -> dict[str, str]:
    """{relpath: sha256} of every covered file."""
    return {rel: sha256(Path(root) / rel) for rel in files(root)}


def render(entries: dict[str, str]) -> str:
    return "".join(f"{entries[rel]}  {rel}\n" for rel in sorted(entries))


def parse(text: str) -> dict[str, str]:
    """{relpath: sha256} of a manifest's text (ValueError on a bad line)."""
    out = {}
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        digest, sep, rel = line.partition("  ")
        if not sep or len(digest) != 64 or not rel:
            raise ValueError(f"{NAME}:{n}: not '<sha256>  <path>': {line!r}")
        out[rel] = digest
    return out


def write(root: Path) -> Path:
    """Write root/MANIFEST.sha256 (only when its content changes)."""
    path = Path(root) / NAME
    text = render(compute(root))
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def check(root: Path) -> list[str]:
    """Every difference between root's files and its manifest (empty =
    no drift)."""
    path = Path(root) / NAME
    if not path.is_file():
        return [f"{root}: no {NAME}"]
    try:
        want = parse(path.read_text(encoding="utf-8"))
    except ValueError as e:
        return [str(e)]
    have = compute(root)
    out = [f"changed: {rel}" for rel in sorted(want.keys() & have.keys())
           if want[rel] != have[rel]]
    out += [f"missing: {rel}" for rel in sorted(want.keys() - have.keys())]
    out += [f"not in {NAME}: {rel}" for rel in sorted(have.keys() - want.keys())]
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    checking = "--check" in args
    rest = [a for a in args if a != "--check"]
    if len(rest) != 1 or not Path(rest[0]).is_dir():
        print("usage: manifest.py [--check] DIR", file=sys.stderr)
        return 2
    root = Path(rest[0])
    if checking:
        problems = check(root)
        for p in problems:
            print(p)
        print(f"{root}: {'drift' if problems else 'matches ' + NAME}")
        return 1 if problems else 0
    print(write(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
