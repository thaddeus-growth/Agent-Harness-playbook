#!/usr/bin/env python3
"""Run build/'s own tests through the kit's gate (kit.testing.run_tests).

    python3 build/tests/run.py                   every build/tests/test_*.py
    python3 build/tests/run.py intake apply      files whose name contains "intake" or "apply"
    python3 build/tests/run.py -v intake         … echoing each file's full output

A file passes only if it exits 0 AND prints a `RESULT: N passed` line; the runner
also fails a file that leaves anything in its private TMPDIR. Exit 0 only when at
least one file ran and every file passed.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kit.testing import run_tests  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tests.main(sys.argv[1:], root=ROOT,
                                    tests_dir="build/tests"))
