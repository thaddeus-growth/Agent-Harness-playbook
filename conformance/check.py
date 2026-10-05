#!/usr/bin/env python3
"""Black-box conformance of a harness against INVARIANTS.md.

    python3 conformance/check.py conformance/adapters/seoh.toml --root /path/to/harness

A harness passes whether it vendors the kit or wrote its own tables: the checks
drive its CLI, from an adapter file that says which verbs to run. The adapter
lives here, never in the harness. Stdlib only, Python 3.11+.

Adapter (TOML):

    [harness]
    name = "seoh"
    cwd  = "{root}"                       # where the commands run
    [env]                                 # optional; values may use {tmp}, {root}
    SEOH_WORKSPACE = "{tmp}"

    [facts]                               # check C1
    setup = [ [argv…], {argv = […], stdin = "…"} ]   # once, in a fresh {tmp}
    human = [ … ]                         # steps that set value {value} AND confirm it
    agent = [ … ]                         # steps that set {value} as an agent (exit code ignored)
    read  = [argv…]                       # stdout must show the value

    [doctor]                              # check C2
    argv = [argv…]                        # run in a fresh, unconfirmed {tmp}

A step is an argv list, or a table {argv, stdin}. {tmp}, {root}, {value} are filled in.
Exit 0 when every check the adapter has passed; a missing section is a skip, said out loud.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True
TIMEOUT_S = 120
HUMAN, AGENT = "HUMAN-VALUE-1", "AGENT-VALUE-2"
FIX_MARK = re.compile(r"->|→")
WARN = re.compile(r"^\s*(?:⚠\s*)?WARNING\b")  # a warning line starts with the word; a "3 warning(s)" summary does not


def fill(s: str, **kw: str) -> str:
    for k, v in kw.items():
        s = s.replace("{" + k + "}", v)
    return s


def run_step(step, env: dict, cwd: str, **kw: str) -> subprocess.CompletedProcess:
    argv, stdin = (step["argv"], step.get("stdin")) if isinstance(step, dict) else (step, None)
    return subprocess.run([fill(a, **kw) for a in argv], cwd=cwd, env=env, input=fill(stdin, **kw) if stdin else "",
                          capture_output=True, text=True, timeout=TIMEOUT_S)


def scene(cfg: dict, root: str, tmp: str) -> tuple[dict, str]:
    import os
    env = {**os.environ, **{k: fill(v, tmp=tmp, root=root) for k, v in cfg.get("env", {}).items()}}
    return env, fill(cfg["harness"].get("cwd", "{root}"), root=root, tmp=tmp)


def c1_confirmed_is_not_overwritten(cfg: dict, root: str) -> str | None:
    """INVARIANTS I3: an agent write never changes a value a human confirmed. None = pass, else why not."""
    f = cfg["facts"]
    with tempfile.TemporaryDirectory() as tmp:
        env, cwd = scene(cfg, root, tmp)
        for step in f.get("setup", []):
            r = run_step(step, env, cwd, tmp=tmp, root=root)
            if r.returncode:
                return f"setup failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-200:]}"
        for step in f["human"]:
            r = run_step(step, env, cwd, tmp=tmp, root=root, value=HUMAN)
            if r.returncode:
                return f"the human write failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-200:]}"
        for step in f["agent"]:
            run_step(step, env, cwd, tmp=tmp, root=root, value=AGENT)
        r = run_step(f["read"], env, cwd, tmp=tmp, root=root)
        if AGENT in r.stdout:
            return f"after an agent write the confirmed value reads {AGENT!r}; it must stay {HUMAN!r}"
        if HUMAN not in r.stdout:
            return f"the confirmed value {HUMAN!r} is gone from the read verb's output"
    return None


def c2_doctor_names_a_fix(cfg: dict, root: str) -> str | None:
    """INVARIANTS I6: every doctor warning is followed by a line that names its fix. None = pass, else why not."""
    with tempfile.TemporaryDirectory() as tmp:
        env, cwd = scene(cfg, root, tmp)
        for step in cfg["doctor"].get("setup", []):
            run_step(step, env, cwd, tmp=tmp, root=root)
        r = run_step(cfg["doctor"]["argv"], env, cwd, tmp=tmp, root=root)
        lines = (r.stdout + r.stderr).splitlines()
        warns = [i for i, ln in enumerate(lines) if WARN.match(ln)]
        if not warns and r.returncode:
            return f"doctor exits {r.returncode} but prints no WARNING line"
        for i in warns:
            if not any(FIX_MARK.search(x) for x in lines[i + 1:i + 3]):
                return f"warning without a fix line: {lines[i].strip()[:100]!r}"
        if not warns:
            return "doctor printed no warning on a fresh, unconfirmed scene; the check cannot tell (adapter needs a worse scene)"
    return None


CHECKS = (("facts", "C1 an agent write never changes a confirmed value", c1_confirmed_is_not_overwritten),
          ("doctor", "C2 every doctor warning names its fix", c2_doctor_names_a_fix))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = "."
    if "--root" in args:
        i = args.index("--root")
        root = str(Path(args[i + 1]).resolve())
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    cfg = tomllib.loads(Path(args[0]).read_text(encoding="utf-8"))
    passed = failed = 0
    for section, title, fn in CHECKS:
        if section not in cfg:
            print(f"SKIP  {title}: the adapter has no [{section}]")
            continue
        try:
            why = fn(cfg, root)
        except (subprocess.TimeoutExpired, OSError, KeyError) as e:
            why = f"could not run: {type(e).__name__}: {e}"
        if why is None:
            passed += 1
            print(f"PASS  {title}")
        else:
            failed += 1
            print(f"FAIL  {title}\n      {why}")
    print(f"RESULT: {passed} passed" + (f", {failed} failed" if failed else ""))
    return 1 if failed or not passed else 0


if __name__ == "__main__":
    sys.exit(main())
