"""The console's two sides meet only in the event log.

The agent's side (ask.py) never writes what a human writes, and the human's side
(serve.py) never writes what an agent writes; the pure modules (core.py,
pages.py) neither serve nor start anything; and only relay.py starts a process.
These are read from the source with the parser, not with a text search, so a
call made through an alias, an import or getattr is still seen. Each check has
a positive control (the code does use what it is allowed to) and the checker
itself is tried on sources that break the rule, so it cannot pass by looking
at nothing. The tests/ folder is not scanned: tests start processes.
"""

from __future__ import annotations

import ast
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _t  # noqa: E402

AGENT_VERBS = {"post", "withdraw", "applied", "say"}                    # what serve.py must never reach
HUMAN_VERBS = {"answer", "reopen", "note", "ensure_secret", "sign"}   # what ask.py must never reach
NETWORK = {"http", "socket", "socketserver", "subprocess", "ssl", "asyncio", "urllib.request", "ftplib", "smtplib"}
PROCESS_CALLS = {"system", "popen", "spawnl", "spawnv", "spawnvp", "execv", "execl", "execvp", "fork", "forkpty"}
PROCESS_MODULES = {"subprocess", "multiprocessing", "pty", "asyncio"}


def source(name: str) -> str:
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def module_files() -> list[str]:
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py")))


def reaches(src: str, names: set[str]) -> set[str]:
    """Which of `names` the source can reach: as an attribute (x.name), a bare name,
    an imported name, or the string given to getattr/hasattr."""
    hit = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Attribute) and n.attr in names:
            hit.add(n.attr)
        elif isinstance(n, ast.Name) and n.id in names:
            hit.add(n.id)
        elif isinstance(n, ast.alias) and n.name.split(".")[-1] in names:
            hit.add(n.name.split(".")[-1])
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("getattr", "hasattr")
              and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and n.args[1].value in names):
            hit.add(n.args[1].value)
    return hit


def imports(src: str) -> set[str]:
    """Every module a source imports, by full dotted name (import x.y, from x import y, __import__("x")),
    with its parents: importing `urllib.request` is importing `urllib`, but not `urllib.parse`."""
    out = set()

    def add(name):
        parts = name.split(".")
        out.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            for a in n.names:
                add(a.name)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            add(n.module)
            for a in n.names:
                add(f"{n.module}.{a.name}")
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "__import__"
              and n.args and isinstance(n.args[0], ast.Constant)):
            add(str(n.args[0].value))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "import_module":
            out.add("importlib")
    return out


# ------------------------------------------------------ the checker itself --

def test_the_checker_sees_a_call_however_it_is_reached():
    breaking = {
        "a method call": "store.post(x)",
        "through another name": "f = s.withdraw\nf(1)",
        "a bare name": "applied(1)",
        "an import": "from core import say",
        "an import as": "from core import Store as say2\nimport core.applied",
        "getattr": 'getattr(store, "applied")(1)',
        "hasattr": 'hasattr(store, "say")',
    }
    for what, src in breaking.items():
        assert reaches(src, AGENT_VERBS), what
    for src in ("store.answer(x)", "posted = 1", "s.postpone(1)", "say_hello()", 'getattr(store, "answer")', "x = 'post'"):
        assert not reaches(src, AGENT_VERBS), src
    for src in ("import subprocess", "import http.server", "from socket import socket", "from http import server",
                '__import__("subprocess")', "import importlib\nimportlib.import_module('socket')",
                "import urllib.request", "from urllib import request", "from urllib.request import urlopen"):
        assert imports(src) & (NETWORK | {"importlib"}), src
    for src in ("import json, os\nfrom core import x\nfrom . import y", "from urllib.parse import urlsplit", "import urllib.parse"):
        assert not imports(src) & (NETWORK | {"importlib"}), src


# --------------------------------------------------------- the two sides --

def test_the_human_side_never_reaches_the_agents_verbs():
    src = source("serve.py")
    assert not reaches(src, AGENT_VERBS), reaches(src, AGENT_VERBS)
    assert {"answer", "reopen", "note", "ensure_secret"} <= reaches(src, HUMAN_VERBS)   # it does use its own: the check is not vacuous


def test_the_agent_side_never_reaches_the_humans_verbs():
    src = source("ask.py")
    assert not reaches(src, HUMAN_VERBS), reaches(src, HUMAN_VERBS)
    assert AGENT_VERBS <= reaches(src, AGENT_VERBS)                                       # and it does use its own


def test_neither_side_imports_the_other_and_both_meet_only_in_core():
    ask, serve = imports(source("ask.py")), imports(source("serve.py"))
    assert not ask & {"serve", "pages", "relay", "i18n"}, ask
    assert "ask" not in serve, serve
    assert "core" in ask and {"core", "pages", "relay", "i18n"} <= serve


# ----------------------------------------------- pure modules, one process starter --

def test_core_and_pages_import_nothing_that_serves_connects_or_starts_a_process():
    for name in ("core.py", "pages.py", "i18n.py"):
        src = source(name)
        bad = imports(src) & (NETWORK | PROCESS_MODULES | {"importlib"})
        assert not bad, f"{name} imports {sorted(bad)}"
        assert not reaches(src, PROCESS_CALLS), f"{name} starts a process: {sorted(reaches(src, PROCESS_CALLS))}"
    assert "http" in imports(source("serve.py")) and "socket" in imports(source("serve.py"))    # the server side does


def test_only_relay_py_starts_a_process():
    files = module_files()
    assert {"core.py", "ask.py", "serve.py", "relay.py", "pages.py", "i18n.py"} <= set(files), files
    for name in files:
        src = source(name)
        starts = imports(src) & PROCESS_MODULES
        if name == "relay.py":
            assert "subprocess" in starts                                                  # the check would notice it
            continue
        assert not starts, f"{name} imports {sorted(starts)}"
        assert not reaches(src, PROCESS_CALLS), f"{name} starts a process"


if __name__ == "__main__":
    _t.main(globals())
