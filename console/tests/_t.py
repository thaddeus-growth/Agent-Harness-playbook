"""The tiny test harness every tests/test_*.py ends with:

    if __name__ == "__main__": _t.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0, and `run.py` treats a file without that line as failed. A file
that defines no test, or runs under `python -O` (asserts would be skipped, so
nothing could fail), does not pass either.

Helpers: `tmpdir()` (a folder that is removed afterwards), `tmpstore()` (a
core.Store on one) and `assert_raises(code)` (for core.Refused).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import core  # noqa: E402


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
    d = tempfile.mkdtemp(prefix="console-test-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@contextmanager
def tmpstore():
    """A core.Store on a fresh empty folder."""
    with tmpdir() as d:
        yield core.Store(d)


class _Caught:
    err: core.Refused | None = None


@contextmanager
def assert_raises(code: str | None = None):
    """The block must raise core.Refused (with this `code` if given); the
    Refused is on the yielded object's `.err`. Any other exception passes
    through, so a crash is never mistaken for a refusal."""
    caught = _Caught()
    try:
        yield caught
    except core.Refused as e:
        assert e.code in core.CODES, f"unregistered code {e.code!r}"
        assert code is None or e.code == code, f"expected {code!r}, got {e.code!r}: {e}"
        caught.err = e
        return
    raise AssertionError(f"expected Refused({code!r}); nothing was raised")
