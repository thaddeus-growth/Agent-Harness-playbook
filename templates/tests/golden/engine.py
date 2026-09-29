#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Golden diff: every case of a harness, BASE vs HEAD, on the same data.

A refactor must leave every `--json` document and every page exactly as it
was. This tool runs one tree's cases (listed by cases.py, beside this file),
one file per case, and `compare` diffs two trees. It is a tool, not a test:
the test runner never collects it and a release never ships it.

    uv run tests/golden/engine.py compare BASE HEAD [--strict] [--keep DIR]
                                         [--today YYYY-MM-DD | --data DIR]
    uv run tests/golden/engine.py snapshot OUT [--tree T] [--today … | --data DIR]
    uv run tests/golden/engine.py diff A B [--strict]

Trees. BASE, HEAD, T: a checkout's path (`.` for uncommitted work), or a git
rev of the repository this file is in, checked out detached under $TMPDIR and
removed and pruned afterwards, also on a failure, SIGTERM or SIGHUP. OUT and
--keep must be empty or new, and outside any git work tree: a snapshot holds
data, a client's with --data. $TMPDIR must be outside one too.

Data. Default: a sandbox per side, seeded by that side's own verbs
(cases.seed) from BASE's cases.FIXTURES, so a fixture edit shows as a diff.
--data DIR: one copy of a live data folder, taken while no file in it
changed (sizes and mtimes; retried, then refused), holding the targets of
links, never a link, so no case can write through one; then one copy of
that per side. DIR is only read, and never printed.

Cases run one at a time (a database opened for writing makes parallel runs
flaky), each in a child process of its own session, killed with everything
it started when it ends or the run stops. The child (`_run`) puts the tree on
sys.path, patches every clock seam (cases.SEAMS) to one instant and the
runner seam (cases.RUNNER) so a verb's own child scripts run the same way
under the same instant, then runs the script with runpy. Its env is an
allowlist (cases.ENV_KEEP, ENV_PREFIXES, ENV_SET, plus cases.DATA_ENV): no
credential, no write switch, no confirm secret, and never PYTHONHASHSEED, so
a report that depends on set order differs between two runs of one tree. A
case carrying one of cases.REFUSE_ARGS is refused before anything runs.
Pages (cases.pages) render in one more child, under the same pin.

Pin: the run's start; with --today another day, that day at 12:00Z (with
--data always the start: a live folder is judged at the real now). A run
that crosses a UTC midnight fails: run it again.

Masked, because they are not behaviour: the sandbox, work, tree and data
paths (<DATA_DIR>, <WORK>, <TREE>, <DATA_SRC>), and a timestamp inside the
run's own wall-clock span (5 minutes either side) -> <NOW>. Nothing else. A
stamp from the pinned clock falls in that span unless --today moves it out.

Diff: the union of cases. Exit 1 on a removed case or key (REMOVED), a
changed value or type (CHANGED), a list length (LIST-LEN), an exit code (RC),
a document that is not one JSON document (NOT-JSON), a page that raised, or
any HTML difference (shown split on '><'). An added key or case fails only
with --strict: refactors run --strict, features may add.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import difflib
import importlib
import importlib.util
import json
import os
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
MARGIN = dt.timedelta(minutes=5)
CASE_TIMEOUT_S = 1800
FREEZE_TRIES = 3
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
SLOT = re.compile(r"\{[a-z_]+\}")
BAD = ("__not_json__", "__render_failed__")


def _load_cases():
    """cases.py beside this file, loaded by path: a harness module that is
    also called `cases` can never shadow it, or be shadowed."""
    spec = importlib.util.spec_from_file_location("golden_cases",
                                                  HERE.with_name("cases.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _load_cases()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- children
def pinned_argv(tree: str, pin: str, script, args) -> list[str]:
    """The argv that runs `script` of `tree` with `args` under the pin."""
    return [sys.executable, str(HERE), "_run", str(tree), pin, str(script),
            *map(str, args)]


def pin_seams(tree: str, pin: str) -> None:
    """Replace, in this child process, every clock seam the tree has with
    one instant, and its runner seam so each child script it starts is
    pinned too. A seam module the tree does not have yet (an older BASE) is
    skipped; any other import error is the tree's own and surfaces."""
    at = dt.datetime.fromisoformat(pin)
    value = {"now": at, "today": at.date()}

    def module(name: str):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name == name or name.startswith(f"{e.name}."):
                return None
            raise

    for mod, name, kind in C.SEAMS:
        m = module(mod)
        if m is not None and callable(getattr(m, name, None)):
            setattr(m, name, lambda *a, _v=value[kind], **k: _v)
    if C.RUNNER:
        m = module(C.RUNNER[0])
        if m is not None and callable(getattr(m, C.RUNNER[1], None)):
            setattr(m, C.RUNNER[1], lambda script, args, *a, **k: pinned_argv(
                tree, pin, script, args))


def _run(tree: str, pin: str, script: str, *argv: str) -> None:
    """Child: run `script` of `tree` as __main__ under the pin."""
    path = os.path.join(tree, script)
    sys.path[:1] = [os.path.dirname(path),
                    *(os.path.join(tree, p) for p in C.SYS_PATH)]
    pin_seams(tree, pin)
    sys.argv = [path, *argv]
    runpy.run_path(path, run_name="__main__")


def _pages(tree: str, pin: str, out: str, slots: str) -> int:
    """Child: every page cases.pages gives for `tree`, one HTML file each in
    `out`; 1 when a page raised (its file says why)."""
    sys.path[:1] = [os.path.join(tree, p) for p in C.SYS_PATH]
    env = dict(os.environ)

    def run(argv, stdin=None):
        return run_case(pinned_argv(tree, pin, C.ENTRY, argv), env, tree,
                        stdin)

    failed = []
    pin_seams(tree, pin)
    for name, render in C.pages(Path(tree), run, json.loads(slots)):
        if not NAME.fullmatch(name):
            raise SystemExit(f"page name {name!r}: A-Z a-z 0-9 _ . - only")
        try:
            html = render()
        except Exception as e:
            html = f"RENDER FAILED: {type(e).__name__}: {e}"
            failed.append(name)
        Path(out, f"{name}.html").write_text(html, encoding="utf-8")
    if failed:
        print(f"RENDER FAILED: {failed}", file=sys.stderr)
    return 1 if failed else 0


CHILDREN = {"_run": _run, "_pages": _pages}


def run_case(argv: list[str], env: dict, cwd, stdin: str | None = None,
             timeout: float = CASE_TIMEOUT_S) -> subprocess.CompletedProcess:
    """One child in a session of its own. Whatever happens (it ends, times
    out, or this run is stopped), its whole process group is killed, so no
    case outlives its run or writes into a tree already removed."""
    p = subprocess.Popen(argv, env=env, cwd=cwd, text=True,
                         start_new_session=True,
                         stdin=subprocess.DEVNULL if stdin is None
                         else subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"a case gave no answer in {timeout:.0f}s: "
                         f"{' '.join(argv[5:8])}")
    finally:
        with contextlib.suppress(OSError):
            os.killpg(p.pid, signal.SIGKILL)
        if p.returncode is None:
            p.wait()
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


# ------------------------------------------------------------------ a tree
def verbs_of(tree: Path) -> set[tuple[str, ...]]:
    """The keys of cases.ENTRY's cases.VERB_TABLE, read with ast: importing
    a dispatcher may load the operator's env files and credentials."""
    src = (tree / C.ENTRY).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, ast.AnnAssign) else [])
        if (any(getattr(t, "id", None) == C.VERB_TABLE for t in targets)
                and isinstance(node.value, ast.Dict)):
            keys = [ast.literal_eval(k) for k in node.value.keys if k]
            return {tuple(k) if isinstance(k, (tuple, list)) else (k,)
                    for k in keys}
    raise SystemExit(f"no {C.VERB_TABLE} dict in a tree's {C.ENTRY}")


def has_verb(args: list[str], verbs: set) -> bool:
    words = args[:args.index("--")] if "--" in args else args
    return any(tuple(words[:n]) in verbs for n in range(1, len(words) + 1))


def checked(cases: dict) -> dict:
    """cases.plan's cases, refused whole if one name is unsafe as a file
    name or one argument is a write switch."""
    for name, argv in cases.items():
        if not NAME.fullmatch(name):
            raise SystemExit(f"case name {name!r}: A-Z a-z 0-9 _ . - only")
        bad = sorted({a for a in argv if str(a).split("=")[0]
                      in C.REFUSE_ARGS})
        if bad:
            raise SystemExit(f"refused case {name}: {' '.join(bad)}")
    return cases


def case_env(data: Path) -> dict:
    """The only env a case sees: the allowlist, the fixed values, the data
    folder. Never PYTHONHASHSEED: an unpinned seed is what makes a set-order
    bug show as a diff between two runs of the same tree."""
    env = {k: v for k, v in os.environ.items()
           if k in C.ENV_KEEP or k.startswith(tuple(C.ENV_PREFIXES))}
    env.update(C.ENV_SET)
    env[C.DATA_ENV] = str(data)
    env.pop("PYTHONHASHSEED", None)
    return env


class Masker:
    """Replaces what is not behaviour: temp paths, and a timestamp inside
    the run's own wall-clock span. A stamp outside it is data, kept."""
    STAMP = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?"
                       r"(?:Z|[+-]\d\d:?\d\d)?")

    def __init__(self, paths: list, start: dt.datetime, end: dt.datetime):
        pairs = {v: tok for p, tok in paths if p
                 for v in (str(p), os.path.realpath(p))}
        self.paths = sorted(pairs.items(), key=lambda x: -len(x[0]))
        self.t0, self.t1 = start - MARGIN, end + MARGIN

    def _in_run(self, s: str) -> bool:
        try:
            t = dt.datetime.fromisoformat(s)
        except ValueError:
            return False
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return self.t0 <= t <= self.t1

    def text(self, s: str) -> str:
        for p, tok in self.paths:
            s = s.replace(p, tok)
        return self.STAMP.sub(lambda m: "<NOW>" if self._in_run(m.group())
                              else m.group(), s)

    def __call__(self, node):
        if isinstance(node, dict):
            return {self.text(k): self(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self(x) for x in node]
        return self.text(node) if isinstance(node, str) else node


def freeze(src: Path, dest: Path) -> None:
    """One copy of the live data folder `src` at `dest`, taken while no file
    in it changed (sizes and mtimes; a SQLite -shm aside, every reader
    touches it), so a pull or ingest writing meanwhile cannot tear it. Link
    targets are copied, never links: a case may open a file for writing.
    Only reads `src`, and no error names it."""
    def state() -> dict:
        out = {}
        for d, _dirs, files in os.walk(src, followlinks=True):
            for n in files:
                if not n.endswith("-shm"):
                    st = os.stat(os.path.join(d, n))
                    out[os.path.relpath(d, src), n] = (st.st_size,
                                                       st.st_mtime_ns)
        return out
    for _ in range(FREEZE_TRIES):
        try:
            before = state()
            shutil.copytree(src, dest)
            if state() == before:
                break
        except OSError as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise SystemExit(f"--data: copy failed ({type(e).__name__})")
        shutil.rmtree(dest)
    else:
        raise SystemExit(f"--data: the folder changed while it was copied, "
                         f"{FREEZE_TRIES} times; retry once its writers "
                         f"are done")
    if any(p.is_symlink() for p in dest.rglob("*")):
        shutil.rmtree(dest)
        raise SystemExit("--data: the copy holds a link")


def snapshot(tree: Path, out: Path, work: Path, pin: dt.datetime,
             start: dt.datetime, fixtures: Path | None = None,
             data_src: Path | None = None) -> int:
    """Every case of `tree` into `out`: <case>.json = {rc, doc}, masked, and
    <page>.html. `work` is this side's scratch folder (the caller deletes
    it); with `data_src` the caller put its copy at work/data, else it is
    seeded from `fixtures`. 1 when a case printed no JSON or a page raised."""
    data = work / "data"
    env = case_env(data)
    at = pin.isoformat()

    def run(argv, stdin=None):
        return run_case(pinned_argv(str(tree), at, C.ENTRY, argv), env, tree,
                        stdin)

    if not data_src:
        data.mkdir(parents=True)
        C.seed(run, fixtures or tree / C.FIXTURES)
    verbs = verbs_of(tree)
    cases = checked(C.plan(verbs))
    slots = {"{today}": pin.date().isoformat(), "{month}": pin.strftime(
        "%Y-%m"), "{work}": str(work)}
    results: dict[str, tuple] = {}

    def attempt(name: str) -> bool:
        """Run one case; False while it waits on a slot no case filled."""
        args = [slots.get(a, a) for a in cases[name]]
        if any(a is None or SLOT.fullmatch(str(a)) for a in args):
            return False
        args = [str(a) for a in args]
        if has_verb(args, verbs):
            p = run(args)
            try:
                doc = json.loads(p.stdout)
            except ValueError:
                doc = {"__not_json__": p.stdout[-2000:],
                       "__stderr__": p.stderr[-2000:]}
            results[name] = (p.returncode, doc)
            C.learn(name, {"rc": p.returncode, "doc": doc,
                           "stdout": p.stdout}, slots)
        return True

    waiting = list(cases)
    while waiting:          # a case waiting on a slot runs once it is filled
        later = [n for n in waiting if not attempt(n)]
        if len(later) == len(waiting):
            break
        waiting = later
    html = work / "html"
    html.mkdir()
    p = run_case([sys.executable, str(HERE), "_pages", str(tree), at,
                  str(html), json.dumps(slots)], env, tree)
    if p.returncode:
        results["_pages"] = (p.returncode, {"__render_failed__":
                                            p.stderr[-2000:]})
    mask = Masker([(data, "<DATA_DIR>"), (work, "<WORK>"), (tree, "<TREE>"),
                   (data_src, "<DATA_SRC>")], start, utcnow())
    out.mkdir(parents=True, exist_ok=True)
    for name, (rc, doc) in results.items():
        (out / f"{name}.json").write_text(json.dumps(
            {"rc": rc, "doc": mask(doc)}, indent=1, sort_keys=True,
            ensure_ascii=False) + "\n", encoding="utf-8")
    pages = sorted(html.glob("*.html"))
    for f in pages:
        (out / f.name).write_text(mask.text(f.read_text(encoding="utf-8")),
                                  encoding="utf-8")
    bad = sorted(n for n, (_, d) in results.items()
                 if isinstance(d, dict) and any(k in d for k in BAD))
    print(f"{len(results)} cases + {len(pages)} pages -> {out} (pin {at})"
          + (f"; waiting on a slot: {waiting}" if waiting else "")
          + (f"; FAILED: {bad}" if bad else ""), flush=True)
    return 1 if bad else 0


# -------------------------------------------------------------------- diff
def walk(a, b, path: str, out: list) -> None:
    """Every difference between documents a (BASE) and b (HEAD) into out."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}"
            if k not in a:
                out.append(("added", p, b[k]))
            elif k not in b:
                out.append(("REMOVED", p, a[k]))
            else:
                walk(a[k], b[k], p, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(("LIST-LEN", path, (len(a), len(b))))
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out)
    elif a != b or type(a) is not type(b):
        out.append(("CHANGED", path, (a, b)))


def failed_doc(case: dict) -> bool:
    return isinstance(case["doc"], dict) and any(k in case["doc"] for k in BAD)


def diff(a: Path, b: Path, strict: bool = False) -> int:
    """Snapshot A (BASE) vs B (HEAD) case by case; 1 when anything fails."""
    na = {p.name for p in a.iterdir() if p.suffix in (".json", ".html")}
    nb = {p.name for p in b.iterdir() if p.suffix in (".json", ".html")}
    fails = 0
    for n in sorted(na - nb):
        print(f"REMOVED case {n}")
        fails += 1
    for n in sorted(nb - na):
        print(f"added case {n}")
        fails += strict
    differ = 0
    for n in sorted(na & nb):
        ta = (a / n).read_text(encoding="utf-8")
        tb = (b / n).read_text(encoding="utf-8")
        if n.endswith(".html"):
            if ta != tb:
                differ += 1
                print(f"{n}: HTML differs")
                for ln in list(difflib.unified_diff(
                        ta.split("><"), tb.split("><"), "BASE", "HEAD", n=1,
                        lineterm=""))[2:42]:
                    print(f"    {ln[:200]}")
            continue
        ca, cb = json.loads(ta), json.loads(tb)
        found = []
        if ca["rc"] != cb["rc"]:
            found.append(("RC", "", (ca["rc"], cb["rc"])))
        if failed_doc(ca) or failed_doc(cb):
            found.append(("NOT-JSON", "", (failed_doc(ca), failed_doc(cb))))
        walk(ca["doc"], cb["doc"], "", found)
        if not found:
            continue
        failing = strict or any(k != "added" for k, _, _ in found)
        differ += failing
        kinds: dict[str, int] = {}
        for k, _, _ in found:
            kinds[k] = kinds.get(k, 0) + 1
        print(f"{n}: rc {ca['rc']}/{cb['rc']} {json.dumps(kinds)}")
        for k, p, v in found[:25]:
            print(f"    {k:8} {p}  "
                  f"{json.dumps(v, ensure_ascii=False, default=str)[:240]}")
    fails += differ
    print(f"{len(na & nb)} common cases, {len(nb - na)} added, "
          f"{len(na - nb)} removed, {differ} differ"
          + (" (--strict)" if strict else ""))
    return 1 if fails else 0


# ------------------------------------------------------------------- main
def repo_root() -> Path | None:
    """The git work tree this file is in (None outside one)."""
    p = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse",
                        "--show-toplevel"], text=True, capture_output=True)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], text=True,
                          capture_output=True)


def checkout(spec: str, dest: Path, repo: Path | None,
             worktrees: list) -> Path:
    """A tree: `spec` as a path, else a git rev of `repo` checked out
    detached at `dest` (listed in `worktrees` for the caller to remove)."""
    if Path(spec).is_dir():
        tree = Path(spec).resolve()
        if not (tree / C.ENTRY).is_file():
            raise SystemExit(f"{spec}: no {C.ENTRY}")
        return tree
    rev = git(repo, "rev-parse", "--verify", "--quiet",
              f"{spec}^{{commit}}") if repo else None
    if not rev or rev.returncode:
        raise SystemExit(f"{spec}: neither a directory nor a git rev")
    worktrees.append(dest)
    p = git(repo, "worktree", "add", "--detach", "--quiet", str(dest),
            rev.stdout.strip())
    if p.returncode:
        raise SystemExit(f"git worktree add {spec}: {p.stderr.strip()}")
    return dest


def outside(path: Path, roots) -> Path:
    """`path`, resolved; refused inside a git work tree (any folder with a
    .git entry) or one of `roots`, and when it is not empty."""
    p = path.resolve()
    for a in (p, *p.parents):
        if a in roots or (a / ".git").exists():
            raise SystemExit(f"refused: {path} is inside a checkout ({a}): "
                             f"golden output holds data; write it outside")
    if p.exists() and any(p.iterdir()):
        raise SystemExit(f"refused: {path} is not empty")
    return p


def the_pin(today: dt.date | None, data: Path | None,
            start: dt.datetime) -> dt.datetime:
    if data or not today or today == start.date():
        return start
    return dt.datetime.combine(today, dt.time(12), dt.timezone.utc)


def _stop(signum, _frame):
    raise SystemExit(128 + signum)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="engine.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="one tree's cases into OUT")
    s.add_argument("out", type=Path)
    s.add_argument("--tree", help="a checkout's path or a git rev "
                                  "(default: this checkout, as it is)")
    c = sub.add_parser("compare", help="snapshot BASE and HEAD, diff them")
    c.add_argument("base")
    c.add_argument("head")
    c.add_argument("--keep", type=Path, help="keep both snapshots here")
    c.add_argument("--strict", action="store_true",
                   help="an added key or case fails too (refactors)")
    for x in (s, c):
        g = x.add_mutually_exclusive_group()
        g.add_argument("--today", type=dt.date.fromisoformat,
                       help="pin this UTC day, at 12:00Z (default: now)")
        g.add_argument("--data", type=Path, metavar="DIR",
                       help="a copy of this data folder instead of the "
                            "fixture sandbox")
    d = sub.add_parser("diff", help="two snapshot folders, A = BASE")
    d.add_argument("a", type=Path)
    d.add_argument("b", type=Path)
    d.add_argument("--strict", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "diff":
        return diff(args.a, args.b, args.strict)
    if args.data and not args.data.is_dir():
        raise SystemExit("--data: not a directory")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _stop)
    start = utcnow()
    pin = the_pin(args.today, args.data, start)
    data = args.data.resolve() if args.data else None
    repo = repo_root()
    root = Path(tempfile.mkdtemp(prefix="golden_")).resolve()
    worktrees: list[Path] = []
    try:
        specs = ([args.tree or str(repo or ".")] if args.cmd == "snapshot"
                 else [args.base, args.head])
        roots = {r for r in (repo, *(Path(x).resolve() for x in specs
                                     if Path(x).is_dir())) if r}
        outside(root, roots)
        if args.cmd == "snapshot":
            out = outside(args.out, roots)
            tree = checkout(specs[0], root / "tree", repo, worktrees)
            if data:
                freeze(data, root / "work" / "data")
            rc = snapshot(tree, out, root / "work", pin, start, data_src=data)
        else:
            keep = outside(args.keep, roots) if args.keep else root / "out"
            base = checkout(args.base, root / "tree-base", repo, worktrees)
            head = checkout(args.head, root / "tree-head", repo, worktrees)
            if data:    # taken once: both sides read the same state
                freeze(data, root / "work-head" / "data")
                shutil.copytree(root / "work-head" / "data",
                                root / "work-base" / "data")
            fixtures = base / C.FIXTURES
            rc = snapshot(base, keep / "base", root / "work-base", pin, start,
                          fixtures, data)
            rc |= snapshot(head, keep / "head", root / "work-head", pin,
                           start, fixtures, data)
            rc |= diff(keep / "base", keep / "head", args.strict)
        if utcnow().date() != start.date():
            print("FAILED: the run crossed a UTC midnight; run it again")
            rc = 1
        return rc
    finally:
        for wt in worktrees:
            git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(root, ignore_errors=True)
        if worktrees:
            git(repo, "worktree", "prune")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in CHILDREN:
        sys.exit(CHILDREN[sys.argv[1]](*sys.argv[2:]))
    sys.exit(main())
