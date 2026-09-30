#!/usr/bin/env python3
"""Run every test suite of the playbook: one line each, one verdict.

    python3 run_all.py                  every suite, in the order of SUITES
    python3 run_all.py kit console      only the suites with those names (exact; an unknown name fails)
    python3 run_all.py --list           the suites (only those named, if any), the tools each needs, and the run.py files that are not suites

A suite is one runner: a `run.py` that runs the test files of its folder (docs
for the root ones, kit, scaffold, build, console, zylos, templates, workflows,
golden). Suites run one after the other, each in its own process with the
repository root as its working directory, and with an account name in $USER (the
one the run has; when it has none, the name of the OS account the run is, the one
kit.human.changed_by records, and ACCOUNT only when there is no such name: the
console's tests start serve.py, whose default --user is $USER, the kit's tests
compare getpass.getuser(), which reads $USER first, with the name changed_by writes,
and a container, a cron job or `env -i` sets none).

The day-one rule of the playbook, the same as in every runner it calls and in
kit/testing/run_tests.py: a suite passes only when it exits 0 AND its last
`RESULT: N passed[, M failed]` line has N > 0 and M = 0. A suite that crashes,
times out, prints no RESULT line or reports a failure with exit 0 fails. So does
one whose `run.py` is gone or whose required tool (node for the zylos adapter,
git for the suites that build a repository) is not on PATH: a check that cannot
run is never a silent skip. Every `run.py` in the tree is either a suite here or
listed in NOT_SUITES with the reason it must not run here (a template copy): a
new one that is neither fails the run, so a suite cannot be added and forgotten.

A run that is interrupted (Ctrl-C, SIGTERM) kills the suite it is waiting for, with
its children, before it ends: a suite is its own process group, so the signal never
reaches it, and one left running would keep its ports and temp files.

Prints one line per suite (the tail of a failing suite's output below it) and a
last line `RESULT: T passed` (T = the sum of the suites' counts), or
`RESULT: T passed, K failed (K of N checks failed)`. Exit 0 only when every
check passed. Stdlib only, Python 3.11+, macOS or Linux.

Test: tests/test_run_all.py.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
RESULT_LINE = re.compile(r"^\s*RESULT:\s+(\d+)\s+passed(?:\s*,\s*(\d+)\s+failed)?\b", re.MULTILINE)
TIMEOUT_S = 1800                      # per suite: scaffold's own files get 900 s each
TAIL = 40
ACCOUNT = "run-all"                   # $USER for the suites when the run has none and its account has no plain name
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,79}")      # what serve.py --user accepts (console/core.py TOKEN_RE)
SKIP_DIRS = {"__pycache__", "node_modules"}      # and every folder that starts with "."
SKIP_PATHS = {"scaffold/out"}                     # the folder scaffold/new_harness.py may preview a harness in (git-ignored)
TREE = "run.py files"                             # the name of the check that every run.py is accounted for


class Suite(NamedTuple):
    name: str
    run: str                          # its run.py, as a path from the root
    needs: tuple[str, ...] = ()       # tools its tests shell out to: missing one fails the suite
    covers: str = ""


SUITES = (
    Suite("docs", "tests/run.py", (), "the build docs held to the files, and run_all.py"),
    Suite("kit", "kit/tests/run.py", ("git",), "the shared harness modules and their guards"),
    Suite("scaffold", "scaffold/tests/run.py", ("git",), "the scaffolder: a generated harness is green before its first feature"),
    Suite("build", "build/tests/run.py", (), "the build-time tools: intake check, asks, apply"),
    Suite("console", "console/tests/run.py", ("git", "node"), "the owner console and its docs (console.js runs under node)"),
    Suite("zylos", "hosts/zylos/tests/run.py", ("node",), "the Zylos / OpenMax host adapter (its hooks and bin are Node)"),
    Suite("templates", "templates/tests/selftest/run.py", ("git",), "the day-one test templates, installed in a sample harness"),
    Suite("workflows", "templates/workflows/tests/run.py", ("git", "node"), "the workflow script templates (run under a node simulator)"),
    Suite("golden", "templates/tests/golden/tests/run.py", ("git",), "the golden-diff tool, on a fake harness"),
)

NOT_SUITES = {
    "templates/tests/run.py": "a template: the runner a harness copies into its tests/. Run here it runs the day-one test "
                              "templates outside a harness and fails; the `templates` suite installs them in a sample harness",
    "scaffold/skeleton/tests/run.py": "a template: the runner the scaffolder writes into a new harness (placeholders, and the "
                                      "vendored kit on its path); the `scaffold` suite runs it inside a generated harness",
}


def parse_result(out: str) -> tuple[int | None, int]:
    """(passed, failed) of the last RESULT line; passed is None when there is none."""
    found = list(RESULT_LINE.finditer(out))
    if not found:
        return None, 0
    return int(found[-1].group(1)), int(found[-1].group(2) or 0)


def verdict(code: int | None, passed: int | None, failed: int, timeout_s: float) -> str:
    """Why a suite fails the rule ('' = it passes)."""
    if code is None:
        return f"timed out after {timeout_s:g}s"
    if code != 0:
        return f"exit {code}"
    if passed is None:
        return "missing 'RESULT: N passed' line"
    if failed > 0:
        return f"RESULT line reports {failed} failed"
    if passed == 0:
        return "RESULT line reports 0 passed (ran no tests)"
    return ""


def runners(root: Path) -> list[str]:
    """Every file named run.py under `root`, as paths from it. Folders that start with "." (a .git, a .claude
    worktree holding another copy of the repository), __pycache__, node_modules and scaffold/out are not entered."""
    found = []
    for top, dirs, names in os.walk(root):
        here = Path(top).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                         and (here / d).as_posix() not in SKIP_PATHS)
        if "run.py" in names:
            found.append((here / "run.py").as_posix())
    return sorted(found)


def problems(root: Path, suites, not_suites) -> list[str]:
    """What is wrong with the list of suites against the tree: a name or path given twice, a run.py that is gone
    (a suite or an exclusion), a run.py that is neither a suite nor excluded."""
    out = []
    names = [s.name for s in suites]
    paths = [s.run for s in suites]
    out += [f"suite name listed twice: {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    out += [f"run.py listed twice: {p}" for p in sorted({p for p in paths if paths.count(p) > 1})]
    out += [f"{p} is both a suite and in NOT_SUITES" for p in sorted(set(paths) & set(not_suites))]
    on_disk = set(runners(root))
    out += [f"suite {s.name}: {s.run} is missing" for s in suites if s.run not in on_disk]
    out += [f"NOT_SUITES names {p}, which is gone: take it out" for p in sorted(not_suites) if p not in on_disk]
    out += [f"{p} has no reason in NOT_SUITES" for p, why in sorted(not_suites.items()) if not why.strip()]
    out += [f"{p} is neither a suite nor in NOT_SUITES: add it to one in run_all.py"
            for p in sorted(on_disk - set(paths) - set(not_suites))]
    return out


def account() -> str:
    """The name of the OS account this process runs as: its passwd entry, which is what kit.human.changed_by writes
    into a history row (never an environment variable). ACCOUNT when there is no entry or its name is not plain. A
    $USER that is not this name makes kit/tests/test_facts.py and test_decisions.py fail, because they expect
    getpass.getuser() (which reads $USER first) in the rows changed_by wrote."""
    try:
        name = pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):
        return ACCOUNT
    return name if PLAIN_NAME.fullmatch(name) else ACCOUNT


def suite_env() -> dict[str, str]:
    """Our environment, with no bytecode written and an account name in USER (the run's own, else this account's)."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if not env.get("USER"):
        env["USER"] = account()
    return env


def kill_group(p: subprocess.Popen) -> None:
    """SIGKILL the suite's whole process group (it is its own session leader, so its children go with it)."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_suite(suite: Suite, root: Path, timeout_s: float) -> dict:
    """{name, ok, why, passed, secs, out}: one suite, one process group, killed on a timeout and when the run
    itself is interrupted (Ctrl-C, SIGTERM): the suite is its own session, so the terminal's signal never reaches
    it, and a console or adapter suite left running holds its ports and temp files."""
    t0 = time.monotonic()
    out, code, passed, failed = "", 0, None, 0
    absent = [t for t in suite.needs if not shutil.which(t)]
    if not (root / suite.run).is_file():
        why = f"missing: {suite.run}"
    elif absent:
        why = f"required tool not on PATH: {', '.join(absent)}"
    else:
        p = subprocess.Popen([sys.executable, str(root / suite.run)], cwd=root,
                             env=suite_env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                             start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout_s)
            code = p.returncode
        except subprocess.TimeoutExpired:
            kill_group(p)
            out, _ = p.communicate()
            code = None
        except BaseException:               # KeyboardInterrupt, SystemExit from SIGTERM: no orphan, then let it go on
            kill_group(p)
            p.wait()
            p.stdout.close()
            raise
        passed, failed = parse_result(out or "")
        why = verdict(code, passed, failed, timeout_s)
    return {"name": suite.name, "ok": not why, "why": why, "passed": passed or 0,
            "secs": time.monotonic() - t0, "out": out or ""}


@contextlib.contextmanager
def sigterm_exits():
    """While a run is going, SIGTERM raises SystemExit(143) instead of ending the process on the spot, so run_suite
    can kill the suite it is waiting for. The caller's handler is back afterwards. Off the main thread a handler
    cannot be set, and the caller's stays."""
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    try:
        old = signal.signal(signal.SIGTERM, stop)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL if old is None else old)


def show(ok: bool, secs: float, name: str, text: str, out: str = "") -> None:
    print(f"{'ok' if ok else 'FAIL':<5} {secs:5.1f}s  {name:<{max(len(TREE), 9)}}  {text}", flush=True)
    if out and not ok:
        print("\n".join("      | " + ln for ln in out.rstrip().splitlines()[-TAIL:]), flush=True)


def main(argv: list[str] | None = None, *, root: Path | str = ROOT, suites=SUITES, not_suites=NOT_SUITES,
         timeout_s: float = TIMEOUT_S) -> int:
    ap = argparse.ArgumentParser(prog="run_all.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", metavar="SUITE", help="run only these suites")
    ap.add_argument("--list", action="store_true", help="list the suites and the run.py files that are not suites, then exit")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(root).resolve()
    known = [s.name for s in suites]
    unknown = [n for n in args.names if n not in known]
    if unknown:
        print(f"FAILED: no suite named {', '.join(map(repr, unknown))}; the suites are {', '.join(known)}")
        return 1
    chosen = [s for s in suites if not args.names or s.name in args.names]
    if not chosen:
        print("FAILED: no suites are listed")
        return 1
    t0 = time.monotonic()
    wrong = problems(root, suites, not_suites)
    if args.list:
        for s in chosen:
            print(f"{s.name:<10} {s.run:<38} {('needs ' + ', '.join(s.needs)) if s.needs else '':<16} {s.covers}")
        for p, why in sorted(not_suites.items()):
            print(f"not a suite: {p}: {why}")
        for w in wrong:
            print(f"FAIL  {w}")
        return 1 if wrong else 0
    bad = 0
    if wrong:
        bad += 1
        show(False, time.monotonic() - t0, TREE, f"{len(wrong)} problem{'s' if len(wrong) > 1 else ''}",
             "\n".join(wrong))
    else:
        show(True, time.monotonic() - t0, TREE,
             f"{len(runners(root))} found: {len(suites)} suites, {len(not_suites)} templates")
    total = 0
    for s in chosen:
        with sigterm_exits():
            r = run_suite(s, root, timeout_s)
        if r["ok"]:
            total += r["passed"]
            show(True, r["secs"], s.name, f"RESULT: {r['passed']} passed")
        else:
            bad += 1
            show(False, r["secs"], s.name, r["why"], r["out"])
    n = len(chosen) + 1
    print(f"{len(chosen)} suite{'s' if len(chosen) > 1 else ''} in {time.monotonic() - t0:.1f}s")
    if bad:
        print(f"RESULT: {total} passed, {bad} failed ({bad} of {n} checks failed)")
        return 1
    print(f"RESULT: {total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
