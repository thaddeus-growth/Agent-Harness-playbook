"""Claim scope: copy may name only what an approved document allows (a
drug's indication, a health food's approved function, a cosmetic's
efficacy class, a financial product's permitted claims).

A banned-word list cannot do this: the words that go wrong are ordinary
(a symptom, a benefit, a return), and what makes them wrong is the
product. So the check runs the other way round: every claim the copy
makes must be one the product's approved document names.

Two inputs, both the harness's:

  * The lexicon, a harness TSV (kit.registry.read_tsv reads it), one row
    per thing copy can claim:
        id         unique
        kind       `claim` (must be inside the scope: a symptom, a benefit,
                   a promised return) or `always` (a finding whatever the
                   scope: a scope-widener such as "every kind of …", an
                   audience or scene phrase)
        canonical  the claim's name; a claim row may name several joined
                   by `+` (a phrase that says two things names both)
        pattern    a regex
        severity   error | warn
        note       why, in the owner's words (optional)
        law_ref    the clause it rests on (optional column)
        status     pending | confirmed | retired (blank = pending)
    A harness with its own kind names maps them once, with `kinds`
    ({its kind: "claim" | "always"}, e.g. {"symptom": "claim",
    "condition": "claim", "widener": "always", "scene": "always"}); the
    default maps `claim` and `always` to themselves. The row keeps its
    own word as `label`. A retired row is dropped before it is checked;
    any other row with a bad regex, an unknown kind, severity or status,
    or no id, canonical or pattern is refused (claimscope_row_invalid),
    never skipped.
  * The claims record, a JSON file at a path the harness chooses:
        subject     the product, as the person retypes it at the gate
        category    the harness's category word
        scope_text  the approved document's wording, verbatim
        allowed     the canonical names that wording allows
        source      where the wording was read (a photo, a page, a filing)
    plus the confirmation's own fields (status, confirmed_by,
    confirmed_at, confirm_reason, content_sha), which are outside the
    content hash, as are `_`-prefixed notes.

What it guards:

  * matches() finds every row's hits, keeps the longest where they
    overlap (a phrase naming two claims is not read as the one it
    starts with), and merges two hits of one row side by side into one.
  * check(texts, record, rows) -> [finding]; texts are (where, text) or
    (where, text, t). A claim row is a finding when one of its names is
    not in `allowed`; without a record only `always` rows count (there is
    nothing to hold claims against). A finding is {severity, rule, where,
    t, term, names, kind, label, confirmed, law, note, message}, the
    kit.copylint shape plus the claim's names. A finding from a row that
    is not confirmed, or (for a claim) from a record that is not, says so:
    confirmed False and the *_draft code, "draft, not advice".
  * A record counts only through the gate. confirmed(record) is true only
    when status is `confirmed`, confirmed_by and confirmed_at are there
    and content_sha equals the content hash now: a hand-set status, or
    any edit after the confirmation, reads as a draft. confirm() refuses
    an incomplete record before the gate (claimscope_incomplete), shows
    the person subject, category, allowed, scope_text and source, and
    takes the subject retyped at the terminal or a relayed code bound to
    {subject: content hash} with the file's sha as the version (the
    write it unlocks makes it stale). The file moving meanwhile is
    refused.
  * A waiver accepts one finding on purpose, through the gate (the person
    retypes the finding's words). It is stored keyed by a hash of (rule,
    where, term), so a changed line no longer matches it.
    open_errors(findings, waivers_path) -> (open, waived), errors only.
  * required(record_path) is the finding a harness adds for a category
    that must have a record and has none: declare, never guess.
  * The learning loop: every platform rejection is a test case. quoted()
    takes the phrases a reviewer quoted inside “…” (not the ones holding
    the harness's reviewer-paraphrase markers); recall(quotes, check_fn)
    says how many the checker catches and lists the misses, each one a
    lexicon row to add. false_alarms(texts, check_fn) is the other side:
    approved copy that the checker would refuse.

This is a pre-check a person confirms, never legal advice.

Paid for: one OTC product's creatives were refused 20 times in one
afternoon for naming symptoms its approved indication does not (ordinary
words a banned list cannot catch); the checker then caught 10/10 of the
reviewer's quoted phrases while the approved wording passed. And an agent
once hand-set a record's status (kit/consent.py), so a record counts only
through the gate.

Public API: KINDS, lexicon(), load_lexicon(), matches(), content_sha(),
load(), confirmed(), confirm(), check(), required(), errors(),
waiver_key(), waivers(), waive(), open_errors(), quoted(), recall(),
false_alarms().

Test: kit/tests/test_claimscope.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Iterable

from kit import human
from kit.atomic import write_json_atomic
from kit.contract import HarnessError
from kit.messages import msg
from kit.registry import read_tsv

KINDS = ("claim", "always")
SEVERITIES = ("error", "warn")
STATUSES = ("pending", "confirmed", "retired")
REQUIRED = ("id", "kind", "canonical", "pattern", "severity", "status")
CONFIRM_FIELDS = ("status", "confirmed_by", "confirmed_at", "confirm_reason",
                  "content_sha")
QUOTE = re.compile(r"“([^”]+)”")
VERB, WAIVE_VERB = "claims confirm", "claims waive"


# ---- the lexicon -----------------------------------------------------------

def _row_invalid(rule: str, detail: str) -> HarnessError:
    return HarnessError(msg(
        "claimscope_row_invalid",
        f"lexicon row {rule}: {detail}; fix the lexicon, nothing was checked",
        rule=rule, detail=detail))


def lexicon(rows: Iterable[dict], *, kinds: dict[str, str] | None = None
            ) -> list[dict]:
    """The live rows, checked and compiled: each row dict plus `kind`
    (claim | always), `label` (the harness's own kind word), `names` (the
    canonical split on `+`) and `re`. Retired rows are dropped."""
    kinds = dict(kinds) if kinds is not None else {k: k for k in KINDS}
    bad = {k: v for k, v in kinds.items() if v not in KINDS}
    if bad:
        raise ValueError(f"kinds must map to {KINDS}, got {bad}")
    out, seen = [], set()
    for r in rows:
        rid = (r.get("id") or "").strip()
        if not rid:
            raise _row_invalid("(no id)", "a row has no id")
        if rid in seen:
            raise _row_invalid(rid, "the id is listed twice")
        seen.add(rid)
        status = (r.get("status") or "").strip() or "pending"
        if status not in STATUSES:
            raise _row_invalid(rid, f"status {status!r} is not one of "
                                    f"{' | '.join(STATUSES)}")
        if status == "retired":
            continue
        label = (r.get("kind") or "").strip()
        if label not in kinds:
            raise _row_invalid(rid, f"kind {label!r} is not one of "
                                    f"{sorted(kinds)}")
        sev = (r.get("severity") or "").strip()
        if sev not in SEVERITIES:
            raise _row_invalid(rid, f"severity {sev!r} is not one of "
                                    f"{' | '.join(SEVERITIES)}")
        canonical = (r.get("canonical") or "").strip()
        names = [x.strip() for x in canonical.split("+") if x.strip()]
        pattern = r.get("pattern") or ""
        if not names or not pattern:
            raise _row_invalid(rid, "canonical and pattern must not be blank")
        try:
            pat = re.compile(pattern)
        except re.error as e:
            raise _row_invalid(rid, f"pattern {pattern!r} is not a valid "
                                    f"regex ({e})") from None
        out.append({**r, "id": rid, "status": status, "severity": sev,
                    "canonical": canonical, "kind": kinds[label],
                    "label": label, "names": names, "re": pat})
    return out


def load_lexicon(path: Path | str, *, kinds: dict[str, str] | None = None
                 ) -> list[dict]:
    """lexicon() of the TSV at `path` (read strictly by kit.registry)."""
    return lexicon(read_tsv(path, REQUIRED, id_col="id"), kinds=kinds)


def matches(text: str, rows: list[dict]) -> list[dict]:
    """Non-overlapping hits, longest first, in text order; two hits of one
    row side by side (at most one character apart) are merged into one:
    [{start, end, text, row}]."""
    text = text or ""
    found = [{"start": m.start(), "end": m.end(), "text": m.group(0), "row": r}
             for r in rows for m in r["re"].finditer(text)
             if m.end() > m.start()]
    found.sort(key=lambda x: (x["start"] - x["end"], x["start"]))
    kept, taken = [], set()
    for f in found:
        span = set(range(f["start"], f["end"]))
        if not span & taken:
            taken |= span
            kept.append(f)
    kept.sort(key=lambda x: x["start"])
    merged: list[dict] = []
    for f in kept:
        last = merged[-1] if merged else None
        if last and last["row"]["id"] == f["row"]["id"] \
                and f["start"] - last["end"] <= 1:
            merged[-1] = {**last, "end": f["end"],
                          "text": text[last["start"]:f["end"]]}
        else:
            merged.append(f)
    return merged


# ---- the claims record -----------------------------------------------------

def content_sha(record: dict) -> str:
    """What a confirmation is bound to: every field but the confirmation's
    own and the `_` notes, as canonical JSON."""
    body = {k: v for k, v in record.items()
            if k not in CONFIRM_FIELDS and not k.startswith("_")}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() \
        if path.is_file() else ""


def load(path: Path | str) -> dict | None:
    """The claims record at `path`; None when there is none. A file that is
    not a JSON object, or whose `allowed` is not a list of names, is
    refused (claimscope_record_invalid)."""
    f = Path(path)
    if not f.is_file():
        return None
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        detail = "not a JSON object"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        rec, detail = None, str(e)
    if isinstance(rec, dict) and not (
            isinstance(rec.get("allowed"), list)
            and all(isinstance(x, str) for x in rec["allowed"])):
        rec, detail = None, "`allowed` is not a list of names"
    if not isinstance(rec, dict):
        raise HarnessError(msg(
            "claimscope_record_invalid",
            f"{f} is not a claims record: {detail}", path=str(f),
            detail=detail))
    return rec


def confirmed(record: dict | None) -> bool:
    """True only when a person confirmed this exact content through
    confirm(): a hand-set status, or an edit since, reads as a draft."""
    return bool(record) and record.get("status") == "confirmed" \
        and bool(record.get("confirmed_by")) \
        and bool(record.get("confirmed_at")) \
        and record.get("content_sha") == content_sha(record)


def _gaps(rec: dict) -> list[str]:
    out = []
    for field in ("subject", "scope_text", "source"):
        if not isinstance(rec.get(field), str) or not rec[field].strip():
            out.append(f"{field} is blank")
    allowed = rec.get("allowed")
    if not allowed or not all(isinstance(x, str) and x.strip()
                              for x in allowed):
        out.append("allowed names nothing")
    return out


def summary(rec: dict) -> str:
    """What the person reads before confirming."""
    return (f"{rec.get('subject')} ({rec.get('category')}) may name only: "
            f"{', '.join(map(str, rec.get('allowed') or []))}\n"
            f"  approved wording: {rec.get('scope_text')!r}\n"
            f"  read from: {rec.get('source')!r}")


def confirm(path: Path | str, *, reason: str | None, code: str | None = None,
            relay_user: str | None = None, relay_at: str | None = None,
            verb: str = VERB, market: str = "") -> dict:
    """A person confirms the claims record at `path`: they retype its
    subject at the terminal, or relay back a code bound to {subject:
    content hash} and the file's sha. Raises HarnessError
    (claimscope_record_missing, claimscope_incomplete: nothing written, no
    gate asked; claimscope_changed_meanwhile) or kit.human.Refused /
    CodeRequired (the gate)."""
    f = Path(path)
    rec = load(f)
    if rec is None:
        raise HarnessError(msg("claimscope_record_missing",
                               f"no claims record at {f}; write it from the "
                               f"approved document first", path=str(f)))
    gaps = _gaps(rec)
    if gaps:
        detail = "; ".join(gaps)
        raise HarnessError(msg(
            "claimscope_incomplete",
            f"{f}: fill the record from the approved document before a "
            f"person confirms it: {detail}. Nothing was written.",
            path=str(f), detail=detail))
    why = human.why(reason)
    subject, sha, before = str(rec["subject"]), content_sha(rec), _file_sha(f)
    subj = human.subject(verb, market, "claims", {subject: sha}, before)
    channel = human.confirm(
        f"confirm the claims of {subject}",
        f"{summary(rec)}\nretype {subject} to confirm it: ", subject,
        subj=subj, code=code)
    why += human.relay_audit(channel, relay_user, relay_at)
    if _file_sha(f) != before:
        raise HarnessError(msg(
            "claimscope_changed_meanwhile",
            f"{f} changed while the person was being asked, so the "
            f"confirmation no longer matches it. Nothing was written.",
            path=str(f)))
    by, at = human.changed_by(channel), human.now()
    rec.update({"status": "confirmed", "confirmed_by": by, "confirmed_at": at,
                "confirm_reason": why, "content_sha": sha})
    write_json_atomic(f, rec, indent=2)
    return {"subject": subject, "status": "confirmed", "confirmed_by": by,
            "confirmed_at": at, "content_sha": sha,
            "message": msg("claimscope_confirm_done",
                           f"the claims of {subject} are confirmed by {by} "
                           f"for this exact content; any edit needs a new "
                           f"confirmation", subject=subject, by=by)}


# ---- the check -------------------------------------------------------------

def check(texts: Iterable[tuple], record: dict | None,
          rows: list[dict]) -> list[dict]:
    """Findings over `texts`, (where, text) or (where, text, t), against
    `record` (None = no record: `always` rows only) and the compiled
    lexicon `rows`."""
    allowed = set((record or {}).get("allowed") or [])
    scope_ok = confirmed(record)
    shown = str((record or {}).get("scope_text")
                or ", ".join(sorted(allowed)))
    out = []
    for item in texts:
        where, text = item[0], item[1]
        t = item[2] if len(item) > 2 else None
        for m in matches(text, rows):
            r, term = m["row"], m["text"]
            if r["kind"] == "claim":
                if record is None:
                    continue
                names = [n for n in r["names"] if n not in allowed]
                if not names:
                    continue
                ok = r["status"] == "confirmed" and scope_ok
                said = (f"{where}: {term!r} names {', '.join(names)}, outside "
                        f"the approved scope {shown!r}")
                if ok:
                    message = msg("claimscope_outside", said, where=where,
                                  term=term, names=names, scope=shown)
                else:
                    message = msg("claimscope_outside_draft",
                                  said + " (draft rule or record, not "
                                  "confirmed by a person; draft, not advice)",
                                  where=where, term=term, names=names,
                                  scope=shown)
            else:
                names = list(r["names"])
                ok = r["status"] == "confirmed"
                said = f"{where}: {term!r} is {r['canonical']} whatever the scope"
                if ok:
                    message = msg("claimscope_always", said, where=where,
                                  term=term, canonical=r["canonical"])
                else:
                    message = msg("claimscope_always_draft",
                                  said + " (draft rule, not confirmed by a "
                                  "person; draft, not advice)", where=where,
                                  term=term, canonical=r["canonical"])
            out.append({"severity": r["severity"], "rule": r["id"],
                        "where": where, "t": t, "term": term, "names": names,
                        "kind": r["kind"], "label": r["label"],
                        "confirmed": ok, "law": (r.get("law_ref") or "").strip(),
                        "note": (r.get("note") or "").strip(),
                        "message": message})
    return out


def required(record_path: Path | str, *, where: str = "record") -> list[dict]:
    """[] when the claims record exists; else the one error finding a
    harness adds for a category that must have one (declare, never
    guess)."""
    f = Path(record_path)
    if f.is_file():
        return []
    return [{"severity": "error", "rule": "claimscope_record_required",
             "where": where, "t": None, "term": f.name, "names": [],
             "kind": "record", "label": "record", "confirmed": True, "law": "",
             "note": "",
             "message": msg("claimscope_record_required",
                            f"{where}: this category needs a claims record "
                            f"({f}) from the approved document before any "
                            f"copy is made", where=where, path=str(f))}]


def errors(findings: Iterable[dict]) -> list[dict]:
    """The findings of severity error."""
    return [f for f in findings if f.get("severity") == "error"]


# ---- waivers ---------------------------------------------------------------

def waiver_key(rule: str, where: str, term: str) -> str:
    """What a waiver is bound to: its rule, place and exact words."""
    return hashlib.sha256(f"{rule}\x1f{where}\x1f{term}".encode("utf-8")
                          ).hexdigest()[:16]


def waivers(path: Path | str) -> dict[str, dict]:
    """The waivers file ({key: waiver}); {} when there is none. One that
    does not parse is refused: an unreadable waiver waives nothing."""
    f = Path(path)
    if not f.is_file():
        return {}
    try:
        w = json.loads(f.read_text(encoding="utf-8"))
        detail = "not a JSON object"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        w, detail = None, str(e)
    if not isinstance(w, dict):
        raise HarnessError(msg("claimscope_waivers_unreadable",
                               f"{f} is not a waivers file: {detail}",
                               path=str(f), detail=detail))
    return w


def waive(path: Path | str, *, rule: str, where: str, term: str,
          reason: str | None, code: str | None = None,
          relay_user: str | None = None, relay_at: str | None = None,
          verb: str = WAIVE_VERB, market: str = "") -> dict:
    """A person accepts one finding on purpose: they retype its words (or
    relay a code bound to rule, place and words, and the file's sha)."""
    if not all(isinstance(x, str) and x for x in (rule, where, term)):
        raise ValueError("waive() needs the finding's rule, where and term")
    f = Path(path)
    w = waivers(f)
    why = human.why(reason)
    k, before = waiver_key(rule, where, term), _file_sha(f)
    subj = human.subject(verb, market, "finding", {k: [rule, where, term]},
                         before)
    channel = human.confirm(
        f"waive {rule} at {where}",
        f"accept {term!r} ({rule}) at {where}: {why}\n"
        f"retype the words to confirm: ", term, subj=subj, code=code)
    why += human.relay_audit(channel, relay_user, relay_at)
    if _file_sha(f) != before:
        raise HarnessError(msg(
            "claimscope_changed_meanwhile",
            f"{f} changed while the person was being asked, so the waiver "
            f"no longer matches it. Nothing was written.", path=str(f)))
    by = human.changed_by(channel)
    w[k] = {"rule": rule, "where": where, "term": term, "reason": why,
            "waived_by": by, "at": human.now()}
    write_json_atomic(f, w, indent=2)
    return {"waiver": k, **w[k],
            "message": msg("claimscope_waive_done",
                           f"{rule} at {where} ({term!r}) waived by {by}; a "
                           f"changed line no longer matches it", rule=rule,
                           where=where, term=term, by=by)}


def open_errors(findings: Iterable[dict], waivers_path: Path | str
                ) -> tuple[list[dict], list[dict]]:
    """(errors not waived, errors waived)."""
    w = waivers(waivers_path)
    errs = errors(findings)
    hit = [waiver_key(str(f.get("rule", "")), str(f.get("where", "")),
                      str(f.get("term", ""))) in w for f in errs]
    return ([f for f, h in zip(errs, hit) if not h],
            [f for f, h in zip(errs, hit) if h])


# ---- the learning loop -----------------------------------------------------

def quoted(reason: str, *, markers: Iterable[str] = (), min_len: int = 2
           ) -> list[str]:
    """The phrases a reviewer quoted inside “…”, minus the ones holding a
    `markers` word (the reviewer's own paraphrase, not the copy) and the
    ones shorter than `min_len`."""
    marks = [m for m in markers if m]
    return [q.strip() for q in QUOTE.findall(reason or "")
            if len(q.strip()) >= min_len and not any(m in q for m in marks)]


def recall(quotes: Iterable[str], check_fn: Callable[[str], list[dict]]
           ) -> dict:
    """How many quoted phrases `check_fn` (text -> findings) flags with an
    error: {checked, caught, recall (None when nothing was quoted),
    misses}. Each miss is a lexicon row to add."""
    qs = list(quotes)
    misses = [q for q in qs if not errors(check_fn(q))]
    caught = len(qs) - len(misses)
    return {"checked": len(qs), "caught": caught,
            "recall": round(caught / len(qs), 3) if qs else None,
            "misses": misses}


def false_alarms(texts: Iterable[str], check_fn: Callable[[str], list[dict]]
                 ) -> dict:
    """Approved copy the checker would refuse: {checked, flagged, alarms:
    [{text, rules}]}. Each alarm is a lexicon row too wide."""
    ts = list(texts)
    alarms = []
    for t in ts:
        errs = errors(check_fn(t))
        if errs:
            alarms.append({"text": t, "rules": sorted({f["rule"]
                                                       for f in errs})})
    return {"checked": len(ts), "flagged": len(alarms), "alarms": alarms}
