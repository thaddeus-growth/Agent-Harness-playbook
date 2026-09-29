"""How one harness script runs another, and reads its one JSON document.

`script_cmd` is the argv that runs a script: this interpreter
(`sys.executable`), or `uv run <script>` when KIT_RUNNER=uv and uv is on
PATH (a harness whose scripts declare their own PEP 723 dependencies);
uv missing = this interpreter, as a direct run would be.

`run_json` is how a verb snapshots another verb's `--json` (a queue
snapshotting a compute, a story check running a read verb): a failed
child printed its one failure document on stdout (kit.contract.fail), so
that is where its reason, fix commands and message code are; stderr
holds only notices or a crash's traceback. A failure is `ChildFailed`, a
HarnessError that keeps the child's own code (messages.relayed), so the
parent's fail() reports the child's reason, not a generic one.

Test: kit/tests/test_runner.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kit.contract import HarnessError

RUNNER_ENV = "KIT_RUNNER"


def script_cmd(target: Path | str, args: list[str]) -> list[str]:
    """The argv that runs the script at `target` with `args`."""
    uv = shutil.which("uv") if os.environ.get(RUNNER_ENV) == "uv" else None
    return ([uv, "run", str(target), *args] if uv
            else [sys.executable, str(target), *args])


class ChildFailed(HarnessError):
    """A child script failed. str() = its failure document's `error`;
    `next` = its fix commands; `code` = its {code, params}. A child that
    died without a document (a crash): stderr's last line, [] and None."""

    def __init__(self, error: str, next: list[str], code: dict | None):
        try:
            from kit.messages import relayed
            message = relayed(code, error)
        except Exception:        # no bound harness: keep the plain text
            message = error
        super().__init__(message, next)
        self.code = code


def run_json(target: Path | str, args: list[str], *, any_exit: bool = False,
             timeout: float | None = None) -> dict | list:
    """The one JSON document the script at `target` prints for `args` (its
    `--json` among them), run as script_cmd runs it, with this process's
    env. A non-zero exit raises ChildFailed with the child's own failure
    document. `any_exit`: a document without `error` is the child's output
    whatever its exit (an exit code that flags a finding the document
    carries); no JSON document raises ChildFailed even on exit 0."""
    try:
        r = subprocess.run(script_cmd(target, args), capture_output=True,
                           text=True, env=os.environ, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ChildFailed(f"{Path(target).name} timed out after {timeout}s",
                          [], None) from None
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    if r.returncode == 0 and not any_exit and doc is not None:
        return doc
    if doc is not None and any_exit and (r.returncode == 0 or not (
            isinstance(doc, dict) and "error" in doc)):
        return doc
    if isinstance(doc, dict) and "error" in doc:
        code = ({"code": doc["code"], "params": doc.get("params") or {}}
                if doc.get("code") else None)
        raise ChildFailed(str(doc["error"]), list(doc.get("next") or []),
                          code)
    last = (r.stderr.strip().splitlines()
            or [f"{Path(target).name} printed no JSON document "
                f"(exit {r.returncode})"])[-1]
    raise ChildFailed(last, [], None)
