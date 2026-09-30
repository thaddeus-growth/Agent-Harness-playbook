#!/usr/bin/env python3
"""Claim scope (kit/claimscope.py) holds each of its guards, in two
domains: an OTC throat product whose approved indication reads
急慢性咽炎引起的咽干、咽痛 (a harness with its own kind words, mapped), and
an investment fund whose prospectus allows capital growth and
diversification (the default kinds).

  1. The lexicon: kinds mapped, retired rows dropped, every broken row
     refused (bad regex, unknown kind, severity, status, blank or repeated
     id, blank pattern); read from a TSV.
  2. matches(): the longest hit wins, two hits of one row side by side
     merge.
  3. check(): outside the scope is a finding, inside is not, `always` rows
     fire whatever the scope, without a record only they count; the
     finding's shape; the approved wording passes (no false alarm).
  4. Draft vs confirmed: a pending row or an unconfirmed record says
     "draft, not advice" (confirmed False, *_draft code).
  5. The record counts only through the gate: a hand-set status is a
     draft; no terminal and no secret is refused, a wrong retype is
     refused, the exact retype of the subject confirms; any edit
     invalidates it (a `_` note does not); an incomplete, missing or
     unreadable record is refused before the gate.
  6. The relayed code works once, and not after an edit; the record moving
     during the gate is refused.
  7. Waivers: only a person, retyping the words; bound to rule, place and
     words, so a changed line no longer matches; open_errors; an
     unreadable waivers file is refused; required().
  8. The learning loop: quoted() skips the reviewer's paraphrase; recall
     is 100% on the quoted-reason fixture in both domains, and a missing
     lexicon row shows up as a miss.
  9. claimscope.py's msg() calls are closed over its fragment.
"""

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import claimscope, human, messages  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, clean_env, finish, raises, tmp_dir  # noqa: E402

CFG = _shop.use()
os.environ.pop("KIT_TTY", None)
SECRET = CFG.env("CONFIRM_CODE_SECRET")

# ---- domain 1: an OTC throat product (the harness's own kind words) --------
THROAT_KINDS = {"symptom": "claim", "condition": "claim",
                "widener": "always", "scene": "always"}
THROAT_ROWS = [
    ("C001", "symptom", "咽干+咽痒", "(嗓子|咽喉|喉咙|咽)干痒", "error"),
    ("C002", "symptom", "咽干", "咽干|(嗓子|喉咙|咽喉)(发)?干(?![痒燥])",
     "error"),
    ("C003", "symptom", "咽痒", "咽痒|(嗓子|喉咙|咽喉)(发)?痒", "error"),
    ("C004", "symptom", "咽痛", "咽痛|(嗓子|喉咙|咽喉)(疼痛|疼|痛)", "error"),
    ("C005", "symptom", "咽喉灼热刺痛", "灼热|刺痛|灼痛", "error"),
    ("C006", "symptom", "痰多痰黏", "痰多|痰黏|痰点|有痰", "error"),
    ("C007", "symptom", "咽喉异物感", "异物感|有东西卡|咳不出咽不下",
     "error"),
    ("C008", "symptom", "声音嘶哑", "嘶哑|沙哑", "error"),
    ("C009", "symptom", "咳嗽", "咳嗽|干咳|咳个不停", "error"),
    ("C010", "condition", "咽喉炎", "咽喉炎|喉炎", "error"),
    ("C011", "condition", "咽炎", "急慢性咽炎|急性咽炎|慢性咽炎|咽炎",
     "error"),
    ("C012", "symptom", "吞咽困难", "吞咽困难", "error"),
    ("A001", "widener", "widens the scope: every complaint",
     "各种.{0,8}(问题|不适)", "error"),
    ("A002", "widener", "implies a targeted treatment",
     "专门针对|专业调理|专治", "error"),
    ("A003", "scene", "widens who and when", "熬夜|用嗓多|老师|主播",
     "error"),
    ("A099", "widener", "an old rule", "(unclosed", "error"),
]
THROAT_RECORD = {"subject": "本品", "category": "otc_drug",
                 "scope_text": "急慢性咽炎引起的咽干、咽痛",
                 "allowed": ["咽干", "咽痛", "咽炎"],
                 "source": "package insert, photo of the printed leaflet"}
THROAT_APPROVED = ["本品用于急慢性咽炎引起的咽干、咽痛",
                   "嗓子干、嗓子痛，请按说明书服用",
                   "清热利咽，用于急性咽炎"]
# rejection reasons as a reviewer writes them (product name replaced)
THROAT_REASONS = [
    "广告音频中存在“专业调理咽炎”“专门针对咽干咽痒、痰多痰黏的、喉咙有异物感、"
    "吞咽困难等各种喉咙不适”，宣传内容超出了产品说明书标注的适用病症范围，因此不通过。",
    "广告字幕中存在“咳嗽停不下来的，嗓子痛，声音嘶哑的，咳不出咽不下的”，"
    "宣传的可改善病症超出了说明书范围，因此不通过。",
    "广告音频中存在“本品可用于早起嗓子干痒、说话沙哑、咽喉灼热刺痛”，因此不通过。",
    "广告中“熬夜用嗓多的老师主播都在用”，扩大了适用人群，因此不通过。",
    "视频中存在“宣传病症超出说明书范围”的问题，因此不通过。",
    "画面中存在“喉咙有痰、咽喉炎反复”，因此不通过。",
    "广告内容存在夸大宣传，因此不通过。",
]
MARKERS = ("说明书", "超出")

# ---- domain 2: an investment fund (the default kinds) ----------------------
FUND_ROWS = [
    ("F001", "claim", "capital growth",
     "(?i)capital growth|grow your (money|savings)", "error"),
    ("F002", "claim", "diversification", r"(?i)diversif\w+", "error"),
    ("F003", "claim", "income",
     "(?i)(monthly|steady|regular) income|pays? out every month", "error"),
    ("F004", "claim", "capital protection",
     "(?i)protects? your (capital|savings)|never lose", "error"),
    ("F005", "always", "a guarantee",
     "(?i)guaranteed|risk[- ]free|can't lose", "error"),
]
FUND_RECORD = {"subject": "Acme Balanced Fund", "category": "fund",
               "scope_text": "The fund seeks long-term capital growth "
                             "through a diversified portfolio.",
               "allowed": ["capital growth", "diversification"],
               "source": "prospectus, section 2"}
FUND_REASONS = [
    "The ad says “risk-free returns” and “never lose a cent”; both are "
    "claims the prospectus does not make.",
    "The voice-over promises “a steady income you can count on”.",
]


def rows_of(table, status="pending", drop=("A099",)):
    return [{"id": i, "kind": k, "canonical": c, "pattern": p,
             "severity": s, "note": "", "status": status}
            for i, k, c, p, s in table if i not in drop]


def throat(status="pending"):
    return claimscope.lexicon(rows_of(THROAT_ROWS, status),
                              kinds=THROAT_KINDS)


def fund(status="pending"):
    return claimscope.lexicon(rows_of(FUND_ROWS, status))


def rules(findings) -> set:
    return {f["rule"] for f in findings}


def errs(text, record, rows):
    return claimscope.errors(claimscope.check([("t", text)], record, rows))


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


def record_file(rec: dict, name="claims.json") -> Path:
    f = Path(tmp_dir("claims-")) / name
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return f


def read(f: Path) -> dict:
    return json.loads(f.read_text(encoding="utf-8"))


def edit(f: Path, **over) -> None:
    f.write_text(json.dumps({**read(f), **over}, ensure_ascii=False),
                 encoding="utf-8")


def do_confirm(f: Path, **kw):
    return lambda: claimscope.confirm(f, reason=kw.pop("reason", "read the "
                                                       "leaflet"), **kw)


def confirmed_file(rec: dict) -> Path:
    f = record_file(rec)
    _, exc, _, _ = gate(do_confirm(f), answers=[rec["subject"]])
    assert exc is None, exc
    return f


# ---- 1 ---------------------------------------------------------------------

def test_lexicon() -> None:
    print("[1] the lexicon: kinds mapped, retired dropped, broken refused")
    rs = throat()
    c1 = next(r for r in rs if r["id"] == "C001")
    a3 = next(r for r in rs if r["id"] == "A003")
    check("the harness's kind words map to claim / always, the word kept",
          c1["kind"] == "claim" and c1["label"] == "symptom"
          and a3["kind"] == "always" and a3["label"] == "scene"
          and c1["names"] == ["咽干", "咽痒"], (c1, a3))
    check("the default maps claim and always to themselves",
          {r["kind"] for r in fund()} == {"claim", "always"})
    bad_regex = rows_of(THROAT_ROWS, drop=())
    e = raises(lambda: claimscope.lexicon(bad_regex, kinds=THROAT_KINDS),
               HarnessError)
    check("a bad regex is refused, never skipped (claimscope_row_invalid)",
          code(e) == "claimscope_row_invalid"
          and e.message.params["rule"] == "A099", repr(e))
    retired = [{**r, "status": "retired"} if r["id"] == "A099" else r
               for r in bad_regex]
    live = claimscope.lexicon(retired, kinds=THROAT_KINDS)
    check("a retired row is dropped before it is checked",
          "A099" not in {r["id"] for r in live} and len(live) == 15)
    base = {"id": "X1", "kind": "claim", "canonical": "a", "pattern": "a",
            "severity": "error", "status": ""}
    cases = {"unknown kind": {"kind": "symptom"},
             "severity": {"severity": "fatal"},
             "status": {"status": "approved"},
             "blank id": {"id": " "},
             "blank pattern": {"pattern": ""},
             "blank canonical": {"canonical": " + "}}
    for label, over in cases.items():
        e = raises(lambda: claimscope.lexicon([{**base, **over}]),
                   HarnessError)
        check(f"{label}: refused claimscope_row_invalid",
              code(e) == "claimscope_row_invalid", repr(e))
    e = raises(lambda: claimscope.lexicon([base, base]), HarnessError)
    check("an id listed twice is refused", code(e) == "claimscope_row_invalid")
    check("a blank status reads as pending",
          claimscope.lexicon([base])[0]["status"] == "pending")
    check("a kinds mapping to anything but claim / always is a ValueError",
          isinstance(raises(lambda: claimscope.lexicon(
              [base], kinds={"claim": "banned"})), ValueError))
    tsv = Path(tmp_dir("lex-")) / "claim_terms.tsv"
    head = ("id", "kind", "canonical", "pattern", "severity", "note",
            "law_ref", "status")
    tsv.write_text("\t".join(head) + "\n" + "\n".join(
        "\t".join((i, k, c, p, s, "", "clause 16", "pending"))
        for i, k, c, p, s in FUND_ROWS) + "\n", encoding="utf-8")
    got = claimscope.load_lexicon(tsv)
    f = claimscope.check([("vo", "Steady monthly income.")], FUND_RECORD, got)
    check("load_lexicon reads a TSV; law_ref becomes the finding's law",
          len(got) == 5 and f and f[0]["law"] == "clause 16", f)


# ---- 2 ---------------------------------------------------------------------

def test_matches() -> None:
    print("[2] matches(): the longest wins, adjacent hits of a row merge")
    rs = throat()
    m = claimscope.matches("嗓子干痒", rs)
    check("嗓子干痒 is one hit of C001 (咽干+咽痒), not C002 (咽干)",
          [x["row"]["id"] for x in m] == ["C001"], m)
    f = errs("嗓子干痒", THROAT_RECORD, rs)
    check("…and only the name outside the scope is the finding (咽痒)",
          len(f) == 1 and f[0]["names"] == ["咽痒"], f)
    m = claimscope.matches("咽喉灼热刺痛", rs)
    check("灼热 and 刺痛 side by side are one hit 灼热刺痛",
          [(x["row"]["id"], x["text"]) for x in m] == [("C005", "灼热刺痛")],
          m)
    check("咽喉炎 is not 咽炎 (the longer condition wins)",
          errs("咽喉炎", THROAT_RECORD, rs)[0]["names"] == ["咽喉炎"])
    m = claimscope.matches("咳嗽。然后咳嗽", rs)
    check("two hits of one row far apart stay two", len(m) == 2, m)


# ---- 3 ---------------------------------------------------------------------

def test_check() -> None:
    print("[3] check(): outside is a finding, inside is not; always rows")
    rs = throat()
    fs = claimscope.check([("scene 1", "咳嗽停不下来", 2.5)], THROAT_RECORD, rs)
    check("a finding has the copylint shape plus names, kind, label, t",
          len(fs) == 1 and set(fs[0]) >= {"severity", "rule", "where", "term",
                                          "confirmed", "law", "message",
                                          "names", "kind", "label", "t"}
          and fs[0]["rule"] == "C009" and fs[0]["t"] == 2.5
          and fs[0]["where"] == "scene 1" and fs[0]["term"] == "咳嗽", fs)
    for ok in THROAT_APPROVED:
        check(f"the approved wording passes: {ok}",
              errs(ok, THROAT_RECORD, rs) == [], errs(ok, THROAT_RECORD, rs))
    fa = claimscope.false_alarms(
        THROAT_APPROVED, lambda t: claimscope.check([("t", t)],
                                                    THROAT_RECORD, rs))
    check("false_alarms() on approved copy: 0 of 3",
          fa == {"checked": 3, "flagged": 0, "alarms": []}, fa)
    fa = claimscope.false_alarms(
        ["嗓子痛"], lambda t: claimscope.check([("t", t)],
                                             {**THROAT_RECORD,
                                              "allowed": ["咽干"]}, rs))
    check("…and names the rule of an alarm",
          fa["flagged"] == 1 and fa["alarms"][0]["rules"] == ["C004"], fa)
    check("without a record, claims are not findings",
          errs("咽痒咳嗽", None, rs) == [])
    got = claimscope.check([("t", "各种喉咙不适，熬夜用嗓多")], None, rs)
    check("…but always rows are (A001, A003; 熬夜用嗓多 merged into one)",
          rules(got) == {"A001", "A003"} and len(got) == 2
          and any(f["term"] == "熬夜用嗓多" for f in got), got)
    check("an always row fires inside the scope too",
          rules(errs("专门针对咽干", THROAT_RECORD, rs)) == {"A002"})
    fr = fund()
    check("fund: the prospectus wording passes",
          errs("Grow your savings through a diversified portfolio.",
               FUND_RECORD, fr) == [])
    got = errs("Steady monthly income, guaranteed.", FUND_RECORD, fr)
    check("fund: income is outside the scope, guaranteed always fires",
          rules(got) == {"F003", "F005"}
          and {f["kind"] for f in got} == {"claim", "always"}, got)
    warn = claimscope.lexicon([{"id": "W1", "kind": "always",
                                "canonical": "hype", "pattern": "amazing",
                                "severity": "warn", "status": ""}])
    got = claimscope.check([("t", "an amazing fund")], None, warn)
    check("a warn row is a finding but not an error",
          len(got) == 1 and claimscope.errors(got) == [], got)


# ---- 4 ---------------------------------------------------------------------

def test_draft_and_confirmed() -> None:
    print("[4] a pending row or an unconfirmed record says draft")
    f = errs("声音嘶哑", THROAT_RECORD, throat())[0]
    check("pending row, unconfirmed record: confirmed False, draft code, "
          "'draft, not advice'",
          f["confirmed"] is False and f["message"].code
          == "claimscope_outside_draft" and "draft, not advice"
          in f["message"], f)
    rec = read(confirmed_file(THROAT_RECORD))
    f = errs("声音嘶哑", rec, throat("confirmed"))[0]
    check("confirmed row and record: confirmed True, claimscope_outside",
          f["confirmed"] is True and f["message"].code == "claimscope_outside"
          and "draft" not in f["message"], f)
    f = errs("声音嘶哑", rec, throat())[0]
    check("a confirmed record does not confirm a pending row",
          f["confirmed"] is False, f)
    hand = {**THROAT_RECORD, "status": "confirmed", "confirmed_by": "x@tty",
            "confirmed_at": "2026-01-01"}
    f = errs("声音嘶哑", hand, throat("confirmed"))[0]
    check("a hand-set record makes a confirmed row's finding a draft",
          f["confirmed"] is False, f)
    f = claimscope.check([("t", "risk-free")], None, fund())[0]
    check("an always row: draft code while pending",
          f["message"].code == "claimscope_always_draft"
          and "draft, not advice" in f["message"], f)
    f = claimscope.check([("t", "risk-free")], None, fund("confirmed"))[0]
    check("…claimscope_always once confirmed, with no record needed",
          f["confirmed"] is True and f["message"].code == "claimscope_always",
          f)


# ---- 5 ---------------------------------------------------------------------

def test_record_through_the_gate() -> None:
    print("[5] the record counts only through the gate")
    hand = {**FUND_RECORD, "status": "confirmed", "confirmed_by": "owner@tty",
            "confirmed_at": "2026-09-30T10:00:00"}
    check("a hand-set status is not confirmed",
          claimscope.confirmed(hand) is False)
    hand["content_sha"] = "0" * 64
    check("…nor with a made-up content_sha", not claimscope.confirmed(hand))
    f = record_file(FUND_RECORD)
    before = f.read_bytes()
    _, exc, _, _ = gate(do_confirm(f))
    check("no terminal, no secret: confirm_needs_human, nothing written",
          code(exc) == "confirm_needs_human" and f.read_bytes() == before,
          repr(exc))
    _, exc, _, _ = gate(do_confirm(f), answers=["Acme Balanced"])
    check("a wrong retype: confirm_typed_mismatch, nothing written",
          code(exc) == "confirm_typed_mismatch" and f.read_bytes() == before,
          repr(exc))
    _, exc, _, _ = gate(do_confirm(f, reason=" "), answers=[])
    check("a blank reason is refused (reason_required)",
          code(exc) == "reason_required" and f.read_bytes() == before)
    res, exc, err, _ = gate(do_confirm(f), answers=["Acme Balanced Fund"])
    rec = read(f)
    check("the exact retype of the subject confirms",
          exc is None and res["status"] == "confirmed"
          and claimscope.confirmed(rec)
          and res["message"].code == "claimscope_confirm_done", repr(exc))
    check("the person saw allowed, scope_text and source",
          "capital growth, diversification" in err
          and "long-term capital growth" in err and "prospectus" in err, err)
    check("confirmed_by is derived (@tty), the reason kept",
          rec["confirmed_by"] == human.changed_by("tty")
          and rec["confirm_reason"] == "read the leaflet"
          and rec["content_sha"] == claimscope.content_sha(rec), rec)
    edit(f, _note="checked twice", confirm_reason="whatever")
    check("a `_` note and the confirmation fields are outside the hash",
          claimscope.confirmed(read(f)))
    for over in ({"allowed": ["capital growth", "diversification",
                              "income"]},
                 {"scope_text": "The fund seeks income."},
                 {"subject": "Acme Income Fund"}, {"source": "a blog"}):
        g = confirmed_file(FUND_RECORD)
        edit(g, **over)
        check(f"edit {sorted(over)}: no longer confirmed",
              not claimscope.confirmed(read(g)))
    g = record_file({**FUND_RECORD, "scope_text": " ", "allowed": []})
    _, exc, err, left = gate(do_confirm(g), answers=["Acme Balanced Fund"])
    check("an incomplete record is refused before the gate: no prompt, the "
          "answer not consumed",
          code(exc) == "claimscope_incomplete" and err == ""
          and left == ["Acme Balanced Fund"]
          and "scope_text" in exc.message.params["detail"], repr(exc))
    missing = Path(tmp_dir("claims-none-")) / "claims.json"
    _, exc, _, _ = gate(lambda: claimscope.confirm(missing, reason="x"),
                        answers=["x"])
    check("no record: claimscope_record_missing; load() is None",
          code(exc) == "claimscope_record_missing"
          and claimscope.load(missing) is None)
    for body in ("{torn", "[1]", '{"allowed": "咽干"}'):
        missing.write_text(body, encoding="utf-8")
        e = raises(lambda: claimscope.load(missing), HarnessError)
        check(f"{body!r}: claimscope_record_invalid",
              code(e) == "claimscope_record_invalid", repr(e))


# ---- 6 ---------------------------------------------------------------------

def test_relay_and_meanwhile() -> None:
    print("[6] the relayed code works once; the record moving is refused")
    f = record_file(THROAT_RECORD)
    _, exc, _, _ = gate(do_confirm(f), secret=True)
    check("off a TTY with the secret: a code bound to {subject: sha}",
          isinstance(exc, human.CodeRequired)
          and exc.subject["items"] == {"本品": claimscope.content_sha(
              read(f))}, repr(exc))
    c = exc.confirm_code
    audit = {"relay_user": "web:owner",
             "relay_at": "2026-09-30T10:00:00+08:00"}
    _, exc, _, _ = gate(do_confirm(f, code=c), secret=True)
    check("the code without its audit is refused",
          code(exc) == "confirm_relay_audit_missing")
    res, exc, _, _ = gate(do_confirm(f, code=c, **audit), secret=True)
    check("with its audit it confirms (@relay, audit in the reason)",
          exc is None and res["confirmed_by"] == human.changed_by("relay")
          and "[relay user=web:owner" in read(f)["confirm_reason"],
          repr(exc))
    _, exc, _, _ = gate(do_confirm(f, code=c, **audit), secret=True)
    check("the same code twice is refused (the file moved)",
          code(exc) == "confirm_code_mismatch", repr(exc))
    f = record_file(THROAT_RECORD)
    _, exc, _, _ = gate(do_confirm(f), secret=True)
    c = exc.confirm_code
    edit(f, allowed=["咽干", "咽痛", "咽炎", "咽痒"])
    _, exc, _, _ = gate(do_confirm(f, code=c, **audit), secret=True)
    check("a code issued before an edit is refused",
          code(exc) == "confirm_code_mismatch"
          and not claimscope.confirmed(read(f)), repr(exc))
    f = record_file(THROAT_RECORD)
    real = human.confirm

    def moving(*a, **kw):
        channel = real(*a, **kw)
        edit(f, allowed=["咽干", "咽痛", "咽炎", "咳嗽"])
        return channel
    with mock.patch.object(human, "confirm", moving):
        _, exc, _, _ = gate(do_confirm(f), answers=["本品"])
    check("claimscope_changed_meanwhile, nothing written",
          code(exc) == "claimscope_changed_meanwhile"
          and "status" not in read(f), repr(exc))


# ---- 7 ---------------------------------------------------------------------

def test_waivers() -> None:
    print("[7] waivers: a person, the words, bound to them")
    rs = throat()
    w = Path(tmp_dir("waivers-")) / "waivers.json"
    found = claimscope.check([("scene 2", "咳嗽停不下来")], THROAT_RECORD, rs)
    f0 = found[0]

    def do_waive(**kw):
        return lambda: claimscope.waive(
            w, rule=f0["rule"], where=f0["where"], term=f0["term"],
            reason=kw.pop("reason", "the owner accepts it"), **kw)
    _, exc, _, _ = gate(do_waive())
    check("no terminal: confirm_needs_human, nothing written",
          code(exc) == "confirm_needs_human" and not w.exists())
    _, exc, _, _ = gate(do_waive(), answers=["咳"])
    check("a wrong retype of the words is refused",
          code(exc) == "confirm_typed_mismatch" and not w.exists())
    check("before: one open error",
          len(claimscope.open_errors(found, w)[0]) == 1)
    res, exc, err, _ = gate(do_waive(), answers=["咳嗽"])
    check("the exact words waive it (@tty)",
          exc is None and res["waived_by"] == human.changed_by("tty")
          and res["message"].code == "claimscope_waive_done"
          and "咳嗽" in err, repr(exc))
    still, waived = claimscope.open_errors(found, w)
    check("open_errors: none open, one waived", not still and len(waived) == 1)
    changed = claimscope.check([("scene 2", "干咳停不下来")], THROAT_RECORD, rs)
    check("a changed line no longer matches the waiver",
          len(claimscope.open_errors(changed, w)[0]) == 1)
    moved = claimscope.check([("scene 3", "咳嗽停不下来")], THROAT_RECORD, rs)
    check("nor the same words in another place",
          len(claimscope.open_errors(moved, w)[0]) == 1)
    check("a warn finding is never an open error",
          claimscope.open_errors([{**f0, "severity": "warn"}], w) == ([], []))
    _, exc, _, _ = gate(do_waive(), secret=True)
    check("a relayed waiver code is bound to the words",
          isinstance(exc, human.CodeRequired)
          and list(exc.subject["items"].values())
          == [[f0["rule"], f0["where"], f0["term"]]], repr(exc))
    w.write_text("{torn", encoding="utf-8")
    e = raises(lambda: claimscope.open_errors(found, w), HarnessError)
    check("an unreadable waivers file is refused",
          code(e) == "claimscope_waivers_unreadable", repr(e))
    check("waive() without the finding's words is a ValueError",
          isinstance(raises(lambda: claimscope.waive(
              w, rule="C009", where="", term="咳嗽", reason="x")),
              ValueError))
    d = Path(tmp_dir("req-"))
    got = claimscope.required(d / "claims.json", where="project")
    check("required(): a missing record is one error finding",
          len(got) == 1 and got[0]["severity"] == "error"
          and got[0]["message"].code == "claimscope_record_required", got)
    (d / "claims.json").write_text("{}", encoding="utf-8")
    check("…and [] once it exists", claimscope.required(d / "claims.json")
          == [])


# ---- 8 ---------------------------------------------------------------------

def test_learning_loop() -> None:
    print("[8] every rejection is a test case: recall on quoted phrases")
    check("quoted() takes the “…” phrases",
          claimscope.quoted("存在“专治咽炎”和“熬夜”") == ["专治咽炎", "熬夜"])
    check("…skips the reviewer's paraphrase (markers) and short ones",
          claimscope.quoted("“宣传病症超出说明书范围”“x”“咳嗽”",
                            markers=MARKERS) == ["咳嗽"])
    quotes = [q for r in THROAT_REASONS
              for q in claimscope.quoted(r, markers=MARKERS)]
    check("the throat fixture quotes 6 phrases (the paraphrase and the "
          "unquoted reason skipped)", len(quotes) == 6, quotes)
    rs = throat()
    fn = (lambda t: claimscope.check([("quote", t)], THROAT_RECORD, rs))
    r = claimscope.recall(quotes, fn)
    check("throat: recall 100%", r == {"checked": 6, "caught": 6,
                                       "recall": 1.0, "misses": []}, r)
    fewer = [x for x in rs if x["id"] not in ("C009", "C010", "C006")]
    r = claimscope.recall(quotes, lambda t: claimscope.check(
        [("quote", t)], THROAT_RECORD, fewer))
    check("a missing lexicon row shows up as a miss to add",
          r["recall"] < 1 and "喉咙有痰、咽喉炎反复" in r["misses"], r)
    quotes = [q for r in FUND_REASONS for q in claimscope.quoted(r)]
    fr = fund()
    r = claimscope.recall(quotes, lambda t: claimscope.check(
        [("quote", t)], FUND_RECORD, fr))
    check("fund: recall 100% on its 3 quoted phrases",
          r["checked"] == 3 and r["recall"] == 1.0, r)
    check("nothing quoted: recall None",
          claimscope.recall([], fn)["recall"] is None)


# ---- 9 ---------------------------------------------------------------------

def test_closure() -> None:
    print("[9] claimscope.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "claimscope.tsv"}
    check("the fragment holds the claimscope_* codes",
          own and all(c.startswith("claimscope_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["claimscope.py"], registry=own, strict_kit=True)
    check("every msg() in claimscope.py is registered with exact params, and "
          "every claimscope.tsv code is emitted", problems == [], problems)


if __name__ == "__main__":
    test_lexicon()
    test_matches()
    test_check()
    test_draft_and_confirmed()
    test_record_through_the_gate()
    test_relay_and_meanwhile()
    test_waivers()
    test_learning_loop()
    test_closure()
    raise SystemExit(finish())
