"""The sample's database guard: the one module that opens the database file.

A puller never imports it: pull writes raw, ingest reads raw into the database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    """The database at `path`, with foreign keys on."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    return db
