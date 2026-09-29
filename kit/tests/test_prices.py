#!/usr/bin/env python3
"""kit/prices.py holds each of its guards.

  1. Reading: a blank price is unknown (None), 0 is zero; the currency is
     the value column's (price_<ccy>) or none; a broken file is refused
     (missing, no value column, bad status, bad number, short row,
     duplicate key).
  2. A retired row is not found; a key with no row estimates to None.
  3. A pending price is flagged: estimate() says confirmed False with a
     coded note; a hand-set `confirmed` (no gate trace, or a value edited
     after its confirmation) reads as pending; a unit mismatch is refused.
  4. proposals() and propose(): a new key and a changed pending row come
     back pending with their source and date; a confirmed row is never
     overwritten (its change is held); same value, a retired row and a
     blank observation propose nothing; an observation with no source is
     refused.
  5. confirm needs a person: no terminal and no secret is refused, a wrong
     retype is refused, a blank value is refused before the gate; the
     exact retype confirms, confirmed_by comes from the gate, and the log
     keeps it.
  6. The relayed code confirms once and needs its audit.
  7. The file moving during the gate is refused.
  8. Lowering trust needs no gate, only a reason; it clears the
     confirmation; raising it that way is refused.
  9. prices.py's msg() calls are closed over its fragment.
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import human, messages, prices  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

CFG = _shop.use()
SECRET = CFG.env("CONFIRM_CODE_SECRET")
os.environ.pop("KIT_TTY", None)
os.environ.pop(SECRET, None)


class _NoTty(io.StringIO):
    def isatty(self) -> bool:
        return False


sys.stdin = _NoTty("")
AUDIT = {"relay_user": "web:owner", "relay_at": "2026-09-29T10:00:00+08:00"}
HEAD = "provider\tkind\tmodel\tunit\tprice_cny\tstatus\tsource\tread_on"
ROWS = [
    "vendor_a\tvoice\ttts-hd\tper_10k_chars\t\tpending\tconsole price list\t",
    "vendor_a\tavatar\tav-1\tper_second\t0.45\tpending\tvendor page, member "
    "price (list 0.50)\t2026-09-29",
    "vendor_b\tvoice\tfree-tts\tper_second\t0\tpending\tvendor page\t2026-09-29",
    "vendor_b\tavatar\told-1\tper_second\t0.02\tretired\tvendor page\t",
    "vendor_b\tavatar\tav-2@768p\tper_second\t0.04\tpending\tcatalog\t2026-09-29",
]
AV1 = ("vendor_a", "avatar", "av-1")
TTS = ("vendor_a", "voice", "tts-hd")


def price_file(*rows: str, head: str = HEAD) -> Path:
    p = Path(tmp_dir("prices-")) / "provider_prices.tsv"
    p.write_text("\n".join([head, *(rows or ROWS)]) + "\n", encoding="utf-8")
    return p


def code_of(fn) -> str | None:
    e = raises(fn, HarnessError)
    return getattr(getattr(e, "message", None), "code", None)


def at_tty(answer: str, fn):
    """fn() with a person at the KIT_TTY seam typing `answer`; returns
    (result or None, the HarnessError or None, answers left unread)."""
    path = Path(tmp_dir("tty-")) / "answers.txt"
    path.write_text(answer + "\n", encoding="utf-8")
    os.environ["KIT_TTY"] = str(path)
    try:
        try:
            return fn(), None, path.read_text()
        except HarnessError as e:
            return None, e, path.read_text()
    finally:
        os.environ.pop("KIT_TTY", None)


def log(p: Path) -> list[dict]:
    lp = prices.log_path(p)
    return [json.loads(x) for x in lp.read_text().splitlines()] \
        if lp.is_file() else []


def test_reading() -> None:
    print("[1] reading: unknown is not zero, a broken file is refused")
    reg = prices.load(price_file())
    tts = prices.estimate(reg, TTS, 3)
    free = prices.estimate(reg, ("vendor_b", "voice", "free-tts"), 10)
    check("a blank price is unknown: cost None, never 0",
          reg.row(TTS).value is None and tts.cost is None
          and tts.note.code == "prices_unknown", tts)
    check("a price of 0 is zero", free.cost == 0.0, free)
    check("the currency is the value column's", reg.currency == "CNY"
          and reg.column == "price_cny" and free.currency == "CNY")
    plain = prices.load(price_file(
        "x\tk\tm\tper_call\t1.5\tpending\ts", head="provider\tkind\tmodel\t"
        "unit\tvalue\tstatus\tsource"))
    check("a plain value column names no currency (the caller does)",
          plain.currency is None and plain.row(("x", "k", "m")).value == 1.5)
    bad = {
        "missing": (lambda: prices.load(Path(tmp_dir()) / "none.tsv"),
                    "prices_file_missing"),
        "no value column": (lambda: prices.load(price_file(
            "x\tk\tm\tu\tpending\ts",
            head="provider\tkind\tmodel\tunit\tstatus\tsource")),
            "prices_file_invalid"),
        "a required column": (lambda: prices.load(price_file(
            "x\tk\tm\t1\tpending",
            head="provider\tkind\tmodel\tvalue\tstatus")),
            "prices_file_invalid"),
        "bad status": (lambda: prices.load(price_file(
            "x\tk\tm\tu\t1\tok\ts\t")), "prices_row_invalid"),
        "bad number": (lambda: prices.load(price_file(
            "x\tk\tm\tu\t-1\tpending\ts\t")), "prices_row_invalid"),
        "not a number": (lambda: prices.load(price_file(
            "x\tk\tm\tu\tabout 3\tpending\ts\t")), "prices_row_invalid"),
        "short row": (lambda: prices.load(price_file(
            "x\tk\tm\tu\t1\tpending")), "prices_row_invalid"),
        "duplicate key": (lambda: prices.load(price_file(
            "x\tk\tm\tu\t1\tpending\ts\t", "x\tk\tm\tu\t2\tpending\ts\t")),
            "prices_row_duplicate"),
    }
    got = {k: code_of(fn) for k, (fn, _) in bad.items()}
    check("a broken file is refused, each with its code",
          got == {k: c for k, (_, c) in bad.items()}, got)


def test_retired_and_missing() -> None:
    print("\n[2] a retired row is not found")
    reg = prices.load(price_file())
    old = ("vendor_b", "avatar", "old-1")
    e = prices.estimate(reg, old, 5)
    check("lookup() does not find a retired row (row() still sees it)",
          prices.lookup(reg, old) is None and reg.row(old).value == 0.02)
    check("its estimate is missing: cost None",
          e.cost is None and e.status == "missing"
          and e.note.code == "prices_missing", e)
    n = prices.estimate(reg, ("nobody", "voice", "x"), 1)
    check("a key with no row is missing, never free",
          n.cost is None and not n.confirmed, n)


def test_pending_flagged() -> None:
    print("\n[3] a pending price is flagged")
    p = price_file()
    reg = prices.load(p)
    e = prices.estimate(reg, AV1, 10, unit="per_second")
    check("a pending price estimates, flagged unconfirmed with a coded note",
          abs(e.cost - 4.5) < 1e-9 and not e.confirmed
          and e.status == "pending" and e.note.code == "prices_unconfirmed"
          and e.as_dict()["key"] == "vendor_a/avatar/av-1", e)
    check("a quantity in another unit is refused",
          code_of(lambda: prices.estimate(reg, AV1, 10, unit="per_call"))
          == "prices_unit_mismatch")
    hand = price_file(ROWS[1].replace("\tpending\t", "\tconfirmed\t"))
    r = prices.load(hand).row(AV1)
    check("a status typed `confirmed` by hand reads as pending",
          r.written == "confirmed" and r.status == "pending"
          and not prices.estimate(prices.load(hand), AV1, 1).confirmed, r)


def test_proposals() -> None:
    print("\n[4] proposals never overwrite a confirmed row")
    p = price_file()
    at_tty("0.45", lambda: prices.confirm(p, AV1, reason="checked the page"))
    reg = prices.load(p)
    obs = [
        {"provider": "vendor_a", "kind": "avatar", "model": "av-1",
         "unit": "per_second", "value": 0.5},                 # confirmed, differs
        {"provider": "vendor_b", "kind": "avatar", "model": "av-2@768p",
         "unit": "per_second", "value": 0.05},                # pending, differs
        {"provider": "vendor_b", "kind": "avatar", "model": "av-2@480p",
         "unit": "per_second", "value": 0.03},                # new
        {"provider": "vendor_b", "kind": "voice", "model": "free-tts",
         "unit": "per_second", "value": "0"},                 # same
        {"provider": "vendor_b", "kind": "avatar", "model": "old-1",
         "unit": "per_second", "value": 0.9},                 # retired
        {"provider": "vendor_a", "kind": "voice", "model": "tts-hd",
         "unit": "per_10k_chars", "value": None},             # blank
    ]
    props = prices.proposals(reg, obs, source="vendor catalog API",
                             read_on="2026-09-30")
    by = {p_["model"]: p_ for p_ in props}
    check("new and changed rows are proposed; same, retired and blank are "
          "not", sorted(by) == ["av-1", "av-2@480p", "av-2@768p"], sorted(by))
    check("every proposal is pending, with its source and date",
          all(x["status"] == "pending" and x["source"] == "vendor catalog API"
              and x["read_on"] == "2026-09-30" for x in props), props)
    check("the confirmed row's change is reported as confirmed_differs",
          by["av-1"]["change"] == "confirmed_differs"
          and by["av-1"]["was"]["status"] == "confirmed"
          and by["av-2@768p"]["change"] == "changed"
          and by["av-2@480p"]["change"] == "new", props)
    out = prices.propose(p, obs, source="vendor catalog API",
                         read_on="2026-09-30", reason="weekly refresh")
    reg = prices.load(p)
    check("propose writes the new and the changed row, and holds the "
          "confirmed one", (out["added"], out["updated"], len(out["held"]))
          == (1, 1, 1) and out["message"].code == "prices_propose_done", out)
    check("the confirmed row is untouched: still 0.45, still confirmed",
          reg.row(AV1).value == 0.45 and reg.row(AV1).confirmed)
    new = reg.row(("vendor_b", "avatar", "av-2@480p"))
    check("the written rows are pending, with source and date",
          new.status == "pending" and new.value == 0.03
          and new.read_on == "2026-09-30"
          and reg.row(("vendor_b", "avatar", "av-2@768p")).value == 0.05)
    check("the log keeps each proposal with its reason",
          [r["action"] for r in log(p)] == ["confirm", "propose", "propose"]
          and log(p)[-1]["reason"] == "weekly refresh", log(p))
    check("an observation with no source is refused",
          code_of(lambda: prices.proposals(reg, [{**obs[2], "model": "z"}]))
          == "prices_observed_invalid")


def test_confirm_needs_a_person() -> None:
    print("\n[5] confirm needs a person")
    p = price_file()
    before = p.read_text()
    check("no terminal and no secret: refused, nothing written",
          code_of(lambda: prices.confirm(p, AV1, reason="r"))
          == "confirm_needs_human" and p.read_text() == before)
    _, e, _ = at_tty("0.5", lambda: prices.confirm(p, AV1, reason="r"))
    check("a retype that is not the exact value is refused, nothing written",
          e and e.message.code == "confirm_typed_mismatch"
          and p.read_text() == before, e)
    _, e, left = at_tty("0", lambda: prices.confirm(p, TTS, reason="r"))
    check("a blank value is refused before the gate (the answer is unread)",
          e and e.message.code == "prices_value_blank" and left == "0\n", e)
    check("a key with no row is refused",
          code_of(lambda: prices.confirm(p, ("x", "y", "z"), reason="r"))
          == "prices_not_found")
    check("a reason is required",
          at_tty("0.45", lambda: prices.confirm(p, AV1, reason=" "))[1]
          .message.code == "reason_required")
    seen = []
    real = human.confirm

    def spy(what, prompt, expected, **kw):
        seen.append(prompt)
        return real(what, prompt, expected, **kw)
    with mock.patch.object(human, "confirm", spy):
        ok, e, _ = at_tty("0.45", lambda: prices.confirm(
            p, AV1, reason="checked the member price"))
    check("the person sees provider, kind, model, value, unit, source, date",
          seen and all(s in seen[0] for s in (
              "vendor_a / avatar / av-1", "0.45 CNY per_second",
              "member price (list 0.50)", "2026-09-29")), seen)
    reg = prices.load(p)
    row = reg.row(AV1)
    check("the exact retype confirms; confirmed_by comes from the gate",
          ok and row.confirmed and row.confirmed_by.endswith("@tty")
          and row.confirmed_at and ok["message"].code == "prices_confirm_done",
          (ok, e))
    check("the estimate is confirmed now, and other rows kept as they were",
          prices.estimate(reg, AV1, 2).confirmed
          and reg.row(TTS).value is None and len(reg.rows) == len(ROWS))
    check("the log keeps who and why",
          log(p)[-1]["action"] == "confirm"
          and log(p)[-1]["reason"] == "checked the member price"
          and log(p)[-1]["changed_by"] == row.confirmed_by, log(p))
    p.write_text(p.read_text().replace("\t0.45\t", "\t0.40\t"))
    check("a value edited by hand after its confirmation reads as pending",
          not prices.load(p).row(AV1).confirmed)


def test_relay_code() -> None:
    print("\n[6] the relayed code confirms once")
    p = price_file()
    os.environ[SECRET] = "test-secret"
    try:
        e = raises(lambda: prices.confirm(p, AV1, reason="r"),
                   human.CodeRequired)
        check("off a terminal a code is issued, bound to the key and value",
              e and e.subject["items"]["vendor_a/avatar/av-1"]["value"]
              == "0.45", e and e.subject)
        c = e.confirm_code
        check("it needs its relay audit",
              code_of(lambda: prices.confirm(p, AV1, reason="r", code=c))
              == "confirm_relay_audit_missing")
        ok = prices.confirm(p, AV1, reason="r", code=c, **AUDIT)
        check("the code confirms, @relay", ok["confirmed_by"].endswith(
            "@relay") and prices.load(p).row(AV1).confirmed, ok)
        prices.lower(p, AV1, status="pending", reason="re-check")
        check("the same code does not confirm it again",
              code_of(lambda: prices.confirm(p, AV1, reason="r", code=c,
                                             **AUDIT))
              == "confirm_code_mismatch")
    finally:
        os.environ.pop(SECRET, None)


def test_changed_meanwhile() -> None:
    print("\n[7] the file moving during the gate is refused")
    p = price_file()
    real = human.confirm

    def moving(*a, **kw):
        channel = real(*a, **kw)
        p.write_text(p.read_text().replace("\t0.04\t", "\t0.06\t"))
        return channel
    with mock.patch.object(human, "confirm", moving):
        _, e, _ = at_tty("0.45", lambda: prices.confirm(p, AV1, reason="r"))
    check("prices_changed_meanwhile, nothing confirmed",
          e and e.message.code == "prices_changed_meanwhile"
          and not prices.load(p).row(AV1).confirmed, e)


def test_lower() -> None:
    print("\n[8] lowering trust needs no gate")
    p = price_file()
    at_tty("0.45", lambda: prices.confirm(p, AV1, reason="r"))
    out = prices.lower(p, AV1, status="pending", reason="vendor changed "
                       "its page")
    row = prices.load(p).row(AV1)
    check("off a terminal, no gate: pending again, the confirmation cleared",
          out["status"] == "pending" and row.status == "pending"
          and not row.confirmed_by and row.value == 0.45
          and out["message"].code == "prices_lower_done", (out, row))
    prices.lower(p, AV1, status="retired", reason="no longer used")
    check("retired is not found", prices.lookup(prices.load(p), AV1) is None)
    check("raising trust this way is refused",
          code_of(lambda: prices.lower(p, AV1, status="confirmed",
                                       reason="r")) == "prices_status_invalid")
    check("a reason is still required",
          code_of(lambda: prices.lower(p, AV1, status="pending", reason=""))
          == "reason_required")
    check("each change is logged", [r["action"] for r in log(p)]
          == ["confirm", "lower", "lower"], log(p))


def test_closure() -> None:
    print("\n[9] prices.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "prices.tsv"}
    check("the fragment holds the prices_* codes",
          own and all(c.startswith("prices_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["prices.py"], registry=own, strict_kit=True)
    check("every msg() in prices.py is registered with exact params, and "
          "every prices.tsv code is emitted", problems == [], problems)


if __name__ == "__main__":
    test_reading()
    test_retired_and_missing()
    test_pending_flagged()
    test_proposals()
    test_confirm_needs_a_person()
    test_relay_code()
    test_changed_meanwhile()
    test_lower()
    test_closure()
    raise SystemExit(finish())
