"""The tiny test harness every build/tests/test_*.py ends with:

    if __name__ == "__main__": _t.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0, and `run.py` treats a file without that line as failed. A file
that defines no test, or runs under `python -O` (asserts would be skipped, so
nothing could fail), does not pass either. Same rules as console/tests/_t.py.

Helpers: `tmpdir()`, `tool()` (one build script as a process, its JSON read
back), `ask()` (the real console/ask.py), `owner_answers()` (the owner's side,
in-process with console/core.py, because ask.py has no verb for it), and
`workspace()` (a repo with ssot/ copied from templates/ssot/, a data folder
outside it and a console folder).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
ROOT = os.path.dirname(BUILD)
CONSOLE = os.path.join(ROOT, "console")
ASK = os.path.join(CONSOLE, "ask.py")
FIXTURES = os.path.join(HERE, "fixtures")
FIX1 = os.path.join(FIXTURES, "2026-03-02-northwind.intake.json")
FIX2 = os.path.join(FIXTURES, "2026-03-16-northwind.intake.json")
for _k in ("CONSOLE_DIR", "CONSOLE_SECRET"):     # the tests' own folders must not see the developer's
    os.environ.pop(_k, None)
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def main(g: dict) -> None:
    """Run the `test_*` functions defined in the module namespace `g`."""
    if not __debug__:
        print("FAILED: python -O skips every assert; run without -O")
        sys.exit(1)
    tests = [(n, f) for n, f in g.items()
             if n.startswith("test_") and callable(f)
             and getattr(f, "__module__", None) == g.get("__name__")]
    if not tests:
        print("FAILED: no test_* function found")
        sys.exit(1)
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except (Exception, SystemExit):
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
    if failed:
        print(f"FAILED: {failed} of {len(tests)}")
        sys.exit(1)
    print(f"RESULT: {len(tests)} passed")


@contextmanager
def tmpdir():
    """A fresh empty folder, removed on exit (even after a failure)."""
    d = tempfile.mkdtemp(prefix="build-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        raise AssertionError(f"not one JSON document: {text[:500]!r}") from None


def tool(script: str, *args: str, stdin: str | None = None, env: dict | None = None,
         raw: bool = False, cwd: str | None = None):
    """(exit code, JSON document) of build/<script>; with raw, the text instead."""
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run([sys.executable, os.path.join(BUILD, script), *args],
                       capture_output=True, encoding="utf-8", timeout=120,
                       env={**ENV, **(env or {})}, cwd=cwd or ROOT, **kw)
    assert p.stderr == "", p.stderr
    return p.returncode, (p.stdout if raw else _json(p.stdout))


def ask(d: str, *args: str, stdin: str | None = None, ask_path: str = ASK):
    """(exit code, JSON document) of the real console/ask.py on folder d."""
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run([sys.executable, ask_path, "--dir", d, *args], capture_output=True,
                       encoding="utf-8", timeout=120, env=ENV, **kw)
    return p.returncode, _json(p.stdout)


def owner_answers(d: str, answers: dict, user: str = "mara") -> None:
    """The owner answers, as the console page would: {ask id: value or (value, comment)}."""
    sys.path.insert(0, CONSOLE)
    try:
        import core
    finally:
        sys.path.remove(CONSOLE)
    s = core.Store(d)
    for i, v in answers.items():
        value, comment = v if isinstance(v, tuple) else (v, "")
        s.answer(user, i, value, shown=s.state()["asks"][i]["hash"], comment=comment)


def owner_reopens(d: str, i: str, user: str = "mara") -> None:
    sys.path.insert(0, CONSOLE)
    try:
        import core
    finally:
        sys.path.remove(CONSOLE)
    s = core.Store(d)
    s.reopen(user, i, s.state()["asks"][i]["answer"]["seq"])


def load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, doc) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return path


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def tsv(path: str) -> list[dict]:
    lines = read(path).splitlines()
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln.strip()]


TRAIL_FILES = ("glossary.tsv", "glossary.agent.tsv", "user-stories.tsv", "user-stories.agent.tsv",
               "policies.tsv", "policies.agent.tsv")


@contextmanager
def workspace():
    """{root, repo, ssot, data, con}: a repository holding ssot/ copied from
    templates/ssot/ as a new harness starts it (the owner and agent files keep
    their header rows only: templates/ssot/README.md), the client's data
    folder beside it and a console folder."""
    with tmpdir() as d:
        w = {"root": d, "repo": os.path.join(d, "repo"), "data": os.path.join(d, "data"),
             "con": os.path.join(d, "console")}
        w["ssot"] = os.path.join(w["repo"], "ssot")
        os.makedirs(os.path.join(w["repo"], ".git"))
        os.makedirs(w["data"])
        shutil.copytree(os.path.join(ROOT, "templates", "ssot"), w["ssot"])
        for name in TRAIL_FILES:
            p = os.path.join(w["ssot"], name)
            if os.path.exists(p):
                head = read(p).splitlines()[0]
                with open(p, "w", encoding="utf-8") as f:
                    f.write(head + "\n")
        yield w


def snapshot(*folders: str) -> dict:
    """Every file under the folders and its bytes: to show nothing changed."""
    out = {}
    for top in folders:
        for base, _dirs, names in os.walk(top):
            for n in names:
                p = os.path.join(base, n)
                with open(p, "rb") as f:
                    out[p] = f.read()
    return out
