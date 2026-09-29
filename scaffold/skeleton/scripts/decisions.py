"""Decisions about one entity: pending until a person confirms them where
ssot/decision_keys.tsv says so (kit.decisions)."""

import sys

from kit import decisions
from kit.registry import DecisionRegistry

from _lib.schema import SPEC

if __name__ == "__main__":
    raise SystemExit(decisions.main(sys.argv[1:], spec=SPEC,
                                    registry=DecisionRegistry()))
