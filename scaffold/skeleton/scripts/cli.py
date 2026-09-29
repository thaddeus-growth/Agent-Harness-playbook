#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""{{name}}: `{{cli}} <verb> [args…]`, the one entry point.

The verbs and their kinds are scripts/verbs.py; the dispatcher is the
vendored kit's (kit.cli): the env chain loaded once, the data-dir guard,
the first `--` stripped, an unlisted verb refused, `{{cli}} doctor` and
`{{cli}} verbs --json` built in. `{{cli}} --help` lists every verb.
"""

import sys
from pathlib import Path

from kit.cli import Dispatcher

from _lib.schema import SPEC
from verbs import VERBS

HERE = Path(__file__).resolve().parent

# The harness's own doctor checks, each `check(ctx) -> None` reporting with
# ctx.ok/info/warn/fail(check_id, coded message, next) (kit.doctor).
DOCTOR_CHECKS: list = []


def main(argv: list[str] | None = None) -> int:
    return Dispatcher(VERBS, scripts_dir=HERE, spec=SPEC,
                      doctor_checks=DOCTOR_CHECKS,
                      description="{{name}}: every command below reads, "
                                  "proposes or asks a person; `--json` is "
                                  "the contract.").main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
