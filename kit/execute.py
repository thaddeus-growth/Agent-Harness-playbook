"""The executor: approved queue items go out through the guarded writer,
each with a durable effect record, and never twice.

Ported from the reference harness's `execute_actions.py` (the generic
core; its domain guards, re-reads and API calls become the harness's
hooks) plus prior-art #10 (a basis hash; an effect ledger where an
ambiguous send blocks its target until a read-back reconciles it).

    apply [--market M] [--dry-run | --apply] [--json]
    reconcile ID [--json]

What it guards:

  * Dry run by default. Without `--apply` nothing is written and nothing
    is called: no writer is built, no effect row, no status change. The
    plan (`--json`: one document) shows per approved item its verdict,
    coded, and the numbers each cap was checked with.
  * Only `approved` rows of the resolved market (kit.market: validated
    and declared) are candidates, in queue order.
  * An `unknown` effect blocks its target: while any effect for the same
    market and target_ref has `sent` or `unknown` as its latest state, no
    item on that target is sent (execute_unknown_effect_blocks) until
    `execute reconcile ID` reads the target back. Checked in the plan and
    again right before each send.
  * Basis staleness: `plan_item(con, row)` recomputes the basis (a hash of
    the rows the rule reads now plus the ingest version); an approval
    rests on the basis it was proposed on, so a mismatch refuses the item
    (execute_basis_changed; kit.human.stale_basis) and, under --apply,
    marks the row superseded (reason_code execute_basis_changed), so the
    next `queue add` proposes afresh. An approval older than `ttl_hours`
    is refused (execute_expired) and, under --apply, marked expired.
  * The harness's own refusal (`plan_item` returns {"refusal": Msg}) is
    the item's verdict, its code kept.
  * Per-run caps (thresholds, via hooks): at most `max_items` items are
    sent per run, and the |delta| of the sent items together (`plan_item`
    returns {"delta": number}, e.g. a daily spend change) stays within
    `max_delta`; items past a cap are deferred to a later run in queue
    order; with a delta cap set, an item without a delta is refused
    (execute_delta_unknown): its size cannot be proven within the cap.
  * The write switches first: under --apply with anything to send, the
    writer's Guard is checked (kit.write_guard: kill switch, STOP file,
    `<P>_ALLOW_WRITES=1`) before any row is written; a refusal writes
    nothing. One run at a time (a lock under the data dir, execute_busy).
  * The effect ledger (action_effects, append-only; effect_id =
    action_id): `pending` when the item is claimed, `sent` right before
    `apply_item` makes its call, then `confirmed` or `failed` from
    `read_back`, or `unknown` when the call or the read-back raised or
    could not tell (a timeout after send is ambiguous: never re-sent).
    The queue row follows: executed | failed | unknown. A write refused
    by the Guard's allowlist sent nothing (failed); a switch that trips
    mid-run (kill, STOP, opt-in gone) stops the run, the refused item
    stays approved and later items are not tried.
  * `reconcile ID`: for a row whose latest effect is `sent` or `unknown`,
    `read_back(writer, row, None)` decides: True -> confirmed + executed,
    False -> failed + failed, None -> nothing written, still blocked
    (execute_reconcile_unsure).

Hooks (all harness code; the kit knows no API):

  * writer_factory() -> kit.write_guard.Writer (built only under --apply
    or reconcile)
  * plan_item(con, row) -> {"basis": str, "refusal"?: Msg | None,
    "delta"?: number | None, "request"?: JSON}. `row` is the queue row
    with payload/evidence/expected parsed. Read-only; called in the dry
    run too.
  * apply_item(writer, row, plan) -> JSON response. Makes ONE external
    write through `writer.call` (reads before it are fine).
  * read_back(writer, row, response | None) -> True | False | None.

Deviations (SPEC §execute): the extra keyword hooks ttl_hours, max_items
and max_delta (the SPEC's "caps (thresholds)"); `--json` is allowed with
`--apply` (one document: the outcomes) where the reference refused it;
a run with a failed or unknown item exits 1, as the reference did; an
item superseded or expired by execute keeps its approver in
decided_by/decided_at (only status, reason and reason_code move).

Test: kit/tests/test_execute.py.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Callable

from kit import contract, db, human
from kit import market as markets
from kit import paths
from kit import queue as q
from kit.config import config
from kit.contract import HarnessError
from kit.messages import Msg, code, coded, failure, msg
from kit.single_instance import hold
from kit.write_guard import WriteRefused, Writer

EFFECTS = "action_effects"
BLOCKING = ("sent", "unknown")          # latest effect states that block
SWITCH_CODES = ("write_killed", "write_stop_file", "write_stop_unchecked",
                "write_off")
LOCK_FILE = ".execute.lock"
GO = "go"

WriterFactory = Callable[[], Writer]
PlanItem = Callable[[sqlite3.Connection, dict], dict]
ApplyItem = Callable[[Writer, dict, dict], Any]
ReadBack = Callable[[Writer, dict, Any], "bool | None"]
Cap = Callable[[sqlite3.Connection, str], "float | None"]


# ---- the effect ledger -----------------------------------------------------

def unplanned(con: sqlite3.Connection, row: dict) -> dict:
    """The `plan_item` hook of a harness with no executor yet (a new
    harness, B1): every approved item is refused, coded, with its saved
    basis, so a dry run shows it and --apply sends nothing."""
    return {"basis": row["basis"], "refusal": msg(
        "execute_unplanned", f"#{row['id']} {row['kind']} on "
        f"{row['target_ref']}: this harness has no executor for it yet",
        id=row["id"], kind=row["kind"], target_ref=row["target_ref"])}


def effects(con: sqlite3.Connection, queue_id: int) -> list[dict]:
    """Every effect row of one queue item, oldest first."""
    return q._all(con, f"SELECT * FROM {EFFECTS} WHERE queue_id=? "
                       f"ORDER BY id", (queue_id,))


def latest(con: sqlite3.Connection, queue_id: int) -> dict | None:
    """The newest effect row of one queue item (its current state)."""
    return q._one(con, f"SELECT * FROM {EFFECTS} WHERE queue_id=? "
                       f"ORDER BY id DESC LIMIT 1", (queue_id,))


def blocker(con: sqlite3.Connection, market: str, target_ref: str
            ) -> dict | None:
    """The queue item whose ambiguous effect blocks this target, with its
    `state`, or None: any item of the same market and target whose
    latest effect is sent or unknown."""
    rows = q._all(
        con, f"SELECT e.queue_id AS id, e.state FROM {EFFECTS} e JOIN "
             f"{q.TABLE} a ON a.id = e.queue_id WHERE a.market=? AND "
             f"a.target_ref=? AND e.id = (SELECT MAX(id) FROM {EFFECTS} "
             f"WHERE queue_id = e.queue_id) ORDER BY e.queue_id",
        (market, target_ref))
    return next((r for r in rows if r["state"] in BLOCKING), None)


def _record(spec: db.SchemaSpec, con: sqlite3.Connection, row: dict,
            state: str, *, request: Any = None, response: Any = None,
            status: str | None = None) -> None:
    """One effect row (and the queue row's new status) in one transaction."""
    with db.keep_human_rows(spec, con):
        con.execute(
            f"INSERT INTO {EFFECTS} (queue_id, market, effect_id, state, "
            f"request, response, at, changed_by) VALUES (?,?,?,?,?,?,?,?)",
            (row["id"], row["market"], row["action_id"], state,
             None if request is None else q.canonical(request),
             None if response is None else q.canonical(response),
             human.now(), human.changed_by("cli")))
        if status is not None:
            con.execute(f"UPDATE {q.TABLE} SET status=? WHERE id=?",
                        (status, row["id"]))


def _set_status(spec: db.SchemaSpec, con: sqlite3.Connection, row: dict,
                status: str, why: Msg) -> None:
    """superseded / expired by execute: the approver stays in decided_by."""
    with db.keep_human_rows(spec, con):
        con.execute(f"UPDATE {q.TABLE} SET status=?, reason=?, reason_code=? "
                    f"WHERE id=? AND status='approved'",
                    (status, str(why), why.code, row["id"]))


# ---- the plan --------------------------------------------------------------

def _blocked(row: dict, b: dict) -> Msg:
    return msg(
        "execute_unknown_effect_blocks",
        f"#{row['id']}: #{b['id']} on {row['target_ref']} is {b['state']}: "
        f"it may have been written. Nothing goes to this target until "
        f"`{config().cli} execute reconcile {b['id']}` reads it back",
        id=row["id"], target_ref=row["target_ref"], blocker=b["id"],
        state=b["state"])


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) \
        and not isinstance(v, bool) else None


def plan(con: sqlite3.Connection, market: str, *, plan_item: PlanItem,
         ttl_hours: Cap | None = None, max_items: Cap | None = None,
         max_delta: Cap | None = None) -> list[dict]:
    """Every approved item of `market`, in queue order: {row (payload …
    parsed), plan (plan_item's), verdict (Msg; GO = send), guards (the
    numbers each check used), to (the status --apply moves it to, or
    None)}. Reads only."""
    rows = q._all(con, f"SELECT * FROM {q.TABLE} WHERE market=? AND "
                       f"status='approved' ORDER BY id", (market,))
    cap_n = max_items(con, market) if max_items else None
    cap_d = max_delta(con, market) if max_delta else None
    ttl = ttl_hours(con, market) if ttl_hours else None
    sent, used = 0, 0.0
    out = []
    for raw in rows:
        row = q.row_dict(raw)
        guards: dict = {}
        to = None
        p: dict = {}
        b = blocker(con, market, row["target_ref"])
        if b is not None:
            verdict = _blocked(row, b)
        elif ttl is not None and q.expired(raw, ttl):
            verdict = msg(
                "execute_expired",
                f"#{row['id']}: approved {row['decided_at'][:16]}, older than "
                f"{ttl:g}h; approve it again after a fresh `queue add`",
                id=row["id"], decided_at=row["decided_at"], ttl_hours=ttl)
            to = "expired"
        else:
            p = plan_item(con, row)
            if not isinstance(p, dict):
                raise TypeError(f"plan_item returned {type(p).__name__}, "
                                f"not a dict")
            guards["basis"] = {"saved": row["basis"],
                               "current": p.get("basis")}
            refusal = p.get("refusal")
            if refusal is not None:
                code(refusal)                    # a plain str is a bug
            delta = p.get("delta")
            if human.stale_basis(row["basis"], p.get("basis")):
                verdict = msg(
                    "execute_basis_changed",
                    f"#{row['id']}: the data it was approved on moved (basis "
                    f"{row['basis'][:12]} is now {str(p.get('basis'))[:12]}); "
                    f"a fresh `queue add` proposes it again", id=row["id"],
                    saved=row["basis"], current=p.get("basis"))
                to = "superseded"
            elif refusal is not None:
                verdict = refusal
            elif cap_n is not None and sent >= cap_n:
                guards["max_items"] = {"max": cap_n, "count": sent + 1}
                verdict = msg(
                    "execute_cap_items_deferred",
                    f"#{row['id']}: deferred, this run already sends "
                    f"{sent} item(s) (max {cap_n:g})", id=row["id"],
                    max=cap_n)
            elif cap_d is not None and _num(delta) is None:
                verdict = msg(
                    "execute_delta_unknown",
                    f"#{row['id']}: no delta, so it cannot be proven within "
                    f"the run cap {cap_d:g}", id=row["id"])
            elif cap_d is not None and used + abs(_num(delta)) > cap_d + 1e-9:
                total = used + abs(_num(delta))
                guards["max_delta"] = {"cap": cap_d, "delta": delta,
                                       "total": total}
                verdict = msg(
                    "execute_cap_delta_deferred",
                    f"#{row['id']}: deferred, the run's total change would "
                    f"be {total:g} > {cap_d:g}", id=row["id"], total=total,
                    cap=cap_d)
            else:
                if cap_n is not None:
                    guards["max_items"] = {"max": cap_n, "count": sent + 1}
                if cap_d is not None:
                    used += abs(_num(delta))
                    guards["max_delta"] = {"cap": cap_d, "delta": delta,
                                           "total": used}
                sent += 1
                verdict = msg("execute_go", GO)
        out.append({"row": row, "plan": p, "verdict": verdict,
                    "guards": guards, "to": to})
    return out


def _item(i: dict) -> dict:
    r = i["row"]
    d = {"id": r["id"], "action_id": r["action_id"], "kind": r["kind"],
         "target_ref": r["target_ref"], "payload": r["payload"],
         "expected": r["expected"], "basis": r["basis"],
         "decided_by": r["decided_by"], "decided_at": r["decided_at"],
         "go": i["verdict"].code == "execute_go",
         **coded("verdict", i["verdict"]), "guards": i["guards"]}
    if "outcome" in i:
        d["outcome"] = i["outcome"]
    return d


# ---- apply -----------------------------------------------------------------

def _send(spec: db.SchemaSpec, con: sqlite3.Connection, w: Writer,
          i: dict, apply_item: ApplyItem, read_back: ReadBack) -> Msg | None:
    """One go item: claim, send once, read back. Sets i["outcome"]; returns
    the switch refusal that stops the run, else None."""
    row, p = i["row"], i["plan"]
    b = blocker(con, row["market"], row["target_ref"])
    now = q.fetch(con, row["id"])
    if b is not None:
        i["verdict"], i["outcome"] = _blocked(row, b), "skipped"
        return None
    if now is None or now["status"] != "approved":
        i["verdict"] = msg("execute_item_changed",
                           f"#{row['id']} changed before it was sent; not "
                           f"sent", id=row["id"])
        i["outcome"] = "skipped"
        return None
    request = p.get("request", row["payload"])
    _record(spec, con, row, "pending", request=request)
    _record(spec, con, row, "sent", request=request)
    try:
        response = apply_item(w, row, p)
    except WriteRefused as e:
        # refused before the send: nothing went out
        switch = getattr(e.message, "code", None) in SWITCH_CODES
        _record(spec, con, row, "failed", response=failure(e),
                status=None if switch else "failed")
        i["outcome"] = "failed"
        return e.message if switch else None
    except Exception as e:           # a timeout, a transport error: ambiguous
        _record(spec, con, row, "unknown", response=failure(e),
                status="unknown")
        i["outcome"] = "unknown"
        return None
    stop = None
    try:
        ok = read_back(w, row, response)
    except WriteRefused as e:
        ok = None
        if getattr(e.message, "code", None) in SWITCH_CODES:
            stop = e.message
    except Exception:
        ok = None
    state, status = ({True: ("confirmed", "executed"),
                      False: ("failed", "failed")}
                     .get(ok, ("unknown", "unknown")))
    _record(spec, con, row, state, response=response, status=status)
    i["outcome"] = state
    return stop


def apply(spec: db.SchemaSpec, con: sqlite3.Connection, market: str, *,
          send: bool, writer_factory: WriterFactory, plan_item: PlanItem,
          apply_item: ApplyItem, read_back: ReadBack,
          ttl_hours: Cap | None = None, max_items: Cap | None = None,
          max_delta: Cap | None = None) -> tuple[dict, int]:
    """The run: (its document, exit code). send=False is the dry run."""
    items = plan(con, market, plan_item=plan_item, ttl_hours=ttl_hours,
                 max_items=max_items, max_delta=max_delta)
    go = [i for i in items if i["verdict"].code == "execute_go"]
    if not send:
        summary = msg("execute_dry_run",
                      f"dry run: {len(go)} of {len(items)} approved item(s) "
                      f"would be sent; nothing was written or called. "
                      f"`--apply` sends them", go=len(go), total=len(items))
        return ({"market": market, "mode": "dry_run",
                 **coded("message", summary), "go": len(go),
                 "items": [_item(i) for i in items]}, 0)
    lock = paths.data_dir() / LOCK_FILE
    with hold(lock) as mine:
        if not mine:
            raise HarnessError(msg(
                "execute_busy", f"another execute run holds {lock}; nothing "
                f"was written", lock=str(lock)))
        w = None
        if go:
            w = writer_factory()
            w.guard.ensure_on()                  # nothing written if off
        for i in items:
            if i["to"]:
                _set_status(spec, con, i["row"], i["to"], i["verdict"])
        stop = None
        for i in go:
            if stop is not None:
                i["verdict"] = msg(
                    "execute_stopped", f"#{i['row']['id']}: not sent, the "
                    f"run stopped: {stop}", id=i["row"]["id"], reason=stop)
                i["outcome"] = "skipped"
                continue
            stop = _send(spec, con, w, i, apply_item, read_back)
    counts = {s: sum(1 for i in go if i.get("outcome") == s)
              for s in ("confirmed", "failed", "unknown", "skipped")}
    summary = msg("execute_applied",
                  f"sent {len(go) - counts['skipped']} of {len(items)} "
                  f"approved item(s): {counts['confirmed']} confirmed, "
                  f"{counts['failed']} failed, {counts['unknown']} unknown",
                  total=len(items), confirmed=counts["confirmed"],
                  failed=counts["failed"], unknown=counts["unknown"],
                  skipped=counts["skipped"])
    doc = {"market": market, "mode": "apply", **coded("message", summary),
           "go": len(go), **counts, "items": [_item(i) for i in items]}
    if stop is not None:
        doc.update(coded("stopped", stop))
    bad = counts["failed"] or counts["unknown"] or stop is not None
    return doc, 1 if bad else 0


def reconcile(spec: db.SchemaSpec, con: sqlite3.Connection, queue_id: int, *,
              writer_factory: WriterFactory, read_back: ReadBack) -> dict:
    """Read an ambiguous item back and record what it did."""
    raw = q.fetch(con, queue_id)
    if raw is None:
        raise HarnessError(msg("execute_item_not_found",
                               f"no queue item #{queue_id}", id=queue_id))
    last = latest(con, queue_id)
    state = last["state"] if last else None
    if state not in BLOCKING:
        raise HarnessError(msg(
            "execute_reconcile_nothing",
            f"#{queue_id}: its latest effect is {state or 'none'}, not sent "
            f"or unknown; nothing to reconcile", id=queue_id,
            state=state or "none"))
    row = q.row_dict(raw)
    ok = read_back(writer_factory(), row, None)
    if ok is None:
        raise HarnessError(msg(
            "execute_reconcile_unsure",
            f"#{queue_id}: the read-back cannot tell whether it was written; "
            f"it stays {state} and keeps blocking {row['target_ref']}. Check "
            f"the target by hand", id=queue_id))
    new, status = ("confirmed", "executed") if ok else ("failed", "failed")
    _record(spec, con, row, new, response={"reconciled": True},
            status=status)
    done = msg("execute_reconciled",
               f"#{queue_id}: read back, {new}; {row['target_ref']} is no "
               f"longer blocked", id=queue_id, state=new)
    return {**coded("message", done), "id": queue_id, "state": new,
            "status": status, "effects": effects(con, queue_id)}


# ---- the CLI ---------------------------------------------------------------

def _parser(prog: str) -> argparse.ArgumentParser:
    p = contract.Parser(prog=prog, description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="verb", required=True)
    sp = sub.add_parser("apply", help="execute approved queue items (a dry "
                                      "run unless --apply)")
    sp.add_argument("--market")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="print the plan, call nothing (the default)")
    mode.add_argument("--apply", action="store_true",
                      help="send the items marked go (needs the write "
                           "switch on)")
    contract.add_json_arg(sp)
    sp = sub.add_parser("reconcile", help="read back an item whose send was "
                                          "ambiguous (sent/unknown)")
    sp.add_argument("id", type=int, metavar="ID")
    contract.add_json_arg(sp)
    return p


def _print(doc: dict) -> None:
    print(f"{doc['mode'].upper().replace('_', ' ')}, market {doc['market']}")
    for i in doc["items"]:
        out = f" -> {i['outcome']}" if "outcome" in i else ""
        print(f"  #{i['id']:<4} {i['kind']} {i['target_ref']} "
              f"{q.canonical(i['payload'])}  [{i['verdict']}]{out}")
    print(doc["message"])
    if doc.get("stopped"):
        print(f"stopped: {doc['stopped']}", file=sys.stderr)


def main(argv: list[str] | None = None, *, spec: db.SchemaSpec,
         writer_factory: WriterFactory, plan_item: PlanItem,
         apply_item: ApplyItem, read_back: ReadBack,
         ttl_hours: Cap | None = None, max_items: Cap | None = None,
         max_delta: Cap | None = None) -> int:
    """`<cli> execute apply|reconcile …`; the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = config()
    cmd = [cfg.cli, "execute", *argv]
    parser = _parser(f"{cfg.cli} execute")

    def run(argv: list[str]) -> Any:
        a = parser.parse_args(argv)
        if (e := contract.missing_db_error()) is not None:
            raise e
        send = a.verb == "reconcile" or a.apply
        con = db.connect(spec, read_only=not send)
        try:
            if a.verb == "reconcile":
                doc = reconcile(spec, con, a.id, writer_factory=writer_factory,
                                read_back=read_back)
                if a.json:
                    return doc
                print(doc["message"])
                return None
            m = markets.resolve(con, a.market, cmd=cmd)
            doc, rc = apply(spec, con, m, send=a.apply,
                            writer_factory=writer_factory,
                            plan_item=plan_item, apply_item=apply_item,
                            read_back=read_back, ttl_hours=ttl_hours,
                            max_items=max_items, max_delta=max_delta)
        finally:
            con.close()
        if a.json:
            contract.emit(doc)
        else:
            _print(doc)
        return rc

    return contract.run_main(run, argv, cmd=cmd)
