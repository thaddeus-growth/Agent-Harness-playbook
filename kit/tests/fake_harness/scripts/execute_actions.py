"""Approved restocks go out through the one guarded writer (dry run
unless --apply)."""

import sys

from kit import execute

from _lib import steer, writer
from _lib.schema import SPEC


def plan_item(con, row):
    return {"basis": steer.basis(con, row["market"])}


def apply_item(w, row, plan):
    return w.call("POST", f"/stock/{row['target_ref']}",
                  {"units": row["payload"]["units"]})[1]


def read_back(w, row, response):
    return None


if __name__ == "__main__":
    raise SystemExit(execute.main(sys.argv[1:], spec=SPEC,
                                  writer_factory=writer.writer,
                                  plan_item=plan_item, apply_item=apply_item,
                                  read_back=read_back))
