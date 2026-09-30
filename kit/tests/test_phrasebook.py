#!/usr/bin/env python3
"""The phrasebook (kit/phrasebook.py) holds each of its guards, in two
domains: an OTC throat product's Chinese script lines (checked by
kit.claimscope against 急慢性咽炎引起的咽干、咽痛, with a speech-recognition
homophone fix) and an investment fund's English lines.

  1. norm(): spaces and punctuation gone, NFKC, case folded, the harness's
     `fix` applied first; clauses() splits on ，,。！？；!?; and line
     breaks, dropping short ones.
  2. add(): checked on the way in (an error = rejected, else pending),
     stored as meant (after `fix`), one row per norm whatever the
     punctuation; a blank phrase is refused; harvest() in bulk.
  3. A phrase the lexicon misses, marked rejected by a person (no gate,
     a reason), is refused next time whatever the punctuation; before
     that it was only a warning.
  4. approve() needs a person and the exact retype of the id; it is
     refused before the gate while the check finds an error; an approved
     phrase lints clean; an edited text or a hand-set status is not
     approved.
  5. The relayed code works once; the file moving during the gate is
     refused; an unknown id and a bad status are refused.
  6. lint(): a short rejected phrase is not matched inside lines; no
     phrasebook = no findings; a clause covered by a rejected hit is not
     warned twice.
  7. phrasebook.py's msg() calls are closed over its fragment.
"""

import contextlib
import io
import os
import sys
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import claimscope, human, messages, phrasebook  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, clean_env, finish, raises, tmp_dir  # noqa: E402

CFG = _shop.use()
os.environ.pop("KIT_TTY", None)
SECRET = CFG.env("CONFIRM_CODE_SECRET")

LEX = claimscope.lexicon([
    {"id": i, "kind": k, "canonical": c, "pattern": p, "severity": "error",
     "status": "pending"} for i, k, c, p in [
        ("C002", "claim", "咽干", "咽干|(嗓子|喉咙|咽喉)(发)?干"),
        ("C004", "claim", "咽痛", "咽痛|(嗓子|喉咙|咽喉)(疼痛|疼|痛)"),
        ("C009", "claim", "咳嗽", "咳嗽|干咳"),
        ("C010", "claim", "咽喉炎", "咽喉炎|喉炎"),
        ("C011", "claim", "咽炎", "急慢性咽炎|急性咽炎|慢性咽炎|咽炎"),
        ("A002", "always", "a targeted treatment", "专门针对|专治"),
        ("F003", "claim", "income", "(?i)(monthly|steady) income"),
        ("F005", "always", "a guarantee", "(?i)guaranteed|risk[- ]free")]])
RECORD = {"subject": "本品", "allowed": ["咽干", "咽痛", "咽炎",
                                       "capital growth"],
          "scope_text": "急慢性咽炎引起的咽干、咽痛"}
HOMOPHONES = {"咽喉咽": "咽喉炎"}


def fix(text: str) -> str:
    for heard, meant in HOMOPHONES.items():
        text = text.replace(heard, meant)
    return text


def check_fn(text: str) -> list[dict]:
    return claimscope.check([("phrase", text)], RECORD, LEX)


def book() -> Path:
    return Path(tmp_dir("phrases-")) / "phrases.tsv"


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


def found(p: Path, line: str) -> list[tuple]:
    return [(f["rule"], f["severity"])
            for f in phrasebook.lint([("scene 1", line)], p, fix=fix)]


def do_approve(p: Path, pid: str, **kw):
    return lambda: phrasebook.approve(p, pid, check_fn=check_fn,
                                      reason=kw.pop("reason", "leaflet "
                                                    "wording"), fix=fix, **kw)


# ---- 1 ---------------------------------------------------------------------

def test_norm_and_clauses() -> None:
    print("[1] norm() and clauses()")
    check("spaces and punctuation of both widths go",
          phrasebook.norm("用于 急慢性咽炎，引起的“咽干”、咽痛！")
          == "用于急慢性咽炎引起的咽干咽痛")
    check("NFKC and case folded: Ｇｕａｒａｎｔｅｅｄ 8%! == guaranteed 8",
          phrasebook.norm("Ｇｕａｒａｎｔｅｅｄ 8%!") == phrasebook.norm(
              "guaranteed 8") == "guaranteed8")
    check("the harness's fix runs first",
          phrasebook.norm("咽喉咽", fix) == "咽喉炎"
          and phrasebook.norm("咽喉咽") == "咽喉咽")
    check("text_sha is of the norm: punctuation does not move it",
          phrasebook.text_sha("咽干，咽痛。") == phrasebook.text_sha("咽干咽痛"))
    got = phrasebook.clauses("早起嗓子干，咽痛！好。\n用了三天?  no; "
                             "Diversified, long-term growth")
    check("clauses split on ，,。！？；!?; and line breaks, short ones dropped",
          got == ["早起嗓子干", "用了三天", "Diversified",
                  "long-term growth"], got)
    check("min_len is the harness's",
          phrasebook.clauses("好。可以。", min_len=2) == ["可以"]
          and phrasebook.clauses("好。可以。", min_len=1) == ["好", "可以"])


# ---- 2 ---------------------------------------------------------------------

def test_add_and_harvest() -> None:
    print("[2] add() checks on the way in; harvest() in bulk")
    p = book()
    r = phrasebook.add(p, "咽喉咽是不会自己好的", check_fn=check_fn,
                       source="rejected creative 7", fix=fix)
    check("heard 咽喉咽, stored as meant (咽喉炎) and rejected for it",
          r["new"] and r["text"] == "咽喉炎是不会自己好的"
          and r["status"] == "rejected" and "咽喉炎 (C010)" in r["findings"],
          r)
    r = phrasebook.add(p, "用于急慢性咽炎引起的咽干、咽痛", check_fn=check_fn,
                       tag="claim")
    check("a clean phrase is pending, with its id and tag",
          r["status"] == "pending" and r["id"] == "P0002"
          and r["tag"] == "claim", r)
    r = phrasebook.add(p, "用于急慢性咽炎引起的咽干，咽痛。", check_fn=check_fn)
    check("punctuation does not make it new", r["new"] is False
          and r["id"] == "P0002", r)
    e = raises(lambda: phrasebook.add(p, " ，。", check_fn=check_fn),
               HarnessError)
    check("a blank phrase is refused (phrasebook_text_blank)",
          code(e) == "phrasebook_text_blank", repr(e))
    rows = phrasebook.load(p)
    check("the TSV holds every column, in order",
          p.read_text(encoding="utf-8").split("\n")[0].split("\t")
          == list(phrasebook.HEAD) and len(rows) == 2, rows)
    q = book()
    got = phrasebook.harvest(q, [
        "Grow with a diversified portfolio; steady monthly income, "
        "guaranteed!",
        "Diversified, for the long term.",
        "ok"], check_fn=check_fn, source="rejected creative 12")
    rows = {r["text"]: r for r in phrasebook.load(q)}
    check("harvest(): every clause, checked; short ones skipped",
          got["new"] == 5 and got["new_rejected"] == 2
          and got["by_status"] == {"approved": 0, "pending": 3,
                                   "rejected": 2}
          and rows["guaranteed"]["status"] == "rejected"
          and rows["steady monthly income"]["status"] == "rejected"
          and rows["Diversified"]["source"] == "rejected creative 12", got)
    again = phrasebook.harvest(q, ["Diversified, for the long term."],
                               check_fn=check_fn, source="x")
    check("harvesting the same lines again adds nothing",
          again["new"] == 0 and again["phrases"] == 5, again)


# ---- 3 ---------------------------------------------------------------------

def test_known_bad_is_refused_next_time() -> None:
    print("[3] a phrase the lexicon misses, rejected once, is refused next "
          "time")
    p = book()
    r = phrasebook.add(p, "忍一忍只会让它越来越严重", check_fn=check_fn)
    check("the lexicon sees nothing in it: pending",
          r["status"] == "pending" and not check_fn(r["text"]), r)
    check("…so a line saying it is only a warning",
          found(p, "忍一忍只会让它越来越严重。") == [("phrase_unapproved", "warn")])
    _, exc, _, _ = gate(lambda: phrasebook.reject(p, r["id"], reason=""))
    check("reject needs a reason (reason_required)",
          code(exc) == "reason_required")
    res, exc, _, _ = gate(lambda: phrasebook.reject(
        p, r["id"], reason="fear appeal, refused with its creative"))
    check("reject needs no gate (off a TTY, no secret)",
          exc is None and res["status"] == "rejected"
          and res["message"].code == "phrasebook_reject_done", repr(exc))
    check("the same words, other punctuation, inside a longer line: error",
          found(p, "嗓子不舒服？忍一忍，只会让它越来越严重！")
          == [("phrase_rejected", "error"), ("phrase_unapproved", "warn")],
          found(p, "嗓子不舒服？忍一忍，只会让它越来越严重！"))
    f = phrasebook.lint([("vo", "忍一忍，只会让它越来越严重！", 3.0)], p)
    check("the finding names the phrase and why, the copylint shape",
          len(f) == 1 and f[0]["phrase"] == r["id"] and f[0]["t"] == 3.0
          and f[0]["message"].code == "phrasebook_rejected"
          and "fear appeal" in f[0]["message"]
          and set(f[0]) >= {"severity", "rule", "where", "term", "confirmed",
                            "law", "message"}, f)
    q = book()
    phrasebook.harvest(q, ["Guaranteed 8% a year."], check_fn=check_fn,
                       source="rejected creative 3")
    check("fund: a rejected English phrase is caught in other case and "
          "punctuation",
          [(x["rule"]) for x in phrasebook.lint(
              [("vo", "GUARANTEED: 8 % a year!!")], q)] == ["phrase_rejected"])


# ---- 4 ---------------------------------------------------------------------

def test_approve() -> None:
    print("[4] only a person approves, only this text, only without errors")
    p = book()
    r = phrasebook.add(p, "用于急慢性咽炎引起的咽干、咽痛", check_fn=check_fn)
    pid = r["id"]
    before = p.read_bytes()
    _, exc, _, _ = gate(do_approve(p, pid))
    check("no terminal, no secret: confirm_needs_human, nothing written",
          code(exc) == "confirm_needs_human" and p.read_bytes() == before,
          repr(exc))
    _, exc, _, _ = gate(do_approve(p, pid), answers=["P0003"])
    check("a wrong retype: confirm_typed_mismatch, nothing written",
          code(exc) == "confirm_typed_mismatch" and p.read_bytes() == before)
    res, exc, err, _ = gate(do_approve(p, pid), answers=[pid])
    row = phrasebook.load(p)[0]
    check("the exact retype approves (@tty), the text shown at the prompt",
          exc is None and res["approved_by"] == human.changed_by("tty")
          and res["message"].code == "phrasebook_approve_done"
          and "咽干、咽痛" in err and phrasebook.approved(row, fix)
          and row["note"] == "leaflet wording", (repr(exc), row))
    check("an approved phrase lints clean",
          found(p, "用于急慢性咽炎引起的咽干，咽痛。") == [])
    rows = phrasebook.load(p)
    rows[0]["text"] = "用于急慢性咽炎引起的咽干、咽痛、咳嗽"
    phrasebook.save(p, rows)
    check("an edited text is no longer approved",
          not phrasebook.approved(phrasebook.load(p)[0], fix))
    q = book()
    r = phrasebook.add(q, "嗓子干了就用它", check_fn=check_fn)
    rows = phrasebook.load(q)
    rows[0].update({"status": "approved", "approved_by": "owner@tty",
                    "approved_at": "2026-09-30"})
    phrasebook.save(q, rows)
    check("a hand-set status is not approved (no text_sha)",
          not phrasebook.approved(phrasebook.load(q)[0])
          and found(q, "嗓子干了就用它") == [("phrase_unapproved", "warn")])
    r = phrasebook.add(q, "专治各种咳嗽", check_fn=check_fn)
    _, exc, err, left = gate(do_approve(q, r["id"]), answers=[r["id"]])
    check("a phrase with an error is refused before the gate "
          "(phrasebook_has_errors, no prompt, the answer not consumed)",
          code(exc) == "phrasebook_has_errors" and err == ""
          and left == [r["id"]], repr(exc))
    rej = phrasebook.add(q, "忍一忍只会越来越严重", check_fn=check_fn)
    phrasebook.reject(q, rej["id"], reason="refused")
    res, exc, _, _ = gate(do_approve(q, rej["id"]), answers=[rej["id"]])
    check("a person may raise a rejected phrase the check passes, at the gate",
          exc is None and res["status"] == "approved", repr(exc))
    gate(lambda: phrasebook.reject(q, rej["id"], reason="refused again"))
    check("…and reject clears the approval",
          not phrasebook.approved(next(x for x in phrasebook.load(q)
                                       if x["id"] == rej["id"])))


# ---- 5 ---------------------------------------------------------------------

def test_relay_meanwhile_and_refusals() -> None:
    print("[5] the relayed code works once; refusals")
    p = book()
    pid = phrasebook.add(p, "Diversified for the long term",
                         check_fn=check_fn)["id"]
    _, exc, _, _ = gate(do_approve(p, pid), secret=True)
    check("off a TTY with the secret: a code bound to {id: text sha}",
          isinstance(exc, human.CodeRequired) and exc.subject["items"]
          == {pid: phrasebook.text_sha("Diversified for the long term")},
          repr(exc))
    c = exc.confirm_code
    audit = {"relay_user": "web:owner",
             "relay_at": "2026-09-30T10:00:00+08:00"}
    res, exc, _, _ = gate(do_approve(p, pid, code=c, **audit), secret=True)
    check("with its audit it approves (@relay)",
          exc is None and res["approved_by"] == human.changed_by("relay"),
          repr(exc))
    gate(lambda: phrasebook.reject(p, pid, reason="second thoughts"))
    _, exc, _, _ = gate(do_approve(p, pid, code=c, **audit), secret=True)
    check("the same code again is refused (the file moved)",
          code(exc) == "confirm_code_mismatch", repr(exc))
    q = book()
    pid = phrasebook.add(q, "嗓子干了就用它", check_fn=check_fn)["id"]
    real = human.confirm

    def moving(*a, **kw):
        channel = real(*a, **kw)
        phrasebook.add(q, "嗓子痛了也用它", check_fn=check_fn)
        return channel
    with mock.patch.object(human, "confirm", moving):
        _, exc, _, _ = gate(do_approve(q, pid), answers=[pid])
    check("the file moving meanwhile: phrasebook_changed_meanwhile",
          code(exc) == "phrasebook_changed_meanwhile"
          and phrasebook.load(q)[0]["status"] == "pending", repr(exc))
    e = raises(lambda: phrasebook.reject(q, "P0099", reason="x"),
               HarnessError)
    check("an unknown id: phrasebook_unknown", code(e) == "phrasebook_unknown")
    q.write_text(q.read_text(encoding="utf-8").replace("\tpending\t",
                                                       "\tmaybe\t", 1),
                 encoding="utf-8")
    e = raises(lambda: phrasebook.load(q), HarnessError)
    check("a status outside the set: phrasebook_row_invalid",
          code(e) == "phrasebook_row_invalid", repr(e))


# ---- 6 ---------------------------------------------------------------------

def test_lint_edges() -> None:
    print("[6] lint() edges")
    p = book()
    check("no phrasebook: no findings",
          phrasebook.lint([("s", "anything at all")], p) == [])
    r = phrasebook.add(p, "咳嗽", check_fn=check_fn)
    check("a rejected phrase under reject_min is not matched inside lines",
          r["status"] == "rejected"
          and ("phrase_rejected", "error") not in found(p, "晚上咳嗽很烦人"))
    r = phrasebook.add(p, "晚上咳嗽好几天了", check_fn=check_fn)
    got = found(p, "晚上咳嗽好几天了，真的太难受了")
    check("a clause covered by a rejected hit is not warned twice",
          got == [("phrase_rejected", "error"), ("phrase_unapproved", "warn")]
          and r["status"] == "rejected", got)


# ---- 7 ---------------------------------------------------------------------

def test_closure() -> None:
    print("[7] phrasebook.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "phrasebook.tsv"}
    check("the fragment holds the phrasebook_* codes",
          own and all(c.startswith("phrasebook_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["phrasebook.py"], registry=own, strict_kit=True)
    check("every msg() in phrasebook.py is registered with exact params, and "
          "every phrasebook.tsv code is emitted", problems == [], problems)


if __name__ == "__main__":
    test_norm_and_clauses()
    test_add_and_harvest()
    test_known_bad_is_refused_next_time()
    test_approve()
    test_relay_meanwhile_and_refusals()
    test_lint_edges()
    test_closure()
    raise SystemExit(finish())
