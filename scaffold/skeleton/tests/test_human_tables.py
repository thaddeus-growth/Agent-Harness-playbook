#!/usr/bin/env python3
"""Human tables survive every rebuild; a lossy rebuild is refused; an older
tool refuses a newer database; a human table is never migrated.

A thin call into the vendored kit (kit.testing.suites.human_tables); the rules live
there, so every harness holds the same ones.
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["KIT_HARNESS_ROOT"] = str(ROOT)

from _lib.schema import SPEC  # noqa: E402
from kit.testing import suites  # noqa: E402
from kit.testing.check import finish  # noqa: E402

suites.run(suites.human_tables(ROOT, SPEC))
raise SystemExit(finish())
