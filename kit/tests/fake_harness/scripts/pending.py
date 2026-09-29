"""Everything waiting on a person, as ready console asks."""

import sys

from kit import pending
from kit.registry import DecisionRegistry, FactKeys, Thresholds

from _lib.schema import SPEC

if __name__ == "__main__":
    raise SystemExit(pending.main(sys.argv[1:], spec=SPEC, keys=FactKeys(),
                                  registry=DecisionRegistry(),
                                  thresholds=Thresholds()))
