#!/usr/bin/env python3
"""The executor (kit/execute.py) holds each of its guards, on the fake
"shop" harness with a fake API behind a real kit.write_guard Writer
(PUT /stock/{sku} {units}, GET /stock/{sku}).

  1. A dry run (the default) writes nothing and calls nothing: no writer
     is built, no effect row, no status change; --json is the full plan.
  2. --apply with writes off (and with the kill switch / STOP file) is
     refused by the write guard before anything is written or sent.
  3. --apply with writes on: pending -> sent -> confirmed effect rows, the
     row executed, one call; a second run sends nothing (never twice).
  4. A basis that moved since the approval refuses the item
     (execute_basis_changed) and --apply marks it superseded, the
     approver kept.
  5. A transport timeout after send leaves an `unknown` effect that
     blocks the next execute on that target; `reconcile` reads it back
     and clears it (or, unsure, writes nothing).
  6. Per-run caps: max items, max |delta|, an unknown delta refused.
  7. The harness's own refusal, an expired approval, an allowlist
     refusal (failed, nothing sent) and a kill switch that trips mid-run
     (the run stops; later items stay approved).
  8. Markets, no DB, one run at a time; execute.tsv is closed.
"""

import os
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import dates, db, execute, human, messages, paths  # noqa: E402
from kit import queue as q  # noqa: E402
from kit.messages import msg  # noqa: E402
from kit.single_instance import hold  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc)
from kit.write_guard import Endpoint, Guard, Writer, shape  # noqa: E402

CFG = _shop.use()
DATA = _shop.data_dir()
DB = DATA / CFG.db_file
SPEC = db.with_human({}, version=1)
ON = {CFG.env("ALLOW_WRITES"): "1"}

ALLOWED = {
    ("PUT", "/stock/{sku}"): Endpoint("application/json", None,
                                      (shape("units"),)),
    ("GET", "/stock/{sku}"): Endpoint("application/json", None, (),
                                      read=True),
}
API: dict = {}            # the fake account: sku -> units
CALLS: list = []          # every call that reached the transport
BASIS: dict = {}          # target -> the basis plan_item recomputes now
STATE = {"built": 0, "timeout": set(), "kill_on": set(), "readback": "real",
         "refuse": set(), "extra_field": set(), "kill_after_read": set()}


def transport(method, path, body, media):
    CALLS.append((method, path, body))
    sku = path.rsplit("/", 1)[1]
    if method == "PUT":
        API[sku] = body["units"]
        if sku in STATE["kill_on"]:           # the operator flips the switch
            os.environ[CFG.env("KILL")] = "1"
        if sku in STATE["timeout"]:           # written, then the line drops
            raise TimeoutError("read timed out")
        return 200, {"sku": sku, "units": body["units"]}
    if sku in STATE["kill_after_read"]:       # flipped between two items
        os.environ[CFG.env("KILL")] = "1"
    return 200, {"sku": sku, "units": API.get(sku)}


def writer_factory():
    STATE["built"] += 1
    return Writer(Guard(ALLOWED), transport)


def plan_item(con, row):
    t = row["target_ref"]
    p = {"basis": BASIS.get(t, "b1"), "delta": row["payload"].get("delta")}
    if t in STATE["refuse"]:
        p["refusal"] = msg("shop_price_missing", f"no price for {t}", sku=t)
    return p


def apply_item(w, row, plan):
    body = {"units": row["payload"]["units"]}
    if row["target_ref"] in STATE["extra_field"]:
        body["price"] = 1
    return w.call("PUT", f"/stock/{row['target_ref']}", body)[1]


def read_back(w, row, response):
    if STATE["readback"] == "unsure":
        return None
    _, got = w.call("GET", f"/stock/{row['target_ref']}", None)
    return got.get("units") == row["payload"]["units"]


CAPS: dict = {"items": None, "delta": None, "ttl": None}


def MAIN(argv):
    def cap(name):
        return (lambda con, m: CAPS[name]) if CAPS[name] is not None else None
    return execute.main(argv, spec=SPEC, writer_factory=writer_factory,
                        plan_item=plan_item, apply_item=apply_item,
                        read_back=read_back, ttl_hours=cap("ttl"),
                        max_items=cap("items"), max_delta=cap("delta"))


def env(**extra) -> dict:
    return clean_env(**{CFG.env("DATA_DIR"): str(DATA),
                        CFG.env("AUTH_ENV_PATHS"): "none", **extra})


def run(argv, **extra):
    return capture(MAIN, list(argv), env=env(**extra))


def codeof(out):
    return (one_doc(out) or {}).get("code")


def approved(target, units=5, basis="b1", delta=None, market="US"):
    """Queue one proposal and approve it (as the gate would); its id."""
    payload = {"units": units, **({"delta": delta} if delta is not None
                                  else {})}
    p = {"kind": "restock", "market": market, "target_ref": target,
         "payload": payload, "basis": basis, "evidence": {},
         "expected": {"units": units}}
    with closing(db.connect(SPEC)) as c:
        rep = q.add(SPEC, c, {"meta": {}, "proposals": [p]},
                    lambda con, x: None)
        i = (rep["queued"] or rep["revived"] or [rep["known"][0]["id"]])[0]
        with db.keep_human_rows(SPEC, c):
            c.execute("UPDATE action_queue SET status='approved', "
                      "decided_by='owner@tty', decided_at=?, reason='ok' "
                      "WHERE id=?", (human.now(), i))
    return i


def row(i):
    with closing(sqlite3.connect(DB)) as c:
        return q.fetch(c, i)


def states(i):
    with closing(sqlite3.connect(DB)) as c:
        return [e["state"] for e in execute.effects(c, i)]


def all_effects():
    with closing(sqlite3.connect(DB)) as c:
        return c.execute("SELECT COUNT(*) FROM action_effects").fetchone()[0]


def counts():
    with closing(db.connect(SPEC, read_only=True)) as c:
        return db.human_row_counts(SPEC, c)


def item(out, i):
    return [x for x in one_doc(out)["items"] if x["id"] == i][0]


# ---- 8a (before the DB exists) ----------------------------------------------

def test_no_db() -> None:
    print("[8a] no DB: refused, nothing created")
    rc, out, _ = run(["apply", "--json"])
    check("apply without a DB: no_db, no file", rc == 2 and codeof(out) ==
          "no_db" and not DB.exists(), out)
    db.connect(SPEC).close()
    with closing(sqlite3.connect(DB)) as c:
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "('US', 'market_declared', 'US', 0, 't', 't', 't@tty')")
        c.commit()


# ---- 1 ---------------------------------------------------------------------

def test_dry_run() -> None:
    print("[1] a dry run writes nothing and calls nothing")
    i = approved("SKU-1")
    before, mtime = counts(), DB.stat().st_mtime_ns
    rc, out, err = run(["apply", "--json"], **ON)
    d = one_doc(out)
    check("--json (no --apply): the plan, mode dry_run, the item go, coded "
          "verdict and message",
          rc == 0 and d["mode"] == "dry_run" and d["go"] == 1
          and item(out, i)["go"] is True
          and item(out, i)["verdict_code"]["code"] == "execute_go"
          and d["message_code"]["code"] == "execute_dry_run"
          and item(out, i)["guards"]["basis"] == {"saved": "b1",
                                                  "current": "b1"},
          (out, err))
    check("nothing written: human rows, effects and the file unchanged",
          counts() == before and all_effects() == 0
          and DB.stat().st_mtime_ns == mtime and row(i)["status"] ==
          "approved")
    check("nothing called: no writer built, no transport call",
          STATE["built"] == 0 and CALLS == [], (STATE, CALLS))
    rc, out, _ = run(["apply", "--dry-run"], **ON)
    check("--dry-run (text): the plan printed, still nothing written",
          rc == 0 and "DRY RUN" in out and "SKU-1" in out
          and all_effects() == 0 and CALLS == [], out)


# ---- 2 ---------------------------------------------------------------------

def test_writes_off() -> None:
    print("[2] --apply with writes off is refused by the write guard")
    for label, extra, want in (
            ("writes off (the default)", {}, "write_off"),
            ("kill switch", {**ON, CFG.env("KILL"): "1"}, "write_killed")):
        rc, out, _ = run(["apply", "--apply", "--json"], **extra)
        check(f"{label}: {want}, exit 2, next doctor, nothing written or "
              f"sent", rc == 2 and codeof(out) == want
              and one_doc(out)["next"] == ["shop doctor"]
              and all_effects() == 0 and CALLS == []
              and row(1)["status"] == "approved", out)
    (DATA / "STOP").write_text("")
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    (DATA / "STOP").unlink()
    check("STOP file in the data dir: write_stop_file, nothing written",
          rc == 2 and codeof(out) == "write_stop_file" and all_effects() == 0
          and CALLS == [], out)


# ---- 3 ---------------------------------------------------------------------

def test_apply() -> None:
    print("[3] --apply sends once, with the effect ledger")
    rc, out, err = run(["apply", "--apply", "--json"], **ON)
    d = one_doc(out)
    check("sent: exit 0, mode apply, confirmed 1, the item's outcome",
          rc == 0 and d["mode"] == "apply" and d["confirmed"] == 1
          and item(out, 1)["outcome"] == "confirmed"
          and d["message_code"]["code"] == "execute_applied", (out, err))
    check("the effect ledger: pending -> sent -> confirmed, effect_id = "
          "action_id; the row executed",
          states(1) == ["pending", "sent", "confirmed"]
          and row(1)["status"] == "executed", states(1))
    with closing(sqlite3.connect(DB)) as c:
        e = execute.effects(c, 1)
    check("request and response recorded; approver kept",
          e[0]["effect_id"] == row(1)["action_id"]
          and e[0]["request"] == '{"units":5}'
          and '"units":5' in e[-1]["response"]
          and row(1)["decided_by"] == "owner@tty", e)
    check("one PUT went out (and one GET read-back)",
          [c[0] for c in CALLS] == ["PUT", "GET"], CALLS)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    check("a second run: nothing approved, nothing sent (never twice)",
          rc == 0 and one_doc(out)["go"] == 0 and len(CALLS) == 2, out)
    with closing(sqlite3.connect(DB)) as c:
        err = None
        try:
            c.execute("UPDATE action_effects SET state='x'")
        except sqlite3.DatabaseError as x:
            err = x
    check("effects are append-only (trigger)", err is not None)


# ---- 4 ---------------------------------------------------------------------

def test_basis() -> None:
    print("[4] a moved basis refuses the approval")
    i = approved("SKU-4", basis="b1")
    BASIS["SKU-4"] = "b2"
    rc, out, _ = run(["apply", "--json"], **ON)
    it = item(out, i)
    check("dry run: execute_basis_changed, not go, saved/current shown",
          rc == 0 and it["go"] is False
          and it["verdict_code"]["code"] == "execute_basis_changed"
          and it["guards"]["basis"] == {"saved": "b1", "current": "b2"}, it)
    n = len(CALLS)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    r = row(i)
    check("--apply: not sent, the row superseded (reason_code "
          "execute_basis_changed), the approver kept",
          rc == 0 and len(CALLS) == n and r["status"] == "superseded"
          and r["reason_code"] == "execute_basis_changed"
          and r["decided_by"] == "owner@tty" and states(i) == [], r)
    BASIS.clear()


# ---- 5 ---------------------------------------------------------------------

def test_unknown_blocks() -> None:
    print("[5] a timeout after send is unknown and blocks its target")
    i = approved("SKU-5", units=7)
    STATE["timeout"].add("SKU-5")
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    STATE["timeout"].clear()
    check("the timeout: exit 1, effect pending -> sent -> unknown, row "
          "unknown, the error coded in the response",
          rc == 1 and one_doc(out)["unknown"] == 1
          and states(i) == ["pending", "sent", "unknown"]
          and row(i)["status"] == "unknown", (out, states(i)))
    with closing(sqlite3.connect(DB)) as c:
        last = execute.latest(c, i)
        b = execute.blocker(c, "US", "SKU-5")
    check("the unknown effect records the transport failure; blocker() "
          "names it", '"write_failed"' in last["response"]
          and b == {"id": i, "state": "unknown"}, (last, b))
    j = approved("SKU-5", units=9)
    n = len(CALLS)
    rc, out, _ = run(["apply", "--json"], **ON)
    it = item(out, j)
    check("the next plan: execute_unknown_effect_blocks naming the blocker",
          it["verdict_code"]["code"] == "execute_unknown_effect_blocks"
          and it["verdict_code"]["params"]["blocker"] == i
          and "execute reconcile" in it["verdict"], it)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    check("--apply: the blocked item is not sent, stays approved",
          len(CALLS) == n and row(j)["status"] == "approved"
          and states(j) == [], (out, CALLS[n:]))
    STATE["readback"] = "unsure"
    rc, out, _ = run(["reconcile", str(i), "--json"], **ON)
    STATE["readback"] = "real"
    check("reconcile, read-back unsure: execute_reconcile_unsure, nothing "
          "written, still blocked", rc == 2 and codeof(out) ==
          "execute_reconcile_unsure" and states(i)[-1] == "unknown", out)
    rc, out, _ = run(["reconcile", str(i), "--json"])
    check("reconcile with writes off: the guard refuses the read too "
          "(write_off), nothing written", rc == 2 and codeof(out) ==
          "write_off" and states(i)[-1] == "unknown", out)
    rc, out, _ = run(["reconcile", str(i), "--json"], **ON)
    d = one_doc(out)
    check("reconcile, the write took: confirmed, the row executed",
          rc == 0 and d["state"] == "confirmed" and row(i)["status"] ==
          "executed" and states(i)[-1] == "confirmed"
          and d["message_code"]["code"] == "execute_reconciled", out)
    rc, out, _ = run(["reconcile", str(i), "--json"], **ON)
    check("reconcile again: execute_reconcile_nothing",
          rc == 2 and codeof(out) == "execute_reconcile_nothing", out)
    rc, out, _ = run(["reconcile", "999", "--json"], **ON)
    check("reconcile of an unknown id: execute_item_not_found",
          rc == 2 and codeof(out) == "execute_item_not_found", out)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    check("unblocked: the waiting item is sent and confirmed",
          rc == 0 and item(out, j)["outcome"] == "confirmed"
          and API["SKU-5"] == 9, out)
    with closing(db.connect(SPEC)) as c:        # one snapshot, two items
        two = [{"kind": kd, "market": "US", "target_ref": "SKU-55",
                "payload": {"units": 2}, "basis": "b1"}
               for kd in ("restock", "relabel")]
        x, y = q.add(SPEC, c, {"meta": {}, "proposals": two},
                     lambda con, p: None)["queued"]
        with db.keep_human_rows(SPEC, c):
            c.execute("UPDATE action_queue SET status='approved', decided_by="
                      "'owner@tty', decided_at=? WHERE id IN (?, ?)",
                      (human.now(), x, y))
    STATE["timeout"].add("SKU-55")
    n = len(CALLS)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    STATE["timeout"].clear()
    check("two items on one target in one run: the first ends unknown, the "
          "second is re-checked right before its send and not sent",
          states(x)[-1] == "unknown" and states(y) == []
          and item(out, y)["verdict_code"]["code"] ==
          "execute_unknown_effect_blocks" and row(y)["status"] == "approved"
          and len(CALLS) == n + 1, (out, CALLS[n:]))
    run(["reconcile", str(x), "--json"], **ON)
    run(["apply", "--apply", "--json"], **ON)
    check("after reconcile the second one goes", row(y)["status"] ==
          "executed", row(y))
    k = approved("SKU-6", units=3)
    STATE["timeout"].add("SKU-6")
    run(["apply", "--apply", "--json"], **ON)
    STATE["timeout"].clear()
    API["SKU-6"] = 1                     # the write did not take after all
    rc, out, _ = run(["reconcile", str(k), "--json"], **ON)
    check("reconcile, the write did not take: failed, the row failed",
          rc == 0 and one_doc(out)["state"] == "failed"
          and row(k)["status"] == "failed", out)


# ---- 6 ---------------------------------------------------------------------

def test_caps() -> None:
    print("[6] per-run caps")
    a = approved("SKU-61", delta=4)
    b = approved("SKU-62", delta=4)
    c = approved("SKU-63", delta=4)
    CAPS["items"] = 2
    rc, out, _ = run(["apply", "--json"], **ON)
    check("max_items 2: the first two go, the third deferred",
          [item(out, x)["go"] for x in (a, b, c)] == [True, True, False]
          and item(out, c)["verdict_code"]["code"] ==
          "execute_cap_items_deferred", out)
    CAPS["items"], CAPS["delta"] = None, 6
    rc, out, _ = run(["apply", "--json"], **ON)
    check("max_delta 6 with |delta| 4 each: one goes, the rest deferred "
          "with the total", [item(out, x)["go"] for x in (a, b, c)] ==
          [True, False, False] and item(out, b)["verdict_code"]["params"] ==
          {"id": b, "total": 8.0, "cap": 6}, out)
    d = approved("SKU-64")
    rc, out, _ = run(["apply", "--json"], **ON)
    check("with a delta cap, an item without a delta: execute_delta_unknown",
          item(out, d)["verdict_code"]["code"] == "execute_delta_unknown",
          item(out, d))
    CAPS["delta"] = None
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    check("no caps: all four sent", rc == 0 and one_doc(out)["confirmed"]
          == 4, out)


# ---- 7 ---------------------------------------------------------------------

def test_refusals() -> None:
    print("[7] harness refusal, expiry, allowlist, a switch mid-run")
    a = approved("SKU-71")
    STATE["refuse"].add("SKU-71")
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    STATE["refuse"].clear()
    check("plan_item's refusal is the verdict, its code kept; not sent",
          item(out, a)["verdict_code"]["code"] == "shop_price_missing"
          and row(a)["status"] == "approved" and states(a) == [], out)
    CAPS["ttl"] = 24.0
    later = dates.now() + timedelta(hours=30)
    with mock.patch.object(dates, "now", lambda: later):
        rc, out, _ = run(["apply", "--json"], **ON)
        dry = item(out, a)
        rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    CAPS["ttl"] = None
    check("an approval older than ttl: execute_expired; --apply marks it "
          "expired, not sent", dry["verdict_code"]["code"] ==
          "execute_expired" and row(a)["status"] == "expired"
          and states(a) == [], (dry, row(a)))
    b = approved("SKU-72")
    STATE["extra_field"].add("SKU-72")
    n = len(CALLS)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    STATE["extra_field"].clear()
    check("a body off the allowlist: refused before the send, effect "
          "failed (write_fields_not_allowed), row failed, exit 1",
          rc == 1 and len(CALLS) == n and states(b)[-1] == "failed"
          and row(b)["status"] == "failed", (out, states(b)))
    c, d = approved("SKU-73"), approved("SKU-74")
    STATE["kill_on"].add("SKU-73")
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    os.environ.pop(CFG.env("KILL"), None)
    STATE["kill_on"].clear()
    doc = one_doc(out)
    check("the kill switch flipped after the first write: its read-back is "
          "refused (unknown), the run stops, the next item not sent and "
          "still approved", rc == 1 and states(c)[-1] == "unknown"
          and doc["stopped_code"]["code"] == "write_killed"
          and item(out, d)["verdict_code"]["code"] == "execute_stopped"
          and row(d)["status"] == "approved" and states(d) == [], doc)
    e, f, g = approved("SKU-75"), approved("SKU-76"), approved("SKU-77")
    STATE["kill_after_read"].add("SKU-75")
    n = len(CALLS)
    rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    os.environ.pop(CFG.env("KILL"), None)
    STATE["kill_after_read"].clear()
    doc = one_doc(out)
    check("the kill switch flipped between items (the earlier run's SKU-74 "
          "goes first): the next call is refused before it is sent (effect "
          "failed, the row still approved), the run stops, the item after "
          "it untouched",
          rc == 1 and item(out, e)["outcome"] == "confirmed"
          and states(f)[-1] == "failed" and row(f)["status"] == "approved"
          and item(out, g)["verdict_code"]["code"] == "execute_stopped"
          and states(g) == [] and doc["stopped_code"]["code"] ==
          "write_killed" and row(d)["status"] == "executed"
          and [c[1] for c in CALLS[n:]] == ["/stock/SKU-74"] * 2
          + ["/stock/SKU-75"] * 2,
          (doc, CALLS[n:]))
    run(["apply", "--apply", "--json"], **ON)
    check("the next run sends the refused item and the one after it",
          row(f)["status"] == "executed" and row(g)["status"] == "executed")


# ---- 8 ---------------------------------------------------------------------

def test_market_lock_closure() -> None:
    print("[8b] markets, one run at a time, closure")
    rc, out, _ = run(["apply", "--market", "CA", "--json"], **ON)
    check("an undeclared market: market_not_onboarded",
          rc == 2 and codeof(out) == "market_not_onboarded", out)
    os.environ[CFG.env("DATA_DIR")] = str(DATA)
    with hold(paths.data_dir() / execute.LOCK_FILE) as mine:
        rc, out, _ = run(["apply", "--apply", "--json"], **ON)
    check("another run holds the lock: execute_busy, nothing written",
          mine and rc == 2 and codeof(out) == "execute_busy", out)
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "execute.tsv"}
    probs = messages.check_registry_closed(_shop.KIT, ["execute.py"], own,
                                           strict_kit=True)
    check("every msg() in execute.py is literal and registered; every "
          "execute.tsv code is emitted", probs == [], probs)


test_no_db()
test_dry_run()
test_writes_off()
test_apply()
test_basis()
test_unknown_blocks()
test_caps()
test_refusals()
test_market_lock_closure()
raise SystemExit(finish())
