#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The layering and boundary invariants, checked on the code (archtest.py
holds the mechanics). Edit the two tables to your layout. A rule whose layer
matches no module fails, so a rename cannot switch a rule off; a text rule's
pattern is tried on its samples before it reads a file.

The example layout is the playbook's: runtime code in src/ (on sys.path),
shared helpers in lib/, the platform client in api/ with api/writer.py the one
code that writes to the platform, verb scripts pull_*, ingest_*, compute_* and
the write verbs; a client console in webconsole/, the host glue in adapter/.
"""

import re
import sys
from pathlib import Path

import archtest
from _check import check, finish

ROOT = Path(__file__).resolve().parent.parent
TOOL = "harness"                                      # the CLI a consumer runs
WRITE_VERBS = ("facts", "decisions", "queue_actions", "execute")
VERBS = ("pull_*", "ingest_*", "compute_*", *WRITE_VERBS)
API = ("api", "api.*")
R = archtest.Rule

RULES = [
    R("a", "only the executor imports the one writer",
      layer=("api.writer",), importers=frozenset({"execute"})),
    R("b", "pull reaches no database, ingest, compute, write verb or writer",
      layer=("pull_*",), bad=("lib.db", "ingest_*", "compute_*", *WRITE_VERBS, "api.writer")),
    R("c", "ingest reaches no platform client, pull or compute",
      layer=("ingest_*",), bad=(*API, "pull_*", "compute_*")),
    R("d", "compute reaches no platform client, pull, ingest or write verb",
      layer=("compute_*",), bad=(*API, "pull_*", "ingest_*", *WRITE_VERBS)),
    R("e", "compute spawns nothing (the story-check runner runs read verbs only)",
      layer=("compute_*", "!compute_stories"), bad=("subprocess", "multiprocessing", "multiprocessing.*")),
    R("f", "a write verb reaches no compute: it re-checks with the rules in lib",
      layer=WRITE_VERBS, bad=("compute_*",)),
    R("g", "lib imports no verb script and not the writer",
      layer=("lib", "lib.*"), bad=(*VERBS, "api.writer")),
]

S = archtest.Scan
WRITE_VERB = archtest.verbs(TOOL, "facts set|unconfirm|init;decisions set|withdraw;queue add|reject",
                            "pull|ingest|execute")
SCANS = [
    S("i", "the core never names the console or the host adapter", ("src", "ssot"),
      re.compile(r"\bwebconsole\b|\badapter/", re.I),
      hits=("open('webconsole/pages.py')", "see adapter/hooks.py"),
      misses=("print to the console", "an adapter pattern")),
    S("j", "the console runs no write verb, as shell text or as argv", ("webconsole",), WRITE_VERB,
      hits=("harness facts set margin 0.3", "uv run src/harness.py pull ads",
            "run([TOOL, 'queue', 'reject', qid])", '("execute", "--apply")', '[TOOL, "pull"]'),
      misses=("harness facts list --json", "run([TOOL, 'compute', 'kpi', '--json'])",
              "harness queue list", '("facts", "confirm", key, "--code", code)'),
      optional=True),
    S("k", "the console never opens the database file", ("webconsole",),
      re.compile(r"\bsqlite3?\b|\.db\b"),
      hits=("import sqlite3", "open('data/harness.db')"), misses=("harness db -- coverage",),
      optional=True),
]


def main() -> int:
    graph = archtest.Graph(archtest.modules_in(ROOT / "src"))
    print("import rules")
    for label, problems in archtest.evaluate(graph, RULES):
        check(label, not problems, problems)
    print("text rules")
    for label, problems in archtest.scan(ROOT, SCANS):
        check(label, not problems, problems)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
