#!/usr/bin/env python3
"""The runner fails a file that exits non-zero, prints no `RESULT: N passed`
line, ran no test, or left files in its temp folder; tests/run.py is that
runner.

A thin call into the vendored kit (kit.testing.suites.run_tests); the rules live
there, so every harness holds the same ones.
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["KIT_HARNESS_ROOT"] = str(ROOT)

from kit.testing import suites  # noqa: E402
from kit.testing.check import finish  # noqa: E402

suites.run(suites.run_tests(ROOT))
raise SystemExit(finish())
