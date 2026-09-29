"""The tiny test harness every tests/test_*.py ends with:

    if __name__ == "__main__": _t.main(globals())

`main` runs each `test_*` function the file defines, in file order. A failure
prints `FAIL <name>` and its traceback; the run then exits 1 and prints
`FAILED: k of N`. Only a run where every test passed prints `RESULT: N passed`
and exits 0, and `run.py` treats a file without that line as failed. A file
that defines no test, or runs under `python -O` (asserts would be skipped, so
nothing could fail), does not pass either.

Helpers: `tmpdir()` (a folder that is removed afterwards) and
`refused(*words)` (the block must stop with a SystemExit naming every word).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path


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
    """A fresh empty folder (resolved: no symlinked prefix), removed on exit."""
    d = Path(tempfile.mkdtemp(prefix="golden-test-")).resolve()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _Caught:
    message = ""


@contextmanager
def refused(*words: str):
    """The block must raise SystemExit whose message holds every word; the
    message is on the yielded object's `.message`. Any other exception
    passes through, so a crash is never mistaken for a refusal."""
    caught = _Caught()
    try:
        yield caught
    except SystemExit as e:
        caught.message = str(e.code)
        for w in words:
            assert w in caught.message, f"{w!r} not in {caught.message!r}"
        return
    raise AssertionError(f"expected a refusal naming {words}; nothing was raised")
