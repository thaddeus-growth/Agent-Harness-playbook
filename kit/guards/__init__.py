"""Guards: the structural rules a harness's own tests hold it to.

Each guard is a library call a harness test makes. It reads its parameters
from the harness's harness.toml (kit.config: the bound harness, or the one
at `root` when a root is passed) and returns a list of problems: plain
strings, empty when the rule holds. A guard runs in a test, never under a
verb, so its findings are for the developer and are not coded messages.
Each module also has a `check_*()` convenience that reports through
kit.testing.check.

  ssot.py           the ssot index, the owner-file lint, ids kept not deleted
  release.py        what `git archive` ships, and .gitattributes from config
  layering.py       the import graph: pull / ingest / compute / one writer
  json_contract.py  one --json document, meta, every message coded
  boundary.py       the core never names an adapter; a consumer only reads
  drift.py          a vendored kit/ or console/ matches its MANIFEST.sha256

Every rule is also tried on a planted violation, so a guard cannot pass by
looking at nothing. This file holds the helpers they share.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from kit import config as _config

NONE = "—"
TEXT_EXT = (".py", ".tsv", ".md", ".json", ".js", ".mjs", ".cjs", ".ts",
            ".html", ".css", ".sh", ".yml", ".yaml", ".toml", ".txt")
SKIP_DIRS = ("node_modules", ".git", "__pycache__")


def harness(root: Path | str | None = None) -> _config.HarnessConfig:
    """The harness a guard checks: the one at `root` (read, not bound), else
    the bound one."""
    return _config.load(root) if root is not None else _config.config()


def section(cfg: _config.HarnessConfig, *keys: str) -> dict:
    """A nested table of harness.toml, {} when absent:
    section(cfg, "guards", "ssot") = [guards.ssot]."""
    node: Any = cfg.raw
    for k in keys:
        node = node.get(k, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def strs(value: Any, default: Iterable[str] = ()) -> tuple[str, ...]:
    """A config value that may be one string or a list of strings."""
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def module_name(spec: str) -> str:
    """A module as harness.toml names it ("_lib/writer", "_lib.writer",
    "_lib/writer.py") -> its import name ("_lib.writer")."""
    s = spec.strip().removesuffix(".py").strip("/")
    return s.replace("/", ".")


def tsv(path: Path | str) -> list[list[str]]:
    """The rows of a TSV, strictly as the registries are written: a cell is
    everything between two tabs (no quoting); blank lines are dropped."""
    text = Path(path).read_text(encoding="utf-8")
    rows = [line.rstrip("\r").split("\t") for line in text.split("\n")]
    return [r for r in rows if any(c.strip() for c in r)]


def paths(cell: str) -> list[str]:
    """The paths a `a; b` index cell names ("" and — name none)."""
    return [p.strip() for p in cell.split(";") if p.strip() not in ("", NONE)]


def text_files(base: Path | str, exts: tuple[str, ...] = TEXT_EXT,
               skip: Iterable[str] = SKIP_DIRS) -> Iterator[Path]:
    """Every text file under `base` (sorted), `skip` dirs left out."""
    skip = set(skip)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for f in sorted(filenames):
            if f.endswith(exts):
                yield Path(dirpath) / f


def read(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def verb_table(cfg: _config.HarnessConfig, verbs: Any = None) -> list:
    """The harness's verb table as a list of verbs (objects with `words`,
    `script`, `kind`, `takes_market`): `verbs` as given (a list or a
    {words: verb} mapping), else kit.verbs.load(<scripts_dir>/verbs.py)."""
    if verbs is None:
        from kit import verbs as kit_verbs     # the one verb table (§verbs)
        verbs = kit_verbs.load(cfg.root / cfg.scripts_dir / "verbs.py")
    if isinstance(verbs, Mapping):
        verbs = verbs.values()
    return list(verbs)


def report(label: str, problems: list[str]) -> bool:
    """One kit.testing.check line for a guard's problems."""
    from kit.testing.check import check
    return check(label, not problems, "\n        " + "\n        ".join(
        problems) if problems else "")
