#!/usr/bin/env python3
"""`git archive` leaves out the internal files, ships the kit and the
console, and no shipped file holds a build fill marker once the docs are
signed (the B8 row of ssot/stages.agent.tsv) or on a release tag.

A thin call into the vendored kit (kit.testing.suites.release); the rules live
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

suites.run(suites.release(ROOT))
raise SystemExit(finish())
