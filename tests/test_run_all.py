"""run_all.py runs every suite and fails on anything that is not a clear pass.

The entry point is run over throwaway trees of planted suites, in this process
(`run_all.main(root=..., suites=...)`), and its own output is read as text:

  * a passing suite passes the run and its count is added to the total; a failing
    one fails the run (exit 1, `RESULT: T passed, K failed ...`), shows the tail of
    its output, and the suites after it still run;
  * the day-one rule, per suite: a non-zero exit, no RESULT line, `0 passed`,
    `M failed` with exit 0, a crash and a timeout each fail; the last RESULT line
    counts; a hung suite's children are killed with it;
  * a run that is interrupted (Ctrl-C, SIGTERM) while a suite runs kills that suite
    and its children before it ends: the suite is its own session, so the terminal's
    signal never reaches it (run in a subprocess, with the real signals);
  * a suite whose run.py is gone fails; so does one whose required tool is not on
    PATH, and then it is not run (the real zylos suite, with node off PATH);
  * what a suite prints on stderr is in the tail of its failure: a runner that dies with
    `raise SystemExit('FAILED: ...')` says why;
  * a subset runs by exact name, in the order of the list, and an unknown name
    fails before anything runs; `--list` runs nothing;
  * every run.py in the real tree is a suite or is listed as a template with its
    reason; a stray one, a gone one, a double entry or a reason left empty fails the
    run; dot-folders (a .claude worktree holds another copy), bytecode folders,
    node_modules and scaffold/out are not searched;
  * a suite's runner finds the test files of the folder it lives in: each runner that
    calls kit.testing.run_tests.main is run with that function replaced by a recorder, and
    the folder it names must be its own; the runners that do not call it are the four
    that glob their own folder;
  * a suite runs with the root as its working directory and leaves no bytecode, and
    has an account name in $USER when the run has none: the name of the OS account
    (the one kit.human.changed_by writes; the console's tests need a plain name and the
    kit's tests compare getpass.getuser() with it), proven on the real kit/tests/test_facts.py;
  * the real list keeps the Zylos adapter's suite (the host every harness is
    adapted to), names node and git for every suite whose tests call them (each
    folder is scanned), and its RESULT parsing agrees with kit/testing/run_tests.py;
  * .github/workflows/ci.yml runs `python3 run_all.py` on push and pull request,
    with pinned actions, no secret, no `if:` and nothing that swallows a failure, in
    the two-space shape the text checks can read; the README names both, and every
    suite name in its `python3 run_all.py <names>` example is a suite of the list.

Each check is tried on a broken input first, so it cannot pass by looking at
nothing.
"""

from __future__ import annotations

import contextlib
import io
import os
import pwd
import re
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True            # importing run_all and the kit leaves no .pyc beside them
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)
import _t  # noqa: E402
import run_all  # noqa: E402
from kit.testing import run_tests  # noqa: E402

S = run_all.Suite
PASS3 = "print('RESULT: 3 passed')\n"
PASS4 = "print('RESULT: 4 passed')\n"
CI = ".github/workflows/ci.yml"


def tree(d: str, files: dict) -> str:
    for rel, body in files.items():
        path = os.path.join(d, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    return d


def go(d: str, suites, *argv: str, not_suites: dict | None = None, timeout_s: float = 30) -> tuple[int, str]:
    """(exit code, output) of run_all.main over the planted suites in `d`."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_all.main(list(argv), root=d, suites=tuple(suites), not_suites=not_suites or {}, timeout_s=timeout_s)
    return code, buf.getvalue()


@contextlib.contextmanager
def env(**changes):
    """os.environ with `changes` applied (None removes a variable), restored afterwards."""
    old = dict(os.environ)
    for k, v in changes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


@contextlib.contextmanager
def open_stdin():
    """File descriptor 0 is a pipe nobody writes to or closes: a child that inherits it blocks on a read."""
    r, w = os.pipe()
    try:
        saved = os.dup(0)
    except OSError:
        saved = None
    os.dup2(r, 0)
    try:
        yield
    finally:
        if saved is None:
            os.close(0)
        else:
            os.dup2(saved, 0)
            os.close(saved)
        os.close(r)
        os.close(w)


def order(out: str) -> list[str]:
    """The suites that ran, in the order they ran (the tree check's own line left out)."""
    return [m.group(1) for ln in out.splitlines() if (m := re.match(r"(?:ok|FAIL)\s+[\d.]+s\s+(\S+)\s", ln)) and m.group(1) != "run.py"]


def line_of(out: str, name: str) -> str:
    found = [ln for ln in out.splitlines() if re.match(rf"(ok|FAIL)\s+[\d.]+s\s+{re.escape(name)}\s", ln)]
    assert len(found) == 1, (name, out)
    return found[0]


def read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- the verdict --

def test_passing_suites_pass_the_run_and_their_counts_add_up():
    with _t.tmpdir() as d:
        tree(d, {"a/run.py": PASS3, "b/run.py": PASS4})
        code, out = go(d, [S("a", "a/run.py"), S("b", "b/run.py")])
        assert code == 0, out
        lines = out.splitlines()
        assert lines[-1] == "RESULT: 7 passed", out                                    # the exact line every runner prints
        assert re.fullmatch(r"ok\s+[\d.]+s\s+a\s+RESULT: 3 passed", line_of(out, "a"))
        assert re.fullmatch(r"ok\s+[\d.]+s\s+b\s+RESULT: 4 passed", line_of(out, "b"))
        assert "FAIL" not in out and "2 suites in" in out
        assert re.fullmatch(r"ok\s+[\d.]+s\s+run\.py files\s+2 found: 2 suites, 0 templates", line_of(out, "run.py files"))


def test_a_failing_suite_fails_the_run_shows_its_tail_and_the_rest_still_run():
    with _t.tmpdir() as d:
        noisy = "for i in range(60):\n    print('line', i)\nprint('FAILED: 1 of 2')\nraise SystemExit(1)\n"
        tree(d, {"a/run.py": PASS3, "b/run.py": noisy, "c/run.py": PASS4})
        code, out = go(d, [S("a", "a/run.py"), S("b", "b/run.py"), S("c", "c/run.py")])
        assert code == 1, out
        assert out.splitlines()[-1] == "RESULT: 7 passed, 1 failed (1 of 4 checks failed)", out
        assert re.fullmatch(r"FAIL\s+[\d.]+s\s+b\s+exit 1", line_of(out, "b"))
        assert line_of(out, "a").startswith("ok") and line_of(out, "c").startswith("ok")       # c ran after b failed
        assert "      | FAILED: 1 of 2" in out and "      | line 59" in out
        assert "line 20" not in out and "line 21" in out                                      # the last 40 lines, no more
    with _t.tmpdir() as d:                                                                   # stderr is part of the tail, not lost
        died = "raise SystemExit('FAILED: this runner needs the playbook kit next to it')\n"
        tree(d, {"a/run.py": died})
        code, out = go(d, [S("a", "a/run.py")])
        assert code == 1 and re.fullmatch(r"FAIL\s+[\d.]+s\s+a\s+exit 1", line_of(out, "a")), out
        assert "      | FAILED: this runner needs the playbook kit next to it" in out, out
        both = "import sys\nprint('to stdout')\nsys.stderr.write('to stderr\\n')\nprint('RESULT: 1 passed, 1 failed')\n"
        tree(d, {"b/run.py": both})
        code, out = go(d, [S("b", "b/run.py")])
        assert "      | to stdout" in out and "      | to stderr" in out, out               # one stream, in the order it was written


def test_each_way_a_suite_can_fail_without_a_clean_pass_fails_the_run():
    cases = {
        "a crash": ("raise RuntimeError('boom')\n", "exit 1"),
        "a non-zero exit after a RESULT line": ("print('RESULT: 3 passed')\nraise SystemExit(2)\n", "exit 2"),
        "no RESULT line": ("print('all good')\n", "missing 'RESULT: N passed' line"),
        "a FAILED line but no RESULT line, exit 0": ("print('FAILED: 1 of 2')\n", "missing 'RESULT: N passed' line"),
        "0 passed": ("print('RESULT: 0 passed')\n", "0 passed"),
        "a failure reported with exit 0": ("print('RESULT: 2 passed, 1 failed')\n", "reports 1 failed"),
        "a good line, then a bad one": ("print('RESULT: 4 passed')\nprint('RESULT: 1 passed, 2 failed')\n", "reports 2 failed"),
    }
    for label, (body, why) in cases.items():
        with _t.tmpdir() as d:
            tree(d, {"x/run.py": body})
            code, out = go(d, [S("x", "x/run.py")])
            assert code == 1 and why in line_of(out, "x") and line_of(out, "x").startswith("FAIL"), (label, out)
            assert out.splitlines()[-1] == "RESULT: 0 passed, 1 failed (1 of 2 checks failed)", (label, out)
    with _t.tmpdir() as d:                                                                   # the last RESULT line counts
        tree(d, {"x/run.py": "print('RESULT: 0 passed')\nprint('RESULT: 4 passed')\n"})
        assert go(d, [S("x", "x/run.py")])[0] == 0
    assert run_all.verdict(0, 3, 0, 1) == "" and run_all.verdict(0, None, 0, 1) and run_all.verdict(None, None, 0, 1)
    assert run_all.parse_result("a\nRESULT: 3 passed, 0 failed\n") == (3, 0) and run_all.parse_result("FAILED: 1") == (None, 0)


def test_a_suite_that_hangs_is_killed_with_its_children_and_fails():
    with _t.tmpdir() as d:
        pidfile = os.path.join(d, "child.pid")
        hang = ("import subprocess, sys, time\n"
                f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                f"open({pidfile!r}, 'w').write(str(p.pid))\n"
                "time.sleep(30)\n")
        tree(d, {"x/run.py": hang})
        t0 = time.monotonic()
        code, out = go(d, [S("x", "x/run.py")], timeout_s=2)
        assert code == 1 and "timed out after 2s" in line_of(out, "x") and time.monotonic() - t0 < 15, out
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read())
        for _ in range(50):                                                                  # the kill is asynchronous
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            raise AssertionError("the hung suite's child is still running")


def gone(pid: int, what: str) -> None:
    """Wait until process `pid` is gone (the kill is asynchronous); kill it and fail if it is not."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, 9)
    raise AssertionError(f"{what} is still running after run_all was interrupted")


def test_an_interrupted_run_kills_the_suite_it_is_waiting_for_with_its_children():
    for label, sig in (("Ctrl-C", signal.SIGINT), ("SIGTERM", signal.SIGTERM)):
        with _t.tmpdir() as d:
            pids = os.path.join(d, "pids")
            sleeper = ("import os, subprocess, sys, time\n"
                       "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                       f"open({pids!r} + '.tmp', 'w').write(f'{{os.getpid()}} {{p.pid}}')\n"
                       f"os.rename({pids!r} + '.tmp', {pids!r})\n"
                       "time.sleep(60)\n")
            tree(d, {"x/run.py": sleeper})
            driver = ("import signal, sys\n"
                      "signal.signal(signal.SIGINT, signal.default_int_handler)\n"       # a shell's `&` leaves SIGINT ignored
                      f"sys.path.insert(0, {REPO!r})\n"
                      "import run_all\n"
                      f"sys.exit(run_all.main([], root={d!r}, suites=(run_all.Suite('x', 'x/run.py'),), not_suites={{}}, timeout_s=120))\n")
            run = subprocess.Popen([sys.executable, "-c", driver], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for _ in range(200):
                    if os.path.exists(pids) or run.poll() is not None:
                        break
                    time.sleep(0.05)
                assert os.path.exists(pids), (label, "the planted suite never started", run.communicate()[0])
                with open(pids, encoding="utf-8") as f:
                    suite_pid, child_pid = (int(n) for n in f.read().split())
                run.send_signal(sig)
                out, _ = run.communicate(timeout=30)
            finally:
                if run.poll() is None:
                    run.kill()
                    run.wait()
            assert run.returncode != 0, (label, "an interrupted run is not a pass", out)
            gone(suite_pid, f"{label}: the suite")
            gone(child_pid, f"{label}: the suite's child")


def test_a_suite_runs_in_the_root_leaves_no_bytecode_and_cannot_read_a_terminal():
    with _t.tmpdir() as d:
        body = ("import helper, os, sys\n"
                f"assert os.path.realpath(os.getcwd()) == os.path.realpath({d!r}), os.getcwd()\n"
                "assert sys.stdin.read() == ''\n"              # stdin is empty: a suite that asks a question gets EOF, not a hang
                "print('RESULT: 1 passed')\n")
        tree(d, {"x/run.py": body, "x/helper.py": "VALUE = 1\n"})
        with env(PYTHONDONTWRITEBYTECODE=None), open_stdin():     # run_all, not its caller, keeps the suite clean and unblocked
            code, out = go(d, [S("x", "x/run.py")], timeout_s=5)
        assert code == 0, out
        assert not [top for top, dirs, _ in os.walk(d) if "__pycache__" in dirs], "a suite left bytecode in the tree"


NO_NAME = {"USER": None, "LOGNAME": None, "LNAME": None, "USERNAME": None}     # what getpass.getuser() reads, all unset
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,79}")                       # a name serve.py --user accepts (console/core.py TOKEN_RE)


def passwd_name() -> str | None:
    """The name kit.human.changed_by writes: the passwd entry of the effective uid (None without one)."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):
        return None


def test_a_suite_has_an_account_name_when_the_run_has_none_and_keeps_the_one_it_has():
    me = passwd_name()
    default = me if me and PLAIN.fullmatch(me) else run_all.ACCOUNT      # the account's own name; ACCOUNT only when it has no plain one
    assert run_all.account() == default
    want = lambda name: ("import os, re\n"                                                    # noqa: E731
                         f"assert os.environ.get('USER') == {name!r}, os.environ.get('USER')\n"
                         "assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,79}', os.environ['USER'])\n"   # a name serve.py --user accepts
                         "print('RESULT: 1 passed')\n")
    with _t.tmpdir() as d:
        for label, user, expected in (("none", None, default), ("empty", "", default), ("a person", "mara", "mara")):
            tree(d, {"x/run.py": want(expected)})
            with env(**{**NO_NAME, "USER": user}):
                code, out = go(d, [S("x", "x/run.py")])
            assert code == 0, (label, out)
        tree(d, {"x/run.py": want("mara")})                                                     # the check can fail
        with env(**NO_NAME):
            assert go(d, [S("x", "x/run.py")])[0] == 1
    assert PLAIN.fullmatch(run_all.ACCOUNT) and PLAIN.fullmatch(default)


def test_the_name_a_suite_gets_is_the_one_the_kit_writes_into_its_rows():
    """The kit's tests expect getpass.getuser() (USER first) in the rows kit.human.changed_by wrote, and changed_by
    reads the passwd entry, never the environment: a $USER the run made up fails them. Not seen by a run that has
    USER or LOGNAME already (a login shell does), so these runs have neither."""
    me = passwd_name()
    if not me or not PLAIN.fullmatch(me) or me == run_all.ACCOUNT:
        return                                   # no passwd entry, or an account named like the fallback: nothing to tell apart
    same = ("import getpass, os, pwd\n"
            "me = pwd.getpwuid(os.geteuid()).pw_name\n"
            "assert getpass.getuser() == me, (getpass.getuser(), me)\n"
            "print('RESULT: 1 passed')\n")
    with _t.tmpdir() as d:
        tree(d, {"x/run.py": same})
        with env(**NO_NAME):
            code, out = go(d, [S("x", "x/run.py")])
        assert code == 0, out
        with env(**{**NO_NAME, "USER": run_all.ACCOUNT}):                         # the fallback as the only name: what a run.py sees if USER was made up
            code, out = go(d, [S("x", "x/run.py")])
        assert code == 1 and "exit 1" in line_of(out, "x"), out
    facts = S("facts", "kit/tests/test_facts.py", (), "a kit test that compares getpass.getuser() with changed_by")
    with env(**NO_NAME):                                                                      # the real test, as `env -i python3 run_all.py` runs it
        r = run_all.run_suite(facts, Path(REPO), 120)
    assert r["ok"], r["out"][-2000:]
    with env(**{**NO_NAME, "USER": run_all.ACCOUNT}):
        assert not run_all.run_suite(facts, Path(REPO), 120)["ok"]                                  # and it does fail on a made-up name


# ---------------------------------------------------- missing suites and tools --

def test_a_suite_whose_run_py_is_gone_fails():
    with _t.tmpdir() as d:
        tree(d, {"a/run.py": PASS3})
        code, out = go(d, [S("a", "a/run.py"), S("gone", "gone/run.py")])
        assert code == 1, out
        assert re.fullmatch(r"FAIL\s+[\d.]+s\s+gone\s+missing: gone/run\.py", line_of(out, "gone")), out
        assert line_of(out, "a").startswith("ok")                                             # the others still run
        assert "suite gone: gone/run.py is missing" in out                                    # and the tree check says so once more
        assert out.splitlines()[-1] == "RESULT: 3 passed, 2 failed (2 of 3 checks failed)", out


def test_a_missing_required_tool_fails_the_suite_and_it_is_not_run():
    with _t.tmpdir() as d:
        marker = os.path.join(d, "ran")
        ran = f"open({marker!r}, 'w').close()\nprint('RESULT: 1 passed')\n"
        tree(d, {"x/run.py": ran})
        code, out = go(d, [S("x", "x/run.py", ("no-such-tool-xyz", "sh"))])
        assert code == 1 and "required tool not on PATH: no-such-tool-xyz" in line_of(out, "x"), out
        assert [t.strip() for t in line_of(out, "x").split("PATH:")[1].split(",")] == ["no-such-tool-xyz"], out   # only the absent one is named
        assert not os.path.exists(marker), "a suite that lacks its tool must not run"
        code, out = go(d, [S("x", "x/run.py", ("sh",))])                                      # the same suite, its tool there
        assert code == 0 and os.path.exists(marker), out


def test_the_zylos_suite_fails_when_node_is_not_on_path():
    zylos = [s for s in run_all.SUITES if s.name == "zylos"]
    assert len(zylos) == 1 and "node" in zylos[0].needs, zylos
    with _t.tmpdir() as empty, env(PATH=empty):
        code, out = go(REPO, run_all.SUITES, "zylos", not_suites=run_all.NOT_SUITES)
    assert code == 1 and "required tool not on PATH: node" in line_of(out, "zylos"), out
    assert out.splitlines()[-1].startswith("RESULT: 0 passed, 1 failed"), out


# ------------------------------------------------------------ subsets, --list --

def test_a_subset_runs_by_exact_name_in_list_order_and_an_unknown_name_fails_first():
    with _t.tmpdir() as d:
        names = ("ant", "bee", "cat")
        marks = {n: os.path.join(d, f"ran-{n}") for n in names}
        tree(d, {f"{n}/run.py": f"open({p!r}, 'w').close()\nprint('RESULT: 1 passed')\n" for n, p in marks.items()})
        suites = [S(n, f"{n}/run.py") for n in names]

        def ran():
            found = sorted(n for n, p in marks.items() if os.path.exists(p))
            for p in marks.values():
                if os.path.exists(p):
                    os.unlink(p)
            return found

        code, out = go(d, suites, "cat", "ant")
        assert code == 0 and ran() == ["ant", "cat"], out
        assert order(out) == ["ant", "cat"], out                                              # list order; bee did not run
        assert out.splitlines()[-1] == "RESULT: 2 passed", out
        code, out = go(d, suites, "ant", "nosuch")
        assert code == 1 and ran() == [] and "no suite named 'nosuch'" in out and "RESULT" not in out, out   # nothing ran
        code, out = go(d, suites, "an")                                                       # exact, not a substring
        assert code == 1 and ran() == [] and "no suite named 'an'" in out, out
        code, out = go(d, suites)
        assert code == 0 and ran() == list(names) and order(out) == list(names), out         # no name: all of them
        code, out = go(d, [])
        assert code == 1 and out.startswith("FAILED: no suites"), out


def test_list_names_the_suites_and_runs_none():
    with _t.tmpdir() as d:
        marker = os.path.join(d, "ran")
        tree(d, {"a/run.py": f"open({marker!r}, 'w').close()\nprint('RESULT: 1 passed')\n", "t/run.py": PASS3})
        code, out = go(d, [S("a", "a/run.py", ("node",), "the a things")], "--list", not_suites={"t/run.py": "a template"})
        assert code == 0 and not os.path.exists(marker), out
        assert re.search(r"^a\s+a/run\.py\s+needs node\s+the a things$", out, re.M), out
        assert "not a suite: t/run.py: a template" in out and "RESULT" not in out, out
        code, out = go(d, [S("a", "a/run.py")], "--list")                                     # t/run.py is now unaccounted for
        assert code == 1 and "t/run.py is neither a suite nor in NOT_SUITES" in out, out
    out = subprocess.run([sys.executable, os.path.join(REPO, "run_all.py"), "--list"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr                                       # the script as a script
    assert [ln.split()[0] for ln in out.stdout.splitlines() if not ln.startswith("not a suite")] == [s.name for s in run_all.SUITES]
    bad = subprocess.run([sys.executable, os.path.join(REPO, "run_all.py"), "nosuch"], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 1 and bad.stdout.startswith("FAILED: no suite named 'nosuch'"), bad.stdout


# ------------------------------------------------ every run.py is accounted for --

def test_every_run_py_in_the_tree_is_a_suite_or_a_template_with_a_reason():
    found = run_all.runners(REPO)
    suites = {s.run for s in run_all.SUITES}
    assert len(found) >= 10, found                                                            # the search saw something
    assert set(found) == suites | set(run_all.NOT_SUITES), sorted(set(found) ^ (suites | set(run_all.NOT_SUITES)))
    assert run_all.problems(REPO, run_all.SUITES, run_all.NOT_SUITES) == []
    assert all(why.strip() for why in run_all.NOT_SUITES.values())
    names = [s.name for s in run_all.SUITES]
    assert len(names) == len(set(names)) and all(re.fullmatch(r"[a-z]+", n) for n in names), names


def test_a_stray_gone_double_or_unexplained_run_py_fails_the_run():
    with _t.tmpdir() as d:
        tree(d, {"a/run.py": PASS3, "stray/run.py": PASS3, "tpl/run.py": PASS3})
        a = [S("a", "a/run.py")]
        code, out = go(d, a, not_suites={"tpl/run.py": "a template"})                          # stray/run.py is neither
        assert code == 1, out
        assert "stray/run.py is neither a suite nor in NOT_SUITES" in out and "FAIL" in line_of(out, "run.py files"), out
        assert line_of(out, "a").startswith("ok")                                             # the suite itself ran and passed
        assert out.splitlines()[-1] == "RESULT: 3 passed, 1 failed (1 of 2 checks failed)", out

        def wrong(suites, not_suites):
            return run_all.problems(d, suites, not_suites)

        both = {"tpl/run.py": "a template", "stray/run.py": "a template"}
        assert wrong(a, both) == []                                                           # all accounted for: nothing wrong
        assert wrong(a, {**both, "gone/run.py": "a template"}) == ["NOT_SUITES names gone/run.py, which is gone: take it out"]
        assert wrong(a + [S("a", "tpl/run.py")], {"stray/run.py": "x"}) == ["suite name listed twice: a"]
        assert wrong(a + [S("b", "a/run.py")], both) == ["run.py listed twice: a/run.py"]
        assert wrong(a, {**both, "a/run.py": "x"}) == ["a/run.py is both a suite and in NOT_SUITES"]
        assert wrong(a, {**both, "tpl/run.py": "  "}) == ["tpl/run.py has no reason in NOT_SUITES"]


GATE_RUNNER = ("import sys\nfrom pathlib import Path\nfrom kit.testing import run_tests\n"
               "raise SystemExit(run_tests.main(sys.argv[1:], root=Path(__file__).resolve().parents[{up}], tests_dir={tests_dir!r}))\n")
OWN_FOLDER_RUNNERS = {"docs": "tests/run.py", "console": "console/tests/run.py", "zylos": "hosts/zylos/tests/run.py",
                      "templates": "templates/tests/selftest/run.py"}      # these glob their own folder (HERE) and use no gate


def searched(runner: str) -> list[str]:
    """The folders `runner` hands to kit.testing.run_tests.main, root joined to tests_dir (none when it does not call it).
    The runner's __main__ is run with main replaced by a recorder that searches and runs nothing."""
    seen, real, path = [], run_tests.main, list(sys.path)

    def spy(argv=None, *, root, tests_dir="tests", **_):
        seen.append(os.path.realpath(os.path.join(str(root), tests_dir)))
        return 0

    run_tests.main = spy
    try:
        with contextlib.suppress(SystemExit):
            runpy.run_path(runner, run_name="__main__")
    finally:
        run_tests.main, sys.path[:] = real, path
    return seen


def test_a_runner_that_uses_the_gate_searches_the_folder_it_lives_in():
    with _t.tmpdir() as d:                                                                   # the check on runners it can tell apart
        for label, up, tests_dir, ok in (("root is the folder above, tests_dir its name", 1, "tests", True),
                                         ("root two up, the path spelled out", 2, "a/tests", True),
                                         ("root two up, only `tests`: the folder of another suite", 2, "tests", False)):
            tree(d, {"a/tests/run.py": GATE_RUNNER.format(up=up, tests_dir=tests_dir)})
            here = os.path.realpath(os.path.join(d, "a", "tests"))
            assert (searched(os.path.join(d, "a", "tests", "run.py")) == [here]) is ok, (label, searched(os.path.join(d, "a", "tests", "run.py")))
        tree(d, {"a/tests/run.py": "X = 1\n"})
        assert searched(os.path.join(d, "a", "tests", "run.py")) == []                        # a runner with no gate call records nothing
    gated = 0
    for s in run_all.SUITES:
        folder = os.path.realpath(os.path.join(REPO, os.path.dirname(s.run)))
        if s.name in OWN_FOLDER_RUNNERS:
            assert OWN_FOLDER_RUNNERS[s.name] == s.run and "run_tests.main" not in read(s.run) and "HERE" in read(s.run), s.name
            continue
        assert searched(os.path.join(REPO, s.run)) == [folder], (s.name, s.run, searched(os.path.join(REPO, s.run)))
        gated += 1
    assert gated == len(run_all.SUITES) - len(OWN_FOLDER_RUNNERS) and gated >= 5, gated     # a new suite is gated or named above


def test_the_search_skips_dot_folders_bytecode_node_modules_and_the_preview_folder():
    with _t.tmpdir() as d:
        tree(d, {"run.py": "", "a/b/run.py": "", "xrun.py": "", "a/run.pyc": "", "a/run.pyw": "",
                 ".claude/worktrees/w/tests/run.py": "", ".git/run.py": "", ".github/run.py": "",
                 "__pycache__/run.py": "", "a/node_modules/x/run.py": "", "scaffold/out/h/tests/run.py": "",
                 "scaffold/skeleton/tests/run.py": ""})
        assert run_all.runners(d) == ["a/b/run.py", "run.py", "scaffold/skeleton/tests/run.py"]
        assert run_all.runners(os.path.join(d, "nope")) == []


# ---------------------------------------------------------- the real list --

WANTS = {"node": re.compile(r"""which\(\s*["']node["']\s*\)"""),                        # a test that looks for node
         "git": re.compile(r"""\[\s*["']git["']\s*,""")}                                   # a test that runs ["git", ...]


def test_the_real_list_keeps_the_zylos_suite_and_names_the_tools_the_tests_need():
    by = {s.name: s for s in run_all.SUITES}
    assert by["zylos"].run == "hosts/zylos/tests/run.py" and by["zylos"].needs == ("node",)   # the host every harness is adapted to
    assert os.path.isfile(os.path.join(REPO, "hosts", "zylos", "tests", "run.py"))
    assert all(s.covers for s in run_all.SUITES)
    for name, tool in (("console", "node"), ("workflows", "node"), ("kit", "git"), ("scaffold", "git"), ("golden", "git"),
                       ("templates", "git"), ("console", "git"), ("workflows", "git")):
        assert name in by, name                                                               # a renamed suite fails here, not silently
        assert tool in by[name].needs, (name, tool)                                           # these build a repository or run node
    assert WANTS["node"].search("NODE = shutil.which('node')") and not WANTS["node"].search("which('nodejs')")   # the scans can tell
    assert WANTS["git"].search('subprocess.run(["git", "-C", d])') and WANTS["git"].search("run(\n    [ 'git',\n") \
        and not WANTS["git"].search('print("git is needed")') and not WANTS["git"].search('("kit", "git")')
    for tool, wants in WANTS.items():                                                          # a test that needs a tool: its suite names it
        used = 0
        for s in run_all.SUITES:
            found = []
            for top, _, names in os.walk(os.path.join(REPO, os.path.dirname(s.run))):
                for n in names:
                    if n.endswith(".py") and n != os.path.basename(__file__):               # this file quotes the patterns
                        with open(os.path.join(top, n), encoding="utf-8") as f:
                            if wants.search(f.read()):
                                found.append(n)
            assert not found or tool in s.needs, (s.name, tool, found)
            used += bool(found)
        assert used >= 3 and any(tool in s.needs for s in run_all.SUITES), (tool, used)    # the scan found the suites that use it


def test_the_result_rule_agrees_with_the_kit_gate():
    outs = ["RESULT: 3 passed\n", "RESULT: 3 passed, 0 failed\n", "RESULT: 3 passed, 2 failed (2 of 9 files)\n", "RESULT: 0 passed\n",
            "FAILED: 1 of 2 files\n", "", "x\nRESULT: 1 passed\nRESULT: 5 passed, 1 failed\n", "  RESULT:  7  passed  \n",
            "xRESULT: 1 passed\n", "RESULT: 1 passedd\n", "RESULT: -1 passed\n"]
    for out in outs:
        assert run_all.parse_result(out) == run_tests.parse_result(out), out
    assert run_all.parse_result("xRESULT: 1 passed\n")[0] is None                              # the table saw a miss
    for code in (0, 1, None):
        for passed in (None, 0, 3):
            for failed in (0, 2):
                assert bool(run_all.verdict(code, passed, failed, 5)) == bool(run_tests.verdict(code, passed, failed, [], 5)), \
                    (code, passed, failed)


# ------------------------------------------------------------------ CI and docs --

def yaml_shape_problems(text: str) -> list[str]:
    """Not a YAML parser (the standard library has none): the shape this workflow is written in. No tabs; an even indent;
    every line a `key:` or `key: value` or a `- item`; a line is deeper than the one above it only where YAML allows
    children: 2 more under a `key:`, 2 or 4 more under a `- key:`, 2 more under a `- key: value`, never under
    `key: value`; and a `- item` never stands among the keys of a mapping (right under a `key: value` at its own indent).
    A `key:` may have nothing under it (`push:` is null). The lines of a `|` or `>` block are skipped."""
    out, prev, block = [], None, None
    for n, raw in enumerate(text.splitlines(), 1):
        body = raw.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        indent = len(raw) - len(body)
        if block is not None and indent > block:
            continue
        block = None
        item, opens = body.startswith("- "), body.endswith(":")
        if "\t" in raw:
            out.append(f"line {n}: has a tab")
        elif indent % 2:
            out.append(f"line {n}: is indented by an odd number of spaces")
        elif not re.fullmatch(r"-\s.*|[\w.-]+:(?:\s.*)?", body):
            out.append(f"line {n}: is neither `key:`, `key: value` nor `- item`")
        elif prev is not None:
            p_indent, p_opens, p_item = prev
            deeper = {(True, False): {2}, (True, True): {2, 4}, (False, True): {2}, (False, False): set()}[(p_opens, p_item)]
            if indent > p_indent and indent - p_indent not in deeper:
                out.append(f"line {n}: is indented deeper than the line above it allows")
            elif indent == p_indent and item and not p_opens and not p_item:
                out.append(f"line {n}: is a list item among the keys of a mapping")
        prev = (indent, opens, item)
        block = indent if re.search(r":\s+[|>][+-]?$", body) else None
    return out


def ci_problems(text: str) -> list[str]:
    """What is wrong with a workflow file, read as text (there is no YAML parser in the standard library)."""
    out = []
    if not re.search(r"^on:\n(?:  .*\n)*?  push:", text, re.M) or not re.search(r"^on:\n(?:  .*\n)*?  pull_request:", text, re.M):
        out.append("does not run on both push and pull_request")
    if not re.search(r"^\s*- run: python3 run_all\.py$", text, re.M):
        out.append("does not run `python3 run_all.py`")
    uses = re.findall(r"^\s*- uses: (\S+)$", text, re.M)
    out += [f"{u} is not pinned by a major tag" for u in uses if not re.fullmatch(r"actions/[\w-]+@v\d+", u)]
    if not {"actions/checkout", "actions/setup-python", "actions/setup-node"} <= {u.split("@")[0] for u in uses}:
        out.append("does not check out, set up python and set up node")
    if not re.search(r"^\s+python-version: \"3\.12\"$", text, re.M) or not re.search(r"^\s+node-version: \"\d+\"$", text, re.M):
        out.append("python 3.12 and a node major are not set")
    if "ubuntu-latest" not in text:
        out.append("does not run on ubuntu-latest")
    if re.search(r"secrets\.|\$\{\{\s*env\.|GITHUB_TOKEN|pull_request_target", text):
        out.append("names a secret, a token or pull_request_target")
    if re.search(r"continue-on-error|\|\|\s*(true|:)|if:\s*always\(\)|set \+e", text):
        out.append("swallows a failure")
    if re.search(r"^\s*(?:-\s+)?if:", text, re.M):
        out.append("has an `if:`: a condition on the job or the step can switch the tests off")
    out += yaml_shape_problems(text)
    if not re.search(r"^permissions:\n  contents: read$", text, re.M):
        out.append("does not limit the token to reading")
    return out


GOOD_CI = ("name: ci\n\non:\n  push:\n  pull_request:\n\npermissions:\n  contents: read\n\njobs:\n  suites:\n"
           "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v7\n      - uses: actions/setup-python@v7\n"
           "        with:\n          python-version: \"3.12\"\n      - uses: actions/setup-node@v7\n        with:\n"
           "          node-version: \"24\"\n      - run: python3 run_all.py\n")


def test_the_ci_workflow_runs_run_all_on_push_and_pull_request_and_hides_no_failure():
    assert ci_problems(GOOD_CI) == []
    broken = {
        "on: push only": GOOD_CI.replace("  pull_request:\n", ""),
        "no run_all": GOOD_CI.replace("run_all.py", "tests/run.py"),
        "a branch tag": GOOD_CI.replace("setup-node@v7", "setup-node@main"),
        "a sha": GOOD_CI.replace("checkout@v7", "checkout@" + "a" * 40),
        "no node": GOOD_CI.replace("actions/setup-node@v7", "actions/cache@v4"),
        "python 3.11": GOOD_CI.replace('"3.12"', '"3.11"'),
        "a secret": GOOD_CI + "        env:\n          K: ${{ secrets.K }}\n",
        "failures swallowed": GOOD_CI.replace("python3 run_all.py", "python3 run_all.py || true"),
        "continue-on-error": GOOD_CI.replace("    steps:", "    continue-on-error: true\n    steps:"),
        "write token": GOOD_CI.replace("contents: read", "contents: write"),
        "macos": GOOD_CI.replace("ubuntu-latest", "macos-latest"),
        "the run step switched off": GOOD_CI.replace("- run: python3 run_all.py\n", "- run: python3 run_all.py\n        if: false\n"),
        "a step that runs on push only": GOOD_CI.replace("      - run:", "      - if: github.event_name == 'push'\n        run:"),
        "the job switched off": GOOD_CI.replace("    steps:", "    if: false\n    steps:"),
        "not a mapping": GOOD_CI.replace("  suites:\n", "  suites\n"),
        "a tab": GOOD_CI.replace("    runs-on:", "\truns-on:"),
        "an odd indent": GOOD_CI.replace("      - uses: actions/checkout", "       - uses: actions/checkout"),
        "steps pushed too deep": GOOD_CI.replace("    steps:\n      - uses: actions/checkout", "    steps:\n          - uses: actions/checkout"),
        "a step among the keys of a mapping": GOOD_CI.replace("      - run: python3 run_all.py", "          - run: python3 run_all.py"),
    }
    for label, text in broken.items():
        assert ci_problems(text), label
    assert yaml_shape_problems(GOOD_CI.replace("- run: python3 run_all.py\n", "- run: |\n          python3 run_all.py\n          true\n")) == []   # a block's own lines are not keys
    assert yaml_shape_problems("a:\n  b: |\n    x y z\n  c: 1\n") == [] and yaml_shape_problems("a:\n  b: |\n    x y z\n c: 1\n")
    assert ci_problems(read(CI)) == [], ci_problems(read(CI))


def example_suites(text: str) -> list[str]:
    """The suite names in every `python3 run_all.py <names>` code span of `text` (flags left out)."""
    return [w for span in re.findall(r"`python3 run_all\.py([^`]*)`", text) for w in span.split() if not w.startswith("-")]


def test_the_readme_names_the_entry_point_and_the_workflow_and_both_exist():
    text = read("README.md")
    assert "python3 run_all.py" in text and f"({CI})" in text and "(run_all.py)" in text
    known = {s.name for s in run_all.SUITES}
    shown = example_suites(text)
    assert shown and set(shown) <= known, (shown, sorted(known))                              # the README's example runs, it does not say "no suite named"
    assert example_suites("`python3 run_all.py --list`, `python3 run_all.py kits console`") == ["kits", "console"]
    assert not set(example_suites("`python3 run_all.py kits console`")) <= known              # the check sees a suite that was renamed
    assert os.path.isfile(os.path.join(REPO, "run_all.py")) and os.path.isfile(os.path.join(REPO, CI))
    for name in ("--list",):
        assert name in text and name in run_all.__doc__
    assert "python3 run_all.py" not in "see python3 tests/run.py"                              # the check can tell


if __name__ == "__main__":
    _t.main(globals())
