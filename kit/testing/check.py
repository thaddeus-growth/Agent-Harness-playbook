"""check(), finish() and the helpers every test_*.py shares.

A test file runs as a script (`python3 kit/tests/test_x.py`), calls
`check(label, ok, detail)` per assertion and ends with
`raise SystemExit(finish())`. `finish()` prints the gate line the runner
keys on: `RESULT: N passed` when nothing failed, `RESULT: N passed, M
failed` otherwise (both conventions the playbook uses parse the same
way), and returns 1 when M > 0 or N == 0: a file that ran no check does
not pass.

The function-per-test style (the console's `_t.main(globals())`) runs
over the same gate: `raise SystemExit(run_functions(globals()))` runs
each `test_*` function as one check (it passes when it returns without
raising), refusing `python -O`, which would skip every assert.

`tmp_dir()` makes a sandbox under the run's TMPDIR that no with/finally
has to remove: it goes when the file exits, because the runner fails a
file that leaves anything in its TMPDIR.

Test: kit/tests/test_check.py (and every kit test uses it).
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback
from typing import Any, Callable

PASSES = 0
FAILS: list[tuple[str, str]] = []


def check(label: str, ok: object, detail: object = "") -> bool:
    """Record one assertion; prints PASS/FAIL. Returns bool(ok)."""
    global PASSES
    if ok:
        PASSES += 1
        print(f"  PASS  {label}")
    else:
        FAILS.append((label, str(detail)))
        print(f"  FAIL  {label} -- {detail}")
    return bool(ok)


def finish() -> int:
    """Print the gate line; 0 only when at least one check ran and none
    failed."""
    for label, detail in FAILS:
        print(f"  FAIL  {label} -- {detail}")
    if FAILS:
        print(f"\nRESULT: {PASSES} passed, {len(FAILS)} failed")
    else:
        print(f"\nRESULT: {PASSES} passed")
    sys.stdout.flush()
    return 1 if FAILS or PASSES == 0 else 0


def run_functions(g: dict) -> int:
    """Run every `test_*` function defined in module namespace `g`, in
    file order, each as one check; then finish()."""
    if not __debug__:
        print("FAILED: python -O skips every assert; run without -O")
        return 1
    tests = [(n, f) for n, f in g.items()
             if n.startswith("test_") and callable(f)
             and getattr(f, "__module__", None) == g.get("__name__")]
    for name, fn in tests:
        try:
            fn()
            check(name, True)
        except (Exception, SystemExit):
            check(name, False, traceback.format_exc().rstrip())
    return finish()


def tmp_dir(prefix: str | None = None) -> str:
    """tempfile.mkdtemp(prefix=…) under the run's TMPDIR, removed when the
    test file exits."""
    path = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, path, True)
    return path


def raises(fn: Callable[[], Any], exc: type[BaseException] = Exception
           ) -> BaseException | None:
    """The `exc` that fn() raised, None if it returned. Anything else that
    it raises propagates (a crash is never mistaken for a refusal)."""
    try:
        fn()
    except exc as e:
        return e
    return None


@contextlib.contextmanager
def _environ(env: dict[str, str] | None):
    if env is None:
        yield
        return
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class _Stdin(io.StringIO):
    def isatty(self) -> bool:
        return False


def capture(main: Callable[[list[str]], Any], argv: list[str], *,
            env: dict[str, str] | None = None, stdin: str | None = None,
            tty_answers: list[str] | None = None) -> tuple[int, str, str]:
    """Run `main(argv)` in this process: (exit code, stdout, stderr).

    `env` replaces os.environ for the call (restored after); `stdin`
    feeds sys.stdin (never a TTY; default: empty); `tty_answers` are the
    lines a human would type at the terminal: written to a temp file whose
    path is exported as KIT_TTY for the call (the human gate's test seam),
    one answer per line. A returned None is exit 0; SystemExit is caught
    and its code returned; any other exception propagates."""
    out, err = io.StringIO(), io.StringIO()
    answers = None
    if tty_answers is not None:
        fd, answers = tempfile.mkstemp(prefix="tty-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(a + "\n" for a in tty_answers))
    saved_stdin = sys.stdin
    sys.stdin = _Stdin(stdin or "")
    try:
        with _environ(env):
            old_tty = os.environ.get("KIT_TTY")
            if answers is not None:
                os.environ["KIT_TTY"] = answers
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    try:
                        rc = main(argv)
                    except SystemExit as e:
                        rc = e.code
                        if isinstance(rc, str):
                            print(rc, file=sys.stderr)
                            rc = 1
            finally:
                if answers is not None:
                    if old_tty is None:
                        os.environ.pop("KIT_TTY", None)
                    else:
                        os.environ["KIT_TTY"] = old_tty
    finally:
        sys.stdin = saved_stdin
        if answers is not None:
            os.remove(answers)
    return (0 if rc is None else int(rc)), out.getvalue(), err.getvalue()


def one_doc(out: str) -> Any:
    """The one JSON document `out` holds, None when it holds none or more
    than one (the one-document contract)."""
    try:
        return json.loads(out)
    except ValueError:
        return None


def clean_env(prefix: str | None = None, **set_: str) -> dict[str, str]:
    """os.environ without any `<prefix>_*` var (default prefix: the bound
    harness's) nor KIT_TTY, then `set_` on top."""
    if prefix is None:
        from kit.config import config
        prefix = config().env_prefix
    p = prefix.rstrip("_") + "_"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(p) and k != "KIT_TTY"}
    env.update(set_)
    return env
