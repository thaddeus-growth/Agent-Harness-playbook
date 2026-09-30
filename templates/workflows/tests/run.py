#!/usr/bin/env python3
"""Run the workflow templates' own tests through the kit's gate (kit.testing.run_tests).

    python3 templates/workflows/tests/run.py                  every tests/test_*.py of this folder
    python3 templates/workflows/tests/run.py flow refcheck    files whose name contains either
    python3 templates/workflows/tests/run.py -v scripts       … echoing each file's full output

A file passes only if it exits 0 AND prints a `RESULT: N passed` line; the gate
also fails a file that leaves anything in its private TMPDIR. Exit 0 only when at
least one file ran and every file passed. The tests need `node` (sim.js).

This runner works inside the playbook: it imports the playbook's `kit/` from the
root of the checkout, three folders up. A harness copies the scripts and
`refcheck.py` (see ../README.md), not these tests.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
MODULE = Path(__file__).resolve().parents[1]      # templates/workflows
PLAYBOOK = Path(__file__).resolve().parents[3]    # the playbook's root, where kit/ is
sys.path.insert(0, str(PLAYBOOK))

try:
    from kit.testing import run_tests
except ImportError as e:
    raise SystemExit(f"FAILED: this runner needs the playbook's kit/testing/run_tests.py, "
                     f"expected at {PLAYBOOK / 'kit'} (run it inside the playbook checkout): {e}")

if __name__ == "__main__":
    raise SystemExit(run_tests.main(sys.argv[1:], root=MODULE, tests_dir="tests"))
