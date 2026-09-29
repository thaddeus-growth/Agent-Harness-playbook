#!/usr/bin/env python3
"""Run the kit's own tests through the kit's gate (kit.testing.run_tests).

    python3 kit/tests/run.py              every kit/tests/test_*.py
    python3 kit/tests/run.py raw env      files whose name contains "raw" or "env"
    python3 kit/tests/run.py -v raw       … echoing each file's full output
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kit.testing import run_tests  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_tests.main(sys.argv[1:], root=ROOT,
                                    tests_dir="kit/tests"))
