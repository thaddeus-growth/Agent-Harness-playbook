#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run every tests/test_*.py (or those whose name contains any argument) as its
own process, 4 at a time, 120 s each. A file passes only when all of these hold:

  1. it exits 0;
  2. it prints `RESULT: N passed` (or `RESULT: N passed, M failed`) with N > 0
     and M = 0. A file that crashes early, prints nothing or runs no test fails,
     and so does one that reports failures but exits 0 (it ends `main()`
     instead of `sys.exit(main())`); the last such line counts;
  3. it leaves nothing in its temp dir. The run makes one private temp dir and
     gives each file its own folder in it as TMPDIR, emptied after the file, so
     a leak is named on the file that made it, then removes it. uv's own
     `uv-*.lock` files are not a leak. Only writes that honour TMPDIR are seen.

Each file runs on an environment allowlist (ENV_KEEP, ENV_PREFIXES): no
credential, data folder or setting of the operator reaches a test by accident.
A test that starts a child hands it `_check.child_env()`, the same allowlist.

    python3 tests/run.py                  all files
    python3 tests/run.py core             files with "core" in the name
    python3 tests/run.py core serve       files matching either: an argument is never ignored

Exit 0 only when at least one file ran and every file passed.
"""

from __future__ import annotations

import concurrent.futures
import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_RE = re.compile(r"^RESULT: (\d+) passed(?:, (\d+) failed)?$", re.M)
TIMEOUT = 120
WORKERS = 4
ENV_KEEP = ("PATH", "HOME", "LANG", "TZ")
ENV_PREFIXES = ("LC_", "UV_")
UV_LOCK = "uv-*.lock"


def allowed_env(**extra: str) -> dict:
    """The allowlisted part of this process's environment, plus `extra`."""
    env = {k: v for k, v in os.environ.items()
           if k in ENV_KEEP or k.startswith(ENV_PREFIXES)}
    return {**env, **extra}


def sweep(tmp: str) -> list[str]:
    """Empty `tmp`; return the names a test left there."""
    left = []
    for name in sorted(os.listdir(tmp)):
        path = os.path.join(tmp, name)
        if not fnmatch.fnmatch(name, UV_LOCK):
            left.append(name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.unlink(path)
    return left


def run_one(path: str, tmp_root: str | None = None) -> dict:
    """{name, ok, secs, count, why, out}: one test file, one process, its own
    TMPDIR (a new folder in `tmp_root`, removed afterwards)."""
    name = os.path.basename(path)
    t0 = time.monotonic()
    tmp = tempfile.mkdtemp(prefix=name.removesuffix(".py") + "-", dir=tmp_root)
    env = allowed_env(TMPDIR=tmp, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, errors="replace", timeout=TIMEOUT,
                           cwd=os.path.dirname(HERE), env=env)
        out, code = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or b""
        out = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        code = None
    finally:
        left = sweep(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
    found = list(RESULT_RE.finditer(out))
    m = found[-1] if found else None
    count = int(m.group(1)) if m else 0
    failed = int(m.group(2) or 0) if m else 0
    if code is None:
        why = f"timed out after {TIMEOUT}s"
    elif code != 0:
        why = f"exit {code}"
    elif not m:
        why = "no RESULT line"
    elif failed:
        why = f"exit 0, but its RESULT line reports {failed} failed"
    elif count == 0:
        why = "no test ran"
    elif left:
        shown = ", ".join(left[:3]) + (", ..." if len(left) > 3 else "")
        why = (f"left {len(left)} temp entr{'y' if len(left) == 1 else 'ies'} "
               f"({shown}): remove them, or make sandboxes with _check.tmp_dir()")
    else:
        why = ""
    return {"name": name, "ok": not why, "secs": time.monotonic() - t0,
            "count": count, "why": why, "out": out,
            "line": m.group(0) if m else ""}


def main(argv: list[str], tests: str = HERE) -> int:
    patterns = argv[1:] or [""]
    files = sorted(f for f in glob.glob(os.path.join(tests, "test_*.py"))
                   if any(p in os.path.basename(f) for p in patterns))
    if not files:
        print(f"FAILED: no tests/test_*.py matches {', '.join(map(repr, patterns))}")
        return 1
    failed = total = 0
    root = tempfile.mkdtemp(prefix="tests-")
    try:
        with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
            for r in pool.map(lambda f: run_one(f, root), files):
                if r["ok"]:
                    total += r["count"]
                    print(f"ok    {r['secs']:5.1f}s  {r['name']:<24} {r['line']}")
                else:
                    failed += 1
                    print(f"FAIL  {r['secs']:5.1f}s  {r['name']:<24} {r['why']}")
                    tail = r["out"].rstrip().splitlines()[-40:]
                    print("\n".join("      | " + ln for ln in tail))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if failed:
        print(f"FAILED: {failed} of {len(files)} files")
        return 1
    print(f"RESULT: {total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
