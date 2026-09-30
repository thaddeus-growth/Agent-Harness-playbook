"""The phrasebook: every phrase a subject's copy has said or may say, each
approved, pending or rejected, so a known-bad phrase is refused next time
even where the lexicon misses it, and new copy is assembled from phrases a
person already approved.

Creatives are remixes: the same few scripts rearranged. A phrase the
reviewer refused comes back in the next variant unless something
remembers it, and a lexicon only remembers words, not the phrase.

The store is a TSV at a path the harness chooses (one per subject):

    id           P0001, P0002 …
    text         the phrase, as it was meant (after the harness's `fix`)
    tag          the harness's word for its role (hook, claim, cta …)
    status       approved   a person approved this exact text at the gate
                 pending    no error found, nobody approved it yet
                 rejected   the check found an error, or a person marked it
                            (a platform refused copy that said it)
    source       where it came from (written, a harvested creative …)
    findings     the errors that rejected it, `term (rule); …`
    approved_by, approved_at, text_sha
                 written by approve() only
    note         the reason of the last approve or reject

What it guards:

  * One phrase, one row, whatever the punctuation: norm(text, fix) is the
    NFKC, case-folded text with every space and punctuation mark removed,
    after the harness's `fix` (e.g. speech-recognition homophone fixes),
    and add() dedupes on it.
  * clauses(line) splits on ，,。！？；!?; and line breaks and keeps the
    ones at least `min_len` normalised characters long.
  * A phrase is checked on the way in: add() and harvest() run the
    harness's `check_fn` (text -> findings, kit.copylint's shape) and
    store a phrase with an error as rejected, else pending.
  * Only a person approves, only for this exact text, only with no open
    error: approve() runs `check_fn` again (today's rules), refuses a
    phrase with an error (phrasebook_has_errors) before the gate, then a
    person retypes the id at the terminal or relays a code bound to {id:
    text sha} and the file's sha (the write it unlocks makes it stale).
    approved(row) is true only with the gate's trace for this very text:
    a hand-set status, or an edited text, reads as not approved. A file
    that moved while the person was asked is refused.
  * Anyone lowers trust: reject() needs a reason, not the gate.
  * lint(lines, path) -> findings: a rejected phrase (at least
    `reject_min` normalised characters) contained anywhere in a line is an
    error, whatever the punctuation or clause split; a clause that is not
    an approved phrase is a warning (clauses a rejected hit covers are not
    warned twice, and a clause inside an approved phrase the line says
    whole is not warned at all).

Paid for: 26 rejected creatives turned out to be 5 scripts rearranged:
one bad sentence was rejected again and again.

Public API: HEAD, STATUSES, norm(), text_sha(), clauses(), load(), save(),
add(), harvest(), approve(), reject(), approved(), lint().

Test: kit/tests/test_phrasebook.py.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from kit import human
from kit.atomic import open_atomic
from kit.contract import HarnessError
from kit.messages import msg
from kit.registry import read_tsv

HEAD = ("id", "text", "tag", "status", "source", "findings", "approved_by",
        "approved_at", "text_sha", "note")
STATUSES = ("approved", "pending", "rejected")
SPLIT = re.compile(r"[，,。！？；!?;\n]+")
MIN_LEN = 4
REJECT_MIN = 6
VERB = "phrases approve"

Fix = Callable[[str], str] | None
CheckFn = Callable[[str], list[dict]]


# ---- the text --------------------------------------------------------------

def norm(text: str, fix: Fix = None) -> str:
    """The form two phrases are compared in: `fix` applied, NFKC, case
    folded, every space and punctuation mark removed."""
    s = fix(text or "") if fix else (text or "")
    s = unicodedata.normalize("NFKC", s).casefold()
    return "".join(c for c in s if not c.isspace()
                   and not unicodedata.category(c).startswith("P"))


def text_sha(text: str, fix: Fix = None) -> str:
    """What an approval is bound to: the sha256 of the normalised text."""
    return hashlib.sha256(norm(text, fix).encode("utf-8")).hexdigest()


def clauses(line: str, *, min_len: int = MIN_LEN, fix: Fix = None
            ) -> list[str]:
    """The clauses of `line`, split on ，,。！？；!?; and line breaks, each at
    least `min_len` normalised characters long."""
    return [c.strip() for c in SPLIT.split(line or "")
            if len(norm(c, fix)) >= min_len]


def _errors(findings: Iterable[dict]) -> list[dict]:
    return [f for f in findings or [] if f.get("severity") == "error"]


# ---- the store -------------------------------------------------------------

def load(path: Path | str) -> list[dict]:
    """The rows of the phrasebook at `path`; [] when there is none. Read
    strictly (kit.registry.read_tsv); a status outside the set is refused."""
    f = Path(path)
    if not f.is_file():
        return []
    rows = read_tsv(f, HEAD, id_col="id")
    for r in rows:
        if r["status"] not in STATUSES:
            raise HarnessError(msg(
                "phrasebook_row_invalid",
                f"{f}: phrase {r['id']} has status {r['status']!r}, not one "
                f"of {' | '.join(STATUSES)}. Nothing was read",
                path=str(f), phrase=r["id"],
                detail=f"status {r['status']!r}"))
    return rows


def _cell(v: object) -> str:
    return " ".join(str(v if v is not None else "").split())


def save(path: Path | str, rows: list[dict]) -> None:
    """The whole phrasebook, written through kit.atomic."""
    with open_atomic(Path(path)) as out:
        out.write("\t".join(HEAD) + "\n")
        for r in rows:
            out.write("\t".join(_cell(r.get(h, "")) for h in HEAD) + "\n")


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() \
        if path.is_file() else ""


def _next_id(rows: list[dict]) -> str:
    n = max([int(r["id"][1:]) for r in rows
             if re.fullmatch(r"P\d+", r.get("id", ""))] or [0]) + 1
    return f"P{n:04d}"


def _find(rows: list[dict], phrase: str, path: Path) -> dict:
    r = next((r for r in rows if r["id"] == phrase), None)
    if r is None:
        raise HarnessError(msg("phrasebook_unknown",
                               f"no phrase {phrase} in {path}",
                               phrase=phrase, path=str(path)))
    return r


def _add(rows: list[dict], text: str, *, check_fn: CheckFn, tag: str,
         source: str, fix: Fix) -> dict:
    meant = (fix(text or "") if fix else (text or "")).strip()
    if not norm(meant):
        raise HarnessError(msg("phrasebook_text_blank",
                               "the phrase is blank once spaces and "
                               "punctuation are removed. Nothing was written"))
    have = {norm(r["text"], fix): r for r in rows}
    if norm(meant) in have:
        return {**have[norm(meant)], "new": False}
    errs = _errors(check_fn(meant))
    row = {"id": _next_id(rows), "text": meant, "tag": tag,
           "status": "rejected" if errs else "pending", "source": source,
           "findings": "; ".join(f"{f.get('term')} ({f.get('rule')})"
                                 for f in errs),
           "approved_by": "", "approved_at": "", "text_sha": "", "note": ""}
    rows.append(row)
    return {**row, "new": True}


def add(path: Path | str, text: str, *, check_fn: CheckFn, tag: str = "",
        source: str = "written", fix: Fix = None) -> dict:
    """One phrase into the phrasebook, checked on the way in (rejected when
    `check_fn` finds an error, else pending); a phrase already there
    (same norm) is returned as it is. The row, plus `new`."""
    f = Path(path)
    rows = load(f)
    row = _add(rows, text, check_fn=check_fn, tag=tag, source=source, fix=fix)
    if row["new"]:
        save(f, rows)
    return row


def harvest(path: Path | str, texts: Iterable[str], *, check_fn: CheckFn,
            source: str, tag: str = "", fix: Fix = None,
            min_len: int = MIN_LEN) -> dict:
    """Every clause of every text into the phrasebook (a text with no clause
    long enough counts whole when it is), one write at the end:
    {phrases, new, new_rejected, by_status}."""
    f = Path(path)
    rows = load(f)
    new = flagged = 0
    for text in texts:
        parts = clauses(text, min_len=min_len, fix=fix) or (
            [text] if len(norm(text, fix)) >= min_len else [])
        for part in parts:
            r = _add(rows, part, check_fn=check_fn, tag=tag, source=source,
                     fix=fix)
            new += r["new"]
            flagged += r["new"] and r["status"] == "rejected"
    if new:
        save(f, rows)
    return {"phrases": len(rows), "new": new, "new_rejected": flagged,
            "by_status": {s: sum(r["status"] == s for r in rows)
                          for s in STATUSES}}


def approved(row: dict, fix: Fix = None) -> bool:
    """True only with the gate's trace for this very text: status approved,
    approved_by and approved_at there, text_sha equal to the text's now."""
    return row.get("status") == "approved" and bool(row.get("approved_by")) \
        and bool(row.get("approved_at")) \
        and row.get("text_sha") == text_sha(row.get("text", ""), fix)


def approve(path: Path | str, phrase: str, *, check_fn: CheckFn,
            reason: str | None, code: str | None = None,
            relay_user: str | None = None, relay_at: str | None = None,
            verb: str = VERB, market: str = "", fix: Fix = None) -> dict:
    """A person approves one phrase for its exact text: retyping its id at
    the terminal, or relaying a code bound to {id: text sha} and the
    file's sha. Refused before the gate while `check_fn` finds an error."""
    f = Path(path)
    rows = load(f)
    r = _find(rows, phrase, f)
    errs = _errors(check_fn(r["text"]))
    if errs:
        first = errs[0]
        raise HarnessError(msg(
            "phrasebook_has_errors",
            f"{phrase} cannot be approved: {first.get('term')!r} "
            f"({first.get('rule')}). Rewrite it as a new phrase. Nothing "
            f"was written", phrase=phrase, term=str(first.get("term")),
            rule=str(first.get("rule"))))
    why = human.why(reason)
    sha, before = text_sha(r["text"], fix), _file_sha(f)
    subj = human.subject(verb, market, "phrase", {phrase: sha}, before)
    channel = human.confirm(
        f"approve phrase {phrase}",
        f"{r['text']}\nretype {phrase} to approve it: ", phrase, subj=subj,
        code=code)
    why += human.relay_audit(channel, relay_user, relay_at)
    if _file_sha(f) != before:
        raise HarnessError(msg(
            "phrasebook_changed_meanwhile",
            f"{f} changed while the person was being asked, so the approval "
            f"of {phrase} no longer matches it. Nothing was written",
            phrase=phrase, path=str(f)))
    by = human.changed_by(channel)
    r.update({"status": "approved", "approved_by": by,
              "approved_at": human.now(), "text_sha": sha, "findings": "",
              "note": why})
    save(f, rows)
    return {**{k: r[k] for k in ("id", "text", "status", "approved_by")},
            "message": msg("phrasebook_approve_done",
                           f"{phrase} approved by {by} for this exact text",
                           phrase=phrase, by=by)}


def reject(path: Path | str, phrase: str, *, reason: str | None) -> dict:
    """Anyone marks a phrase rejected (a platform refused copy that said
    it): a reason, no gate. Clears any approval."""
    f = Path(path)
    rows = load(f)
    r = _find(rows, phrase, f)
    r.update({"status": "rejected", "note": human.why(reason),
              "approved_by": "", "approved_at": "", "text_sha": ""})
    save(f, rows)
    return {**{k: r[k] for k in ("id", "text", "status", "note")},
            "message": msg("phrasebook_reject_done",
                           f"{phrase} is rejected: copy that says it is "
                           f"refused from now on", phrase=phrase)}


def lint(lines: Iterable[tuple], path: Path | str, *, fix: Fix = None,
         min_len: int = MIN_LEN, reject_min: int = REJECT_MIN) -> list[dict]:
    """Each line, (where, text) or (where, text, t), against the
    phrasebook: a rejected phrase inside it = error, a clause that is not
    an approved phrase = warn. [] when there is no phrasebook."""
    rows = load(path)
    if not rows:
        return []
    have = {norm(r["text"], fix): r for r in rows}
    bad = [(norm(r["text"], fix), r) for r in rows
           if r["status"] == "rejected"
           and len(norm(r["text"], fix)) >= reject_min]
    out = []
    for item in lines:
        where, line = item[0], item[1] or ""
        t = item[2] if len(item) > 2 else None
        whole = norm(line, fix)
        hit = [r for n, r in bad if n in whole]
        for r in hit:
            why = r["findings"] or r["note"] or r["source"]
            out.append({
                "severity": "error", "rule": "phrase_rejected",
                "where": where, "t": t, "term": r["text"], "confirmed": True,
                "law": "", "phrase": r["id"],
                "message": msg("phrasebook_rejected",
                               f"{where}: says rejected phrase {r['id']} "
                               f"{r['text']!r} ({why})", where=where,
                               phrase=r["id"], sentence=r["text"],
                               why=why)})
        covered = [norm(r["text"], fix) for r in hit]
        said = [n for n, r in have.items() if n and n in whole
                and approved(r, fix)]
        for c in clauses(line, min_len=min_len, fix=fix):
            n = norm(c, fix)
            if any(n in k or k in n for k in covered) \
                    or any(n in k for k in said):
                continue
            r = have.get(n)
            if r and approved(r, fix):
                continue
            out.append({
                "severity": "warn", "rule": "phrase_unapproved",
                "where": where, "t": t, "term": c, "confirmed": True,
                "law": "", "phrase": r["id"] if r else "",
                "message": msg("phrasebook_unapproved",
                               f"{where}: {c!r} is not an approved phrase "
                               f"yet (add it, then a person approves it)",
                               where=where, sentence=c)})
    return out
