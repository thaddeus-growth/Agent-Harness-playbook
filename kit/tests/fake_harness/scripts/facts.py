"""Client facts: what the owner told us, pending until a person confirms."""

import sys

from kit import facts
from kit.registry import FactKeys, Thresholds

from _lib.schema import SPEC

if __name__ == "__main__":
    raise SystemExit(facts.main(sys.argv[1:], spec=SPEC, keys=FactKeys(),
                                thresholds=Thresholds(),
                                init_questions=[{"key": "unit_cost"}]))
