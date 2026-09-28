"""The test harness tests itself: `run.py` fails any test file that does not exit
0 AND print `RESULT: N passed` (the playbook's day-one rule), and `_t.py`
reports honestly. Every case runs fake test files in a throw-away copy of the
tests folder, so nothing here can hide a broken runner behind a passing file.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import _t  # noqa: E402
import core  # noqa: E402
from _t import assert_raises, tmpdir, tmpstore  # noqa: E402

PRELUDE = ("import os, sys\n"
           "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
           "import _t\n")
MAIN = '\nif __name__ == "__main__":\n    _t.main(globals())\n'
GOOD = PRELUDE + "def test_a():\n    assert 1 + 1 == 2\ndef test_b():\n    pass\n" + MAIN
BAD = PRELUDE + 'def test_a():\n    assert False, "boom"\ndef test_b():\n    pass\n' + MAIN
TWO_BAD = (PRELUDE + 'def test_a():\n    assert False, "boom-a"\ndef test_b():\n    pass\n'
           'def test_c():\n    assert False, "boom-c"\n' + MAIN)


@contextmanager
def sandbox(**files):
    """A copy of console/ holding run.py, _t.py, core.py and fake files
    (`name=source`, stored as tests/<name>.py). Yields the tests folder."""
    with tmpdir() as d:
        tests = os.path.join(d, "console", "tests")
        os.makedirs(tests)
        shutil.copy(os.path.join(HERE, "..", "core.py"), os.path.join(d, "console"))
        for n in ("run.py", "_t.py"):
            shutil.copy(os.path.join(HERE, n), tests)
        for name, src in files.items():
            with open(os.path.join(tests, name + ".py"), "w", encoding="utf-8") as f:
                f.write(src)
        yield tests


def run(tests, *args):
    p = subprocess.run([sys.executable, os.path.join(tests, "run.py"), *args],
                       capture_output=True, text=True, timeout=90)
    return p.returncode, p.stdout + p.stderr


def run_file(tests, name, *flags):
    p = subprocess.run([sys.executable, *flags, os.path.join(tests, name + ".py")],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------- run.py --

def test_runner_passes_good_files_and_totals_them():
    with sandbox(test_one=GOOD, test_two=GOOD) as t:
        code, out = run(t)
        assert code == 0, out
        assert out.rstrip().splitlines()[-1] == "RESULT: 4 passed"
        for name in ("test_one.py", "test_two.py"):
            assert any(ln.startswith("ok") and name in ln and "RESULT: 2 passed" in ln
                       for ln in out.splitlines()), out


def test_runner_fails_a_failing_test_and_shows_why():
    with sandbox(test_good=GOOD, test_bad=BAD) as t:
        code, out = run(t)
        assert code == 1 and "RESULT" not in out.splitlines()[-1]
        assert "FAIL" in out and "boom" in out and "FAILED: 1 of 2 files" in out
        assert any(ln.startswith("ok") and "test_good.py" in ln for ln in out.splitlines())


def test_runner_fails_a_file_with_no_result_line():
    with sandbox(test_silent="print('all fine')\n", test_empty="") as t:
        code, out = run(t)
        assert code == 1 and out.count("no RESULT line") == 2, out
        assert "FAILED: 2 of 2 files" in out


def test_runner_fails_a_file_that_crashes_before_the_line():
    with sandbox(test_crash="raise RuntimeError('early')\n", test_exit="import sys\nsys.exit(3)\n") as t:
        code, out = run(t)
        assert code == 1 and "exit 1" in out and "exit 3" in out and "early" in out, out


def test_runner_fails_a_result_line_with_a_bad_exit_code():
    with sandbox(test_liar="print('RESULT: 3 passed')\nimport sys\nsys.exit(1)\n") as t:
        code, out = run(t)
        assert code == 1 and "exit 1" in out and "FAILED: 1 of 1 files" in out, out


def test_runner_result_line_must_match_exactly():
    with sandbox(test_suffix="print('RESULT: 3 passed, 2 skipped')\n",
                 test_indent="print('  RESULT: 3 passed')\n",
                 test_prefix="print('xRESULT: 3 passed')\n",
                 test_word="print('RESULT: many passed')\n") as t:
        code, out = run(t)
        assert code == 1 and "FAILED: 4 of 4 files" in out, out


def test_runner_zero_tests_is_not_a_pass():
    with sandbox(test_nothing="print('RESULT: 0 passed')\n") as t:
        code, out = run(t)
        assert code == 1 and "no test ran" in out, out


def test_runner_pattern_and_no_match():
    with sandbox(test_good=GOOD, test_bad=BAD, helper="x = 1\n") as t:
        code, out = run(t, "good")
        assert code == 0 and "test_bad.py" not in out and "RESULT: 2 passed" in out, out
        code, out = run(t, "test_good.py")
        assert code == 0, out
        code, out = run(t, "nomatch")
        assert code == 1 and "no tests/test_*.py matches 'nomatch'" in out and "RESULT" not in out
    with sandbox(helper="x = 1\n", fake_harness="print('RESULT: 1 passed')\n") as t:
        code, out = run(t)                                    # only test_*.py files are tests
        assert code == 1 and "no tests/test_*.py matches" in out, out


def test_runner_takes_several_patterns_and_ignores_none():
    """`run.py core serve` runs the files matching either word: a dropped argument
    would print a RESULT for a subset and look like a pass."""
    with sandbox(test_alpha=GOOD, test_beta=BAD, test_gamma=GOOD) as t:
        code, out = run(t, "alpha", "gamma")
        assert code == 0 and "test_beta.py" not in out, out
        assert out.rstrip().splitlines()[-1] == "RESULT: 4 passed"
        assert sum(ln.startswith("ok") for ln in out.splitlines()) == 2
        code, out = run(t, "alpha", "beta")                    # the failing one is not skipped
        assert code == 1 and "test_beta.py" in out and "FAILED: 1 of 2 files" in out, out
        code, out = run(t, "alpha", "nomatch")                 # one match is enough
        assert code == 0 and "test_alpha.py" in out and "RESULT: 2 passed" in out, out
        code, out = run(t, "nomatch", "nada")
        assert code == 1 and "matches 'nomatch', 'nada'" in out and "RESULT" not in out, out


def test_runner_times_out_a_hanging_file():
    with sandbox(test_hang="import time\ntime.sleep(60)\n") as t:
        spec = importlib.util.spec_from_file_location("run_under_test", os.path.join(t, "run.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.TIMEOUT == 120 and mod.WORKERS == 4        # the documented budget
        mod.TIMEOUT = 1
        t0 = time.monotonic()
        r = mod.run_one(os.path.join(t, "test_hang.py"))
        assert r["ok"] is False and r["why"].startswith("timed out") and time.monotonic() - t0 < 15


def test_runner_runs_files_in_parallel():
    nap = "import time\ntime.sleep(1)\nprint('RESULT: 1 passed')\n"
    with sandbox(**{f"test_nap{i}": nap for i in range(8)}) as t:
        t0 = time.monotonic()
        code, out = run(t)
        took = time.monotonic() - t0
        assert code == 0 and out.rstrip().splitlines()[-1] == "RESULT: 8 passed", out
        assert took < 7.0, f"8 one-second files took {took:.1f}s: not parallel"


# ------------------------------------------------------------------ _t.py --

def test_t_main_prints_result_only_when_everything_passed():
    with sandbox(test_good=GOOD, test_bad=BAD, test_two=TWO_BAD) as t:
        code, out = run_file(t, "test_good")
        assert code == 0 and out.rstrip().splitlines() == ["RESULT: 2 passed"], out
        code, out = run_file(t, "test_bad")
        assert code == 1 and "FAIL test_a" in out and "boom" in out and "Traceback" in out
        assert out.rstrip().splitlines()[-1] == "FAILED: 1 of 2" and "RESULT" not in out
        code, out = run_file(t, "test_two")                    # a failure does not stop the run
        assert code == 1 and "FAIL test_a" in out and "FAIL test_c" in out and "FAIL test_b" not in out
        assert "boom-a" in out and "boom-c" in out and out.rstrip().splitlines()[-1] == "FAILED: 2 of 3"


def test_t_main_runs_in_file_order_and_only_the_files_own_tests():
    other = "def test_imported():\n    raise AssertionError('must not run')\n"
    src = (PRELUDE + "from other import test_imported\nSEQ = []\n"
           "def test_c():\n    SEQ.append('c')\n"
           "def test_a():\n    SEQ.append('a')\n"
           "def test_b():\n    SEQ.append('b')\n"
           "def helper_not_a_test():\n    raise AssertionError('never')\n"
           "def test_last():\n    assert SEQ == ['c', 'a', 'b'], SEQ\n" + MAIN)
    with sandbox(test_order=src, other=other) as t:
        code, out = run_file(t, "test_order")
        assert code == 0 and out.strip() == "RESULT: 4 passed", out


def test_t_main_counts_a_system_exit_as_a_failure():
    src = (PRELUDE + "def test_exits():\n    sys.exit(0)\ndef test_fine():\n    pass\n" + MAIN)
    with sandbox(test_x=src) as t:
        code, out = run_file(t, "test_x")
        assert code == 1 and "FAIL test_exits" in out and "FAILED: 1 of 2" in out and "RESULT" not in out, out


def test_t_main_needs_tests_and_asserts():
    with sandbox(test_none=PRELUDE + "def helper():\n    pass\n" + MAIN, test_good=GOOD) as t:
        code, out = run_file(t, "test_none")
        assert code == 1 and "no test_* function" in out and "RESULT" not in out, out
        code, out = run_file(t, "test_good", "-O")             # -O skips every assert: nothing could fail
        assert code == 1 and "RESULT" not in out and "-O" in out, out


def test_assert_raises_semantics():
    with assert_raises("changed") as r:
        raise core.Refused("changed", id="x")
    assert r.err.params == {"id": "x"} and r.err.code == "changed"
    with assert_raises() as r:
        raise core.Refused("not_open", id="x")
    assert r.err.code == "not_open"
    for code, body in (("changed", lambda: None), ("changed", lambda: (_ for _ in ()).throw(core.Refused("not_open")))):
        try:
            with assert_raises(code):
                body()
        except AssertionError as e:
            assert "expected" in str(e)
        else:
            raise AssertionError("assert_raises let a wrong outcome through")
    try:                                                       # a crash is not a refusal
        with assert_raises("changed"):
            raise KeyError("boom")
    except KeyError:
        pass
    else:
        raise AssertionError("assert_raises swallowed a KeyError")


def test_tmpdir_and_tmpstore_clean_up():
    with tmpdir() as d:
        assert os.path.isdir(d) and os.listdir(d) == []
        open(os.path.join(d, "f"), "w").close()
    assert not os.path.exists(d)
    try:
        with tmpdir() as d2:
            raise ValueError("test body failed")
    except ValueError:
        pass
    assert not os.path.exists(d2)
    with tmpstore() as s:
        assert isinstance(s, core.Store) and os.path.isdir(s.dir) and s.events() == []
        keep = s.dir
    assert not os.path.exists(keep)


if __name__ == "__main__":
    _t.main(globals())
