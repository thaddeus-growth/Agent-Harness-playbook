#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""takes.py: a paid take is made once and kept; a broken one is not cached; a
plan needs a cap, prices, room under the cap and a person, in that order."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402
from core import takes  # noqa: E402


def store(d):
    return takes.TakeStore(Path(d) / "takes", Path(d) / "ledger.jsonl")


def refused(fn, code):
    try:
        fn()
    except takes.TakeRefused as e:
        assert e.args[0].code == code, (e.args[0].code, code)
        return
    raise AssertionError(f"expected {code}")


def test_a_take_is_made_once_and_kept():
    with _t.tmpdir() as d:
        s, calls = store(d), []

        def make(folder):
            calls.append(1)
            (folder / "out.bin").write_bytes(b"x")
            return {"file": "out.bin"}
        req = {"kind": "voice", "text": "你好", "impl": "1"}
        k1, r1, made1 = s.put(req, make)
        k2, r2, made2 = s.put(req, make)
        assert made1 and not made2 and k1 == k2 and r1 == r2 and len(calls) == 1
        k3, _, made3 = s.put({**req, "take": 2}, make)      # a re-roll is a new take
        assert made3 and k3 != k1 and s.get(k1) == r1


def test_the_key_moves_with_the_generator_version():
    assert takes.key({"a": 1, "impl": "1"}) != takes.key({"a": 1, "impl": "2"})
    assert takes.key({"a": 1, "b": 2}) == takes.key({"b": 2, "a": 1})


def test_a_broken_take_is_not_cached_and_retries():
    with _t.tmpdir() as d:
        s = store(d)
        req = {"kind": "voice", "text": "真的非常感谢"}
        check = lambda r: "0.01 s of audio for 6 characters" if r["seconds"] < 0.5 else None  # noqa: E731
        refused(lambda: s.put(req, lambda f: {"seconds": 0.0115}, check=check), "take_rejected")
        assert s.get(takes.key(req)) is None
        _, r, made = s.put(req, lambda f: {"seconds": 2.1}, check=check)
        assert made and r["seconds"] == 2.1


def test_plan_prices_only_what_is_not_cached():
    with _t.tmpdir() as d:
        s = store(d)
        a, b = {"n": 1}, {"n": 2}
        s.put(a, lambda f: {})
        items = s.plan([(a, True, 5.0), (b, True, 5.0)])
        assert [(i.cached, i.paid, i.cost) for i in items] == [(True, False, 0.0), (False, True, 5.0)]


def test_approve_refuses_in_order_cap_price_room_person():
    with _t.tmpdir() as d:
        s = store(d)
        os.environ.pop("CONFIRM_CODE_SECRET", None)
        priced = s.plan([({"n": 1}, True, 30.0)])
        unpriced = s.plan([({"n": 2}, True, None)])
        refused(lambda: s.approve(priced, cap=None, scope="p", reason="r"), "spend_cap_missing")
        refused(lambda: s.approve(unpriced, cap=100, scope="p", reason="r"), "price_unknown")
        s.charge("old", 80.0, approved_by="x@tty")
        refused(lambda: s.approve(priced, cap=100, scope="p", reason="r"), "spend_cap_exceeded")
        with _t.refused("confirm_needs_human"):              # stdin is not a TTY here
            s.approve(priced, cap=200, scope="p", reason="r")
        assert s.approve(s.plan([({"n": 1}, False, 0.0)]), cap=None, scope="p", reason=None) is None


def test_a_relayed_code_approves_this_plan_once():
    with _t.tmpdir() as d:
        s = store(d)
        os.environ["CONFIRM_CODE_SECRET"] = "test-secret"
        try:
            plan = s.plan([({"n": 1}, True, 3.0)])
            with _t.refused("confirm_code_required") as c:
                s.approve(plan, cap=10, scope="p", reason="first scene")
            code = c.err.confirm_code
            ok = s.approve(plan, cap=10, scope="p", reason="first scene", code=code,
                           relay_user="web:owner", relay_at="2026-09-29T10:00:00+08:00")
            assert ok["approved_by"].endswith("@relay") and "relay user=web:owner" in ok["reason"]
            s.charge(plan[0].key, 3.0, **ok)
            with _t.refused("confirm_code_mismatch"):        # the ledger moved: the code is spent
                s.approve(plan, cap=10, scope="p", reason="again", code=code,
                          relay_user="web:owner", relay_at="2026-09-29T10:00:00+08:00")
            assert json.loads((Path(d) / "ledger.jsonl").read_text().splitlines()[0])["cost"] == 3.0
        finally:
            os.environ.pop("CONFIRM_CODE_SECRET", None)


if __name__ == "__main__":
    _t.main(globals())
