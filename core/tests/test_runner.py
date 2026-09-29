#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""runner.py: a child verb runs in its own PEP 723 environment, and what it says
is read from its one document on stdout.

Every child here is a real process. `uv` is a stand-in placed first on PATH that
records its argv and then runs the script, so the test needs no network and
shows the exact command. The cases: `uv run <script>` when uv is there, this
interpreter when it is not; a child's failure is its document's error, next and
code even with noise on stderr; a crash is stderr's last line with no code; an
exit 0 with no document is a failure, not a crash of the parent; `any_exit`
takes a document without `error` whatever the exit.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402
from core import messages, runner  # noqa: E402

HEADER = ("# /// script\n# requires-python = \">=3.11\"\n# dependencies = []\n# ///\n"
          "import json, sys\n")
FAKE_UV = '''import json, os, sys
with open(os.environ["UV_LOG"], "w") as f:
    json.dump(sys.argv[1:], f)
assert sys.argv[1] == "run", sys.argv
os.execv(sys.executable, [sys.executable, "-B", *sys.argv[2:]])
'''


def child(d: str, name: str, body: str) -> str:
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + body)
    return path


def fake_uv(d: str) -> str:
    """A folder holding an executable `uv` that logs its argv, then runs the script."""
    bin_dir = os.path.join(d, "bin")
    os.mkdir(bin_dir)
    with open(os.path.join(d, "uv.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_UV)
    uv = os.path.join(bin_dir, "uv")
    with open(uv, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" -B "{os.path.join(d, "uv.py")}" "$@"\n')
    os.chmod(uv, os.stat(uv).st_mode | stat.S_IXUSR)
    return bin_dir


def run(target, args, path_dirs, **kw):
    with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(path_dirs),
                                      "UV_LOG": os.path.join(os.path.dirname(target), "uv.log")}):
        return runner.run_json(target, args, **kw)


def failed(fn) -> runner.ChildFailed:
    try:
        fn()
    except runner.ChildFailed as e:
        return e
    raise AssertionError("no ChildFailed")


def test_a_child_runs_with_uv_run_so_its_own_header_names_its_environment():
    with _t.tmpdir() as d:
        c = child(d, "report.py", 'print(json.dumps({"rows": [1, 2], "args": sys.argv[1:]}))\n')
        got = run(c, ["--json", "--scope", "north"], [fake_uv(d)])
        assert got == {"rows": [1, 2], "args": ["--json", "--scope", "north"]}
        with open(os.path.join(d, "uv.log"), encoding="utf-8") as f:
            assert json.load(f) == ["run", c, "--json", "--scope", "north"]
        with mock.patch.dict(os.environ, {"PATH": os.path.join(d, "bin")}):
            assert runner.script_cmd(c, ["-x"]) == [os.path.join(d, "bin", "uv"), "run", c, "-x"]


def test_without_uv_this_interpreter_runs_the_child():
    with _t.tmpdir() as d:
        c = child(d, "report.py", 'print(json.dumps({"ok": 1}))\n')
        assert run(c, [], [d]) == {"ok": 1}
        assert not os.path.exists(os.path.join(d, "uv.log"))
        with mock.patch.dict(os.environ, {"PATH": d}):
            assert runner.script_cmd(c, ["-x"]) == [sys.executable, c, "-x"]


def test_a_failed_child_is_read_from_its_document_not_from_stderr():
    with _t.tmpdir() as d:
        c = child(d, "report.py", 'print("Installed 3 packages in 4ms", file=sys.stderr)\n'
                  'print(json.dumps({"error": "approve needs a person", "next": ["h.py approve a"],\n'
                  '                  "code": "confirm_needs_human", "params": {"what": "approve"}}))\n'
                  'print("uv: done", file=sys.stderr)\n'
                  'sys.exit(1)\n')
        for path_dirs in ([fake_uv(d)], [d]):
            e = failed(lambda: run(c, ["--json"], path_dirs))
            assert (str(e), e.next, e.code) == ("approve needs a person", ["h.py approve a"],
                                                {"code": "confirm_needs_human", "params": {"what": "approve"}})
        m = messages.relayed(e.code, f"snapshot failed: {e}")
        assert messages.code(m) == {"code": "confirm_needs_human", "params": {"what": "approve"}}


def test_a_crash_is_its_last_stderr_line_with_no_code():
    with _t.tmpdir() as d:
        c = child(d, "report.py", 'raise ValueError("boom")\n')
        e = failed(lambda: run(c, ["--json"], [d]))
        assert (str(e), e.next, e.code) == ("ValueError: boom", [], None)
        assert messages.code(messages.relayed(e.code, str(e)))["code"] == "unclassified_error"


def test_exit_0_without_a_document_is_a_failure_not_a_crash_of_the_parent():
    with _t.tmpdir() as d:
        c = child(d, "report.py", 'print("all done")\n')
        e = failed(lambda: run(c, ["--json"], [d]))
        assert str(e) == "report.py exited 0 with no failure document" and e.code is None


def test_any_exit_takes_a_document_without_error_whatever_the_exit():
    with _t.tmpdir() as d:
        finding = child(d, "check.py", 'print(json.dumps({"rows": [], "failing": 1}))\nsys.exit(2)\n')
        assert run(finding, [], [d], any_exit=True) == {"rows": [], "failing": 1}
        e = failed(lambda: run(finding, [], [d]))
        assert str(e) == "check.py exited 2 with no failure document"
        refused = child(d, "refused.py", 'print(json.dumps({"error": "no store", "next": []}))\n'
                        'sys.exit(1)\n')
        e = failed(lambda: run(refused, [], [d], any_exit=True))
        assert str(e) == "no store" and e.code is None


if __name__ == "__main__":
    _t.main(globals())
