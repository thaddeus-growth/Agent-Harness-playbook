"""The approval queue: nothing is written to an external system unapproved.

Ported from the reference harness's `queue_actions.py` + `_lib/approvals.py`
(the generic core; the domain's flatten/refusal/slot rules become the
harness's compute and its `validate` hook). The queue reads a proposal
document and writes only `action_queue`; it never calls an external API
(kit.execute does, after a human approved). What it guards:

  * `add [--market M] [--from FILE]`: the snapshot is the harness's own
    compute (the `snapshot` hook) or a saved copy of it, never recomputed
    here. A snapshot run with `--assume` (non-empty
    meta.assumed_thresholds) is a what-if and is refused whole: only
    confirmed thresholds may drive a write. Every proposal's market is
    validated and must be declared (kit.market). `action_id` = sha256 of
    canonical {kind, market, target_ref, payload, basis}, so ANY change
    to what a proposal rests on (its basis: the rows its rule read plus
    the ingest version) is a different action, and the same action twice
    is one row (UNIQUE in the file). A live (pending or approved) row for
    a target the new snapshot proposes something else for is superseded.
    Each new proposal passes the harness's `validate(con, proposal)` or is
    recorded as `rejected` with the refusal's code in `reason_code`
    (decided_by `validate`), so a later run can read why (`rejected()`).
    A rejected proposal is never proposed again unchanged: the same
    action_id stays one rejected row.
  * `approve ID… --reason R` is a human act through kit.human.confirm:
    retype `approve` at the terminal, or off a TTY a relayed one-time
    code bound to subject ("queue approve", markets, "action_queue",
    {id: [market, kind, target_ref, payload, basis]}, sorted [id, status,
    decided_at]); every approval moves decided_at, so a code is single
    use. Only a pending row may be approved: superseded, expired (status
    `expired`, or an approval older than `ttl_hours`) and every other
    status are refused, nothing written. Under the write lock every row
    is checked again (queue_item_changed).
  * `reject ID… --reason R --reason-code CODE` lowers risk, so anyone,
    agents included, may run it (no gate); the snake_case reason code is
    what the next proposal run reads (`rejected()`).
  * `list [--market M] [--status S]` is the one read verb: it opens the
    file read-only and never creates it.

Rows are never deleted and a queued proposal is frozen by triggers in the
file (kit.schema_base): only status, decided_by, decided_at, reason and
reason_code move.

Deviations (SPEC §queue):
  * Supersede covers live rows (pending AND approved), as the reference
    did: an approval of an older proposal for a target the new snapshot
    proposes something else for must not be executed. Rows for targets
    the snapshot does not mention are left alone (execute's basis check
    refuses them if their data moved).
  * `add` re-opens (status back to pending, decided_by `snapshot`) a row
    whose identical proposal is proposed again after it was superseded
    or its approval expired: action_id is UNIQUE, so it cannot be queued
    twice, and this is the kit's way to renew an expired approval (the
    reference re-approved an approved row; the SPEC refuses that). A
    rejected, executed, failed or unknown row is never re-opened.
  * Every verb refuses with no_db when the file is missing (a queue is
    only ever filled after `facts init`); `reject` takes an optional
    closed set of reason codes (`reason_codes=`).
  * The payload is bound in the approve subject as its stored canonical
    JSON text (the reference bound its stored JSON old/new texts), so the
    console's `expect` holds a string whatever the payload's depth
    (kit.pending builds that `expect` from approve_items()).
  * A proposal's optional `because` / `because_code` (the rule's own
    coded words) is kept in its evidence.

Test: kit/tests/test_queue.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from kit import contract, dates, db, human
from kit import market as markets
from kit.config import config
from kit.contract import HarnessError
from kit.messages import Msg, code, coded, msg

TABLE = "action_queue"
LIVE = ("pending", "approved")
REVIVABLE = ("superseded", "expired")
PROPOSAL_KEYS = ("kind", "market", "target_ref", "payload", "basis")
JSON_COLUMNS = ("payload", "evidence", "expected")
SNAPSHOT = "snapshot"        # decided_by of a row a newer snapshot moved
VALIDATE = "validate"        # decided_by of a proposal `validate` refused
APPROVE_WORD = "approve"     # what a human retypes at the terminal
REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

Snapshot = Callable[[str | None], dict]
Validate = Callable[[sqlite3.Connection, dict], Msg | None]
TtlHours = Callable[[sqlite3.Connection, str], float]


# ---- rows ------------------------------------------------------------------

def _all(con: sqlite3.Connection, sql: str, args: Iterable = ()
         ) -> list[dict]:
    cur = con.execute(sql, tuple(args))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(con: sqlite3.Connection, sql: str, args: Iterable = ()
         ) -> dict | None:
    rows = _all(con, sql, args)
    return rows[0] if rows else None


def canonical(obj: Any) -> str:
    """Sorted keys, compact, non-ASCII kept; NaN/inf refused (ValueError)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False, default=str)


def action_id(proposal: dict) -> str:
    """sha256 of canonical {kind, market, target_ref, payload, basis}."""
    core = {k: proposal[k] for k in PROPOSAL_KEYS}
    return hashlib.sha256(canonical(core).encode()).hexdigest()


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def row_dict(r: dict) -> dict:
    """A queue row with its JSON columns parsed."""
    d = dict(r)
    for k in JSON_COLUMNS:
        if k in d:
            d[k] = _loads(d[k])
    return d


def fetch(con: sqlite3.Connection, queue_id: int) -> dict | None:
    """One queue row as stored (JSON columns as text), None if absent."""
    return _one(con, f"SELECT * FROM {TABLE} WHERE id=?", (queue_id,))


def approve_items(rows: Iterable[dict]) -> dict[str, list]:
    """What an approval binds, per row: {id: [market, kind, target_ref,
    payload (stored canonical JSON text), basis]}. The gate's subject and
    kit.pending's console `expect` are both built here."""
    return {str(r["id"]): [r["market"], r["kind"], r["target_ref"],
                           r["payload"], r["basis"]] for r in rows}


def approve_market(rows: Iterable[dict]) -> str:
    """The subject's market: the rows' markets, sorted, comma-joined."""
    return ",".join(sorted({r["market"] for r in rows}))


def approve_subject(rows: list[dict]) -> str:
    """The subject a relayed approval code is bound to."""
    return human.subject(
        "queue approve", approve_market(rows), TABLE, approve_items(rows),
        # every approval moves decided_at, so a used code goes stale
        sorted([r["id"], r["status"], r["decided_at"]] for r in rows))


def expired(row: dict, ttl_hours: float, at: datetime | None = None) -> bool:
    """An `expired` row, or an approval older than ttl_hours."""
    if row["status"] == "expired":
        return True
    if row["status"] != "approved" or not row["decided_at"]:
        return False
    decided = datetime.fromisoformat(row["decided_at"])
    return (at or dates.now()) - decided > timedelta(hours=ttl_hours)


def rejected(con: sqlite3.Connection, market: str, target_ref: str,
             kind: str | None = None) -> list[dict]:
    """The "no"s a proposal run reads before proposing again: every
    rejected row for this market and target (and kind), newest first,
    with its reason and reason_code (decided_by `validate` = the
    harness's own check; anything else = a person or an agent)."""
    sql = (f"SELECT id, action_id, kind, target_ref, payload, basis, reason, "
           f"reason_code, decided_by, decided_at FROM {TABLE} WHERE "
           f"status='rejected' AND market=? AND target_ref=?")
    args: list = [market, target_ref]
    if kind is not None:
        sql += " AND kind=?"
        args.append(kind)
    return [row_dict(r) for r in
            _all(con, sql + " ORDER BY decided_at DESC, id DESC", args)]


# ---- add -------------------------------------------------------------------

def load_snapshot(explicit: str | None, src: str | None,
                  snapshot: Snapshot) -> dict:
    """`--from FILE` (a saved proposal document) or a fresh run of the
    harness's compute (the `snapshot` hook), checked for its shape."""
    if src:
        try:
            doc = json.loads(Path(src).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise HarnessError(msg(
                "queue_snapshot_unreadable",
                f"cannot read the snapshot {src}: {e}. Nothing was written",
                path=str(src), detail=str(e))) from None
    else:
        doc = snapshot(explicit)
    problem = None
    if not isinstance(doc, dict):
        problem = "not a JSON object"
    elif not isinstance(doc.get("meta"), dict):
        problem = "no meta object"
    elif not isinstance(doc.get("proposals"), list):
        problem = "no proposals list"
    if problem:
        raise HarnessError(msg(
            "queue_snapshot_invalid",
            f"not a proposal document ({problem}): expected {{meta, "
            f"proposals: [...]}}. Nothing was written", problem=problem))
    return doc


def _proposal(i: int, p: Any, explicit: str | None) -> dict:
    def bad(problem: str) -> HarnessError:
        return HarnessError(msg(
            "queue_proposal_invalid",
            f"proposal {i}: {problem}. Nothing was written",
            index=i, problem=problem))
    if not isinstance(p, dict):
        raise bad("not an object")
    for k in ("kind", "target_ref", "basis"):
        if not isinstance(p.get(k), str) or not p[k].strip():
            raise bad(f"{k} must be a non-empty string")
    if not isinstance(p.get("payload"), dict):
        raise bad("payload must be an object")
    for k in ("evidence", "expected"):
        if p.get(k) is not None and not isinstance(p[k], dict):
            raise bad(f"{k} must be an object")
    m = markets.validate(p.get("market"))
    if explicit and m != explicit:
        raise HarnessError(msg(
            "queue_snapshot_market",
            f"proposal {i} is for market {m}, not {explicit}. Nothing was "
            f"written", market=explicit, found=m))
    out = {"kind": p["kind"], "market": m, "target_ref": p["target_ref"],
           "payload": p["payload"], "basis": p["basis"],
           "evidence": dict(p.get("evidence") or {}),
           "expected": dict(p.get("expected") or {})}
    for k in ("because", "because_code"):          # the rule's own words
        if p.get(k) is not None:
            out["evidence"].setdefault(k, p[k])
    try:
        canonical(out)
    except (ValueError, TypeError) as e:
        raise bad(f"not plain JSON ({e})") from None
    return out


def add(spec: db.SchemaSpec, con: sqlite3.Connection, doc: dict,
        validate: Validate, *, explicit: str | None = None) -> dict:
    """Queue one proposal document (already loaded); returns the report.
    All or nothing: one keep_human_rows transaction."""
    meta = doc["meta"]
    if meta.get("assumed_thresholds"):
        names = sorted(meta["assumed_thresholds"])
        raise HarnessError(msg(
            "queue_snapshot_assumed",
            f"the snapshot ran with --assume ({', '.join(names)}): a "
            f"what-if, not the live state. Confirm the threshold or rerun "
            f"the compute without --assume. Nothing was written",
            assumed=names))
    props: dict[str, dict] = {}
    for i, p in enumerate(doc["proposals"]):
        n = _proposal(i, p, explicit)
        props.setdefault(action_id(n), n)
    for m in sorted({p["market"] for p in props.values()}):
        markets.require_declared(con, m)
    targets = {(p["market"], p["target_ref"]) for p in props.values()}
    rep: dict[str, list] = {"queued": [], "known": [], "rejected": [],
                            "superseded": [], "revived": [], "refused": []}
    at = human.now()
    with db.keep_human_rows(spec, con):
        live = _all(con, f"SELECT id, market, target_ref, action_id FROM "
                         f"{TABLE} WHERE status IN (?, ?) ORDER BY id", LIVE)
        for r in live:
            if (r["market"], r["target_ref"]) not in targets \
                    or r["action_id"] in props:
                continue
            why = msg("queue_superseded",
                      f"superseded: a newer snapshot proposes something else "
                      f"for {r['target_ref']}", target_ref=r["target_ref"])
            con.execute(
                f"UPDATE {TABLE} SET status='superseded', decided_by=?, "
                f"decided_at=?, reason=?, reason_code=? WHERE id=?",
                (SNAPSHOT, at, str(why), why.code, r["id"]))
            rep["superseded"].append(r["id"])
        for aid, p in props.items():
            row = _one(con, f"SELECT id, status FROM {TABLE} WHERE "
                            f"action_id=?", (aid,))
            if row is not None and row["status"] not in REVIVABLE:
                rep["known"].append({"id": row["id"],
                                     "status": row["status"]})
                continue
            why = validate(con, p)
            if why is not None:
                code(why)                   # a plain str is a bug: TypeError
            if row is not None:
                if why is not None:
                    rep["refused"].append({"id": row["id"], "action_id": aid,
                                           **coded("why", why)})
                    continue
                again = msg("queue_revived",
                            f"proposed again by a newer snapshot (was "
                            f"{row['status']})", status=row["status"])
                con.execute(
                    f"UPDATE {TABLE} SET status='pending', decided_by=?, "
                    f"decided_at=?, reason=?, reason_code=? WHERE id=?",
                    (SNAPSHOT, at, str(again), again.code, row["id"]))
                rep["revived"].append(row["id"])
                continue
            cur = con.execute(
                f"INSERT INTO {TABLE} (market, action_id, kind, target_ref, "
                f"payload, evidence, basis, expected, status, created_at, "
                f"decided_by, decided_at, reason, reason_code) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["market"], aid, p["kind"], p["target_ref"],
                 canonical(p["payload"]), canonical(p["evidence"]),
                 p["basis"], canonical(p["expected"]),
                 "rejected" if why else "pending", at,
                 VALIDATE if why else None, at if why else None,
                 str(why) if why else None, why.code if why else None))
            if why is None:
                rep["queued"].append(cur.lastrowid)
            else:
                rep["rejected"].append({"id": cur.lastrowid,
                                        "target_ref": p["target_ref"],
                                        "kind": p["kind"],
                                        **coded("why", why)})
    warnings = []
    if meta.get("stale"):
        tables = [str(s.get("table")) for s in meta["stale"]
                  if isinstance(s, dict)]
        warnings.append(msg(
            "queue_snapshot_stale",
            f"the snapshot's data is stale ({', '.join(tables)}); refresh "
            f"it before approving: execute refuses an item whose basis has "
            f"moved", stale=tables))
    summary = msg(
        "queue_added",
        f"queued {len(rep['queued'])} new, {len(rep['known'])} already "
        f"queued, {len(rep['rejected'])} rejected by validation, "
        f"{len(rep['superseded'])} superseded, {len(rep['revived'])} "
        f"proposed again", queued=len(rep["queued"]),
        known=len(rep["known"]), rejected=len(rep["rejected"]),
        superseded=len(rep["superseded"]), revived=len(rep["revived"]))
    return {**coded("message", summary),
            "markets": sorted({p["market"] for p in props.values()}),
            **rep, **coded("warnings", warnings)}


# ---- decisions -------------------------------------------------------------

def _rows(con: sqlite3.Connection, ids: list[int], allowed: tuple[str, ...],
          ttl_hours: TtlHours | None) -> list[dict]:
    """The rows to decide, or a coded refusal before anything is written."""
    rows = []
    for i in sorted(set(ids)):
        r = fetch(con, i)
        if r is None:
            raise HarnessError(msg(
                "queue_item_not_found",
                f"no queue item #{i}. Nothing was written", id=i))
        if r["status"] == "superseded" and "superseded" not in allowed:
            raise HarnessError(msg(
                "queue_item_superseded",
                f"#{i} was superseded ({r['reason']}); approve the newer "
                f"proposal instead. Nothing was written", id=i))
        if ttl_hours is not None:
            ttl = ttl_hours(con, r["market"])
            if expired(r, ttl):
                raise HarnessError(msg(
                    "queue_item_expired",
                    f"#{i}: its approval of {r['decided_at']} is older than "
                    f"{ttl:g}h; a fresh `queue add` proposes it again. "
                    f"Nothing was written", id=i,
                    decided_at=r["decided_at"], ttl_hours=ttl))
        if r["status"] not in allowed:
            raise HarnessError(msg(
                "queue_item_wrong_status",
                f"#{i} is {r['status']}, not {' or '.join(allowed)}. "
                f"Nothing was written", id=i, status=r["status"],
                allowed=list(allowed)))
        rows.append(r)
    return rows


def _unchanged(con: sqlite3.Connection, rows: list[dict]) -> None:
    """Inside the write transaction: every row still as it was read."""
    for r in rows:
        now = fetch(con, r["id"])
        if (now["status"], now["decided_at"]) != (r["status"],
                                                  r["decided_at"]):
            raise HarnessError(msg(
                "queue_item_changed",
                f"#{r['id']} changed while it was being decided; list it "
                f"again. Nothing was written", id=r["id"]))


def _ids(rows: Iterable[dict]) -> str:
    return ", ".join(f"#{r['id']}" for r in rows)


def _summary(rows: list[dict]) -> str:
    def one(r: dict) -> str:
        p = r["payload"] if len(r["payload"]) <= 120 else \
            r["payload"][:119] + "…"
        return f"#{r['id']} [{r['market']}] {r['kind']} {r['target_ref']} {p}"
    return "; ".join(one(r) for r in rows)


def approve(spec: db.SchemaSpec, con: sqlite3.Connection, ids: list[int], *,
            reason: str | None, code_: str | None = None,
            relay_user: str | None = None, relay_at: str | None = None,
            ttl_hours: TtlHours) -> list[dict]:
    """The gate, then the approval of every row in `ids`."""
    why = human.why(reason)
    rows = _rows(con, ids, ("pending",), ttl_hours)
    what = f"`{config().cli} queue approve`"
    channel = human.confirm(
        what, f"approve {len(rows)} item(s): {_summary(rows)}; type "
              f"'{APPROVE_WORD}' to approve {_ids(rows)}: ", APPROVE_WORD,
        subj=approve_subject(rows), code=code_)
    why += human.relay_audit(channel, relay_user, relay_at)
    who, at = human.changed_by(channel), human.now()
    with db.keep_human_rows(spec, con):
        _unchanged(con, rows)
        for r in rows:
            con.execute(f"UPDATE {TABLE} SET status='approved', "
                        f"decided_by=?, decided_at=?, reason=?, "
                        f"reason_code=NULL WHERE id=?",
                        (who, at, why, r["id"]))
    return [row_dict(fetch(con, r["id"])) for r in rows]


def reject(spec: db.SchemaSpec, con: sqlite3.Connection, ids: list[int], *,
           reason: str | None, reason_code: str | None,
           reason_codes: Iterable[str] | None = None) -> list[dict]:
    """A "no" (no gate: it lowers risk), with the reason code the next
    proposal run reads."""
    why = human.why(reason)
    rc = (reason_code or "").strip()
    if not REASON_CODE_RE.match(rc):
        raise HarnessError(msg(
            "queue_reason_code_invalid",
            f"--reason-code {reason_code!r}: one snake_case word (a-z, 0-9, "
            f"_), at most 64 characters. Nothing was written",
            reason_code=reason_code or ""))
    allowed = sorted(reason_codes) if reason_codes is not None else None
    if allowed is not None and rc not in allowed:
        raise HarnessError(msg(
            "queue_reason_code_unknown",
            f"--reason-code {rc}: not one of {', '.join(allowed)}. Nothing "
            f"was written", reason_code=rc, allowed=allowed))
    rows = _rows(con, ids, LIVE, None)
    who, at = human.changed_by("cli"), human.now()
    with db.keep_human_rows(spec, con):
        _unchanged(con, rows)
        for r in rows:
            con.execute(f"UPDATE {TABLE} SET status='rejected', "
                        f"decided_by=?, decided_at=?, reason=?, "
                        f"reason_code=? WHERE id=?", (who, at, why, rc,
                                                      r["id"]))
    return [row_dict(fetch(con, r["id"])) for r in rows]


def listing(con: sqlite3.Connection, *, market: str | None = None,
            status: str = "live", ttl_hours: TtlHours) -> list[dict]:
    """Queue rows (live = pending + approved; `all`; or one status), in
    queue order, each with `expired`."""
    where, args = [], []
    if market:
        where.append("market=?")
        args.append(market)
    if status == "live":
        where.append("status IN (?, ?)")
        args += LIVE
    elif status != "all":
        where.append("status=?")
        args.append(status)
    sql = f"SELECT * FROM {TABLE}" + (
        " WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    ttl: dict[str, float] = {}
    out = []
    for r in _all(con, sql, args):
        if r["market"] not in ttl:
            ttl[r["market"]] = ttl_hours(con, r["market"])
        out.append({**row_dict(r), "expired": expired(r, ttl[r["market"]])})
    return out


def pending(con: sqlite3.Connection, market: str | None = None
            ) -> list[dict]:
    """Every row waiting on a human's yes/no (status pending), as stored
    (JSON columns as text), in queue order: what `<cli> pending` asks."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                   "name=?", (TABLE,)).fetchone() is None:
        return []
    sql = f"SELECT * FROM {TABLE} WHERE status='pending'"
    args: list = []
    if market is not None:
        sql += " AND market=?"
        args.append(market)
    return _all(con, sql + " ORDER BY id", args)


# ---- the CLI ---------------------------------------------------------------

def _parser(prog: str) -> argparse.ArgumentParser:
    from kit.schema_base import QUEUE_STATUSES
    p = contract.Parser(prog=prog, description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="verb", required=True)
    sp = sub.add_parser("add", help="queue a proposal snapshot (the "
                                    "harness's compute, or --from FILE)")
    sp.add_argument("--market")
    sp.add_argument("--from", dest="src", metavar="FILE",
                    help="a saved proposal document instead of a fresh run")
    contract.add_json_arg(sp)
    sp = sub.add_parser("list", help="queued proposals (read-only)")
    sp.add_argument("--market")
    sp.add_argument("--status", default="live",
                    choices=("live", "all", *QUEUE_STATUSES),
                    help="live = pending + approved (default)")
    contract.add_json_arg(sp)
    sp = sub.add_parser("approve", help="a human act: retype 'approve' at "
                                        "a terminal, or a relayed --code")
    sp.add_argument("ids", nargs="+", type=int, metavar="ID")
    human.add_gate_args(sp)
    contract.add_json_arg(sp)
    sp = sub.add_parser("reject", help="anyone, agents included: a no with "
                                       "its reason code")
    sp.add_argument("ids", nargs="+", type=int, metavar="ID")
    sp.add_argument("--reason", help="why (required)")
    sp.add_argument("--reason-code", help="one snake_case word the next "
                                          "proposal run can read (required)")
    contract.add_json_arg(sp)
    return p


def _print_rows(items: list[dict]) -> None:
    if not items:
        print("queue empty")
        return
    for i in items:
        state = i["status"] + (" EXPIRED" if i["expired"] else "")
        if i["decided_by"]:
            state += f" by {i['decided_by']} {(i['decided_at'] or '')[:16]}"
        print(f"  #{i['id']:<4} [{i['market']}] {i['kind']} {i['target_ref']}"
              f" {canonical(i['payload'])}  ({state})")


def main(argv: list[str] | None = None, *, spec: db.SchemaSpec,
         snapshot: Snapshot, validate: Validate, ttl_hours: TtlHours,
         reason_codes: Iterable[str] | None = None) -> int:
    """`<cli> queue …`. Hooks: snapshot(market | None) -> proposal document
    (a fresh compute); validate(con, proposal) -> Msg | None (None = fine);
    ttl_hours(con, market) -> how long an approval stays good."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = config()
    cmd = [cfg.cli, "queue", *argv]
    parser = _parser(f"{cfg.cli} queue")

    def run(argv: list[str]) -> Any:
        a = parser.parse_args(argv)
        if (e := contract.missing_db_error()) is not None:
            raise e
        con = db.connect(spec, read_only=a.verb == "list")
        try:
            return _verb(con, a)
        finally:
            con.close()

    def _verb(con: sqlite3.Connection, a: argparse.Namespace) -> Any:
        if a.verb == "list":
            m = markets.validate(a.market) if a.market else None
            items = listing(con, market=m, status=a.status,
                            ttl_hours=ttl_hours)
            if a.json:
                return {"market": m, "status": a.status, "items": items}
            _print_rows(items)
            return None
        if a.verb == "add":
            explicit = markets.validate(a.market) if a.market else None
            doc = load_snapshot(explicit, a.src, snapshot)
            rep = add(spec, con, doc, validate, explicit=explicit)
            if a.json:
                return rep
            print(rep["message"])
            for r in rep["rejected"]:
                print(f"  rejected #{r['id']} {r['kind']} {r['target_ref']}: "
                      f"{r['why']}")
            for w in rep["warnings"]:
                print(f"  warning: {w}")
            return None
        if a.verb == "approve":
            items = approve(spec, con, a.ids, reason=a.reason, code_=a.code,
                            relay_user=a.relay_user, relay_at=a.relay_at,
                            ttl_hours=ttl_hours)
            done = msg("queue_approved",
                       f"approved {_ids(items)} by {items[0]['decided_by']}",
                       ids=[i["id"] for i in items],
                       by=items[0]["decided_by"])
        else:
            items = reject(spec, con, a.ids, reason=a.reason,
                           reason_code=a.reason_code,
                           reason_codes=reason_codes)
            done = msg("queue_rejected",
                       f"rejected {_ids(items)} ({items[0]['reason_code']})",
                       ids=[i["id"] for i in items],
                       reason_code=items[0]["reason_code"])
        if a.json:
            return {**coded("message", done), "items": items}
        print(done)
        return None

    return contract.run_main(run, argv, cmd=cmd)
