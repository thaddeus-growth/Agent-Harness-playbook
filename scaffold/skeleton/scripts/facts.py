"""Client facts: what the client told us, per scope; pending until a
person confirms it (kit.facts, the one write path of client_facts)."""

import sys

from kit import facts
from kit.registry import FactKeys, Thresholds

from _lib.schema import SPEC

# The facts `{{cli}} facts init` asks for after the scope, in order:
# [{"key": "<a key of ssot/fact_keys.tsv>", "prompt": "…"}] (BUILD.md B5).
INIT_QUESTIONS: list[dict] = []

if __name__ == "__main__":
    raise SystemExit(facts.main(sys.argv[1:], spec=SPEC, keys=FactKeys(),
                                thresholds=Thresholds(),
                                init_questions=INIT_QUESTIONS))
