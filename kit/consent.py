"""Third-party rights: a person's (or a creator's) consent to be used in the
harness's output, confirmed by a person through the human gate.

A consent record is a JSON file whose path the harness chooses (one per
person or per creator). It says who (`person`), how they stand to the
advertiser (`relation`, one of the harness's closed set), what of them may
be used (`uses`, a non-empty subset of the harness's closed set, e.g.
face, voice, name, words), where (`scope`, a dict of lists, e.g. brands,
channels, purposes; "*" = any), when (`valid_from`, `valid_until`, ISO
dates), and the signed document it was filled from (`document`, a path
relative to the harness root, and its `document_sha256`). `revoked` ends
it. The harness declares its closed sets once, as a `Terms`.

What this module guards:

  * Consent is confirmed through the gate, never by editing the file.
    confirm() refuses an incomplete record before the gate is even asked
    (consent_incomplete, nothing written), then a person retypes the
    record id at the terminal, or a relayed code comes back that is bound
    to {record id: content sha} with version = the log's length
    (kit.human.confirm; the confirm's own row moves it, so a code works
    once). Only then are status, confirmed_by (derived by
    kit.human.changed_by, never typed), confirmed_at and confirm_reason
    written, and a `confirm` row appended to the log.
  * The log is `<record minus .json>.log.jsonl`, next to the record
    (`cast/ana.json` -> `cast/ana.log.jsonl`): append-only, one JSON row
    per confirm or revoke, rewritten whole through kit.atomic so a torn
    row is never on disk. A log row that does not parse blocks every use
    (consent_log_unreadable): the evidence is gone, not "probably fine".
  * A confirmation belongs to the content it confirmed. content_sha()
    covers every field but the confirmation's own (status, confirmed_by,
    confirmed_at, confirm_reason) and the `_`-prefixed notes, so a
    hand-set status never counts, and any edit after a confirmation (the
    person, a use, a scope entry, a date, the document's sha) needs a new
    one.
  * Anyone may lower trust: revoke() needs no gate, only a --reason, and
    logs a `revoke` row. A revoke voids every confirmation before it, so
    setting `revoked` back to false by hand (which restores the old
    content sha) does not bring the old confirmation back.
  * problems() is the one check a harness runs before it uses a person:
    [] only when a confirm row after the last revoke matches the current
    content sha, the record is not revoked, today (kit.dates.host_today)
    is inside valid_from..valid_until, `uses` covers what this use needs,
    every scope dimension the use names allows it (or holds "*"), the
    signed document is on disk under the root with the recorded sha256,
    and the person is not blank. Each failure is its own coded Msg.
  * confirm() checks again, after the gate and before writing, that the
    record and its log did not move while the person was being asked
    (consent_changed_meanwhile).

Public API: Terms, draft(), content_sha(), log_path(), log_rows(),
confirmed_shas(), problems(), summary(), confirm(), revoke().

Paid for: in the source harness an agent set `status: confirmed` by
editing the consent JSON on the owner's word in chat, and the old check
read that field, so the person's face and voice were cleared for ads
without anyone passing the gate. Confirmation became a gate row bound to
the content hash; the status field is now only a label.

Test: kit/tests/test_consent.py.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from kit import dates, human
from kit.atomic import open_atomic, write_json_atomic
from kit.contract import HarnessError
from kit.messages import Msg, joined, msg

SCHEMA = "kit.consent/1"
ANY = "*"
CONFIRM_FIELDS = ("status", "confirmed_by", "confirmed_at", "confirm_reason")
LOG_SUFFIX = ".log.jsonl"
VERB = "consent confirm"


@dataclass(frozen=True)
class Terms:
    """The harness's closed sets: who a person may be to the advertiser
    (`relations`), what of them a record may cover (`uses`), and the scope
    dimensions every record must name (`scope_keys`; () = any keys)."""

    relations: tuple[str, ...]
    uses: tuple[str, ...]
    scope_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("relations", "uses", "scope_keys"):
            vals = tuple(getattr(self, name))
            object.__setattr__(self, name, vals)
            if (name != "scope_keys" and not vals) or any(
                    not isinstance(v, str) or not v.strip() or v == ANY
                    for v in vals):
                raise ValueError(f"Terms.{name} must be non-blank words "
                                 f"(not {ANY!r}), got {vals!r}")


# ---- the record ------------------------------------------------------------

def draft(record_id: str, *, terms: Terms, document: str = "") -> dict:
    """A blank record for the harness to fill from the signed document: no
    person, relation or uses yet, every declared scope dimension empty,
    valid for a year from today. It passes nothing until it is filled and
    a person confirms it."""
    today = dates.host_today()
    try:
        until = today.replace(year=today.year + 1)
    except ValueError:                    # 29 February
        until = today.replace(year=today.year + 1, day=28)
    return {"schema": SCHEMA, "id": record_id, "person": "", "relation": "",
            "uses": [], "scope": {k: [] for k in terms.scope_keys},
            "valid_from": today.isoformat(), "valid_until": until.isoformat(),
            "document": document, "document_sha256": "", "revoked": False,
            "status": "pending", "confirmed_by": "", "confirmed_at": "",
            "confirm_reason": "",
            "_how": "fill person, relation, uses, scope and the dates from "
                    "the signed document; put the scan at `document` and its "
                    "sha256 in document_sha256; then a person confirms it "
                    "through the gate. Setting status by hand does not "
                    "count."}


def content_sha(record: dict) -> str:
    """What a confirmation is bound to: the sha256 of the canonical JSON of
    every field but the confirmation's own and the `_` notes."""
    body = {k: v for k, v in record.items()
            if k not in CONFIRM_FIELDS and not k.startswith("_")}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _path(root: Path | str, record_path: Path | str) -> Path:
    return Path(root) / record_path


def _read(f: Path) -> dict:
    if not f.is_file():
        raise HarnessError(msg("consent_missing",
                               f"no consent record at {f}", path=str(f)))
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        rec, detail = None, str(e)
    else:
        detail = "not a JSON object"
    if not isinstance(rec, dict):
        raise HarnessError(msg("consent_unreadable",
                               f"{f} is not a consent record: {detail}",
                               path=str(f), detail=detail))
    return rec


# ---- the log ---------------------------------------------------------------

def log_path(record_path: Path | str) -> Path:
    """The record's append-only log: `<record minus .json>.log.jsonl`."""
    p = Path(record_path)
    return p.with_name((p.name[:-5] if p.name.endswith(".json")
                        else p.name) + LOG_SUFFIX)


def log_rows(record_path: Path | str) -> list[dict]:
    """Every row of the record's log, oldest first; [] when there is none.
    A row that does not parse is a coded refusal, never skipped."""
    f = log_path(record_path)
    if not f.is_file():
        return []
    rows = []
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        if not isinstance(row, dict):
            raise HarnessError(msg(
                "consent_log_unreadable",
                f"{f}:{n} is not a log row; the confirmations cannot be "
                f"proven, so none counts", path=str(f), line=n))
        rows.append(row)
    return rows


def confirmed_shas(record_path: Path | str) -> set[str]:
    """The content shas confirmed through the gate since the last revoke."""
    out: set[str] = set()
    for row in log_rows(record_path):
        if row.get("action") == "revoke":
            out.clear()
        elif row.get("action") == "confirm" and row.get("content_sha"):
            out.add(row["content_sha"])
    return out


def _append(record_path: Path, row: dict) -> None:
    """The log with `row` added, rewritten whole through kit.atomic (the
    lines already there are kept byte for byte, even one that does not
    parse), so a torn row is never on disk."""
    f = log_path(record_path)
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    with open_atomic(f) as out:
        out.write(text + json.dumps({"at": human.now(), **row},
                                    ensure_ascii=False, default=str) + "\n")


# ---- the checks ------------------------------------------------------------

def _day(v: object) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _strings(v: object) -> bool:
    return isinstance(v, list) and bool(v) and all(
        isinstance(x, str) and x.strip() for x in v)


def _document(root: Path, rec: dict) -> Msg | None:
    doc = rec.get("document")
    base = root.resolve()
    f = (base / doc).resolve() if isinstance(doc, str) and doc.strip() \
        else None
    if f is None or not f.is_relative_to(base) or not f.is_file():
        return msg("consent_document_missing",
                   f"the signed document {doc!r} is not a file under the "
                   f"harness root", document=str(doc))
    want = str(rec.get("document_sha256") or "").strip().lower()
    if _file_sha(f) != want:
        return msg("consent_document_changed",
                   f"the signed document {doc} is not the one recorded "
                   f"(document_sha256)", document=doc)
    return None


def _content(root: Path, rec: dict, terms: Terms) -> list[Msg]:
    """Everything about the record itself, apart from who confirmed it and
    what one use needs."""
    out = []
    if not isinstance(rec.get("person"), str) or not rec["person"].strip():
        out.append(msg("consent_person_blank",
                       "person is blank: the record names nobody"))
    if rec.get("relation") not in terms.relations:
        out.append(msg("consent_relation_unknown",
                       f"relation {rec.get('relation')!r} is not one of "
                       f"{list(terms.relations)}",
                       relation=str(rec.get("relation")),
                       relations=list(terms.relations)))
    uses = rec.get("uses")
    if not _strings(uses) or not set(uses) <= set(terms.uses):
        out.append(msg("consent_uses_invalid",
                       f"uses {uses!r} must be a non-empty subset of "
                       f"{list(terms.uses)}", uses=str(uses),
                       allowed=list(terms.uses)))
    scope, bad = rec.get("scope"), ""
    if not isinstance(scope, dict):
        bad = "scope is not an object of lists"
    else:
        unknown = sorted(set(scope) - set(terms.scope_keys)) \
            if terms.scope_keys else []
        missing = [k for k in terms.scope_keys if k not in scope]
        empty = sorted(k for k, v in scope.items() if not _strings(v))
        bad = "; ".join(x for x in (
            unknown and f"unknown dimension(s) {unknown}",
            missing and f"missing dimension(s) {missing}",
            empty and f"empty or non-text list(s) {empty}") if x)
    if bad:
        out.append(msg("consent_scope_invalid", f"scope: {bad}", detail=bad))
    start, end = _day(rec.get("valid_from")), _day(rec.get("valid_until"))
    if start is None or end is None or start > end:
        out.append(msg("consent_dates_invalid",
                       f"valid_from {rec.get('valid_from')!r} .. valid_until "
                       f"{rec.get('valid_until')!r} is not a date range",
                       valid_from=str(rec.get("valid_from")),
                       valid_until=str(rec.get("valid_until"))))
    if rec.get("revoked"):
        out.append(msg("consent_revoked", "the consent was revoked"))
    doc = _document(root, rec)
    if doc is not None:
        out.append(doc)
    return out


def _in_force(rec: dict, *, started: bool) -> Msg | None:
    """consent_not_in_force when today is past valid_until (or, with
    `started`, before valid_from); None when the dates are unreadable
    (consent_dates_invalid says so)."""
    start, end = _day(rec.get("valid_from")), _day(rec.get("valid_until"))
    if start is None or end is None:
        return None
    today = dates.host_today()
    if today > end or (started and today < start):
        return msg("consent_not_in_force",
                   f"not in force today ({today}): valid {start}..{end}",
                   today=today, valid_from=start, valid_until=end)
    return None


def problems(root: Path | str, record_path: Path | str, *, terms: Terms,
             need_uses: set[str] | frozenset[str] | tuple = (),
             need_scope: dict[str, str] | None = None) -> list[Msg]:
    """Why the record at `record_path` (relative to `root`, or absolute)
    does not clear a use that needs `need_uses` within `need_scope`
    ({dimension: value}); [] when it does."""
    root = Path(root)
    f = _path(root, record_path)
    try:
        rec = _read(f)
        shas = confirmed_shas(f)
    except HarnessError as e:
        return [e.message]
    out = []
    if content_sha(rec) not in shas:
        out.append(msg("consent_unconfirmed",
                       "not confirmed by a person, through the gate, for "
                       "this exact content (a hand-set status, or any edit "
                       "since the last confirmation, does not count)"))
    out += _content(root, rec, terms)
    late = _in_force(rec, started=True)
    if late is not None:
        out.append(late)
    uses = rec.get("uses") if isinstance(rec.get("uses"), list) else []
    missing = sorted(set(need_uses) - set(uses))
    if missing:
        out.append(msg("consent_uses_uncovered",
                       f"does not cover {', '.join(missing)}",
                       missing=missing))
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    for key, value in sorted((need_scope or {}).items()):
        allowed = scope.get(key) if isinstance(scope.get(key), list) else []
        if value not in allowed and ANY not in allowed:
            out.append(msg("consent_scope_uncovered",
                           f"scope.{key} {allowed} does not allow {value!r}",
                           key=key, value=value, allowed=allowed))
    return out


def summary(rec: dict) -> str:
    """The one line a person reads before confirming: who, as what, which
    uses, where, when, and which signed document."""
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    where = "; ".join(f"{k}: {', '.join(map(str, v))}"
                      for k, v in sorted(scope.items())
                      if isinstance(v, list))
    return (f"{rec.get('person')} ({rec.get('relation')}) consents to the "
            f"use of their {' + '.join(map(str, rec.get('uses') or []))} "
            f"[{where}], {rec.get('valid_from')}..{rec.get('valid_until')}; "
            f"signed document {rec.get('document')} sha256 "
            f"{str(rec.get('document_sha256'))[:12]}")


# ---- the writes ------------------------------------------------------------

def confirm(root: Path | str, record_path: Path | str, *, terms: Terms,
            record_id: str, reason: str | None, code: str | None = None,
            relay_user: str | None = None, relay_at: str | None = None,
            verb: str = VERB, market: str = "") -> dict:
    """A person confirms the record: they retype `record_id` at the
    terminal, or relay back a code bound to {record_id: content sha} and
    the log's length (`verb` and `market` go in that subject as well).
    Raises HarnessError (consent_incomplete, nothing written, no gate
    asked) or kit.human.Refused / CodeRequired (the gate)."""
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("confirm() needs the record id the person retypes")
    root = Path(root)
    f = _path(root, record_path)
    rec, rows = _read(f), log_rows(f)
    probs = _content(root, rec, terms)
    late = _in_force(rec, started=False)
    if late is not None:
        probs.append(late)
    if probs:
        detail = joined(probs)
        raise HarnessError(msg(
            "consent_incomplete",
            f"{record_id}: fix the record before a person confirms it: "
            f"{detail}. Nothing was written.",
            record=record_id, detail=detail))
    why = human.why(reason)
    sha, said = content_sha(rec), summary(rec)
    subj = human.subject(verb, market, "consent", {record_id: sha}, len(rows))
    channel = human.confirm(
        f"confirm consent {record_id}",
        f"{said}\nretype {record_id} to confirm it: ", record_id,
        subj=subj, code=code)
    why += human.relay_audit(channel, relay_user, relay_at)
    now_rec, now_rows = _read(f), log_rows(f)
    if content_sha(now_rec) != sha or len(now_rows) != len(rows):
        raise HarnessError(msg(
            "consent_changed_meanwhile",
            f"{record_id} changed while the person was being asked, so the "
            f"confirmation no longer matches it. Nothing was written.",
            record=record_id))
    by, at = human.changed_by(channel), human.now()
    _append(f, {"action": "confirm", "record": record_id,
                          "content_sha": sha, "reason": why,
                          "changed_by": by, "summary": said})
    now_rec.update({"status": "confirmed", "confirmed_by": by,
                    "confirmed_at": at, "confirm_reason": why})
    write_json_atomic(f, now_rec, indent=2)
    return {"record": record_id, "status": "confirmed", "confirmed_by": by,
            "confirmed_at": at, "content_sha": sha, "summary": said,
            "message": msg("consent_confirm_done",
                           f"{record_id}: confirmed by {by} for this exact "
                           f"content; any edit needs a new confirmation",
                           record=record_id, by=by)}


def revoke(root: Path | str, record_path: Path | str, *, record_id: str,
           reason: str | None) -> dict:
    """Anyone lowers trust: no gate, a required reason, a `revoke` row that
    voids every earlier confirmation. It works even when the log does not
    parse: lowering trust is never blocked."""
    f = _path(root, record_path)
    rec = _read(f)
    why = human.why(reason)
    rec.update({"revoked": True, "status": "revoked"})
    by = human.changed_by("cli")
    _append(f, {"action": "revoke", "record": record_id,
                      "content_sha": content_sha(rec), "reason": why,
                      "changed_by": by})
    write_json_atomic(f, rec, indent=2)
    return {"record": record_id, "status": "revoked", "changed_by": by,
            "message": msg("consent_revoke_done",
                           f"{record_id}: revoked; nothing may use it until a "
                           f"person confirms it again through the gate",
                           record=record_id)}
