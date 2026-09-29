"""The test gate: every test file must prove it ran and passed.

Runs each `<tests_dir>/<pattern>` file (sorted; an argument keeps the
files whose name contains it, several arguments = any of them) as its own
process, `workers` at a time, `timeout_s` each, with cwd = `root`, and
fails a file that

  1. exits non-zero, or times out (its whole process group is killed);
  2. prints no `RESULT: N passed` line (a file that crashed early, or
     whose main was never called, looks like a pass otherwise);
  3. reports `, M failed` with M > 0 on that line, even with exit 0 (a
     file that forgot `raise SystemExit(finish())`);
  4. reports `0 passed` (it ran no check);
  5. leaves anything in the private TMPDIR it was given (uv's own
     `uv-*.lock` files excepted): a sandbox left behind piles up run after
     run. Each file gets its own TMPDIR, so a leak is named on the file
     that made it; the dirs are swept and removed at the end either way.

When several RESULT lines appear (a test that prints a nested run's
output), the last one counts. Both conventions the playbook uses parse
here: `RESULT: N passed` (console/tests/_t.py, kit check.finish() when
all passed) and `RESULT: N passed, M failed` (the reference harness).

Prints one line per file (the tail of a failing file's output below it)
and one summary line: `RESULT: T passed` when every file passed (T = the
sum of their passed counts), else `RESULT: T passed, K failed` (K
files). Exit 0 only when at least one file ran and every file passed.

Test: kit/tests/test_run_tests.py.
"""

from __future__ import annotations

import concurrent.futures
import fnmatch
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESULT_LINE = re.compile(
    r"^\s*RESULT:\s+(\d+)\s+passed(?:\s*,\s*(\d+)\s+failed)?\b", re.MULTILINE)
IGNORE_LEAKS = ("uv-*.lock",)
TAIL = 40


def parse_result(out: str) -> tuple[int | None, int]:
    """(passed, failed) of the last RESULT line; passed None when none."""
    ms = list(RESULT_LINE.finditer(out))
    if not ms:
        return None, 0
    m = ms[-1]
    return int(m.group(1)), int(m.group(2) or 0)


def discover(root: Path, tests_dir: str, pattern: str,
             filters: list[str]) -> list[Path]:
    files = sorted((Path(root) / tests_dir).glob(pattern))
    if filters:
        files = [f for f in files if any(s in f.name for s in filters)]
    return files


def sweep(tmp: Path, ignore: tuple[str, ...] = IGNORE_LEAKS) -> list[str]:
    """Empty `tmp`; return the names a test left there (ignored ones not
    counted, but swept too)."""
    left = []
    for entry in sorted(tmp.iterdir()):
        if not any(fnmatch.fnmatch(entry.name, g) for g in ignore):
            left.append(entry.name)
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    return left


def _exec(file: Path, root: Path, tmp: Path, timeout_s: float
          ) -> tuple[int | None, str]:
    """(exit code or None on timeout, stdout+stderr)."""
    env = {**os.environ, "TMPDIR": str(tmp), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.Popen([sys.executable, str(file)], cwd=root, env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout_s)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = p.communicate()
        return None, out or ""


def verdict(rc: int | None, passed: int | None, failed: int,
            left: list[str], timeout_s: float) -> str:
    """Why a file fails the gate ("" = it passes)."""
    if rc is None:
        return f"timed out after {timeout_s:g}s"
    if rc != 0:
        return f"exit {rc}"
    if passed is None:
        return "missing 'RESULT: N passed' gate line"
    if failed > 0:
        return f"RESULT line reports {failed} failed"
    if passed == 0:
        return "RESULT line reports 0 passed (ran no tests)"
    if left:
        shown = ", ".join(left[:3]) + (", …" if len(left) > 3 else "")
        return (f"left {len(left)} temp entr"
                f"{'y' if len(left) == 1 else 'ies'} ({shown}); clean them "
                f"up, e.g. a sandbox dir via kit.testing.check.tmp_dir()")
    return ""


def run_one(file: Path, *, root: Path, tmp: Path, timeout_s: float) -> dict:
    """{file, ok, why, passed, secs, out} of one file run with TMPDIR=tmp."""
    t0 = time.monotonic()
    rc, out = _exec(file, root, tmp, timeout_s)
    left = sweep(tmp)
    passed, failed = parse_result(out)
    why = verdict(rc, passed, failed, left, timeout_s)
    return {"file": file, "ok": not why, "why": why, "passed": passed or 0,
            "secs": time.monotonic() - t0, "out": out}


def main(argv: list[str] | None = None, *, root: Path | str,
         tests_dir: str = "tests", pattern: str = "test_*.py",
         workers: int = 4, timeout_s: float = 300) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = any(a in ("-v", "--verbose") for a in argv)
    filters = [a for a in argv if a not in ("-v", "--verbose")]
    root = Path(root).resolve()
    files = discover(root, tests_dir, pattern, filters)
    if not files:
        print(f"FAILED: no {tests_dir}/{pattern} matches "
              f"{', '.join(map(repr, filters)) or '(no filter)'}")
        return 1
    run_tmp = Path(tempfile.mkdtemp(prefix="kit-test-"))
    total, bad = 0, 0
    try:
        tmps = []
        for i, f in enumerate(files):
            t = run_tmp / f"{i:03d}"
            t.mkdir()
            tmps.append(t)
        with concurrent.futures.ThreadPoolExecutor(max(1, workers)) as pool:
            results = pool.map(
                lambda ft: run_one(ft[0], root=root, tmp=ft[1],
                                   timeout_s=timeout_s), zip(files, tmps))
            for r in results:
                try:
                    rel = r["file"].relative_to(root)
                except ValueError:
                    rel = r["file"]
                if r["ok"]:
                    total += r["passed"]
                    print(f"ok    {r['secs']:5.1f}s  {rel}  "
                          f"RESULT: {r['passed']} passed")
                else:
                    bad += 1
                    print(f"FAIL  {r['secs']:5.1f}s  {rel}: {r['why']}")
                if verbose or not r["ok"]:
                    lines = r["out"].rstrip().splitlines()
                    tail = lines if verbose else lines[-TAIL:]
                    print("\n".join("      | " + ln for ln in tail))
    finally:
        shutil.rmtree(run_tmp, ignore_errors=True)
    if bad:
        print(f"RESULT: {total} passed, {bad} failed "
              f"({bad} of {len(files)} files failed the gate)")
        return 1
    print(f"RESULT: {total} passed")
    return 0
