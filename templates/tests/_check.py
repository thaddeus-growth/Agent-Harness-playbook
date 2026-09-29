"""check(), finish(), tmp_dir() and child_env(): what every tests/test_*.py shares.

A test file runs as a script, so tests/ is on sys.path and it imports
`from _check import check, finish`. It ends `sys.exit(finish())`, or
`return finish()` from a `main()` run as `sys.exit(main())`: `finish()` prints
the `RESULT: N passed, M failed` line run.py keys on and returns the exit code.
A file that forgets the `sys.exit` still fails, on its own RESULT line.

`tmp_dir()` makes a sandbox (a database, a data folder, a fake HOME) that goes
when the file exits, whatever happened in between: run.py fails a file that
leaves anything in its temp dir. `child_env()` is the environment for a child
process a test starts: run.py's allowlist plus the test's own values, never the
operator's credentials, data folder or HOME. It keeps TMPDIR, so the child's
temp files land where the leak check sees them, and gives HOME an empty
sandbox of this file's (a child with no HOME at all would find the real one).
"""

from __future__ import annotations

import atexit
import shutil
import tempfile

PASSES = 0
FAILS: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    """Record one check; print it."""
    global PASSES
    if ok:
        PASSES += 1
        print(f"  PASS  {label}")
    else:
        FAILS.append((label, str(detail)))
        print(f"  FAIL  {label} -- {detail}")


def finish() -> int:
    """Print the RESULT line (and every failure again); 1 if anything failed."""
    print(f"\nRESULT: {PASSES} passed, {len(FAILS)} failed")
    for label, detail in FAILS:
        print(f"  FAIL  {label} -- {detail}")
    return 1 if FAILS else 0


def tmp_dir(prefix: str | None = None) -> str:
    """`tempfile.mkdtemp(prefix=...)`, removed when the test file exits."""
    path = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


_HOME: list[str] = []


def child_env(**extra: str) -> dict:
    """run.py's environment allowlist, this process's TMPDIR, a sandbox HOME
    (one per test file), no bytecode written into the checkout, and `extra`
    (which may override any of them)."""
    from run import allowed_env    # tests/run.py: the allowlist lives there once
    if not _HOME:
        _HOME.append(tmp_dir(prefix="home-"))
    return allowed_env(**{"TMPDIR": tempfile.gettempdir(), "HOME": _HOME[0],
                          "PYTHONDONTWRITEBYTECODE": "1", **extra})

