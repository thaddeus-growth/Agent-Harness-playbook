#!/usr/bin/env python3
"""The index lists every ssot file; owner files hold no engineering words;
siblings share ids; ids are unique and never reused; every owner row names
its `decided` answer; no stage is signed before its `after`.

A thin call into the vendored kit (kit.testing.suites.ssot); the rules live
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

suites.run(suites.ssot(ROOT))
raise SystemExit(finish())
