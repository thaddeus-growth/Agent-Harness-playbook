#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The gate every other test stands on, tested: run.py and _check.py.

Each case copies run.py and _check.py into a sandbox tests/ folder with fake
test files, runs the gate there with its own TMPDIR, and reads what it says and
what it left. Nothing here can hide a broken runner behind a passing file.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import time

from _check import check, child_env, finish, tmp_dir

HERE = os.path.dirname(os.path.abspath(__file__))

PASS = "print('  PASS  x')\nprint('RESULT: 1 passed, 0 failed')\n"
LEAK = "import tempfile\ntempfile.mkdtemp()\n" + PASS
UV_LOCK = ("import os, tempfile\n"
           "open(os.path.join(tempfile.gettempdir(), 'uv-0a1b.lock'), 'w').close()\n" + PASS)
HELPER = ("import os, sys\nfrom _check import check, finish, tmp_dir\n"
          "d = tmp_dir(prefix='sandbox-')\nopen(os.path.join(d, 't.db'), 'w').close()\n"
          "check('sandbox made', os.path.isdir(d))\nsys.exit(finish())\n")
FORGOT_EXIT = ("from _check import check, finish\n"
               "check('fine', True)\ncheck('broken', False, 'boom')\nfinish()\n")
ENV_PROBE = ("import os, sys, tempfile\n"
             "from _check import child_env\n"
             "c = child_env(DATA_DIR='/sandbox')\n"
             "ok = ('SECRET_TOKEN' not in os.environ and 'PATH' in os.environ\n"
             "      and os.path.basename(os.path.dirname(tempfile.gettempdir())).startswith('tests-')\n"
             "      and 'SECRET_TOKEN' not in c and c['TMPDIR'] == tempfile.gettempdir()\n"
             "      and c['DATA_DIR'] == '/sandbox')\n"
             "print('RESULT: 1 passed' if ok else 'env leaked'); sys.exit(0 if ok else 1)\n")


def gate(files: dict[str, str], *args: str) -> tuple[int, str, list[str]]:
    """Run a sandbox copy of the gate over {name: source}. Returns (exit code,
    output, what the run left in the TMPDIR it was given)."""
    d = tmp_dir(prefix="gate-")
    tests = os.path.join(d, "tests")
    os.makedirs(tests)
    for n in ("run.py", "_check.py"):
        shutil.copy(os.path.join(HERE, n), tests)
    for name, src in files.items():
        with open(os.path.join(tests, name), "w", encoding="utf-8") as fh:
            fh.write(src)
    tmp = os.path.join(d, "tmp")
    os.mkdir(tmp)
    p = subprocess.run([sys.executable, os.path.join(tests, "run.py"), *args],
                       capture_output=True, text=True, timeout=90,
                       env=child_env(TMPDIR=tmp, SECRET_TOKEN="x" * 20))
    return p.returncode, p.stdout + p.stderr, sorted(os.listdir(tmp))


def main() -> int:
    print("a file passes only with exit 0 and a RESULT line of N > 0 passed, 0 failed")
    code, out, left = gate({"test_ok.py": PASS, "test_two.py": PASS})
    check("clean files pass and are totalled", code == 0
          and out.rstrip().splitlines()[-1] == "RESULT: 2 passed", out)
    check("the run leaves the TMPDIR it was given as it found it", left == [], left)
    code, out, _ = gate({"test_lie.py": "print('RESULT: 3 passed, 2 failed')\n"})
    check("a RESULT line reporting failures fails even with exit 0",
          code == 1 and "reports 2 failed" in out, out)
    code, out, _ = gate({"test_forgot.py": FORGOT_EXIT})
    check("so a file that ends finish() without sys.exit fails on its own line",
          code == 1 and "reports 1 failed" in out, out)
    code, out, _ = gate({"test_zero.py": "print('RESULT: 0 passed, 0 failed')\n"})
    check("'0 passed' fails: no test ran", code == 1 and "no test ran" in out, out)
    code, out, _ = gate({"test_silent.py": "print('fine')\n", "test_empty.py": ""})
    check("no RESULT line fails", code == 1 and out.count("no RESULT line") == 2, out)
    code, out, _ = gate({"test_exit.py": "print('RESULT: 3 passed')\nimport sys\nsys.exit(3)\n",
                         "test_crash.py": "raise RuntimeError('early')\n"})
    check("a non-zero exit fails, RESULT line or not",
          code == 1 and "exit 3" in out and "exit 1" in out and "early" in out, out)
    code, out, _ = gate({"test_a.py": "print('RESULT: 3 passed, 2 skipped')\n",
                         "test_b.py": "print('  RESULT: 3 passed')\n",
                         "test_c.py": "print('xRESULT: 3 passed')\n"})
    check("the RESULT line must match exactly", code == 1 and "FAILED: 3 of 3" in out, out)
    code, out, _ = gate({"test_ok.py": PASS, "test_lie.py": "print('RESULT: 1 passed, 1 failed')\n"})
    check("one bad file fails the run", code == 1 and "FAILED: 1 of 2 files" in out, out)

    print("a file that leaves anything in its temp dir fails")
    code, out, left = gate({"test_leak.py": LEAK, "test_ok.py": PASS})
    check("names the leaking file and the count, and only it",
          code == 1 and "test_leak.py" in out and "left 1 temp entry" in out
          and "FAILED: 1 of 2 files" in out, out)
    check("the leak itself is swept, not left for the next run", left == [], left)
    code, out, _ = gate({"test_uv.py": UV_LOCK})
    check("uv's own lock file is not the test's leak", code == 0, out)
    code, out, left = gate({"test_helper.py": HELPER})
    check("a sandbox made with _check.tmp_dir() is gone when the file exits",
          code == 0 and left == [], (out, left))

    print("each file runs on the environment allowlist")
    code, out, _ = gate({"test_env.py": ENV_PROBE})
    check("a variable off the allowlist never reaches a test or its child; "
          "PATH and the file's own TMPDIR do", code == 0, out)

    print("patterns, parallel runs and the time limit")
    code, out, _ = gate({"test_alpha.py": PASS, "test_beta.py": "print('no')\n",
                         "test_gamma.py": PASS}, "alpha", "gamma")
    check("several patterns: files matching either, none dropped",
          code == 0 and "test_beta" not in out and out.rstrip().endswith("RESULT: 2 passed"), out)
    code, out, _ = gate({"test_alpha.py": PASS, "helper.py": PASS}, "nomatch")
    check("no match fails and says so",
          code == 1 and "no tests/test_*.py matches 'nomatch'" in out, out)
    nap = "import time\ntime.sleep(1)\nprint('RESULT: 1 passed')\n"
    t0 = time.monotonic()
    code, out, _ = gate({f"test_nap{i}.py": nap for i in range(8)})
    took = time.monotonic() - t0
    # 4 workers: about 2 s. The limit keeps 3x headroom for a busy machine,
    # and a serial run (8 s or more) still fails it.
    check(f"8 one-second files run 4 at a time (took {took:.1f}s, limit 6s)",
          code == 0 and took < 6.0, out)
    spec = importlib.util.spec_from_file_location("run_under_test", os.path.join(HERE, "run.py"))
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    check("the documented budget: 120 s a file, 4 at a time",
          (run.TIMEOUT, run.WORKERS) == (120, 4))
    hang = os.path.join(tmp_dir(prefix="hang-"), "test_hang.py")
    with open(hang, "w", encoding="utf-8") as fh:
        fh.write("import time\ntime.sleep(60)\n")
    run.TIMEOUT = 1
    t0 = time.monotonic()
    r = run.run_one(hang)
    check("a file past its time limit fails and is stopped",
          not r["ok"] and r["why"].startswith("timed out") and time.monotonic() - t0 < 15, r["why"])
    return finish()


if __name__ == "__main__":
    sys.exit(main())
