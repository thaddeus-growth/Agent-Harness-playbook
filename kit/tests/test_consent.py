#!/usr/bin/env python3
"""Third-party consent (kit/consent.py) holds each of its guards, on the fake
"shop" harness.

  1. A draft passes nothing; a filled record with a hand-set status is
     still unconfirmed (the incident this module paid for).
  2. confirm needs a person: no terminal and no secret is refused, a wrong
     retype is refused, nothing is written either way; the exact retype
     confirms, confirmed_by is derived (@tty), a log row is appended, and
     problems() is then [].
  3. An incomplete record is refused before the gate (no prompt, the
     terminal's answer is not consumed), each gap coded; a missing or
     unreadable record, an unreadable log.
  4. Any edit after the confirmation invalidates it; a `_` note and the
     confirmation fields do not.
  5. Each condition blocks alone: revoked, not in force (the one clock),
     uses, scope ("*" allows any), document changed, document missing or
     outside the root.
  6. revoke needs no gate but a reason, logs, and voids every earlier
     confirmation (un-revoking by hand does not bring it back).
  7. The relayed code route: a code is issued off a TTY with the secret
     set, confirms once with its audit (@relay), refuses a reuse and a
     code issued before an edit.
  8. The record moving during the gate is refused.
  9. consent.py's msg() calls are closed over its fragment.
"""

import contextlib
import datetime
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import consent, dates, human, messages  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, clean_env, finish, tmp_dir  # noqa: E402

CFG = _shop.use()
os.environ.pop("KIT_TTY", None)
SECRET = CFG.env("CONFIRM_CODE_SECRET")
TERMS = consent.Terms(relations=("staff", "owner", "external"),
                      uses=("face", "voice", "name", "words"),
                      scope_keys=("brands", "channels"))
RID = "ana"
REC = "people/ana.json"
NEED = {"need_uses": {"face"}, "need_scope": {"brands": "acme"}}


class _NoTty(io.StringIO):
    def isatty(self) -> bool:
        return False


def gate(fn, *, answers=None, secret=False):
    """Run fn() off a TTY (or with a person typing `answers` at the KIT_TTY
    seam): (result or None, exception or None, stderr, answers left)."""
    env = clean_env(**({SECRET: "s3cret"} if secret else {}))
    tty = None
    if answers is not None:
        tty = Path(tmp_dir("tty-")) / "tty.txt"
        tty.write_text("".join(a + "\n" for a in answers), encoding="utf-8")
        env["KIT_TTY"] = str(tty)
    saved_env, saved_in = dict(os.environ), sys.stdin
    os.environ.clear()
    os.environ.update(env)
    sys.stdin, err = _NoTty(""), io.StringIO()
    res = exc = None
    try:
        with contextlib.redirect_stderr(err):
            try:
                res = fn()
            except Exception as e:      # noqa: BLE001 — the refusal is data
                exc = e
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.stdin = saved_in
    left = tty.read_text(encoding="utf-8").splitlines() if tty else None
    return res, exc, err.getvalue(), left


def code(e) -> str | None:
    m = getattr(e, "message", None) if e is not None else None
    return getattr(m, "code", None)


def codes(probs) -> list[str]:
    return [getattr(p, "code", None) for p in probs]


def root(**over) -> Path:
    """A harness root with a signed document and a filled, unconfirmed
    record (`over` on top)."""
    r = Path(tmp_dir("consent-"))
    (r / "people").mkdir()
    doc = r / "people" / "ana_signed.pdf"
    doc.write_bytes(b"%PDF signed")
    rec = consent.draft(RID, terms=TERMS, document="people/ana_signed.pdf")
    rec.update({"person": "Ana Example", "relation": "staff",
                "uses": ["face", "voice"],
                "scope": {"brands": ["acme"], "channels": ["*"]},
                "document_sha256": hashlib.sha256(b"%PDF signed").hexdigest(),
                **over})
    write(r, rec)
    return r


def read(r: Path) -> dict:
    return json.loads((r / REC).read_text(encoding="utf-8"))


def write(r: Path, rec: dict) -> None:
    (r / REC).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def edit(r: Path, **over) -> None:
    write(r, {**read(r), **over})


def probs(r: Path, **need) -> list:
    return consent.problems(r, REC, terms=TERMS, **({**NEED, **need}))


def do_confirm(r: Path, **kw):
    return lambda: consent.confirm(r, REC, terms=TERMS, record_id=RID,
                                   reason=kw.pop("reason", "letter signed"),
                                   **kw)


def signed(**over) -> Path:
    r = root(**over)
    res, exc, _, _ = gate(do_confirm(r), answers=[RID])
    assert exc is None, exc
    return r


def log(r: Path) -> list[dict]:
    return consent.log_rows(r / REC)


# ---- 1 ---------------------------------------------------------------------

def test_draft_and_hand_set_status() -> None:
    print("[1] a draft passes nothing; a hand-set status never counts")
    d = consent.draft(RID, terms=TERMS)
    check("the draft is blank and pending, every scope dimension empty",
          d["person"] == "" and d["uses"] == [] and d["status"] == "pending"
          and d["scope"] == {"brands": [], "channels": []}
          and d["valid_from"] == dates.host_today().isoformat(), d)
    r = Path(tmp_dir("consent-draft-"))
    (r / "people").mkdir()
    write(r, d)
    got = codes(probs(r))
    check("a draft is blocked for every reason it is blank",
          {"consent_unconfirmed", "consent_person_blank",
           "consent_relation_unknown", "consent_uses_invalid",
           "consent_scope_invalid", "consent_document_missing"} <= set(got),
          got)
    r = root(status="confirmed", confirmed_by="owner@tty",
             confirmed_at="2026-01-01T00:00:00", confirm_reason="owner said so")
    check("a filled record with status=confirmed set by hand is unconfirmed",
          codes(probs(r)) == ["consent_unconfirmed"], codes(probs(r)))
    check("…and nothing is in its log", log(r) == [])
    with mock.patch.object(dates, "now", lambda: datetime.datetime(
            2028, 2, 29, 12, tzinfo=datetime.timezone.utc)):
        d = consent.draft(RID, terms=TERMS)
    check("a draft on 29 February is valid to 28 February next year",
          d["valid_until"] == "2029-02-28", d["valid_until"])
    bad = []
    for kw in ({"relations": (), "uses": ("face",)},
               {"relations": ("staff",), "uses": ("*",)},
               {"relations": ("staff",), "uses": ("face",),
                "scope_keys": (" ",)}):
        try:
            consent.Terms(**kw)
            bad.append(kw)
        except ValueError:
            pass
    check("Terms refuses an empty set, '*' and a blank word", not bad, bad)


# ---- 2 ---------------------------------------------------------------------

def test_confirm_needs_a_person() -> None:
    print("[2] confirm needs a person and the exact retype")
    r = root()
    before = (r / REC).read_bytes()
    _, exc, _, _ = gate(do_confirm(r))
    check("no terminal, no secret: refused confirm_needs_human",
          isinstance(exc, human.Refused)
          and code(exc) == "confirm_needs_human", repr(exc))
    check("…nothing written", (r / REC).read_bytes() == before
          and not consent.log_path(r / REC).exists())
    _, exc, _, _ = gate(do_confirm(r), answers=["Ana Example"])
    check("a wrong retype is refused confirm_typed_mismatch",
          code(exc) == "confirm_typed_mismatch", repr(exc))
    check("…nothing written", (r / REC).read_bytes() == before
          and log(r) == [])
    _, exc, _, _ = gate(do_confirm(r, reason="  "), answers=[RID])
    check("a blank reason is refused before the gate (reason_required)",
          code(exc) == "reason_required" and log(r) == [], repr(exc))
    res, exc, err, left = gate(do_confirm(r), answers=[RID])
    check("the exact retype confirms", exc is None
          and res["status"] == "confirmed", repr(exc))
    check("the person saw the summary at the prompt",
          "Ana Example (staff)" in err and "face + voice" in err, err)
    rec = read(r)
    check("confirmed_by is derived, never typed (@tty)",
          rec["confirmed_by"] == human.changed_by("tty")
          and rec["status"] == "confirmed"
          and rec["confirm_reason"] == "letter signed", rec)
    rows = log(r)
    check("one confirm row bound to the content sha",
          len(rows) == 1 and rows[0]["action"] == "confirm"
          and rows[0]["content_sha"] == consent.content_sha(rec)
          and rows[0]["changed_by"] == rec["confirmed_by"], rows)
    check("the log sits next to the record: people/ana.log.jsonl",
          consent.log_path(r / REC) == r / "people" / "ana.log.jsonl")
    check("problems() is [] for a use it covers", probs(r) == [], probs(r))
    check("the note is coded consent_confirm_done",
          res["message"].code == "consent_confirm_done")


# ---- 3 ---------------------------------------------------------------------

def test_incomplete_before_the_gate() -> None:
    print("[3] an incomplete record is refused before the gate")
    cases = {
        "consent_person_blank": {"person": " "},
        "consent_relation_unknown": {"relation": "friend"},
        "consent_uses_invalid": {"uses": ["face", "smell"]},
        "consent_scope_invalid": {"scope": {"brands": ["acme"]}},
        "consent_dates_invalid": {"valid_from": "2026-13-01"},
        "consent_revoked": {"revoked": True},
        "consent_not_in_force": {"valid_from": "2000-01-01",
                                 "valid_until": "2000-12-31"},
        "consent_document_changed": {"document_sha256": "0" * 64},
        "consent_document_missing": {"document": "../outside.pdf"},
    }
    for want, over in cases.items():
        r = root(**over)
        _, exc, err, left = gate(do_confirm(r), answers=[RID])
        inner = ([m.code for m in exc.message.params["detail"].params["parts"]]
                 if code(exc) == "consent_incomplete" else [])
        check(f"{want}: consent_incomplete names it, no prompt, the answer "
              f"is not consumed, nothing written",
              code(exc) == "consent_incomplete" and want in inner
              and err == "" and left == [RID] and log(r) == []
              and read(r)["status"] == "pending", (repr(exc), inner, err))
    r = root(scope={"brands": ["acme"], "channels": ["*"], "moods": ["x"]})
    check("an undeclared scope dimension is invalid",
          "consent_scope_invalid" in codes(probs(r)))
    r = root(valid_from=(dates.host_today() + datetime.timedelta(days=9))
             .isoformat())
    _, exc, _, _ = gate(do_confirm(r), answers=[RID])
    check("a consent starting later may be confirmed ahead of time…",
          exc is None, repr(exc))
    check("…but does not clear a use until it starts",
          codes(probs(r)) == ["consent_not_in_force"], codes(probs(r)))
    r = Path(tmp_dir("consent-none-"))
    check("no record: consent_missing",
          codes(probs(r)) == ["consent_missing"])
    _, exc, _, _ = gate(do_confirm(r), answers=[RID])
    check("…and confirm refuses it", code(exc) == "consent_missing")
    (r / "people").mkdir()
    (r / REC).write_text("[1, 2]", encoding="utf-8")
    check("a record that is not a JSON object: consent_unreadable",
          codes(probs(r)) == ["consent_unreadable"])
    r = signed()
    with open(consent.log_path(r / REC), "a", encoding="utf-8") as f:
        f.write("{torn\n")
    check("a log row that does not parse blocks: consent_log_unreadable",
          codes(probs(r)) == ["consent_log_unreadable"], codes(probs(r)))
    res, exc, _, _ = gate(lambda: consent.revoke(r, REC, record_id=RID,
                                                 reason="asked"))
    text = consent.log_path(r / REC).read_text(encoding="utf-8")
    check("…revoke still works and keeps the log's lines",
          exc is None and read(r)["revoked"] is True and "{torn" in text
          and text.rstrip().endswith("}"), (repr(exc), text))


# ---- 4 ---------------------------------------------------------------------

def test_edit_after_confirm() -> None:
    print("[4] an edit after the confirmation needs a new one")
    for over in ({"person": "Ana Other"}, {"uses": ["face", "voice", "name"]},
                 {"scope": {"brands": ["acme", "beta"], "channels": ["*"]}},
                 {"valid_until": "2099-12-31"}, {"relation": "owner"}):
        r = signed()
        edit(r, **over)
        check(f"edit {sorted(over)}: consent_unconfirmed",
              "consent_unconfirmed" in codes(probs(r)), codes(probs(r)))
    r = signed()
    edit(r, _note="scanned twice", status="whatever", confirm_reason="x")
    check("a `_` note and the confirmation fields are outside the sha",
          probs(r) == [], probs(r))
    r = signed()
    edit(r, person="Ana Other")
    _, exc, _, _ = gate(do_confirm(r), answers=[RID])
    check("a new confirmation clears the edited record",
          exc is None and probs(r) == [] and len(log(r)) == 2, repr(exc))


# ---- 5 ---------------------------------------------------------------------

def test_each_condition_blocks() -> None:
    print("[5] each condition blocks alone")
    r = signed()
    edit(r, revoked=True)
    check("revoked by hand: consent_revoked (and the content moved)",
          {"consent_revoked", "consent_unconfirmed"} == set(codes(probs(r))))
    r = signed()
    late = datetime.datetime.combine(
        datetime.date.fromisoformat(read(r)["valid_until"])
        + datetime.timedelta(days=2), datetime.time(12),
        datetime.timezone.utc)
    with mock.patch.object(dates, "now", lambda: late):
        got = codes(probs(r))
    check("past valid_until on the one clock: consent_not_in_force only",
          got == ["consent_not_in_force"], got)
    check("uses: a use it does not cover is consent_uses_uncovered only",
          codes(probs(r, need_uses={"face", "words"}))
          == ["consent_uses_uncovered"])
    got = probs(r, need_scope={"brands": "beta"})
    check("scope: another brand is consent_scope_uncovered only",
          codes(got) == ["consent_scope_uncovered"]
          and got[0].params["key"] == "brands", got)
    check("scope: '*' allows any channel",
          probs(r, need_scope={"brands": "acme", "channels": "any"}) == [])
    check("scope: a dimension the record does not name blocks",
          codes(probs(r, need_scope={"purposes": "ads"}))
          == ["consent_scope_uncovered"])
    (r / "people" / "ana_signed.pdf").write_bytes(b"%PDF other")
    check("document changed on disk: consent_document_changed only",
          codes(probs(r)) == ["consent_document_changed"], codes(probs(r)))
    (r / "people" / "ana_signed.pdf").unlink()
    check("document gone: consent_document_missing only",
          codes(probs(r)) == ["consent_document_missing"], codes(probs(r)))


# ---- 6 ---------------------------------------------------------------------

def test_revoke() -> None:
    print("[6] revoke needs no gate, needs a reason, voids the past")
    r = signed()
    _, exc, _, _ = gate(lambda: consent.revoke(r, REC, record_id=RID,
                                               reason=""))
    check("a blank reason is refused (reason_required), nothing written",
          code(exc) == "reason_required" and len(log(r)) == 1)
    res, exc, _, _ = gate(lambda: consent.revoke(r, REC, record_id=RID,
                                                 reason="the person asked"))
    rows = log(r)
    check("off a TTY, no secret: revoked, @cli, a revoke row",
          exc is None and res["status"] == "revoked"
          and res["message"].code == "consent_revoke_done"
          and rows[-1]["action"] == "revoke"
          and rows[-1]["changed_by"] == human.changed_by("cli"), repr(exc))
    check("the record says revoked and is blocked",
          read(r)["revoked"] is True
          and "consent_revoked" in codes(probs(r)))
    edit(r, revoked=False, status="confirmed")
    check("un-revoked by hand (the old content sha): still unconfirmed",
          codes(probs(r)) == ["consent_unconfirmed"], codes(probs(r)))
    check("confirmed_shas is empty after the revoke",
          consent.confirmed_shas(r / REC) == set())
    _, exc, _, _ = gate(do_confirm(r), answers=[RID])
    check("a person confirming it again clears it",
          exc is None and probs(r) == [], repr(exc))


# ---- 7 ---------------------------------------------------------------------

def test_relay_code() -> None:
    print("[7] the relayed code route")
    r = root()
    _, exc, _, _ = gate(do_confirm(r), secret=True)
    check("off a TTY with the secret: a code is issued, nothing written",
          isinstance(exc, human.CodeRequired) and exc.confirm_code
          and exc.subject["items"] == {RID: consent.content_sha(read(r))}
          and exc.subject["version"] == 0 and log(r) == [], repr(exc))
    c = exc.confirm_code
    _, exc, _, _ = gate(do_confirm(r, code=c), secret=True)
    check("the code without its audit is refused",
          code(exc) == "confirm_relay_audit_missing" and log(r) == [],
          repr(exc))
    audit = {"relay_user": "web:owner", "relay_at": "2026-09-29T10:00:00+08:00"}
    res, exc, _, _ = gate(do_confirm(r, code=c, **audit), secret=True)
    check("the code with its audit confirms, @relay, audit in the reason",
          exc is None and res["confirmed_by"] == human.changed_by("relay")
          and "[relay user=web:owner" in read(r)["confirm_reason"]
          and probs(r) == [], repr(exc))
    _, exc, _, _ = gate(do_confirm(r, code=c, **audit), secret=True)
    check("the same code twice is refused (the log moved)",
          code(exc) == "confirm_code_mismatch" and len(log(r)) == 1)
    r = root()
    _, exc, _, _ = gate(do_confirm(r), secret=True)
    c = exc.confirm_code
    edit(r, uses=["face", "voice", "name"])
    _, exc, _, _ = gate(do_confirm(r, code=c, **audit), secret=True)
    check("a code issued before an edit is refused",
          code(exc) == "confirm_code_mismatch" and log(r) == [], repr(exc))


# ---- 8 ---------------------------------------------------------------------

def test_changed_meanwhile() -> None:
    print("[8] the record moving during the gate is refused")
    r = root()
    real = human.confirm

    def moving(*a, **kw):
        channel = real(*a, **kw)
        edit(r, person="Someone Else")
        return channel
    with mock.patch.object(human, "confirm", moving):
        _, exc, _, _ = gate(do_confirm(r), answers=[RID])
    check("consent_changed_meanwhile, nothing written",
          code(exc) == "consent_changed_meanwhile" and log(r) == []
          and read(r)["status"] == "pending", repr(exc))


# ---- 9 ---------------------------------------------------------------------

def test_closure() -> None:
    print("[9] consent.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "consent.tsv"}
    check("the fragment holds the consent_* codes",
          own and all(c.startswith("consent_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["consent.py"], registry=own, strict_kit=True)
    check("every msg() in consent.py is registered with exact params, and "
          "every consent.tsv code is emitted", problems == [], problems)


if __name__ == "__main__":
    test_draft_and_hand_set_status()
    test_confirm_needs_a_person()
    test_incomplete_before_the_gate()
    test_edit_after_confirm()
    test_each_condition_blocks()
    test_revoke()
    test_relay_code()
    test_changed_meanwhile()
    test_closure()
    raise SystemExit(finish())
