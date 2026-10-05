"""Packs: which kit modules a harness vendors.

Every kit module belongs to one pack in kit/packs.tsv (module, pack, note).
A harness names the packs it takes in harness.toml:

    [kit]
    packs = ["compliance"]

`base` and `testkit` are always taken. With no `[kit] packs` a harness takes
every pack, as before packs existed. A module imports only `base` and its
own pack (`testkit` may import `base` and itself); kit/tests/test_packs.py
holds the table and the code to that.

Used by kit/tools/vendor.py. Stdlib only; runs without a bound harness.
Test: kit/tests/test_packs.py.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

TABLE = "packs.tsv"
ALWAYS = ("base", "testkit")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "out"}


class PackError(ValueError):
    """A bad pack table or a bad `[kit] packs` declaration."""


def table(kit_dir: Path) -> dict[str, str]:
    """{module: pack} from `<kit_dir>/packs.tsv` (module names are dotted: guards.ssot)."""
    out: dict[str, str] = {}
    lines = (kit_dir / TABLE).read_text("utf-8").splitlines()
    if not lines or lines[0].split("\t")[:2] != ["module", "pack"]:
        raise PackError(f"{TABLE}: the header is not module, pack, note")
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2 or not cols[0] or not cols[1]:
            raise PackError(f"{TABLE}:{n}: a row needs a module and a pack")
        if cols[0] in out:
            raise PackError(f"{TABLE}:{n}: {cols[0]} has two rows")
        out[cols[0]] = cols[1]
    return out


def modules(kit_dir: Path) -> list[str]:
    """Every module the kit holds, as dotted names; tests/ is not part of the kit."""
    return sorted(".".join(p.relative_to(kit_dir).with_suffix("").parts)
                  for p in kit_dir.rglob("*.py")
                  if not {"tests", "__pycache__"} & set(p.relative_to(kit_dir).parts))


def module_of(rel: str, known: set[str]) -> str | None:
    """The module a kit file belongs to (None: the package's own files, always vendored).
    `facts.py` -> facts, `guards/ssot.py` -> guards.ssot, `message_codes.d/facts.tsv` -> facts."""
    p = Path(rel)
    if p.suffix == ".py":
        return ".".join(p.with_suffix("").parts)
    if p.parent.as_posix() == "message_codes.d" and p.stem in known:
        return p.stem
    return None


def declared(harness: Path, names: set[str]) -> list[str] | None:
    """The packs `harness.toml` names under `[kit] packs`, checked against `names`; None when absent."""
    try:
        raw = tomllib.loads((harness / "harness.toml").read_text("utf-8"))
    except OSError:
        return None
    except tomllib.TOMLDecodeError as e:
        raise PackError(f"harness.toml: {e}") from None
    kit = raw.get("kit", {})
    if not isinstance(kit, dict) or "packs" not in kit:
        return None
    packs = kit["packs"]
    if not isinstance(packs, list) or not all(isinstance(p, str) for p in packs):
        raise PackError("harness.toml: [kit] packs must be a list of pack names")
    unknown = sorted(set(packs) - names)
    if unknown:
        raise PackError(f"harness.toml: [kit] packs names an unknown pack: {', '.join(unknown)}"
                        f" (the packs: {', '.join(sorted(names))})")
    return packs


def chosen(harness: Path, kit_dir: Path) -> set[str]:
    """The packs a harness vendors: what it declares plus base and testkit, or every pack."""
    names = set(table(kit_dir).values())
    packs = declared(harness, names)
    return names if packs is None else set(packs) | set(ALWAYS)


def imports(source: str, known: set[str]) -> set[str]:
    """The kit modules (of `known`, dotted) that `source` imports, lazy ones included.
    `from kit.guards import ssot` is the module guards.ssot; `from kit.config import
    config` is config; a name a package defines (`from kit import __version__`) is
    its `__init__`."""
    out: set[str] = set()

    def target(dotted: str, names: list[str]) -> None:
        if dotted in known:
            out.add(dotted)
        init = f"{dotted}.__init__" if dotted else "__init__"
        for n in names:
            sub = f"{dotted}.{n}" if dotted else n
            if sub in known:
                out.add(sub)
            elif init in known:
                out.add(init)

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "kit" or a.name.startswith("kit."):
                    target(".".join(a.name.split(".")[1:]), [])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and (node.module == "kit" or node.module.startswith("kit.")):
            target(".".join(node.module.split(".")[1:]), [a.name for a in node.names])
    return out


def harness_imports(harness: Path, kit_dest: Path, known: set[str]) -> dict[str, list[str]]:
    """{kit module: [files]} for every import of the kit in the harness's own Python code
    (the vendored kit itself, hidden folders and virtual environments are not read)."""
    out: dict[str, list[str]] = {}
    for p in sorted(harness.rglob("*.py")):
        rel = p.relative_to(harness)
        if SKIP_DIRS & set(rel.parts) or any(s.startswith(".") for s in rel.parts[:-1]) \
                or kit_dest == p or kit_dest in p.parents:
            continue
        try:
            found = imports(p.read_text("utf-8"), known)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for m in found:
            out.setdefault(m, []).append(rel.as_posix())
    return out
