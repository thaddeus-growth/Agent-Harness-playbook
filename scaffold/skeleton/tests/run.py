#!/usr/bin/env python3
"""Run {{name}}'s tests through the kit's gate (kit.testing.run_tests):
each file in its own process and temp folder; a file passes only when it
exits 0, prints `RESULT: N passed` with N > 0 and leaves nothing behind.

    python3 tests/run.py            every tests/test_*.py
    python3 tests/run.py gate       files whose name contains "gate"
    {{cli}} test                    the same, through the CLI
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["KIT_HARNESS_ROOT"] = str(ROOT)

from kit.testing import run_tests  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tests.main(sys.argv[1:], root=ROOT,
                                    tests_dir="tests"))
