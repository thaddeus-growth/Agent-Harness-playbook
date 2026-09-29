"""How one harness verb runs another: in the child's own PEP 723 environment.

Every script declares its own inline dependencies. A child started with this
interpreter (`sys.executable`) runs in the parent's environment and fails on a
dependency only the child declares, and only where that dependency is not
already installed, which is usually the host and not the laptop. `uv run
<script>` builds the environment the child's own header names; without uv on
PATH, this interpreter is the fallback, as a direct run would be.

`run_json` is how a verb takes a snapshot of another verb's `--json` (a queue
reading a report, a sender reading alerts). The child prints exactly one JSON
document on stdout, a failure included (`messages.failure_doc`), so that is
where its reason, its fix commands and its message code are. stderr holds only
uv's notices or a crash's traceback, and is read only when stdout has no
document.

Stdlib only, no other core import: any layer may use it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def script_cmd(target: Path, args: list[str]) -> list[str]:
    """The argv that runs the script at `target` with `args`."""
    uv = shutil.which("uv")
    return [uv, "run", str(target), *args] if uv else [sys.executable, str(target), *args]


class ChildFailed(RuntimeError):
    """A child verb failed. str() = its failure document's `error`; `next` = its
    fix commands; `code` = its {code, params} (messages.relayed() keeps them).
    A child that died without a document (a crash): stderr's last line, [], None."""

    def __init__(self, error: str, next_steps: list[str], code: dict | None):
        super().__init__(error)
        self.next, self.code = list(next_steps), code


def run_json(target: Path, args: list[str], *, any_exit: bool = False) -> dict | list:
    """The one JSON document the script at `target` prints for `args` (its
    `--json` among them), run as script_cmd runs it. A non-zero exit raises
    ChildFailed with the child's own failure document; so does an exit 0 with
    no document. `any_exit`: a document without `error` is the child's output
    whatever its exit (an exit code that flags a finding the document carries)."""
    r = subprocess.run(script_cmd(target, args), capture_output=True, text=True, env=os.environ)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    failed = isinstance(doc, dict) and "error" in doc
    if doc is not None and (r.returncode == 0 or (any_exit and not failed)):
        return doc
    if failed:
        c = {"code": doc["code"], "params": doc.get("params") or {}} if doc.get("code") else None
        raise ChildFailed(str(doc["error"]), [str(x) for x in doc.get("next") or []], c)
    last = (r.stderr.strip().splitlines()
            or [f"{Path(target).name} exited {r.returncode} with no failure document"])[-1]
    raise ChildFailed(last, [], None)
