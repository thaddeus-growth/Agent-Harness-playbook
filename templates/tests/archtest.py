"""Architecture rules checked on the code, not trusted to prose.

Import rules (`Graph`, `Rule`, `evaluate`). A stdlib AST scan of one source
folder. A module is its dotted path under the folder (`src/lib/db.py` is
`lib.db`; a package's `__init__.py` is the package). An import at module or
class level is eager: it runs when the module loads. One inside a function is
lazy: it runs when the function is called. `from .x import y` resolves against
the module's package, and `from pkg import Name` also loads the module that
`pkg/__init__.py` re-exports Name from. `reach(m)` is m's own imports, lazy
ones too, plus everything they load eagerly, through any chain. A lazy import
further down the chain is not counted: m runs it only if it calls it. Modules
that are not ours (stdlib, third party) are leaves, so a rule can name
`subprocess`. A dynamic import (`importlib.import_module`) is invisible: keep
the code free of them, or add a text rule.

A `Rule` names its layer by module globs (`!glob` leaves modules out) and
either `bad`, globs no module of the layer may reach, or `importers`, the exact
set of modules that import the layer. Every rule first checks that its layer
matches a module: a renamed file must fail the rule, not switch it off.

Text rules (`Scan`, `scan`). A regex over every text file under some folders:
the core never names the console or the host adapter; a consumer runs no write
verb. Each `Scan` carries samples it must catch and samples it must leave
alone, checked first, so a regex that matches nothing fails instead of
passing. `verbs()` builds the regex for a CLI's verbs as shell text
(`tool facts set`) or as argv items (`["facts", "set"]`).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

SKIP = {"__pycache__", "node_modules", ".git"}
TEXT = (".py", ".md", ".tsv", ".json", ".js", ".mjs", ".ts", ".html", ".css",
        ".sh", ".yml", ".yaml", ".toml", ".txt")


def modules_in(base: Path, package: str = "") -> dict[str, Path]:
    """{dotted module name: file} for every .py under `base`."""
    out = {}
    for p in sorted(Path(base).rglob("*.py")):
        rel = p.relative_to(base)
        if SKIP & set(rel.parts):
            continue
        parts = ([package] if package else []) + list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            out[".".join(parts)] = p
    return out


def _imports(tree: ast.AST, eager: bool = True):
    """(import statement, eager) for every import in `tree`."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node, eager
        else:
            yield from _imports(node, eager and not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))


class Graph:
    """The import graph of one source folder (`modules_in`'s mapping)."""

    def __init__(self, paths: dict[str, Path]):
        self.paths = paths
        self.tops = {m.split(".")[0] for m in paths}
        stmts = {m: list(_imports(ast.parse(p.read_text(encoding="utf-8"), str(p))))
                 for m, p in paths.items()}
        self.reexport = {(m, a.asname or a.name): base
                         for m, p in paths.items() if p.name == "__init__.py"
                         for node, _ in stmts[m] for base, a in self._named(m, node) if a}
        self.edges = {m: {(t, eager) for node, eager in stmts[m]
                          for base, a in self._named(m, node) for t in self._targets(base, a)
                          if t != m} for m in paths}

    def _named(self, mod: str, node: ast.stmt) -> list[tuple[str, ast.alias | None]]:
        """(absolute module, imported name or None) for one statement."""
        if isinstance(node, ast.Import):
            return [(a.name, None) for a in node.names]
        base = node.module or ""
        if node.level:
            pkg = mod
            for _ in range(node.level - (self.paths[mod].name == "__init__.py")):
                pkg = pkg.rpartition(".")[0]
            base = ".".join(filter(None, [pkg, node.module]))
        return [(base, a) for a in node.names]

    def _targets(self, base: str, alias: ast.alias | None) -> set[str]:
        """What one imported name loads: `base`, its parent packages, and the
        submodule (or the re-exporting module) the name comes from. Ours only
        if they exist; others as named."""
        parts = base.split(".")
        out = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
        if alias:
            sub = f"{base}.{alias.name}"
            out.add(sub if sub in self.paths else self.reexport.get((base, alias.name), base))
        return {t for t in out if t in self.paths or t.split(".")[0] not in self.tops}

    def direct(self, m: str, lazy: bool = True) -> set[str]:
        """What m imports itself (its lazy imports too, unless lazy=False)."""
        return {t for t, eager in self.edges[m] if lazy or eager}

    def reach(self, m: str, lazy: bool = True) -> set[str]:
        """direct(m, lazy), and everything those load eagerly, through any chain."""
        seen = self.direct(m, lazy)
        todo = list(seen)
        while todo:
            for t, eager in self.edges.get(todo.pop(), ()):
                if eager and t not in seen:
                    seen.add(t)
                    todo.append(t)
        return seen


@dataclass(frozen=True)
class Rule:
    id: str
    says: str
    layer: tuple[str, ...]                  # module globs; "!glob" leaves modules out
    bad: tuple[str, ...] = ()               # globs no module of the layer may reach
    importers: frozenset[str] | None = None  # or: exactly these modules import the layer
    lazy: bool = True                       # False: an import inside a function is allowed


def select(names, globs: tuple[str, ...]) -> list[str]:
    keep = [g for g in globs if not g.startswith("!")]
    drop = [g[1:] for g in globs if g.startswith("!")]
    return sorted(n for n in names if any(fnmatchcase(n, g) for g in keep)
                  and not any(fnmatchcase(n, g) for g in drop))


def evaluate(graph: Graph, rules: list[Rule]) -> list[tuple[str, list[str]]]:
    """[(label, problems)] for every rule: a check holds when problems is empty."""
    out = []
    for r in rules:
        layer = select(graph.paths, r.layer)
        out.append((f"{r.id}: the layer {' '.join(r.layer)} has modules",
                    [] if layer else ["no module matches"]))
        if r.importers is not None:
            got = {m for m in graph.paths if set(layer) & graph.direct(m, r.lazy)}
            out.append((f"{r.id}: {r.says}", [] if got == set(r.importers) else
                        [f"imported by {sorted(got)}; only {sorted(r.importers)} may"]))
            continue
        for m in layer:
            hit = sorted(t for t in graph.reach(m, r.lazy) if any(fnmatchcase(t, g) for g in r.bad))
            out.append((f"{r.id}: {m}: {r.says}", hit))
    return out


# ------------------------------------------------------------------ text rules

@dataclass(frozen=True)
class Scan:
    id: str
    says: str
    where: tuple[str, ...]      # folders (or files) under the root, read recursively
    pattern: re.Pattern
    hits: tuple[str, ...]       # samples the pattern must catch
    misses: tuple[str, ...] = ()  # samples it must leave alone
    optional: bool = False      # True: an absent folder passes (armed for when it lands)


def text_files(root: Path, where: tuple[str, ...]) -> list[Path]:
    out = []
    for w in where:
        base = Path(root, w)
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out += [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix in TEXT
                    and not SKIP & set(p.relative_to(base).parts)]
    return out


def scan(root: Path, scans: list[Scan]) -> list[tuple[str, list[str]]]:
    """[(label, problems)] for every Scan: its samples first, then its files."""
    out = []
    for s in scans:
        wrong = [f"misses {x!r}" for x in s.hits if not s.pattern.search(x)]
        wrong += [f"catches {x!r}" for x in s.misses if s.pattern.search(x)]
        out.append((f"{s.id}: the pattern catches its samples and only them",
                    wrong if s.hits else ["no sample to catch"]))
        files = text_files(root, s.where)
        if not s.optional:
            out.append((f"{s.id}: {' '.join(s.where)} holds files to read",
                        [] if files else ["nothing to read"]))
        found = []
        for p in files:
            text = p.read_text(encoding="utf-8", errors="replace")
            found += [f"{p.relative_to(root)}:{text.count(chr(10), 0, m.start()) + 1}"
                      for m in s.pattern.finditer(text)]
        out.append((f"{s.id}: {s.says}", found))
    return out


_S = r"[\"'`,\s\[\]]+"   # between two words: shell spaces, or an argv list's quotes and commas
_Q = r"[\"']"


def verbs(tool: str, pairs: str, singles: str = "") -> re.Pattern:
    """A CLI's verbs as shell text (`tool facts set`, `tool.py pull`) or as argv
    items (`"facts", "set"`, `"pull", "ads"`, `"pull"]`). `pairs` looks like
    `"facts set|unset;queue add|reject"`; `singles` like `"pull|execute"`."""
    split = [p.split(" ", 1) for p in pairs.split(";") if p]
    shell = [rf"{a}{_S}(?:{b})" for a, b in split] + ([singles] if singles else [])
    argv = [rf"{_Q}{a}{_Q}\s*,\s*{_Q}(?:{b}){_Q}" for a, b in split]
    if singles:
        argv.append(rf"{_Q}(?:{singles}){_Q}\s*(?:,\s*{_Q}|\])")
    return re.compile(rf"\b{re.escape(tool)}(?:\.py)?{_S}(?:{'|'.join(shell)})\b|" + "|".join(argv))
