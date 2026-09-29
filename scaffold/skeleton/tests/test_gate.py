#!/usr/bin/env python3
"""Confirm and approve need a human; a code is bound to what was shown; a
replayed or swapped code is refused; no bypass flag; the scope enters only
through init.

A thin call into the vendored kit (kit.testing.suites.gate); the rules live
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

suites.run(suites.gate(ROOT))
raise SystemExit(finish())
