"""Raw files the sample's pulls write: merged by key, written atomically.

A puller imports this module and never the database; every stamp is
`dates.utc_stamp()`, so one clock pins them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from common import dates


def write_json_atomic(path: Path, doc) -> None:
    """Write `<path>.tmp`, then rename: a crash leaves the old file, never half a new one."""
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)


def merge_rows(old: list[dict], new: list[dict], key: str) -> list[dict]:
    """Rows by natural key, the new pull winning; a key the pull no longer returns stays."""
    stamp = dates.utc_stamp()
    rows = {r[key]: r for r in old}
    rows.update({r[key]: {**r, "pulledAt": stamp} for r in new})
    return list(rows.values())
