"""The tiny test harness every tests/test_*.py ends with (the same rules as
console/tests/_t.py):

    if __name__ == "__main__": _t.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0, and `run.py` treats a file without that line as failed. A file
that defines no test, or runs under `python -O` (asserts would be skipped), does
not pass either.

Importing this puts the folder that holds core/ on sys.path, so a test says
`from core import gate`. Helpers: `tmpdir()` (removed afterwards) and
`refused(code)` (the block must raise gate.Refused carrying that message code).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))              # the folder that holds core/
sys.path.insert(0, ROOT)
from core import gate  # noqa: E402


def main(g: dict) -> None:
    """Run the `test_*` functions defined in the module namespace `g`."""
    if not __debug__:
        print("FAILED: python -O skips every assert; run without -O")
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


@contextmanager
def tmpdir():
    """A fresh empty folder, removed on exit (even after a failure)."""
    d = tempfile.mkdtemp(prefix="core-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _Caught:
    err: gate.Refused | None = None


@contextmanager
def refused(code: str | None = None):
    """The block must raise gate.Refused whose message carries `code` (if given);
    the exception is on the yielded object's `.err`. Anything else passes
    through, so a crash is never mistaken for a refusal."""
    caught = _Caught()
    try:
        yield caught
    except gate.Refused as e:
        got = getattr(e.args[0] if e.args else None, "code", None)
        assert code is None or got == code, f"expected {code!r}, got {got!r}: {e}"
        caught.err = e
        return
    raise AssertionError(f"expected a refusal ({code!r}); nothing was raised")
