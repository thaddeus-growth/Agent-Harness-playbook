#!/usr/bin/env python3
"""A fake host token command for kit/tests/test_auth.py (command mode).

Stands in for whatever a host wires into <prefix>_TOKEN_COMMAND: prints
one JSON object {"access_token", "expires_at", ...} on stdout. No
credentials, no network.

    fake_token_command.py COUNTER [--expires-in S | --expires-at JSON]
        [--field K=V]... [--stdin-field K] [--raw TEXT] [--exit N]
        [--sleep S]

COUNTER is a file whose run count is bumped on each call, so a test sees
whether the token was cached; the token is `fake-tok-<count>`.
`--stdin-field K` puts whatever the command could read on stdin into K.
"""

import argparse
import json
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("counter")
p.add_argument("--expires-in", type=float, default=3600)
p.add_argument("--expires-at")          # verbatim JSON value ("null", an ISO string…)
p.add_argument("--field", action="append", default=[])
p.add_argument("--stdin-field")
p.add_argument("--raw")
p.add_argument("--exit", type=int, default=0)
p.add_argument("--sleep", type=float, default=0)
a = p.parse_args()

counter = Path(a.counter)
n = int(counter.read_text() or 0) + 1 if counter.exists() else 1
counter.write_text(str(n))
time.sleep(a.sleep)
if a.exit:
    print("connector refused: not authorised", file=sys.stderr)
    sys.exit(a.exit)
if a.raw is not None:
    print(a.raw)
    sys.exit(0)
out = {"access_token": f"fake-tok-{n}",
       "expires_at": (json.loads(a.expires_at) if a.expires_at is not None
                      else time.time() + a.expires_in),
       "ignored_key": ["ignored"]}
for kv in a.field:
    k, _, v = kv.partition("=")
    out[k] = v
if a.stdin_field:
    out[a.stdin_field] = sys.stdin.read()
print(json.dumps(out))
