#!/usr/bin/env python3
"""kit.testing.run_tests: the gate every other test relies on.

The gate is run over throwaway tests dirs of planted files, in its own
process with a TMPDIR we then inspect:

  * a clean file passes, in both RESULT conventions (`N passed` and
    `N passed, 0 failed`), and the run leaves the caller's TMPDIR empty;
  * a file without a RESULT line fails; `0 passed` fails; `M failed` > 0
    fails even with exit 0; a non-zero exit fails; a timeout fails;
  * a file that leaks into its TMPDIR fails and is named; uv's lock is
    not a leak; a sandbox from check.tmp_dir() is not a leak;
  * the last RESULT line counts; one bad file fails the run; the filter
    is a substring (several = any); no match fails;
  * check.finish() and check.run_functions() files pass through the gate.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing import run_tests  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402

ROOT = str(_shop.PLAYBOOK)


def gate(files: dict[str, str], *args: str, timeout_s: float = 60
         ) -> tuple[int, str, list[str]]:
    """Run the gate over {name: body}: (exit, output, what it left in the
    TMPDIR it was given)."""
    d = Path(tmp_dir("gate-"))
    (d / "tests").mkdir()
    for name, body in files.items():
        (d / "tests" / name).write_text(body, encoding="utf-8")
    tmp = d / "tmp"
    tmp.mkdir()
    shim = ("import sys; sys.path.insert(0, %r)\n"
            "from kit.testing import run_tests\n"
            "sys.exit(run_tests.main(%r, root=%r, timeout_s=%r))\n"
            % (ROOT, list(args), str(d), timeout_s))
    p = subprocess.run([sys.executable, "-B", "-c", shim], capture_output=True,
                       text=True, env={**os.environ, "TMPDIR": str(tmp)})
    return p.returncode, p.stdout + p.stderr, sorted(os.listdir(tmp))


def parse(text):
    return run_tests.parse_result(text)


PASS = "print('  PASS  x')\nprint('RESULT: 1 passed')\n"
PASS_OLD = "print('  PASS  x')\nprint('RESULT: 1 passed, 0 failed')\n"
LEAK = "import tempfile; tempfile.mkdtemp()\n" + PASS
UV_LOCK = ("import os, tempfile\nopen(os.path.join(tempfile.gettempdir(), "
           "'uv-0a1b.lock'), 'w').close()\n" + PASS)
HEADER = "import os, sys; sys.path.insert(0, %r)\n" % ROOT
HELPER = (HEADER + "from kit.testing.check import check, finish, tmp_dir\n"
          "d = tmp_dir(prefix='sandbox-')\n"
          "open(os.path.join(d, 't.db'), 'w').close()\n"
          "check('sandbox made', os.path.isdir(d))\n"
          "raise SystemExit(finish())\n")
FUNCS = (HEADER + "from kit.testing.check import run_functions\n"
         "def test_one():\n    assert 1 + 1 == 2\n"
         "def test_two():\n    assert 'a'.upper() == 'A'\n"
         "raise SystemExit(run_functions(globals()))\n")
FUNCS_BAD = FUNCS.replace("== 'A'", "== 'B'")
GREEN_LIE = "print('RESULT: 3 passed, 2 failed')\n"     # exit 0
ZERO = "print('RESULT: 0 passed')\n"
NO_LINE = "print('did some work')\n"
EXIT_1 = "print('RESULT: 4 passed')\nraise SystemExit(1)\n"
SLOW = "import time; time.sleep(30)\n" + PASS
LAST_WINS_BAD = "print('RESULT: 5 passed')\nprint('RESULT: 1 passed, 1 failed')\n"
LAST_WINS_OK = "print('RESULT: 1 passed, 1 failed')\nprint('RESULT: 5 passed')\n"


def main() -> int:
    print("[1] the RESULT line")
    check("'N passed' -> (N, 0)", parse("RESULT: 5 passed") == (5, 0))
    check("'N passed, M failed' -> (N, M)",
          parse("RESULT: 5 passed, 2 failed") == (5, 2))
    check("leading indent tolerated", parse("   RESULT: 7 passed") == (7, 0))
    check("no line -> (None, 0)", parse("nothing here") == (None, 0))
    check("the last line counts",
          parse("RESULT: 9 passed\n  PASS b\nRESULT: 2 passed, 1 failed\n")
          == (2, 1))
    check("the runner's own failure summary parses as a failure",
          parse("RESULT: 7 passed, 1 failed (1 of 2 files failed the gate)")
          == (7, 1))

    print("\n[2] clean files pass")
    rc, out, left = gate({"test_ok.py": PASS, "test_old.py": PASS_OLD})
    check("both RESULT conventions pass; summary 'RESULT: 2 passed'",
          rc == 0 and out.rstrip().endswith("RESULT: 2 passed"), out)
    check("the run leaves the caller's TMPDIR as it found it", left == [], left)
    rc, out, _ = gate({"test_helper.py": HELPER})
    check("a sandbox from check.tmp_dir() is gone when the file exits",
          rc == 0, out)
    rc, out, _ = gate({"test_funcs.py": FUNCS})
    check("run_functions(): each test_* function is one check",
          rc == 0 and "RESULT: 2 passed" in out, out)
    rc, out, _ = gate({"test_uv.py": UV_LOCK})
    check("uv's own lock file is not the test's leak", rc == 0, out)

    print("\n[3] every violation class fails")
    cases = {
        "missing RESULT line": (NO_LINE, "missing 'RESULT: N passed' gate line"),
        "0 passed": (ZERO, "RESULT line reports 0 passed (ran no tests)"),
        "M failed with exit 0": (GREEN_LIE, "RESULT line reports 2 failed"),
        "a non-zero exit": (EXIT_1, "exit 1"),
        "the last RESULT line reports a failure": (
            LAST_WINS_BAD, "RESULT line reports 1 failed"),
        "a failing run_functions() test (exit 1)": (FUNCS_BAD, "exit 1"),
    }
    for label, (body, why) in cases.items():
        rc, out, left = gate({"test_x.py": body})
        check(f"{label}: the gate fails and says why",
              rc == 1 and f"tests/test_x.py: {why}" in out
              and "RESULT: 0 passed, 1 failed" in out and left == [], out)
    rc, out, left = gate({"test_leak.py": LEAK})
    check("a file leaving a temp dir fails, named with the count",
          rc == 1 and "tests/test_leak.py: left 1 temp entry" in out, out)
    check("the leak itself is swept, not left for the next run", left == [],
          left)
    rc, out, left = gate({"test_slow.py": SLOW}, timeout_s=1)
    check("a file past its timeout is killed and fails",
          rc == 1 and "tests/test_slow.py: timed out after 1s" in out
          and left == [], out)
    rc, out, _ = gate({"test_ok.py": PASS, "test_lie.py": GREEN_LIE})
    check("one bad file fails the whole run; the good one still counts",
          rc == 1 and "RESULT: 1 passed, 1 failed" in out, out)
    rc, out, _ = gate({"test_x.py": LAST_WINS_OK})
    check("a nested failure line followed by the file's own pass line passes",
          rc == 0, out)

    print("\n[4] the filter")
    files = {"test_alpha.py": PASS, "test_beta.py": PASS, "test_gamma.py": ZERO}
    rc, out, _ = gate(files, "alpha")
    check("a substring picks files", rc == 0 and "test_beta" not in out
          and out.rstrip().endswith("RESULT: 1 passed"), out)
    rc, out, _ = gate(files, "alpha", "beta")
    check("several arguments = any of them",
          rc == 0 and out.rstrip().endswith("RESULT: 2 passed"), out)
    rc, out, _ = gate(files, "zzz")
    check("no file matches: the run fails",
          rc == 1 and "FAILED: no tests/test_*.py matches 'zzz'" in out, out)
    rc, out, _ = gate({"helper.py": PASS, "test_a.py": PASS})
    check("only test_*.py files run", rc == 0 and "helper.py" not in out, out)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
