#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The smallest harness that wires core/: values an agent proposes and a person
confirms, a verb table, the gate, coded messages. The tests drive it as a real
harness, and console/relay.py talks to it as it would to yours.

    toy_harness.py init SCOPE [--json]                          declare the scope, once
    toy_harness.py set TYPE ID VALUE --reason=R [--json]         an agent writes a pending value
    toy_harness.py confirm TYPE ID… [--value=V] --reason=R [--json]
                   [--code=C --relay-user=U --relay-at=T]        a person confirms: the gate
    toy_harness.py list [--json]

TYPE is `fact` or `budget`. The store is one JSON file named by TOY_STORE
(required, never guessed). Every write to an item moves its `rev`, and the gate's
subject carries the revs as its version: a used code, or one shown before an
agent re-set the value, no longer matches. One id: the person retypes the value;
several: the word `confirm`. `--value` (one id) confirms the person's own value
instead of the pending one. All or nothing: a set with an unknown id, or one with
nothing pending, is refused before any code is issued. The check and the write
happen under one lock, so nothing changes between them.

Under --json every outcome is one document on stdout (a refusal:
messages.failure_doc, or gate.json_refusal for the gate), exit 0 or 1.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))                 # the folder that holds core/
from core import gate, messages  # noqa: E402
from core.messages import coded, msg  # noqa: E402

messages.use_registry(HERE / "toy_codes.tsv")
TYPES = ("fact", "budget")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}")
Refused = gate.Refused


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(msg("bad_request", f"{self.prog}: {message}", detail=message))


def _token(text: str) -> str:
    if not TOKEN.fullmatch(text):
        raise argparse.ArgumentTypeError(f"{text[:40]!r} is not a plain id")
    return text


def parser() -> argparse.ArgumentParser:
    p = _Parser(prog="toy_harness.py", allow_abbrev=False)
    sub = p.add_subparsers(dest="verb", required=True)
    init = sub.add_parser("init", allow_abbrev=False)
    init.add_argument("scope", type=_token)
    st = sub.add_parser("set", allow_abbrev=False)
    st.add_argument("type", choices=TYPES)
    st.add_argument("id", type=_token)
    st.add_argument("value")
    st.add_argument("--reason")
    cf = sub.add_parser("confirm", allow_abbrev=False)
    cf.add_argument("type", choices=TYPES)
    cf.add_argument("ids", nargs="+", type=_token)
    for flag in ("--value", "--reason", "--code", "--relay-user", "--relay-at"):
        cf.add_argument(flag)
    sub.add_parser("list", allow_abbrev=False)
    for sp in (init, st, cf, sub.choices["list"]):
        sp.add_argument("--json", action="store_true")
    return p


def _path() -> Path:
    p = os.environ.get("TOY_STORE")
    if not p:
        raise Refused(msg("store_unset", "TOY_STORE is not set: name the store file. Nothing is guessed."))
    return Path(p)


@contextlib.contextmanager
def opened():
    """The store, locked; written back in one replace if the block raised nothing."""
    path = _path()
    with open(f"{path}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            raise Refused(msg("no_store", f"no store at {path}: run init first.", path=str(path)))
        data = json.loads(path.read_text(encoding="utf-8"))
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = Path(f"{path}.tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, path)


def _row(item, kind, value, changed_by, reason) -> dict:
    return {"item": item, "kind": kind, "value": value, "changed_by": changed_by, "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def cmd_init(a) -> dict:
    path = _path()
    with open(f"{path}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            raise Refused(msg("store_exists", f"{path} already declares its scope; a scope is "
                              f"declared once.", path=str(path)))
        path.write_text(json.dumps({"scope": a.scope, "items": {}, "history": []}), encoding="utf-8")
    return {**coded("message", msg("store_declared", f"Declared scope {a.scope}.", scope=a.scope)),
            "scope": a.scope}


def cmd_set(a) -> dict:
    reason = gate.why(a.reason)
    key = f"{a.type}:{a.id}"
    with opened() as s:
        it = s["items"].setdefault(key, {"pending": None, "value": None, "rev": 0})
        it["pending"], it["rev"] = a.value, it["rev"] + 1
        s["history"].append(_row(key, "set", a.value, gate.changed_by("cli"), reason))
    m = msg("pending_written", f"{key} = {a.value} is pending; a person confirms it.",
            item=key, value=a.value)
    return {**coded("message", m), "item": key, "pending": a.value}


def cmd_confirm(a) -> dict:
    reason = gate.why(a.reason)
    if a.value is not None and len(set(a.ids)) != 1:
        raise Refused(msg("value_needs_one_id", "--value confirms one id at a time. Nothing was written."))
    with opened() as s:
        keys = {i: f"{a.type}:{i}" for i in a.ids}
        missing = sorted(i for i, k in keys.items() if k not in s["items"])
        idle = sorted(i for i, k in keys.items()
                      if k in s["items"] and s["items"][k]["pending"] is None and a.value is None)
        if missing or idle:
            raise Refused(msg("nothing_to_confirm", f"confirm {a.type}: nothing was confirmed. "
                              f"No such id: {missing or '-'}; nothing pending: {idle or '-'}.",
                              entity_type=a.type, missing=missing, idle=idle))
        values = {i: a.value if a.value is not None else s["items"][k]["pending"] for i, k in keys.items()}
        revs = {i: s["items"][k]["rev"] for i, k in keys.items()}
        summary = "; ".join(f"{a.type} {i} = {v}" for i, v in sorted(values.items()))
        expected = next(iter(values.values())) if len(values) == 1 else "confirm"
        passed = gate.confirm(f"confirm {a.type}", f"in {s['scope']}: {summary}", expected,
                              subj=gate.subject("confirm", s["scope"], a.type, values, revs),
                              code=a.code, relay_user=a.relay_user, relay_at=a.relay_at)
        for i, k in keys.items():
            it = s["items"][k]
            it.update(value=values[i], pending=None, rev=it["rev"] + 1)
            s["history"].append(_row(k, "confirm", values[i], passed.changed_by, reason + passed.audit))
    return {**coded("message", msg("confirmed", f"Confirmed {summary}.", summary=summary)),
            "confirmed": sorted(keys.values()), "changed_by": passed.changed_by,
            "history_reason": reason + passed.audit}


def cmd_list(a) -> dict:
    with opened() as s:
        return {"scope": s["scope"], "items": [{"item": k, **v} for k, v in sorted(s["items"].items())]}


VERBS = {"init": cmd_init, "set": cmd_set, "confirm": cmd_confirm, "list": cmd_list}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    try:
        a = parser().parse_args(argv)
        doc = VERBS[a.verb](a)
    except Exception as e:                   # a refusal or a crash: still one document
        if as_json:
            print(json.dumps(gate.json_refusal(e, ["toy_harness.py", *argv])))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(doc) if as_json else doc.get("message") or json.dumps(doc, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
