#!/usr/bin/env python3
"""kit.testing.check and kit.testing.sandbox: the helpers every test uses.

  * finish() prints `RESULT: N passed` (no failures) or `RESULT: N passed,
    M failed`, and returns 1 when M > 0 or N == 0;
  * run_functions() runs test_* functions as checks and refuses python -O;
  * capture() splits stdout/stderr, returns the exit code, swaps env and
    stdin for the call only, and feeds tty answers through KIT_TTY;
  * one_doc() accepts exactly one JSON document;
  * clean_env() / sandbox_env() drop every inherited <P>_* var and KIT_TTY;
    sandbox.run() gives the child no stdin.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing import sandbox  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish, one_doc,  # noqa: E402
                               raises, tmp_dir)

HEADER = "import sys; sys.path.insert(0, %r)\n" % str(_shop.PLAYBOOK)


def script(body: str, *flags: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-B", *flags, "-c", HEADER + body],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    cfg = _shop.use()

    print("[1] finish()")
    rc, out = script("from kit.testing.check import check, finish\n"
                     "check('a', True); check('b', 1)\n"
                     "raise SystemExit(finish())\n")
    check("all passed: 'RESULT: 2 passed', exit 0",
          rc == 0 and out.rstrip().endswith("RESULT: 2 passed"), out)
    rc, out = script("from kit.testing.check import check, finish\n"
                     "check('a', True); check('b', False, 'why')\n"
                     "raise SystemExit(finish())\n")
    check("a failure: 'RESULT: 1 passed, 1 failed', exit 1, the detail shown",
          rc == 1 and "RESULT: 1 passed, 1 failed" in out
          and "FAIL  b -- why" in out, out)
    rc, out = script("from kit.testing.check import finish\n"
                     "raise SystemExit(finish())\n")
    check("no check ran: 'RESULT: 0 passed', exit 1",
          rc == 1 and "RESULT: 0 passed" in out, out)

    print("\n[2] run_functions()")
    rc, out = script("from kit.testing.check import run_functions\n"
                     "def test_a():\n    assert True\n"
                     "def test_b():\n    assert False, 'nope'\n"
                     "def helper():\n    raise RuntimeError\n"
                     "raise SystemExit(run_functions(globals()))\n")
    check("each test_* function is a check; helpers are not run",
          rc == 1 and "PASS  test_a" in out and "FAIL  test_b" in out
          and "AssertionError: nope" in out
          and "RESULT: 1 passed, 1 failed" in out, out)
    rc, out = script("from kit.testing.check import run_functions\n"
                     "def test_a():\n    assert True\n"
                     "raise SystemExit(run_functions(globals()))\n", "-O")
    check("python -O is refused (asserts would be skipped)",
          rc == 1 and "python -O" in out and "RESULT" not in out, out)

    print("\n[3] capture()")

    def cli(argv):
        print("out:", argv)
        print("err!", file=sys.stderr)
        return 3
    check("stdout, stderr and the returned code",
          capture(cli, ["a"]) == (3, "out: ['a']\n", "err!\n"))
    check("None is exit 0", capture(lambda a: None, [])[0] == 0)
    check("SystemExit(n) is caught",
          capture(lambda a: sys.exit(4), [])[0] == 4)
    rc, _, err = capture(lambda a: sys.exit("usage: bad"), [])
    check("SystemExit('text') is exit 1 with the text on stderr",
          rc == 1 and err == "usage: bad\n")
    check("any other exception propagates",
          raises(lambda: capture(lambda a: 1 / 0, []), ZeroDivisionError)
          is not None)
    os.environ["SHOP_KEEP"] = "1"
    seen = {}
    capture(lambda a: seen.update(os.environ), [], env={"ONLY": "this"})
    check("env replaces os.environ for the call only",
          seen == {"ONLY": "this"} and os.environ.get("SHOP_KEEP") == "1")
    del os.environ["SHOP_KEEP"]
    rc, out, _ = capture(lambda a: print(sys.stdin.isatty(),
                                         repr(sys.stdin.read())), [],
                         stdin="typed\n")
    check("stdin is fed, and never a TTY", out == "False 'typed\\n'\n", out)
    stdin_before = sys.stdin
    got = {}

    def gate(a):
        got["path"] = os.environ["KIT_TTY"]
        got["text"] = Path(got["path"]).read_text()
    capture(gate, [], tty_answers=["confirm", "042317"])
    check("tty_answers: a KIT_TTY file, one answer per line",
          got["text"] == "confirm\n042317\n")
    check("… removed after the call, KIT_TTY unset again, stdin restored",
          not Path(got["path"]).exists() and "KIT_TTY" not in os.environ
          and sys.stdin is stdin_before)

    print("\n[4] one_doc(), raises()")
    check("one document", one_doc('{"a": 1}\n') == {"a": 1})
    check("two documents or none -> None",
          one_doc('{"a": 1}\n{"b": 2}\n') is None and one_doc("") is None
          and one_doc("error: x") is None)
    check("raises: the exception, or None",
          isinstance(raises(lambda: int("x"), ValueError), ValueError)
          and raises(lambda: 1) is None)

    print("\n[5] clean_env(), sandbox_env(), sandbox.run()")
    os.environ.update({"SHOP_DATA_DIR": "/live/client", "SHOP_DB": "/live.db",
                       "SHOPPING": "keep", "KIT_TTY": "/tmp/answers"})
    env = clean_env(SHOP_X="1")
    check("clean_env: no SHOP_* (other names kept), no KIT_TTY, set on top",
          "SHOP_DATA_DIR" not in env and "SHOP_DB" not in env
          and env["SHOPPING"] == "keep" and "KIT_TTY" not in env
          and env["SHOP_X"] == "1")
    check("clean_env: an explicit prefix", "SHOP_DB" not in clean_env("SHOP_"))
    data = tmp_dir("sandbox-")
    env = sandbox.sandbox_env(data, EXTRA="x", SHOPPING=None)
    check("sandbox_env: the data dir, no env files, utf-8, the bound harness",
          env["SHOP_DATA_DIR"] == data and env["SHOP_AUTH_ENV_PATHS"] == "none"
          and env["PYTHONUTF8"] == "1"
          and env["KIT_HARNESS_ROOT"] == str(cfg.root))
    check("sandbox_env: no inherited SHOP_* nor KIT_TTY; extra on top; None "
          "removes", "SHOP_DB" not in env and "KIT_TTY" not in env
          and env["EXTRA"] == "x" and "SHOPPING" not in env)
    for k in ("SHOP_DATA_DIR", "SHOP_DB", "SHOPPING", "KIT_TTY"):
        del os.environ[k]
    rc, out, err = sandbox.run(
        [sys.executable, "-c", "import sys, os; print(repr(sys.stdin.read()),"
         " os.environ['SHOP_DATA_DIR'])"], env)
    check("sandbox.run: no stdin (EOF at once), the sandbox env",
          rc == 0 and out == f"'' {data}\n", (rc, out, err))
    rc, out, _ = sandbox.run([sys.executable, "-c",
                              "import sys; print(sys.stdin.read().upper())"],
                             env, stdin="yes")
    check("sandbox.run: stdin when given", out == "YES\n")
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
