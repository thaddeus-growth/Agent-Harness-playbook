"""How one sample verb runs another: the child prints one JSON document.

Stdlib only, imports nothing of the harness: any layer may use it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class ChildFailed(RuntimeError):
    """A child verb failed; str() is its reason."""


def run_json(target: Path, args: list[str]) -> dict | list:
    """The one JSON document the script at `target` prints for `args`."""
    r = subprocess.run([sys.executable, str(target), *args], capture_output=True, text=True)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        raise ChildFailed((r.stderr.strip().splitlines() or ["no document"])[-1]) from None
    if r.returncode != 0:
        raise ChildFailed(str(doc.get("error") if isinstance(doc, dict) else doc))
    return doc
