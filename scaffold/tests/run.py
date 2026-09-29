#!/usr/bin/env python3
"""Run the scaffolder's tests through the kit's gate (kit.testing.run_tests).

    python3 scaffold/tests/run.py            every scaffold/tests/test_*.py
    python3 scaffold/tests/run.py harness    files whose name contains it
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kit.testing import run_tests  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tests.main(sys.argv[1:], root=ROOT,
                                    tests_dir="scaffold/tests",
                                    timeout_s=900))
