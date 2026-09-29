"""The approval queue: proposals wait here for a person's yes."""

import sys

from kit import config, queue, runner

from _lib.schema import SPEC


def snapshot(market):
    """A fresh `compute steer`, through the harness CLI."""
    cfg = config.config()
    args = ["compute", "steer", "--", "--json"]
    args += ["--market", market] if market else []
    return runner.run_json(cfg.root / cfg.scripts_dir / f"{cfg.cli}.py", args)


if __name__ == "__main__":
    raise SystemExit(queue.main(sys.argv[1:], spec=SPEC, snapshot=snapshot,
                                validate=lambda con, p: None,
                                ttl_hours=lambda con, m: 24.0))
