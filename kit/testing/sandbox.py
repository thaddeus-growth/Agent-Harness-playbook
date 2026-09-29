"""The environment a harness CLI runs under in a test.

`sandbox_env(data_dir)` is os.environ minus every inherited `<P>_*` var
(an operator's own exports, e.g. `<P>_AUTH_ENV_PATHS` or a live
`<P>_DATA_DIR`, must never decide a test) and minus KIT_TTY, plus
`<P>_DATA_DIR=<data_dir>`, `<P>_AUTH_ENV_PATHS=none` (no HOME or data-dir
.env file: process env only), `PYTHONUTF8=1`, and `KIT_HARNESS_ROOT` =
the bound harness, so a child that imports the kit binds the same one.
`extra` goes on top (a value of None removes that var).

`run(argv, env)` runs a command with no stdin (a gate must never read an
answer from a pipe) and returns (exit code, stdout, stderr).

Test: kit/tests/test_check.py (sandbox cases).
"""

from __future__ import annotations

import os
import subprocess

from kit.config import ROOT_ENV, config


def sandbox_env(data_dir: str | os.PathLike, **extra: str | None
                ) -> dict[str, str]:
    cfg = config()
    p = cfg.env_prefix + "_"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(p) and k != "KIT_TTY"}
    env.update({cfg.env("DATA_DIR"): os.fspath(data_dir),
                cfg.env("AUTH_ENV_PATHS"): "none",
                "PYTHONUTF8": "1",
                ROOT_ENV: str(cfg.root)})
    for k, v in extra.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def run(argv: list[str], env: dict[str, str], *, stdin: str | None = None,
        timeout: float = 300, cwd: str | os.PathLike | None = None
        ) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of `argv` under `env`."""
    r = subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                       text=True, timeout=timeout,
                       input=stdin if stdin is not None else None,
                       stdin=None if stdin is not None else subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr
