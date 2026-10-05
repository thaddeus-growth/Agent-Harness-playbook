#!/usr/bin/env python3
"""The kit's tier registry, kit-tiers.tsv at the playbook root (docs/kit-and-tools.md, "The
admission rule"), held to the kit's source. The registry and this test sit
outside the bytes a harness vendors (kit/ minus tests/), so a tier change never
touches a vendored copy or kit/MANIFEST.sha256.

  [0] the registry of this repository is clean, and is not vendored
  [1] every kit module is classified exactly once, and no row names a module
      that is not there
  [2] every tier is core, stack or extra
  [3] layering, by the ast import graph (imports inside functions included,
      and the package above a module: importing a.b runs a/__init__):
      core imports only core; stack imports core and stack
  [4] admission, from the registry's own data: a core row names at least two
      harnesses using it in production (video-ads, kol, rednote: the scaffold
      skeleton is no harness and never counts toward core; it counts toward
      the cap on extra), an extra row at most one consumer; a row nothing
      imports (no consumer, or only forks) is extra and says "no consumer"
  [5] the registry's shape: columns, consumers as who:kind with a who from the
      documented list and a kind that fits it, evidence, since, note; every
      file of this repository the evidence names exists and uses the module
      (imports it, directly or through the module its "(via X)" names), and a
      skeleton, playbook or zylos consumer has such a file at home
  [6] what the docs say about vendoring is what vendor.py does
  [7] docs/kit-and-tools.md names the registry and this test

Each rule is proven on a broken copy of the kit and the registry, one plant
at a time, each undone before the next. The graph sees static imports only
(kit/verbs.py loads the harness's own verbs file with importlib; no kit module
loads another that way).
"""

import ast
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing.check import capture, check, finish, tmp_dir  # noqa: E402
from kit.tools import manifest, vendor  # noqa: E402

PLAYBOOK, KIT = _shop.PLAYBOOK, _shop.KIT
NAME = "kit-tiers.tsv"
COLUMNS = ("module", "tier", "consumers", "evidence", "since", "note")
TIERS = ("core", "stack", "extra")
KINDS = ("prod", "skeleton", "test", "tool", "fork")
WHO = ("video-ads", "kol", "rednote", "skeleton", "seo", "outreach", "source", "zylos", "playbook")   # docs/kit-and-tools.md, "Reading the registry"
HARNESSES = ("video-ads", "kol", "rednote")     # the harnesses that vendor the kit: the only `prod`
HERE = ("skeleton", "playbook", "zylos")        # consumers that live in this repository: their evidence is read
COUNTED = ("prod", "skeleton")                  # counted toward the cap on extra; only `prod` counts toward core
MAY_IMPORT = {"core": {"core"}, "stack": {"core", "stack"}}
ROOT = "kit"                       # kit/__init__.py: loads nothing but the version
CONSUMER = re.compile(r"([a-z][a-z0-9-]*):(" + "|".join(KINDS) + r")")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
REPO_PATH = re.compile(r"(?<![\w:/.-])((?:scaffold|hosts|kit|templates|build|tests|console)/[\w./-]*\w)(?::\d+)?")
VIA = re.compile(r"\(via ([\w.]+)\)")


# ------------------------------------------------------------- the kit's graph

def sources(kit: Path) -> dict[str, Path]:
    """Dotted name -> file of every .py under kit/ outside tests/: 'atomic',
    'guards.ssot'; a package's __init__ is named after the package ('guards'),
    kit/__init__.py is ROOT."""
    out = {}
    for p in sorted(kit.rglob("*.py")):
        parts = p.relative_to(kit).with_suffix("").parts
        if parts[0] == "tests" or "__pycache__" in parts:
            continue
        out[".".join(parts[:-1] if parts[-1] == "__init__" else parts) or ROOT] = p
    return out


def imports(src: str, pkg: str) -> list[str]:
    """Every kit-relative dotted name `src` imports, inside functions too:
    `from kit.guards import ssot` gives 'guards' and 'guards.ssot', `from kit
    import db` gives 'db'. A relative import is resolved against `pkg`, the
    package the file is in ('' = the kit root)."""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [a.name[4:] for a in n.names
                    if a.name == "kit" or a.name.startswith("kit.")]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                up = pkg.split(".") if pkg else []
                base = ".".join(up[:max(0, len(up) - (n.level - 1))]
                                + ([n.module] if n.module else []))
            elif n.module == "kit" or (n.module or "").startswith("kit."):
                base = n.module[4:]
            else:
                continue
            out.append(base)
            out += [f"{base}.{a.name}" if base else a.name for a in n.names]
    return out


def graph(kit: Path) -> tuple[dict[str, set[str]], set[str]]:
    """({module: the kit modules it imports}, the names that are a package's
    __init__). Importing a.b runs a/__init__ too, so a module also depends on
    the packages above it."""
    src = sources(kit)
    glue = {n for n, p in src.items() if p.name == "__init__.py"}
    g = {}
    for name, path in src.items():
        parts = name.split(".")
        pkg = "" if name == ROOT else name if name in glue else ".".join(parts[:-1])
        want = set(imports(path.read_text(encoding="utf-8"), pkg))
        if name != ROOT:
            want |= {".".join(parts[:i]) for i in range(1, len(parts))}
        deps = set()
        for d in want:
            ds = d.split(".") if d else []
            deps |= {".".join(ds[:i]) for i in range(1, len(ds) + 1)} & set(src)
        g[name] = deps - {name}
    return g, glue


# ---------------------------------------------------------------- the registry

def read_rows(text: str) -> tuple[list[dict], list[str]]:
    """(rows, problems): one dict per line under the header, plus its number."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or tuple(lines[0].split("\t")) != COLUMNS:
        return [], [f"{NAME}: the header is not {' <TAB> '.join(COLUMNS)}"]
    rows, bad = [], []
    for n, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            bad.append(f"{NAME}:{n}: {len(cells)} columns, not {len(COLUMNS)}")
        else:
            rows.append({"line": n, **dict(zip(COLUMNS, (c.strip() for c in cells)))})
    return rows, bad


def consumers(cell: str) -> tuple[list[tuple[str, str]], list[str]]:
    """([(who, kind)], the entries that are not who:kind)."""
    pairs, bad = [], []
    for tok in (t.strip() for t in cell.split(";")):
        m = CONSUMER.fullmatch(tok)
        if m:
            pairs.append((m[1], m[2]))
        elif tok:
            bad.append(tok)
    return pairs, bad


def repo_paths(cell: str) -> list[tuple[str, str | None, bool]]:
    """(path, via, twin) of every file of this repository the evidence names. An entry is what lies between two ';'; its
    "(via X)" says the file reaches the module through kit module X, and "twin" that it is a standalone copy, not a user."""
    out = []
    for entry in cell.split(";"):
        via = VIA.search(entry)
        out += [(p, via[1] if via else None, "twin" in entry) for p in REPO_PATH.findall(entry)]
    return out


def home(who: str, path: str) -> bool:
    """Is `path` where a consumer `who` of this repository keeps its use: the skeleton's folder, the adapter's folder, or
    (the playbook's own tools and tests) anywhere else."""
    skeleton, zylos = path.startswith("scaffold/skeleton/"), path.startswith("hosts/zylos/")
    return skeleton if who == "skeleton" else zylos if who == "zylos" else not (skeleton or zylos)


def reach(g: dict[str, set[str]], start: str) -> set[str]:
    """`start` and every kit module it imports, directly or not."""
    seen, todo = set(), [start]
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo += sorted(g.get(m, ()))
    return seen


def uses(repo: Path, rel: str, module: str, via: str | None, g: dict[str, set[str]], src: dict[str, Path]) -> bool:
    """Does the file `rel` use kit module `module`? A kit module or a .py file: it imports it (`from kit import x`,
    `from kit.guards import ssot`, `import kit.x`; with `via`, it imports `via`, and `via` reaches the module). Any other
    file (a README, a script in another language): it names `kit/<path>` or `kit.<dotted>`."""
    text = (repo / rel).read_text(encoding="utf-8")
    if not rel.endswith(".py"):
        return re.search(r"kit[/.]" + r"[/.]".join(map(re.escape, module.split("."))) + r"\b", text) is not None
    mine = next((n for n, p in src.items() if p.relative_to(src[ROOT].parent).as_posix() == rel[4:]), None) if rel.startswith("kit/") else None
    if mine is not None:
        direct = g[mine]
    else:
        direct = set()
        for d in imports(text, ""):
            ds = d.split(".") if d else []
            direct |= {".".join(ds[:i]) for i in range(1, len(ds) + 1)} & set(src)
    target = via or module
    return target in direct and module in reach(g, target)


def problems(root: Path, repo: Path | None = None) -> list[str]:
    """Everything wrong with root/kit-tiers.tsv against root/kit, and its evidence against `repo` (default: root): empty = holds."""
    kit, repo = root / "kit", repo or root
    rows, out = read_rows((root / NAME).read_text(encoding="utf-8"))
    g, glue = graph(kit)
    src = sources(kit)
    modules = set(g) - glue
    version = (kit / "VERSION").read_text(encoding="utf-8").strip()

    first: dict[str, dict] = {}                                          # [1]
    for r in rows:
        if r["module"] in first:
            out.append(f"{r['module']}: listed twice (lines {first[r['module']]['line']} and {r['line']})")
        first.setdefault(r["module"], r)
    out += [f"{m}: a kit module with no row in {NAME}" for m in sorted(modules - set(first))]
    out += [f"{m}: a row for a module that is not in kit/ (line {first[m]['line']})"
            for m in sorted(set(first) - modules)]

    for r in rows:                                                       # [2]
        if r["tier"] not in TIERS:
            out.append(f"{r['module']}: unknown tier {r['tier']!r} (line {r['line']}); "
                       f"the tiers are {', '.join(TIERS)}")

    tier = {m: r["tier"] for m, r in first.items()}                      # [3]
    for m in sorted(modules):
        for d in sorted(g[m]):
            dt = "extra" if d in glue else tier.get(d)   # a subpackage's __init__ serves extra modules
            if tier.get(m) in MAY_IMPORT and dt and dt not in MAY_IMPORT[tier[m]]:
                out.append(f"{tier[m]} module {m} imports {dt} module {d}"
                           + (" (a package's __init__ counts as extra)" if d in glue else ""))
    if g.get(ROOT):
        out.append(f"kit/__init__.py imports {sorted(g[ROOT])}: importing kit must load nothing")

    for r in rows:                                                       # [4] [5]
        m, t = r["module"], r["tier"]
        pairs, bad = consumers(r["consumers"])
        out += [f"{m}: consumer {b!r} is not who:kind, kind one of {', '.join(KINDS)}" for b in bad]
        whos = [w for w, _ in pairs]
        out += [f"{m}: consumer {w!r} listed twice" for w in sorted({w for w in whos if whos.count(w) > 1})]
        out += [f"{m}: consumer {w!r} is not one of {', '.join(WHO)}" for w in sorted({w for w in whos if w not in WHO})]
        out += [f"{m}: {w}:{k}: only {', '.join(HARNESSES)} use a module in production" for w, k in pairs
                if k == "prod" and w in WHO and w not in HARNESSES]
        out += [f"{m}: {w}:{k}: the kind skeleton is for the skeleton, and the skeleton's kind is skeleton or test"
                for w, k in pairs if (k == "skeleton" and w != "skeleton") or (w == "skeleton" and k not in ("skeleton", "test"))]
        counted = sorted({w for w, k in pairs if k in COUNTED})
        harnesses = sorted({w for w, k in pairs if k == "prod" and w in HARNESSES})
        if t == "core" and len(harnesses) < 2:
            out.append(f"core {m}: {len(harnesses)} independent consumer(s) ({', '.join(harnesses) or 'none'}); "
                       f"the rule needs 2 harnesses using it in production"
                       + ("; the skeleton is not a harness" if "skeleton" in whos else ""))
        if t == "extra" and len(counted) > 1:
            out.append(f"extra {m}: {len(counted)} independent consumers ({', '.join(counted)}); at most 1, "
                       f"so promote it or recount")
        if all(k == "fork" for _, k in pairs) and (t != "extra" or "no consumer" not in r["note"].lower()):
            out.append(f"{m}: nothing imports it (forks do not count), so its tier is extra and its note "
                       f"says 'no consumer'")
        for col in ("evidence", "note"):
            if not r[col]:
                out.append(f"{m}: empty {col}")
        mine = repo_paths(r["evidence"])                                 # [5] the evidence in this repository
        for path, via, twin in mine:
            if not (repo / path).is_file():
                out.append(f"{m}: evidence {path} is not a file in this repository")
            elif not twin and m in modules and not uses(repo, path, m, via, g, src):
                out.append(f"{m}: evidence {path} does not use kit.{m}" + (f" through kit.{via}" if via else ""))
        for w, k in pairs:
            if w in HERE and k != "fork" and not any(home(w, p) and not twin for p, _, twin in mine):
                out.append(f"{m}: {w}:{k} has no evidence: a file of this repository "
                           f"{'under scaffold/skeleton/' if w == 'skeleton' else 'under hosts/zylos/' if w == 'zylos' else 'outside both'} that uses it")
        if not SEMVER.fullmatch(r["since"]) or tuple(map(int, r["since"].split("."))) > tuple(
                map(int, version.split("."))):
            out.append(f"{m}: since {r['since']!r} is not a kit version up to {version}")
    return out


def summary(root: Path) -> dict[str, tuple[int, int]]:
    """{tier: (modules, lines)} as the registry has them."""
    rows, _ = read_rows((root / NAME).read_text(encoding="utf-8"))
    src = sources(root / "kit")
    out = {t: (0, 0) for t in TIERS}
    for r in rows:
        if r["tier"] in out and r["module"] in src:
            n, loc = out[r["tier"]]
            out[r["tier"]] = (n + 1, loc + len(src[r["module"]].read_text(encoding="utf-8").splitlines()))
    return out


def unmerged_core(rows: list[dict]) -> list[str]:
    """The core rows whose note says a use rests on an unmerged branch: the count the rule reads is not yet a main's."""
    return [r["module"] for r in rows if r["tier"] == "core" and "unmerged" in r["note"].lower()]


def docs_problems(readme: str, unmerged: list[str] | tuple[str, ...] = ()) -> list[str]:
    """What the README's admission rule lacks; `unmerged` are the core modules that must each be named, with the word
    "unmerged", in one paragraph of that section (the rule says "in production"; the registry trusts its own label)."""
    out = []
    if not re.search(r"^#+ The admission rule$", readme, re.M):
        out.append('no "The admission rule" heading')
    for needle in (NAME, "kit/tests/test_tiers.py", "Packs decide what a harness vendors",
                   "never toward core", "at least two independent harnesses use it in production"):
        if needle not in readme:
            out.append(f"does not say {needle!r}")
    out += [f"does not name the consumer `{w}`" for w in WHO if f"`{w}`" not in readme]
    section = re.search(r"^#+ The admission rule$(.*?)(?=^#{1,3} |\Z)", readme, re.M | re.S)
    paragraphs = section[1].split("\n\n") if section else []
    out += [f"does not say, in the admission rule, that core `{m}` rests on an unmerged branch" for m in unmerged
            if not any(f"`{m}`" in para and "unmerged" in para for para in paragraphs)]
    return out


# --------------------------------------------------------------------- the copy

class Copy:
    """A copy of kit/ (no tests) and the registry, to plant faults in."""

    def __init__(self) -> None:
        self.root = Path(tmp_dir("tiers-"))
        shutil.copytree(KIT, self.root / "kit",
                        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        shutil.copyfile(PLAYBOOK / NAME, self.root / NAME)

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def rows(self) -> list[list[str]]:
        return [ln.split("\t") for ln in self.read(NAME).split("\n") if ln]

    def table(self, rows: list[list[str]]) -> str:
        return "".join("\t".join(r) + "\n" for r in rows)

    def cell(self, module: str, column: str, value: str) -> str:
        """The registry text with one cell changed."""
        rows = self.rows()
        col = COLUMNS.index(column)
        for r in rows:
            if r[0] == module:
                r[col] = value
        return self.table(rows)

    def apply(self, files: dict[str, str | None]) -> dict[Path, str | None]:
        """Write the files (rel path -> new text; None deletes); what undo needs."""
        saved: dict[Path, str | None] = {}
        for rel, text in files.items():
            p = self.root / rel
            saved[p] = p.read_text(encoding="utf-8") if p.exists() else None
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
        return saved

    @staticmethod
    def undo(saved: dict[Path, str | None]) -> None:
        for p, text in saved.items():
            if text is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(text, encoding="utf-8")

    def plant(self, label: str, expect: str, files: dict[str, str | None]) -> None:
        """Plant a fault, expect `expect` in a problem, undo it."""
        saved = self.apply(files)
        try:
            found = problems(self.root, PLAYBOOK)
        finally:
            self.undo(saved)
        check(label, any(expect in f for f in found), found)

    def holds(self, label: str, files: dict[str, str | None]) -> None:
        """Change something the rules allow, expect no problem, undo it."""
        saved = self.apply(files)
        try:
            found = problems(self.root, PLAYBOOK)
        finally:
            self.undo(saved)
        check(label, found == [], found)


def main() -> int:
    print("[0] the registry of this repository")
    real = problems(PLAYBOOK)
    check("kit-tiers.tsv holds: " + (", ".join(f"{t} {n}" for t, (n, _) in summary(PLAYBOOK).items())
                                     + " modules"), real == [], real)
    for tier, (n, loc) in summary(PLAYBOOK).items():
        print(f"        {tier:<6}{n:>3} modules {loc:>6} lines")
    src = sources(KIT)
    check("the graph has the kit: more than 40 modules, guards and testing included",
          len([n for n in src if not src[n].name == "__init__.py"]) > 40
          and {"guards.ssot", "testing.check", "tools.vendor"} <= set(src), sorted(src))
    g, glue = graph(KIT)
    check("the graph sees an import inside a function and through a package",
          "paths" in g["contract"] and "guards.ssot" in g["guards.boundary"]
          and "guards" in g["guards.boundary"], sorted(g["contract"]))
    check("…and the package above a module that never names it (kit/testing/check.py does not import `testing`)",
          "testing" in g["testing.check"]
          and "testing" not in imports(src["testing.check"].read_text(encoding="utf-8"), "testing"),
          sorted(g["testing.check"]))
    check("the registry is outside what a harness vendors",
          not any(NAME in r or "test_tiers" in r for r in manifest.files(KIT)))

    cp = Copy()
    check("the copy starts clean", problems(cp.root, PLAYBOOK) == [], problems(cp.root, PLAYBOOK))
    table = cp.read(NAME)
    cell = cp.cell

    print("\n[1] every module classified once, no row for a missing module")
    cp.plant("a new module with no row", "newmod: a kit module with no row",
             {"kit/newmod.py": '"""x"""\nX = 1\n'})
    cp.plant("a new module in a new package with no row", "extras.thing: a kit module with no row",
             {"kit/extras/thing.py": '"""x"""\nX = 1\n'})
    cp.plant("a module deleted, its row left", "retry: a row for a module that is not in kit/",
             {"kit/retry.py": None})
    cp.plant("a row for a module that never existed", "gone: a row for a module that is not in kit/",
             {NAME: table + "gone\textra\t\tx\t0.1.0\tno consumer\n"})
    cp.plant("the same module twice", "config: listed twice",
             {NAME: table + table.split("\n")[1] + "\n"})

    print("\n[2] the tiers are a closed list")
    cp.plant("an unknown tier", "config: unknown tier 'nucleus'", {NAME: cell("config", "tier", "nucleus")})
    cp.plant("an empty tier", "config: unknown tier ''", {NAME: cell("config", "tier", "")})
    cp.plant("a tier in the wrong case", "config: unknown tier 'Core'", {NAME: cell("config", "tier", "Core")})

    print("\n[3] layering: core imports core, stack imports core and stack")
    dates, config, paths, db = (cp.read(f"kit/{m}.py") for m in ("dates", "config", "paths", "db"))
    cp.plant("core imports extra", "core module dates imports extra module doctor",
             {"kit/dates.py": dates + "\nfrom kit import doctor\n"})
    cp.plant("core imports stack, inside a function", "core module config imports stack module db",
             {"kit/config.py": config + "\n\ndef _late():\n    from kit import db\n    return db\n"})
    cp.plant("core imports stack by `import kit.x`", "core module dates imports stack module market",
             {"kit/dates.py": dates + "\nimport kit.market\n"})
    cp.plant("core imports by a relative import", "core module dates imports extra module runner",
             {"kit/dates.py": dates + "\nfrom . import runner\n"})
    cp.plant("core imports a module of a package", "core module paths imports extra module guards.ssot",
             {"kit/paths.py": paths + "\nfrom kit.guards import ssot\n"})
    cp.plant("core imports a package's __init__",
             "core module paths imports extra module guards (a package's __init__ counts as extra)",
             {"kit/paths.py": paths + "\nfrom kit import guards\n"})
    cp.plant("a stack module in a package depends on the package's __init__, though it never imports it",
             "stack module testing.check imports extra module testing (a package's __init__ counts as extra)",
             {NAME: cell("testing.check", "tier", "stack")})
    cp.plant("stack imports extra", "stack module db imports extra module cli",
             {"kit/db.py": db + "\nfrom kit import cli\n"})
    cp.plant("a row moved up to core breaks the graph under it", "core module registry imports stack module market",
             {NAME: cell("registry", "tier", "core")})
    cp.plant("a row moved down to extra breaks the stack over it", "stack module facts imports extra module human",
             {NAME: cell("human", "tier", "extra")})
    cp.plant("kit/__init__.py loads a module", "kit/__init__.py imports ['dates']",
             {"kit/__init__.py": cp.read("kit/__init__.py") + "\nfrom kit import dates\n"})

    print("\n[4] admission: core needs two independent consumers, extra has at most one")
    cp.plant("a core row with one consumer", "core dates: 1 independent consumer(s) (video-ads)",
             {NAME: cell("dates", "consumers", "video-ads:prod")})
    cp.plant("a core row whose other consumers are a test and a fork", "core dates: 1 independent consumer(s)",
             {NAME: cell("dates", "consumers", "video-ads:prod; kol:test; seo:fork")})
    cp.plant("a core row with no consumer", "core dates: 0 independent consumer(s) (none)",
             {NAME: cell("dates", "consumers", "")})
    cp.plant("one harness listed twice", "copylint: consumer 'kol' listed twice",
             {NAME: cell("copylint", "consumers", "kol:prod; kol:skeleton")})
    cp.plant("an extra row with two consumers", "extra takes: 2 independent consumers (kol, video-ads); at most 1",
             {NAME: cell("takes", "consumers", "video-ads:prod; kol:prod")})
    cp.plant("the skeleton is a consumer too", "extra takes: 2 independent consumers (skeleton, video-ads)",
             {NAME: cell("takes", "consumers", "video-ads:prod; skeleton:skeleton")})
    cp.plant("a module nobody uses, its note silent", "guards.evals: nothing imports it",
             {NAME: cell("guards.evals", "note", "A removal candidate.")})
    cp.plant("a module nobody uses, in stack", "guards.evals: nothing imports it",
             {NAME: cell("guards.evals", "tier", "stack")})
    cp.plant("a module only a fork knows, its note silent", "retry: nothing imports it",
             {NAME: cell("retry", "consumers", "outreach:fork")})
    cp.holds("tests, tools and forks never count: an extra row may list any number of them",
             {NAME: cell("takes", "consumers", "video-ads:prod; kol:test; playbook:fork; seo:fork; outreach:fork")})
    cp.plant("the skeleton and one harness are not two harnesses: kol and rednote are built from it",
             "core config: 1 independent consumer(s) (kol); the rule needs 2 harnesses using it in production; "
             "the skeleton is not a harness",
             {NAME: cell("config", "consumers", "skeleton:skeleton; kol:prod")})
    cp.plant("two consumers that are no harness (a skeleton and a test) are not two",
             "core config: 0 independent consumer(s) (none)",
             {NAME: cell("config", "consumers", "skeleton:skeleton; zylos:test")})
    cp.holds("two harnesses in production: a core row is clean",
             {NAME: cell("config", "consumers", "video-ads:prod; kol:prod")})
    cp.holds("stack has no count rule: one consumer, or five",
             {NAME: cell("market", "consumers", "kol:prod; rednote:prod; video-ads:prod")})

    print("\n[5] the registry's shape")
    cp.plant("an invented consumer is not a second harness", "dates: consumer 'kol2' is not one of video-ads, kol, rednote",
             {NAME: cell("dates", "consumers", "video-ads:prod; kol2:prod")})
    cp.plant("a consumer name with a typo", "config: consumer 'rednot' is not one of",
             {NAME: cell("config", "consumers", "kol:prod; rednot:prod")})
    cp.plant("a production use by a project that does not vendor the kit", "retry: outreach:prod: only video-ads, kol, rednote",
             {NAME: cell("retry", "consumers", "kol:prod; outreach:prod")})
    cp.plant("the zylos adapter is not a harness", "env: zylos:prod: only video-ads, kol, rednote",
             {NAME: cell("env", "consumers", "zylos:prod; seo:fork; source:fork")})
    cp.plant("the kind skeleton under a harness", "retry: kol:skeleton: the kind skeleton is for the skeleton",
             {NAME: cell("retry", "consumers", "kol:skeleton")})
    cp.plant("the skeleton as production code", "execute: skeleton:prod: only video-ads, kol, rednote",
             {NAME: cell("execute", "consumers", "skeleton:prod")})
    cp.plant("the skeleton as a fork", "execute: skeleton:fork: the kind skeleton is for the skeleton, and the skeleton's kind",
             {NAME: cell("execute", "consumers", "skeleton:fork")})
    cp.plant("evidence in this repository that is not a file", "queue: evidence scaffold/skeleton/scripts/NO_SUCH_FILE.py is not a file",
             {NAME: cell("queue", "evidence", "scaffold/skeleton/scripts/NO_SUCH_FILE.py; kol:scripts/_lib/steer.py")})
    cp.plant("evidence that is no file of this repository", "queue: skeleton:skeleton has no evidence",
             {NAME: cell("queue", "evidence", "README.md")})
    cp.plant("evidence in the wrong home: the skeleton's use shown by a playbook file",
             "queue: skeleton:skeleton has no evidence: a file of this repository under scaffold/skeleton/",
             {NAME: cell("queue", "evidence", "scaffold/new_harness.py")})
    cp.plant("evidence that does not import the module", "queue: evidence scaffold/skeleton/scripts/pending.py does not use kit.queue",
             {NAME: cell("queue", "evidence", "scaffold/skeleton/scripts/pending.py")})
    cp.plant("the zylos evidence of a file that does not import kit.env",
             "env: evidence hosts/zylos/tests/test_manifest.py does not use kit.env",
             {NAME: cell("env", "evidence", "hosts/zylos/tests/test_manifest.py:21 (from kit.env import parse); seo:scripts/_lib/envchain.py")})
    cp.plant("the zylos evidence, KEEP: tools.vendor named by a README that does not name it",
             "tools.vendor: evidence hosts/zylos/tests/run.py does not use kit.tools.vendor",
             {NAME: cell("tools.vendor", "evidence", "scaffold/new_harness.py; hosts/zylos/tests/run.py")})
    cp.plant("a (via X) whose X the file does not import", "guards.ssot: evidence scaffold/skeleton/tests/test_ssot.py does not use "
             "kit.guards.ssot through kit.db",
             {NAME: cell("guards.ssot", "evidence", "scaffold/skeleton/tests/test_ssot.py (via db)")})
    cp.plant("a (via X) whose X does not reach the module", "guards.ssot: evidence scaffold/skeleton/tests/test_ssot.py does not use "
             "kit.guards.ssot through kit.testing.check",
             {NAME: cell("guards.ssot", "evidence", "scaffold/skeleton/tests/test_ssot.py (via testing.check)")})
    cp.plant("a kit module named as evidence that does not import the module", "schema_base: evidence kit/cli.py does not use kit.schema_base",
             {NAME: cell("schema_base", "evidence", "kit/cli.py; scaffold/skeleton/tests/test_human_tables.py (via testing.suites)")})
    cp.holds("a twin may be any file: it is not a user", {NAME: cell("guards.clock", "evidence",
             "scaffold/skeleton/tests/test_clock.py (via testing.suites); templates/tests/test_layering.py (standalone twin)")})
    cp.holds("a fork's evidence is not read: only its consumers' files are", {NAME: cell("pull", "evidence",
             "source:scripts/pull_spapi.py; ast scan of the production code of video-ads, kol and rednote")})
    cp.plant("a header that changed", "the header is not module",
             {NAME: table.replace("module\ttier", "name\ttier", 1)})
    cp.plant("a row with a column missing", "5 columns, not 6", {NAME: table + "x\textra\ta\tb\tc\n"})
    cp.plant("a consumer that is not who:kind", "retry: consumer 'kol' is not who:kind",
             {NAME: cell("retry", "consumers", "kol")})
    cp.plant("a consumer kind outside the list", "retry: consumer 'kol:wip' is not who:kind",
             {NAME: cell("retry", "consumers", "kol:wip")})
    cp.plant("no evidence", "retry: empty evidence", {NAME: cell("retry", "evidence", "")})
    cp.plant("no note", "retry: empty note", {NAME: cell("retry", "note", "")})
    cp.plant("a since that is not a version", "retry: since 'v1' is not a kit version",
             {NAME: cell("retry", "since", "v1")})
    cp.plant("a since newer than the kit", "retry: since '99.0.0' is not a kit version",
             {NAME: cell("retry", "since", "99.0.0")})

    check("after every plant was undone, the copy is clean again",
          problems(cp.root, PLAYBOOK) == [], problems(cp.root, PLAYBOOK))

    print("\n[6] the docs say what vendoring does")
    dest = Path(tmp_dir("tiers-vendored-")) / "kit"
    vendor.sync(KIT, dest, {})
    rows, _ = read_rows((PLAYBOOK / NAME).read_text(encoding="utf-8"))
    gone = [r["module"] for r in rows
            if r["module"] in src and not (dest / src[r["module"]].relative_to(KIT)).is_file()]
    check("vendor.py copies every module of every tier", not gone and len(rows) > 40, gone)
    check("…and only kit/: the registry and this test stay behind",
          not (dest / NAME).exists() and not (dest / "tests").exists()
          and not (dest.parent / NAME).exists())
    rc, out, _ = capture(vendor.main, ["--help"])
    check("vendor.py has no option that picks tiers (the README says none exists)",
          rc == 0 and "--harness" in out and not re.search(r"\btiers?\b", out, re.I), out)

    print("\n[7] docs/kit-and-tools.md names the registry and this test")
    readme = (PLAYBOOK / "docs" / "kit-and-tools.md").read_text(encoding="utf-8")
    check("the README has the admission rule, the registry, the test and the plain statement",
          docs_problems(readme) == [], docs_problems(readme))
    check("the check fails on a README without the section",
          len(docs_problems("# Agent Harness Playbook\n")) == 1 + 5 + len(WHO)
          and docs_problems(readme.replace("Packs decide what a harness vendors", "x"))
          == ["does not say 'Packs decide what a harness vendors'"])
    check("…on a README that lets the skeleton count toward core, or leaves a consumer name out",
          docs_problems(readme.replace("never toward core", "also toward core"))
          == ["does not say 'never toward core'"]
          and docs_problems(readme.replace("`outreach`", "outreach")) == ["does not name the consumer `outreach`"])
    live = read_rows((PLAYBOOK / NAME).read_text(encoding="utf-8"))[0]
    check("the README says, in the admission rule, which core rows rest on an unmerged branch (the registry's FRAGILE notes)",
          docs_problems(readme, unmerged_core(live)) == [], docs_problems(readme, unmerged_core(live)))
    rows = [{"module": "a", "tier": "core", "note": "FRAGILE: its second use is on an Unmerged branch"},
            {"module": "b", "tier": "extra", "note": "unmerged"}, {"module": "c", "tier": "core", "note": "merged"}]
    doc = "### The admission rule\n\nEvery module has a tier.\n\nCore `a` rests on an unmerged branch.\n\n---\n\n## Next\n"
    def gap(text: str) -> list[str]:
        return [p for p in docs_problems(text, ["a"]) if "unmerged branch" in p]
    check("…the check reads core rows only, and needs the module and the word in one paragraph of that section",
          unmerged_core(rows) == ["a"] and unmerged_core([]) == [] and gap(doc) == []
          and all(gap(doc.replace(a, b)) for a, b in (
              ("`a`", "a"), ("unmerged", "merged"), ("Core `a` rests on an unmerged branch.", "Core."),
              ("\n\nCore `a`", "\n\n## Next\n\nCore `a`"),
              ("Core `a` rests on an unmerged branch.", "Core `a`.\n\nAn unmerged branch."))),
          docs_problems(doc, ["a"]))
    check("the names the README gives are the names the registry may use",
          {c for r in read_rows((PLAYBOOK / NAME).read_text(encoding="utf-8"))[0]
           for c, _ in consumers(r["consumers"])[0]} <= set(WHO))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
