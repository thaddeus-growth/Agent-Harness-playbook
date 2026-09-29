"""The layering a harness states in prose, pinned on its import graph.

A stdlib AST scan of the harness's scripts: `<scripts_dir>/*.py` as
top-level modules (the scripts dir is on sys.path), every package under
it (`_lib.x`, the vendored `kit.x`; the running kit when none is
vendored) and `[layers].packages` (package dirs at the harness root).
"Reaches" = imports directly (inside a function too), or loads at import
time through any chain of top-level imports. `check_layers(root)` returns
every problem of these rules, all named in harness.toml `[layers]`:

  a. only `writer_importers` import `writer`, the one module that calls
     the external write API;
  b. pull (`pull`, a glob such as "pull_*") reaches no database module
     (`db`, default _lib/db + _lib/schema, plus kit.db and
     kit.schema_base), no sqlite3, no compute, ingest, writer or
     `write_verbs` module;
  c. ingest reaches no compute or pull script, no API client
     (`clients`), no writer and no network library;
  d. compute reaches no pull or ingest script, no API client, no writer,
     no `write_verbs` module and no network library;
  e. an API client reaches no database module and no sqlite3;
  f. of the computes only `spawn_allowed` ones run another process (reach
     kit.runner / _lib.runner, or import subprocess themselves);
  g. a `write_verbs` module reaches no compute script;
  h. a harness package module (_lib…) imports no top-level script, and
     the kit imports nothing of the harness's;
  i. harness code imports nothing dynamically (importlib.import_module,
     __import__, spec_from_file_location, runpy): the graph must see
     every edge.

`[layers].exempt = ["m>t", …]` allows the one edge m -> t (a deliberate
lazy import). A module harness.toml names that does not exist leaves its
rule empty rather than failing: `members(root)` shows what each layer
holds, for a test's positive controls. `self_test()` plants one violation
of each rule in a scratch harness and returns the ones the rules miss.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import ast
import tempfile
from fnmatch import fnmatch
from pathlib import Path

import kit
from kit.config import HarnessConfig
from kit.guards import harness, module_name, report, strs

NETWORK = frozenset({"urllib.request", "http.client", "socket", "ssl",
                     "requests", "httpx", "aiohttp", "ftplib", "smtplib",
                     "websocket", "websockets"})
SPAWN = frozenset({"subprocess", "multiprocessing", "pty"})
RUNNERS = frozenset({"kit.runner", "_lib.runner"})
KIT_DB = frozenset({"kit.db", "kit.schema_base"})
DYNAMIC = frozenset({"import_module", "__import__", "spec_from_file_location",
                     "run_path", "run_module"})
SKIP = {"__pycache__", "tests"}


def _collect(roots: dict[str, Path]) -> dict[str, Path]:
    """module name -> file: "" = top-level scripts of a dir (not
    recursive); "pkg" = a package dir, recursive, tests/ left out."""
    out: dict[str, Path] = {}
    for prefix, d in roots.items():
        d = Path(d)
        if not d.is_dir():
            continue
        if not prefix:
            out.update({p.stem: p for p in sorted(d.glob("*.py"))})
            continue
        for p in sorted(d.rglob("*.py")):
            rel = p.relative_to(d)
            if SKIP & set(rel.parts[:-1]):
                continue
            parts = [prefix, *rel.with_suffix("").parts]
            if parts[-1] == "__init__":
                parts = parts[:-1]
            out[".".join(parts)] = p
    return out


def _imports(tree: ast.AST, eager: bool = True):
    """(statement, eager) for every import; eager = runs when the module
    loads (not inside a def or lambda)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node, eager
        else:
            yield from _imports(node, eager and not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))


class ImportGraph:
    """The import edges between a harness's own modules, plus each
    module's external (stdlib / third-party) imports."""

    def __init__(self, roots: dict[str, Path | str]):
        self.paths = _collect({k: Path(v) for k, v in roots.items()})
        self.modules = set(self.paths)
        self.errors: list[str] = []
        self.dynamic: dict[str, list[int]] = {}
        self._stmts: dict[str, list] = {}
        for m, p in self.paths.items():
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
            except (SyntaxError, UnicodeDecodeError) as e:
                self.errors.append(f"{p}: does not parse ({e})")
                tree = ast.Module(body=[], type_ignores=[])
            self._stmts[m] = list(_imports(tree))
            self.dynamic[m] = sorted(
                n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                and ((isinstance(n.func, ast.Name) and n.func.id in DYNAMIC)
                     or (isinstance(n.func, ast.Attribute)
                         and n.func.attr in DYNAMIC)))
        self._tops = {m.split(".")[0] for m in self.modules}
        # a package's re-exports, lazy ones too: `from pkg import Name`
        # loads the module Name comes from
        self._reexport = {
            (m, a.asname or a.name): base
            for m in self.modules if self.paths[m].name == "__init__.py"
            for node, _ in self._stmts[m] for base, a in self._named(m, node)
            if a}
        self.edges: dict[str, set[tuple[str, bool]]] = {}
        self.external: dict[str, set[tuple[str, bool]]] = {}
        for m in self.modules:
            edges, ext = set(), set()
            for node, eager in self._stmts[m]:
                for base, a in self._named(m, node):
                    hit = self._targets(base, a)
                    if hit or base.split(".")[0] in self._tops:
                        edges |= {(t, eager) for t in hit}
                    else:
                        ext |= {(n, eager) for n in _dotted(base, a)}
            self.edges[m], self.external[m] = edges, ext

    def _named(self, mod: str, node) -> list[tuple[str, ast.alias | None]]:
        """(absolute module, alias or None) per name one statement imports."""
        if isinstance(node, ast.Import):
            return [(a.name, None) for a in node.names]
        pkg = mod     # a relative import counts from the package
        for _ in range(node.level - (self.paths[mod].name == "__init__.py")):
            pkg = pkg.rpartition(".")[0]
        base = (".".join(filter(None, [pkg, node.module])) if node.level
                else node.module or "")
        return [(base, a) for a in node.names]

    def _targets(self, base: str, alias: ast.alias | None) -> set[str]:
        """Our modules one imported name loads: base and its packages, plus
        the submodule or re-exporting module the name comes from."""
        parts = base.split(".")
        out = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
        if alias:
            sub = f"{base}.{alias.name}"
            out.add(sub if sub in self.modules
                    else self._reexport.get((base, alias.name), base))
        return out & self.modules

    def direct(self, m: str) -> set[str]:
        return {t for t, _ in self.edges.get(m, ())}

    def reach(self, m: str, lazy: bool = True) -> set[str]:
        """Direct imports (lazy ones too, unless lazy=False) plus every
        module of ours loaded when m loads."""
        seen, todo = set(), [m]
        while todo:
            for t, eager in self.edges.get(todo.pop(), ()):
                if eager and t not in seen:
                    seen.add(t)
                    todo.append(t)
        return seen | self.direct(m) if lazy else seen

    def external_reach(self, m: str) -> set[str]:
        """External modules m imports (lazily too) or that load with the
        modules it reaches."""
        out = {n for n, _ in self.external.get(m, ())}
        for t in self.reach(m):
            out |= {n for n, eager in self.external.get(t, ()) if eager}
        return out

    def importers(self, target: str) -> set[str]:
        return {m for m in self.modules if target in self.direct(m)}


def _dotted(base: str, alias: ast.alias | None) -> set[str]:
    """`import a.b` -> {a, a.b}; `from a import b` -> {a, a.b}."""
    name = f"{base}.{alias.name}" if alias and base else (base or "")
    parts = [p for p in name.split(".") if p]
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}


def default_roots(cfg: HarnessConfig) -> dict[str, Path]:
    scripts = cfg.root / cfg.scripts_dir
    roots: dict[str, Path] = {"": scripts}
    if scripts.is_dir():
        for d in sorted(scripts.iterdir()):
            if d.is_dir() and d.name not in SKIP and not d.name.startswith(
                    ".") and any(d.rglob("*.py")):
                roots[d.name] = d
    roots.setdefault("kit", Path(kit.__file__).resolve().parent)
    for p in strs(cfg.layers.get("packages")):
        roots[Path(p).name] = cfg.root / p
    return roots


def members(root: Path | str | None = None, cfg: HarnessConfig | None = None,
            graph: ImportGraph | None = None) -> dict[str, set[str]]:
    """What each layer holds: {pull, ingest, compute, writer, clients, db,
    write_verbs, scripts}."""
    cfg = cfg or harness(root)
    g = graph or ImportGraph(default_roots(cfg))
    scripts_dir = (cfg.root / cfg.scripts_dir).resolve()
    scripts = {m for m in g.modules if "." not in m
               and g.paths[m].resolve().parent == scripts_dir}
    L = cfg.layers

    def glob(key: str, default: str) -> set[str]:
        return {m for m in scripts
                for pat in strs(L.get(key), (default,)) if fnmatch(m, pat)}

    def names(key: str, default: tuple = ()) -> set[str]:
        return {module_name(x) for x in strs(L.get(key), default)}

    return {"scripts": scripts,
            "pull": glob("pull", "pull_*"),
            "ingest": glob("ingest", "ingest_*"),
            "compute": glob("compute", "compute_*"),
            "writer": names("writer") & g.modules,
            "writer_importers": names("writer_importers"),
            "clients": names("clients") & g.modules,
            "spawn_allowed": names("spawn_allowed"),
            "db": (names("db", ("_lib/db", "_lib/schema")) | KIT_DB)
            & g.modules,
            "write_verbs": names("write_verbs") & g.modules}


def check_layers(root: Path | str | None = None,
                 graph: ImportGraph | None = None) -> list[str]:
    """Every problem of the layering rules (module docstring)."""
    cfg = harness(root)
    g = graph or ImportGraph(default_roots(cfg))
    mem = members(cfg=cfg, graph=g)
    exempt = {tuple(x.split(">", 1)) for x in strs(cfg.layers.get("exempt"))}
    out = list(g.errors)
    writer, clients, db = mem["writer"], mem["clients"], mem["db"]
    pull, ingest, compute = mem["pull"], mem["ingest"], mem["compute"]

    def hits(m: str, bad: set[str], ext_bad: frozenset = frozenset()
             ) -> list[str]:
        found = {t for t in g.reach(m) if t in bad}
        found |= g.external_reach(m) & ext_bad
        return sorted(t for t in found if (m, t) not in exempt)

    for w in sorted(writer):
        extra = sorted(g.importers(w) - mem["writer_importers"])
        if extra:
            out.append(f"a: {w} (the writer) is imported by {extra}; only "
                       f"{sorted(mem['writer_importers'])} may")
    layers = {
        "b": (pull, compute | ingest | db | writer | mem["write_verbs"],
              frozenset({"sqlite3"}),
              "pull reaches no database, compute, ingest or writer"),
        "c": (ingest, compute | pull | clients | writer, NETWORK,
              "ingest reaches no compute, pull, API client, writer or "
              "network"),
        "d": (compute, ingest | pull | clients | writer | mem["write_verbs"],
              NETWORK, "compute reaches no pull, ingest, API client, writer "
                       "or network"),
        "e": (clients, db, frozenset({"sqlite3"}),
              "an API client reaches no database"),
        "g": (mem["write_verbs"], compute, frozenset(),
              "a write verb reaches no compute"),
    }
    for rule, (mods, bad, ext_bad, says) in layers.items():
        for m in sorted(mods):
            found = hits(m, bad - {m}, ext_bad)
            if found:
                out.append(f"{rule}: {m} reaches {found} ({says})")
    spawns = sorted(m for m in compute if (
        (g.reach(m) & RUNNERS) or ({n for n, _ in g.external.get(m, ())}
                                   & SPAWN)) and m not in mem["spawn_allowed"])
    if spawns:
        out.append(f"f: computes that run another process: {spawns}; only "
                   f"{sorted(mem['spawn_allowed'])} may")
    harness_mods = {m for m in g.modules if not m.startswith("kit.")
                    and m != "kit"}
    for m in sorted(harness_mods - mem["scripts"]):
        found = sorted(g.direct(m) & mem["scripts"])
        if found:
            out.append(f"h: {m} imports the script(s) {found} (a library "
                       f"imports no script)")
    for m in sorted(g.modules - harness_mods):
        found = sorted(g.direct(m) & harness_mods)
        if found:
            out.append(f"h: {m} (the kit) imports the harness's {found}")
    for m in sorted(harness_mods):
        if g.dynamic.get(m):
            out.append(f"i: {m} imports dynamically at line(s) "
                       f"{g.dynamic[m]}: the graph cannot see that edge")
    return out


_PLANTED = {
    "harness.toml": '[harness]\nname = "planted"\ncli = "planted"\n'
                    'env_prefix = "PLANTED"\n\n[layers]\n'
                    'writer = "_lib/writer"\n'
                    'writer_importers = ["execute_actions"]\n'
                    'clients = ["_lib/api", "_lib/api_db"]\n'
                    'spawn_allowed = ["compute_stories"]\n'
                    'write_verbs = ["decide"]\n',
    "scripts/_lib/__init__.py": "",
    "scripts/_lib/db.py": "import sqlite3\n",
    "scripts/_lib/api.py": "import urllib.request\n",
    "scripts/_lib/api_db.py": "from _lib import api\nfrom . import db\n",
    "scripts/_lib/writer.py": "from _lib import api\n",
    "scripts/_lib/helper.py": "import compute_ok\n",
    "scripts/_lib/dyn.py": "import importlib\n"
                           "m = importlib.import_module('x')\n",
    "scripts/pull_sql.py": "import sqlite3\n",
    "scripts/pull_db.py": "from _lib import db\n",
    "scripts/ingest_net.py": "from _lib.api import get\n",
    "scripts/compute_write.py": "def f():\n    from _lib.writer import send\n",
    "scripts/compute_spawn.py": "import subprocess\n",
    "scripts/compute_ingest.py": "import ingest_net\n",
    "scripts/compute_stories.py": "from kit import runner\n",
    "scripts/compute_ok.py": "import json\nfrom kit import contract\n",
    "scripts/execute_actions.py": "from _lib import writer\n",
    "scripts/sneak.py": "import _lib.writer\n",
    "scripts/decide.py": "import compute_ok\n",
}
# (the start of the problem line, names it must list, which rule it proves)
_EXPECTED = (
    ("a: _lib.writer (the writer) is imported by", ["sneak", "compute_write"],
     "a: only the executor imports the writer"),
    ("b: pull_sql reaches", ["sqlite3"], "b: pull opens no database"),
    ("b: pull_db reaches", ["_lib.db", "sqlite3"], "b: pull reaches no db"),
    ("c: ingest_net reaches", ["_lib.api", "urllib.request"],
     "c: ingest reaches no API client"),
    ("d: compute_write reaches", ["_lib.writer"],
     "d: compute reaches no writer, even lazily"),
    ("d: compute_ingest reaches", ["ingest_net", "_lib.api"],
     "d: compute reaches no ingest or client"),
    ("e: _lib.api_db reaches", ["_lib.db", "sqlite3"],
     "e: an API client reaches no database"),
    ("f: computes that run another process", ["compute_spawn"],
     "f: only spawn_allowed computes spawn"),
    ("g: decide reaches", ["compute_ok"],
     "g: a write verb reaches no compute"),
    ("h: _lib.helper imports the script(s)", ["compute_ok"],
     "h: a library imports no script"),
    ("i: _lib.dyn imports dynamically", ["2"], "i: no dynamic import"),
)


def self_test() -> list[str]:
    """The rules tried on a scratch harness that breaks each one: [] when
    every planted violation is caught and the clean modules pass."""
    with tempfile.TemporaryDirectory(prefix="layering-") as d:
        root = Path(d)
        for rel, text in _PLANTED.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        got = check_layers(root)
    out = []
    for start, names, why in _EXPECTED:
        line = next((p for p in got if p.startswith(start)), "")
        if not all(f"'{n}'" in line or f"[{n}]" in line for n in names):
            out.append(f"planted violation not caught ({why}): "
                       f"{start} {names}; got {line or 'nothing'}")
    out += [f"flagged a clean module: {p}" for p in got
            if not any(p.startswith(s) for s, _, _ in _EXPECTED)]
    return out


def check_layering(root: Path | str | None = None) -> bool:
    """check_layers() + self_test() as kit.testing.check lines."""
    ok = report("layering: the rules catch each planted violation",
                self_test())
    return report("layering: pull / ingest / compute / writer keep their "
                  "layers", check_layers(root)) and ok
