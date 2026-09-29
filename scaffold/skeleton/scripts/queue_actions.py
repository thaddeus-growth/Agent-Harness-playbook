"""The approval queue: nothing goes out until a person approves it
(kit.queue).

No report proposes anything yet, so `queue add` takes a saved proposal
document (`--from FILE`) until a compute is wired as the snapshot hook
(BUILD.md B7). An approval expires after the approval_ttl_hours threshold.
"""

import sys

from kit import queue
from kit.registry import Thresholds

from _lib.schema import SPEC


def ttl_hours(con, market) -> float:
    return float(Thresholds().get("approval_ttl_hours", con, market))


if __name__ == "__main__":
    raise SystemExit(queue.main(sys.argv[1:], spec=SPEC,
                                snapshot=queue.no_snapshot,
                                validate=lambda con, proposal: None,
                                ttl_hours=ttl_hours))
