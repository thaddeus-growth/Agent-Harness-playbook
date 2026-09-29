"""Approved actions go out through the one writer (scripts/_lib/writer.py),
a dry run unless --apply (kit.execute).

No executor is written yet: every approved item is refused
(execute.unplanned), so a dry run shows it and --apply sends nothing. The
real plan_item (recompute the basis), apply_item (one call through the
writer) and read_back come with the first story whose human step is
approve (BUILD.md B7), merged by the owner.
"""

import sys

from kit import execute

from _lib import writer
from _lib.schema import SPEC


def apply_item(w, row, plan):
    raise RuntimeError("no executor yet: unplanned refuses every item first")


def read_back(w, row, response):
    return None


if __name__ == "__main__":
    raise SystemExit(execute.main(sys.argv[1:], spec=SPEC,
                                  writer_factory=writer.writer,
                                  plan_item=execute.unplanned,
                                  apply_item=apply_item, read_back=read_back,
                                  allowlist=writer.ALLOWED))
