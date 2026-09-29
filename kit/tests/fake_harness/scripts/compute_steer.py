"""Which restock the shop should make now, as queueable proposals."""

import sys

from kit import contract, db, market

from _lib import steer
from _lib.schema import SPEC


def main(argv):
    p = contract.Parser(prog="shop compute steer")
    p.add_argument("--market")
    contract.add_json_arg(p)
    a = p.parse_args(argv)
    if (e := contract.missing_db_error()) is not None:
        raise e
    con = db.connect(SPEC, read_only=True)
    try:
        m = market.resolve(con, a.market)
        return {"meta": contract.meta(window=None, sources=[], stale=[]),
                "market": m, "proposals": steer.proposals(con, m)}
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(contract.run_main(main, sys.argv[1:],
                                       cmd=["shop", "compute", "steer"]))
