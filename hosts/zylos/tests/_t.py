"""The tiny test harness every tests/test_*.py ends with (the style of the
playbook's console/tests/_t.py):

    if __name__ == "__main__": _t.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0, and `run.py` treats a file without that line as failed. A file
that defines no test, or runs under `python -O` (asserts would be skipped, so
nothing could fail), does not pass either. Without `node` on PATH every file
fails: the adapter is Node, so there is nothing to test without it.
"""

from __future__ import annotations

import shutil
import sys
import traceback


def main(g: dict) -> None:
    """Run the `test_*` functions defined in the module namespace `g`."""
    if not __debug__:
        print("FAILED: python -O skips every assert; run without -O")
        sys.exit(1)
    if not shutil.which("node"):
        print("FAILED: node is not on PATH (the adapter's hooks are Node)")
        sys.exit(1)
    tests = [(n, f) for n, f in g.items()
             if n.startswith("test_") and callable(f)
             and getattr(f, "__module__", None) == g.get("__name__")]
    if not tests:
        print("FAILED: no test_* function found")
        sys.exit(1)
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except (Exception, SystemExit):
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
    if failed:
        print(f"FAILED: {failed} of {len(tests)}")
        sys.exit(1)
    print(f"RESULT: {len(tests)} passed")
