#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run the test kit's own tests (selftest/test_*.py) with the kit's runner,
templates/tests/run.py, under its rules. Not copied into a harness.

    python3 templates/tests/selftest/run.py            all files
    python3 templates/tests/selftest/run.py installed  files with "installed" in the name
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kit_run", os.path.join(HERE, "..", "run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

if __name__ == "__main__":
    sys.exit(run.main(sys.argv, tests=HERE))
