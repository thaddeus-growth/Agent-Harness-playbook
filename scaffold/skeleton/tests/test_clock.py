#!/usr/bin/env python3
"""One clock: no code reads the calendar except through scripts/kit/dates.py
(`dates.now()`), so one patch pins every read, a golden diff's included.

A thin call into the vendored kit (kit.testing.suites.clock); the rules live
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

suites.run(suites.clock(ROOT))
raise SystemExit(finish())
