"""{{name}}'s database: its cache tables beside the kit's human tables
(kit.db.with_human: facts, decisions, the queue and its effects).

Each data source adds its cache tables in its own fragment,
scripts/_lib/schema_<source>.py, merged into CACHE here by the integrator
only (BUILD.md, "Fan-out rules"). Bump VERSION when a table's shape
changes; the human tables are never migrated (tests/test_human_tables.py).
"""

from kit import db

VERSION = 1
CACHE: dict[str, dict] = {}

SPEC = db.with_human(CACHE, version=VERSION)
