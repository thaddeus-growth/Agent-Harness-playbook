#!/usr/bin/env python3
"""kit/takes.py holds each of its guards.

  1. A take is made once and kept; a re-roll is a new request and leaves
     the first take as it was; the key moves with the generator's version
     and not with key order; a key that is not a sha256 is refused.
  2. A broken take (check) is refused and not cached; the next put calls
     the generator again.
  3. A plan prices only what is not kept yet.
  4. approve() refuses in order: no cap, an unpriced paid item, over what
     is left of the cap, no reason, no person; a plan with nothing paid
     needs nothing.
  5. At the terminal the person retypes the plan's total; a wrong total is
     refused.
  6. A relayed code approves this exact plan once: another cost or another
     item refuses it, the charge spends it, and it needs its relay audit.
  7. The standing allowance passes a plan at or under it without the gate
     and says so; above it the gate asks; the cap, the prices and the reason
     still hold.
  8. The ledger: charge appends a line with who approved, spent() sums it,
     sub-cent prices are not rounded to free, an unreadable line is refused.
  9. takes.py's msg() calls are closed over its fragment.
 10. An unconfirmed price: an item is confirmed unless the plan says not;
     the allowance never covers a plan with an unconfirmed paid price (the
     gate asks), and the gate's prompt and the approval name how many.
"""

import io
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import human, messages, takes  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

CFG = _shop.use()
SECRET = CFG.env("CONFIRM_CODE_SECRET")
os.environ.pop("KIT_TTY", None)     # this process is never at the seam
os.environ.pop(SECRET, None)


class _NoTty(io.StringIO):
    def isatty(self) -> bool:
        return False


sys.stdin = _NoTty("")              # ... nor at a terminal (run alone)
AUDIT = {"relay_user": "web:owner", "relay_at": "2026-09-29T10:00:00+08:00"}


def store() -> takes.TakeStore:
    d = Path(tmp_dir("takes-"))
    return takes.TakeStore(d / "takes", d / "ledger.jsonl")


def code_of(fn) -> str | None:
    e = raises(fn, HarnessError)
    return getattr(getattr(e, "message", None), "code", None)


def at_tty(answer: str, fn):
    """fn() with a person at the KIT_TTY seam typing `answer`."""
    path = Path(tmp_dir("tty-")) / "answers.txt"
    path.write_text(answer + "\n", encoding="utf-8")
    os.environ["KIT_TTY"] = str(path)
    try:
        return fn()
    finally:
        os.environ.pop("KIT_TTY", None)


def test_made_once_and_kept() -> None:
    print("[1] a take is made once and kept")
    s, calls = store(), []

    def make(folder):
        calls.append(1)
        (folder / "out.bin").write_bytes(b"x")
        return {"file": "out.bin"}
    req = {"kind": "voice", "text": "hello", "impl": "1"}
    k1, r1, made1 = s.put(req, make)
    k2, r2, made2 = s.put(req, make)
    check("the first put makes it, the second returns the kept one and calls "
          "nothing", made1 and not made2 and k1 == k2 and r1 == r2
          and len(calls) == 1, (made1, made2, calls))
    check("the folder holds the request, the result and the media",
          json.loads((s.dir(k1) / "request.json").read_text()) == req
          and (s.dir(k1) / "out.bin").is_file() and "made_at" in r1)
    k3, _, made3 = s.put({**req, "take": 2}, make)
    check("a re-roll is a new take and the first is kept as it was",
          made3 and k3 != k1 and s.get(k1) == r1, (k3, k1))
    check("the key moves with the generator's version",
          takes.key({"a": 1, "impl": "1"}) != takes.key({"a": 1, "impl": "2"}))
    check("the key does not move with key order",
          takes.key({"a": 1, "b": 2}) == takes.key({"b": 2, "a": 1}))
    check("a key that is not a sha256 is refused",
          code_of(lambda: s.get("../../etc")) == "take_key_malformed"
          and code_of(lambda: s.dir(k1.upper())) == "take_key_malformed")


def test_broken_take() -> None:
    print("\n[2] a broken take is not cached")
    s = store()
    req = {"kind": "voice", "text": "thank you so much"}

    def check_(r):
        return "0.01 s of audio for 17 characters" if r["seconds"] < 0.5 \
            else None
    e = raises(lambda: s.put(req, lambda f: {"seconds": 0.0115},
                             check=check_), takes.TakeRefused)
    check("a result the check calls broken is refused with its reason",
          e and e.message.code == "take_not_kept"
          and "0.01 s" in e.message.params["why"], e)
    check("and nothing is cached", s.get(takes.key(req)) is None
          and not (s.dir(takes.key(req)) / "result.json").exists())
    _, r, made = s.put(req, lambda f: {"seconds": 2.1}, check=check_)
    check("the next put calls the generator again and keeps a good one",
          made and r["seconds"] == 2.1)


def test_plan() -> None:
    print("\n[3] a plan prices only what is not kept")
    s = store()
    a, b = {"n": 1}, {"n": 2}
    s.put(a, lambda f: {})
    items = s.plan([(a, True, 5.0), (b, True, 5.0), ({"n": 3}, False, None)])
    check("a kept take costs 0 and is not paid for again",
          [(i.cached, i.paid, i.cost) for i in items]
          == [(True, False, 0.0), (False, True, 5.0), (False, False, None)],
          items)


def test_refusal_order() -> None:
    print("\n[4] approve refuses in order: cap, price, room, reason, person")
    s = store()
    priced = s.plan([({"n": 1}, True, 30.0)])
    unpriced = s.plan([({"n": 2}, True, None)])
    kw = {"scope": "p", "currency": "CNY"}
    check("no cap is refused",
          code_of(lambda: s.approve(priced, cap=None, reason="r", **kw))
          == "take_cap_missing")
    check("an unpriced paid item is refused",
          code_of(lambda: s.approve(unpriced, cap=100, reason="r", **kw))
          == "take_price_missing")
    s.charge("old", 80.0, approved_by="x@tty")
    e = raises(lambda: s.approve(priced, cap=100, reason="r", **kw),
               takes.TakeRefused)
    check("a plan over what is left of the cap is refused, with the numbers",
          e and e.message.code == "take_cap_exceeded"
          and e.message.params == {"cost": 30.0, "left": 20.0,
                                   "currency": "CNY"}, e)
    check("no reason is refused",
          code_of(lambda: s.approve(priced, cap=200, reason=" ", **kw))
          == "reason_required")
    check("off a terminal, no secret: a person is required",
          code_of(lambda: s.approve(priced, cap=200, reason="r", **kw))
          == "confirm_needs_human")
    check("a plan with nothing paid needs nothing",
          s.approve(s.plan([({"n": 1}, False, 0.0)]), cap=None, reason=None,
                    **kw) is None)


def test_tty() -> None:
    print("\n[5] at the terminal the person retypes the total")
    s = store()
    plan = s.plan([({"n": 1}, True, 12.5), ({"n": 2}, True, 0.25)])
    kw = {"cap": 100, "scope": "p", "reason": "first scene",
          "currency": "CNY"}
    ok = at_tty("12.75", lambda: s.approve(plan, **kw))
    check("the right total approves, signed at the terminal",
          ok and ok["approved_by"].endswith("@tty")
          and ok["reason"] == "first scene", ok)
    check("a wrong total is refused",
          at_tty("12", lambda: code_of(lambda: s.approve(plan, **kw)))
          == "confirm_typed_mismatch")


def test_relayed_code() -> None:
    print("\n[6] a relayed code approves this exact plan once")
    s = store()
    os.environ[SECRET] = "test-secret"
    try:
        plan = s.plan([({"n": 1}, True, 3.0)])
        kw = {"cap": 10, "scope": "p", "currency": "CNY"}
        e = raises(lambda: s.approve(plan, reason="first scene", **kw),
                   human.CodeRequired)
        check("off a terminal the gate issues a code bound to {key: cost}",
              e and e.subject["items"] == {plan[0].key: 3.0}
              and e.subject["version"] == 0, e and e.subject)
        code = e.confirm_code
        other = [takes.Item(plan[0].key, 2.0, True, False)]
        check("another cost refuses it",
              code_of(lambda: s.approve(other, reason="r", code=code, **AUDIT,
                                        **kw)) == "confirm_code_mismatch")
        more = plan + s.plan([({"n": 2}, True, 1.0)])
        check("another item refuses it",
              code_of(lambda: s.approve(more, reason="r", code=code, **AUDIT,
                                        **kw)) == "confirm_code_mismatch")
        check("a relayed approval needs its audit",
              code_of(lambda: s.approve(plan, reason="r", code=code, **kw))
              == "confirm_relay_audit_missing")
        ok = s.approve(plan, reason="first scene", code=code, **AUDIT, **kw)
        check("the code approves the plan, with who relayed it",
              ok["approved_by"].endswith("@relay")
              and "relay user=web:owner" in ok["reason"], ok)
        s.charge(plan[0].key, 3.0, **ok)
        check("the charge spends the code",
              code_of(lambda: s.approve(plan, reason="again", code=code,
                                        **AUDIT, **kw))
              == "confirm_code_mismatch")
    finally:
        os.environ.pop(SECRET, None)


def test_standing_allowance() -> None:
    print("\n[7] the standing allowance")
    s = store()
    kw = {"cap": 20, "scope": "p", "currency": "CNY", "allowance": 10}
    small = s.plan([({"n": 1}, True, 4.0)])
    ok = s.approve(small, reason="scene 1", allowance_note="set in chat", **kw)
    check("a plan under it passes without the gate, and says so",
          ok and ok["approved_by"] == "owner, standing allowance 10.0 CNY "
          "(set in chat)" and ok["reason"].startswith("scene 1 [plan 4.0 CNY"),
          ok)
    s.charge(small[0].key, 4.0, **ok)
    exact = s.plan([({"n": 2}, True, 6.0)])
    check("a plan that takes the ledger to exactly the allowance passes",
          s.approve(exact, reason="r", **kw) is not None)
    s.charge(exact[0].key, 6.0, approved_by="owner, standing allowance")
    one = s.plan([({"n": 3}, True, 1.0)])
    check("above the allowance (under the cap) the gate asks",
          code_of(lambda: s.approve(one, reason="r", **kw))
          == "confirm_needs_human")
    big = s.plan([({"n": 4}, True, 11.0)])
    check("above the cap it is refused, whatever the allowance",
          code_of(lambda: s.approve(big, reason="r", **{**kw,
                                                         "allowance": 999}))
          == "take_cap_exceeded")
    s2 = store()
    check("no cap is refused, whatever the allowance",
          code_of(lambda: s2.approve(small, reason="r", **{**kw, "cap": None}))
          == "take_cap_missing")
    check("an unpriced item is refused, whatever the allowance",
          code_of(lambda: s2.approve(s2.plan([({"n": 5}, True, None)]),
                                     reason="r", **kw))
          == "take_price_missing")
    check("the reason is still required",
          code_of(lambda: s2.approve(small, reason=None, **kw))
          == "reason_required")


def test_ledger() -> None:
    print("\n[8] the ledger")
    s = store()
    check("an empty ledger has spent nothing", s.spent() == 0 and s.lines()
          == [])
    s.charge("a" * 64, 1.5, approved_by="x@tty", reason="r", kind="voice")
    s.charge("b" * 64, 0.25, approved_by="x@relay")
    rows = s.lines()
    check("charge appends one line per take, with who approved it",
          [(r["key"][:1], r["cost"], r["approved_by"]) for r in rows]
          == [("a", 1.5, "x@tty"), ("b", 0.25, "x@relay")]
          and rows[0]["kind"] == "voice" and rows[0]["at"].endswith("Z"),
          rows)
    check("spent() sums it", s.spent() == 1.75, s.spent())
    check("charge needs who approved it",
          raises(lambda: s.charge("c" * 64, 1.0), TypeError) is not None)
    tiny = store()
    plan = tiny.plan([({"n": i}, True, 0.004) for i in range(3)])
    check("sub-cent prices are not rounded to free",
          code_of(lambda: tiny.approve(plan, cap=0.01, scope="p", reason="r",
                                       currency="USD")) == "take_cap_exceeded")
    with open(s.ledger, "a", encoding="utf-8") as f:
        f.write('{"key": "c", "cost": 2\n')
    e = raises(s.spent, takes.TakeRefused)
    check("an unreadable line is refused, never skipped",
          e and e.message.code == "take_ledger_unreadable"
          and e.message.params["line"] == 3, e)


def test_closure() -> None:
    print("\n[9] takes.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items() if Path(r["file"]).name == "takes.tsv"}
    check("the fragment holds the take_* codes",
          own and all(c.startswith("take_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["takes.py"], registry=own, strict_kit=True)
    check("every code emitted by takes.py is registered with exact params, "
          "and every code of takes.tsv is emitted", problems == [], problems)


def test_unconfirmed_prices() -> None:
    print("\n[10] an unconfirmed price never rides the allowance")
    s = store()
    items = s.plan([({"n": 1}, True, 2.0), ({"n": 2}, True, 1.0, False),
                    ({"n": 3}, False, 0.0, False)])
    check("an item is confirmed unless the plan says not (Item keeps its "
          "four-field form)",
          [i.confirmed for i in items] == [True, False, False]
          and takes.Item("k", 1.0, True, False).confirmed, items)
    kw = {"cap": 20, "scope": "p", "currency": "CNY", "allowance": 10}
    check("under the allowance, one unconfirmed paid price sends the plan to "
          "the gate", code_of(lambda: s.approve(items, reason="r", **kw))
          == "confirm_needs_human")
    check("the same plan with every price confirmed passes by allowance",
          s.approve(s.plan([({"n": 1}, True, 2.0), ({"n": 2}, True, 1.0)]),
                    reason="r", **kw)["approved_by"].startswith(
              "owner, standing allowance"))
    check("an unconfirmed item that is kept already costs nothing and does "
          "not block the allowance",
          s.approve([takes.Item("k" * 64, 0.0, False, True, False),
                     *s.plan([({"n": 1}, True, 2.0)])], reason="r", **kw)
          is not None)
    os.environ[SECRET] = "test-secret"
    try:
        e = raises(lambda: s.approve(items, reason="r", **kw),
                   human.CodeRequired)
        check("the gate's prompt names how many prices are unconfirmed",
              e and "1 of these prices are unconfirmed"
              in e.message.params["summary"]
              and "allowance does not cover" in e.message.params["summary"],
              e and e.message.params["summary"])
        ok = s.approve(items, reason="r", code=e.confirm_code, **AUDIT, **kw)
        check("a person approves it, and the reason on the ledger says so",
              ok["approved_by"].endswith("@relay")
              and ok["reason"].endswith("[1 prices unconfirmed]"), ok)
    finally:
        os.environ.pop(SECRET, None)
    ok = at_tty("3", lambda: s.approve(items, reason="r", cap=20, scope="p",
                                       currency="CNY"))
    check("at the terminal the retype still approves it",
          ok and ok["approved_by"].endswith("@tty"), ok)


if __name__ == "__main__":
    test_made_once_and_kept()
    test_broken_take()
    test_plan()
    test_refusal_order()
    test_tty()
    test_relayed_code()
    test_standing_allowance()
    test_ledger()
    test_closure()
    test_unconfirmed_prices()
    raise SystemExit(finish())
