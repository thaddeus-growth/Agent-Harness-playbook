#!/usr/bin/env python3
"""Every read verb under --json on fixtures prints one document with `meta`;
every message is coded; a failure is one {error, next, code, params}; a
read verb never creates the database.

A thin call into the vendored kit (kit.testing.suites.json_contract); the rules live
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

suites.run(suites.json_contract(ROOT))
raise SystemExit(finish())
