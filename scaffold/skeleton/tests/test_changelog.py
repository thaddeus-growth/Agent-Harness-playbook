#!/usr/bin/env python3
"""CHANGELOG.md starts with Unreleased, its releases are in order, and SKILL.md's
version is the newest of them.

A thin call into the vendored kit (kit.testing.suites.changelog); the rules live
there, so every harness holds the same ones. That a merge request adds its line is
the CI job `changelog`.
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

suites.run(suites.changelog(ROOT))
raise SystemExit(finish())
