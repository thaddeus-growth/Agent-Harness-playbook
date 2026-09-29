#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run every hosts/zylos/tests/test_*.py (or those whose name contains any
argument) as its own process, 4 at a time, 120 s each. Day-one rule of the playbook: a test file
fails unless it exits 0 AND prints a `RESULT: N passed` line, so a file that
crashes early, prints nothing or runs no test is a failure, not a pass.

    python3 hosts/zylos/tests/run.py                   all files
    python3 hosts/zylos/tests/run.py install           files with "install" in the name
    python3 hosts/zylos/tests/run.py bin manifest      files matching either: an argument is never ignored

Exit 0 only when at least one file ran and every file passed.
"""

from __future__ import annotations

import concurrent.futures
import glob
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_RE = re.compile(r"^RESULT: (\d+) passed$", re.M)
TIMEOUT = 120
WORKERS = 4


def run_one(path: str) -> dict:
    """{name, ok, secs, count, why, out}: one test file, one process."""
    name = os.path.basename(path)
    t0 = time.monotonic()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, errors="replace", timeout=TIMEOUT,
                           cwd=os.path.dirname(HERE), env=env)
        out, code = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or b""
        out = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        code = None
    m = RESULT_RE.search(out)
    count = int(m.group(1)) if m else 0
    if code is None:
        why = f"timed out after {TIMEOUT}s"
    elif code != 0:
        why = f"exit {code}"
    elif not m:
        why = "no RESULT line"
    elif count == 0:
        why = "no test ran"
    else:
        why = ""
    return {"name": name, "ok": not why, "secs": time.monotonic() - t0,
            "count": count, "why": why, "out": out}


def main(argv: list[str]) -> int:
    patterns = argv[1:] or [""]
    files = sorted(f for f in glob.glob(os.path.join(HERE, "test_*.py"))
                   if any(p in os.path.basename(f) for p in patterns))
    if not files:
        print(f"FAILED: no hosts/zylos/tests/test_*.py matches {', '.join(map(repr, patterns))}")
        return 1
    failed = total = 0
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for r in pool.map(run_one, files):
            if r["ok"]:
                total += r["count"]
                line = RESULT_RE.search(r["out"]).group(0)
                print(f"ok    {r['secs']:5.1f}s  {r['name']:<24} {line}")
            else:
                failed += 1
                print(f"FAIL  {r['secs']:5.1f}s  {r['name']:<24} {r['why']}")
                tail = r["out"].rstrip().splitlines()[-40:]
                print("\n".join("      | " + ln for ln in tail))
    if failed:
        print(f"FAILED: {failed} of {len(files)} files")
        return 1
    print(f"RESULT: {total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
