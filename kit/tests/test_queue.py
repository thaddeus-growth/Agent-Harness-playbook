#!/usr/bin/env python3
"""The approval queue (kit/queue.py) holds each of its guards, on the fake
"shop" harness. Ported from the reference harness's queue tests; the
proposals are generic {kind, market, target_ref, payload, basis}.

  1. add: the same action twice is one row; --json is one document; the
     proposal document's shape and markets are checked before any write.
  2. supersede: a newer snapshot proposing something else for a target
     supersedes its live (pending AND approved) rows; other targets stay;
     an identical proposal re-opens a superseded row.
  3. validate: a refused proposal is recorded `rejected` with the
     refusal's code as reason_code (decided_by validate), rejected()
     reads it, and it is never proposed again unchanged.
  4. an --assume snapshot (meta.assumed_thresholds) is refused whole.
  5. approve is the gate: TTY retype, or a relayed code bound to the ids
     and their proposals, single use, with its audit; no secret and no
     TTY = confirm_needs_human; nothing written on any refusal.
  6. expired / superseded / non-pending rows are refused by approve.
  7. reject: anyone, with --reason and a snake_case --reason-code (an
     optional closed set); rejected() is what the next run reads.
  8. list is read-only and never creates the DB; the triggers freeze a
     queued proposal; queue.tsv is closed over queue.py.
"""

import json
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import dates, db, human, messages  # noqa: E402
from kit import queue as q  # noqa: E402
from kit.messages import msg  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, raises, tmp_dir)

CFG = _shop.use()
DATA = _shop.data_dir()
DB = DATA / CFG.db_file
SECRET = CFG.env("CONFIRM_CODE_SECRET")
RELAY = ["--relay-user", "chat:U1", "--relay-at", "2026-09-23T10:00:00Z"]
SPEC = db.with_human({}, version=1)
SNAP: dict = {}
TTL = {"hours": 24.0}


def validate(con, p):
    if p["payload"].get("units", 0) > 100:
        return msg("shop_stock_low", f"{p['target_ref']}: not enough stock "
                   f"for {p['payload']['units']} units",
                   sku=p["target_ref"], units=p["payload"]["units"])
    return None


def MAIN(argv):
    return q.main(argv, spec=SPEC, snapshot=lambda m: SNAP["doc"],
                  validate=validate, ttl_hours=lambda con, m: TTL["hours"])


def env(secret: bool = False) -> dict:
    extra = {SECRET: "s3cret"} if secret else {}
    return clean_env(**{CFG.env("DATA_DIR"): str(DATA),
                        CFG.env("AUTH_ENV_PATHS"): "none", **extra})


def run(argv, *, tty=None, secret=False, main=None):
    return capture(main or MAIN, list(argv), env=env(secret),
                   tty_answers=tty)


def prop(target="SKU-1", units=5, basis="b1", market="US", kind="restock",
         **extra):
    return {"kind": kind, "market": market, "target_ref": target,
            "payload": {"units": units}, "basis": basis,
            "evidence": {"stock": 3}, "expected": {"spend_usd_per_day": 1.5},
            **extra}


def doc(*props, **meta):
    return {"meta": {"stale": [], "assumed_thresholds": {}, **meta},
            "proposals": list(props)}


def rows() -> list[dict]:
    with closing(sqlite3.connect(DB)) as c:
        return q._all(c, "SELECT * FROM action_queue ORDER BY id")


def row(i: int) -> dict:
    with closing(sqlite3.connect(DB)) as c:
        return q.fetch(c, i)


def add(*props, **meta):
    SNAP["doc"] = doc(*props, **meta)
    return run(["add", "--json"])


def declare(market: str) -> None:
    with closing(sqlite3.connect(DB)) as c:
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "(?, 'market_declared', ?, 0, 'test', '2026-09-22', "
                  "'test@tty')", (market, market))
        c.commit()


def codeof(out: str):
    d = one_doc(out) or {}
    return d.get("code")


# ---- 8 (first: before the DB exists) ----------------------------------------

def test_no_db() -> None:
    print("[8a] every verb refuses no_db and creates nothing")
    for argv in (["list", "--json"], ["add", "--json"],
                 ["reject", "1", "--reason", "r", "--reason-code", "x",
                  "--json"]):
        rc, out, _ = run(argv)
        check(f"{argv[0]} without a DB: no_db, exit 2, no file",
              rc == 2 and codeof(out) == "no_db" and not DB.exists(),
              (rc, out))
    db.connect(SPEC).close()
    declare("US")


# ---- 1 ---------------------------------------------------------------------

def test_add_once() -> None:
    print("[1] add: the same action twice is one row")
    rc, out, err = add(prop())
    d = one_doc(out)
    check("add --json: one document, queued [1], coded message",
          rc == 0 and d["queued"] == [1] and d["message_code"]["code"]
          == "queue_added" and d["markets"] == ["US"], (rc, out, err))
    r = row(1)
    check("the row: pending, action_id = sha256 of the proposal core, JSON "
          "columns canonical",
          r["status"] == "pending" and r["action_id"] == q.action_id(prop())
          and len(r["action_id"]) == 64 and r["payload"] == '{"units":5}'
          and json.loads(r["evidence"]) == {"stock": 3}, r)
    rc, out, _ = add(prop(), prop())
    d = one_doc(out)
    check("the same action again (twice in one doc too): one row, known",
          rc == 0 and d["queued"] == [] and d["known"] == [{"id": 1,
                                                            "status":
                                                            "pending"}]
          and len(rows()) == 1, (out, rows()))
    check("action_id: ANY change of kind/market/target/payload/basis is a "
          "new action; evidence is not part of it",
          len({q.action_id(p) for p in (
              prop(), prop(kind="k2"), prop(target="SKU-9"),
              prop(units=6), prop(basis="b2"))}) == 5
          and q.action_id(prop()) == q.action_id({**prop(),
                                                  "evidence": {"x": 1}}))
    rc, out, _ = add({**prop(), "because": msg(
        "shop_price_missing", "no price for SKU-1", sku="SKU-1"),
        "because_code": {"code": "shop_price_missing",
                         "params": {"sku": "SKU-1"}}})
    check("a proposal's because / because_code is kept in its evidence "
          "(and is not part of the action_id)",
          one_doc(out)["known"][0]["id"] == 1, out)

    before = len(rows())
    bad = [
        ("not a proposal document", {"meta": {}, "actions": []},
         "queue_snapshot_invalid"),
        ("a proposal without basis", doc({**prop(), "basis": ""}),
         "queue_proposal_invalid"),
        ("payload not an object", doc({**prop(), "payload": [1]}),
         "queue_proposal_invalid"),
        ("a market outside the closed set", doc(prop(market="XX")),
         "market_not_a_market"),
        ("an undeclared market", doc(prop(market="CA")),
         "market_not_onboarded"),
        ("NaN in a payload", doc({**prop(), "payload": {"u": float("nan")}}),
         "queue_proposal_invalid"),
    ]
    for label, d, want in bad:
        SNAP["doc"] = d
        rc, out, _ = run(["add", "--json"])
        check(f"{label}: {want}, nothing written",
              rc == 2 and codeof(out) == want and len(rows()) == before,
              (rc, out))
    f = Path(tmp_dir("queue-")) / "snap.json"
    f.write_text(json.dumps(doc(prop(market="US"))), encoding="utf-8")
    rc, out, _ = run(["add", "--from", str(f), "--market", "CA", "--json"])
    check("--from FILE with --market CA and a US proposal: "
          "queue_snapshot_market", rc == 2 and codeof(out) ==
          "queue_snapshot_market", out)
    rc, out, _ = run(["add", "--from", str(f.with_name("none.json")),
                      "--json"])
    check("--from a missing file: queue_snapshot_unreadable",
          rc == 2 and codeof(out) == "queue_snapshot_unreadable", out)
    rc, out, _ = run(["add", "--from", str(f), "--json"])
    check("--from FILE (no hook run): the same action, known",
          rc == 0 and one_doc(out)["known"][0]["id"] == 1, out)
    rc, out, _ = add(prop(target="SKU-2"), stale=[{"table": "stock",
                                                   "last_date": "2026-09-01",
                                                   "lag_days": 9,
                                                   "max_lag_days": 2}])
    d = one_doc(out)
    check("a stale snapshot is queued with a coded warning",
          rc == 0 and d["queued"] == [2] and d["warning_codes"][0]["code"]
          == "queue_snapshot_stale", out)


# ---- 4 ---------------------------------------------------------------------

def test_assumed_refused() -> None:
    print("[4] an --assume snapshot is refused whole")
    before = rows()
    rc, out, _ = add(prop(target="SKU-3"),
                     assumed_thresholds={"max_step": {"value": 0.5}})
    check("queue_snapshot_assumed, exit 2, nothing written",
          rc == 2 and codeof(out) == "queue_snapshot_assumed"
          and one_doc(out)["params"] == {"assumed": ["max_step"]}
          and rows() == before, out)


# ---- 3 ---------------------------------------------------------------------

def test_validate() -> None:
    print("[3] validate: a refusal is recorded rejected with its code")
    rc, out, _ = add(prop(target="SKU-7", units=500))
    d = one_doc(out)
    r = rows()[-1]
    check("the refused proposal: a rejected row, reason_code = the hook's "
          "code, decided_by validate, reported coded",
          rc == 0 and d["queued"] == [] and len(d["rejected"]) == 1
          and d["rejected"][0]["why_code"]["code"] == "shop_stock_low"
          and r["status"] == "rejected" and r["reason_code"] ==
          "shop_stock_low" and r["decided_by"] == "validate"
          and "not enough stock" in r["reason"], (out, r))
    with closing(sqlite3.connect(DB)) as c:
        got = q.rejected(c, "US", "SKU-7", "restock")
        none = q.rejected(c, "US", "SKU-7", "other_kind")
    check("rejected(): what the next proposal run reads (reason_code, "
          "payload parsed)", len(got) == 1 and got[0]["reason_code"] ==
          "shop_stock_low" and got[0]["payload"] == {"units": 500}
          and none == [], got)
    n = len(rows())
    rc, out, _ = add(prop(target="SKU-7", units=500))
    check("proposed again unchanged: still one rejected row (known)",
          rc == 0 and one_doc(out)["known"] == [{"id": r["id"],
                                                  "status": "rejected"}]
          and len(rows()) == n, out)

    def plain(con, p):
        return "a plain str"
    rc, out, _ = run(["add", "--json"], main=lambda a: q.main(
        a, spec=SPEC, snapshot=lambda m: doc(prop(target="SKU-8")),
        validate=plain, ttl_hours=lambda c, m: 24))
    check("a validate hook returning a plain str is a bug: refused, "
          "nothing written", rc != 0 and len(rows()) == n, (rc, out))


# ---- 2 ---------------------------------------------------------------------

def test_supersede() -> None:
    print("[2] supersede")
    rc, out, _ = add(prop(target="SKU-4", units=5, basis="b1"),
                     prop(target="SKU-5", units=5, basis="b1"))
    ids = one_doc(out)["queued"]
    with closing(sqlite3.connect(DB)) as c:      # SKU-5's row is approved
        c.execute("UPDATE action_queue SET status='approved', decided_by="
                  "'t@tty', decided_at=? WHERE id=?", (human.now(), ids[1]))
        c.commit()
    other = [r["id"] for r in rows() if r["target_ref"] == "SKU-2"]
    rc, out, _ = add(prop(target="SKU-4", units=6, basis="b2"),
                     prop(target="SKU-5", units=6, basis="b2"))
    d = one_doc(out)
    check("a newer snapshot with other proposals for SKU-4 (pending) and "
          "SKU-5 (approved): both superseded, the new ones queued",
          rc == 0 and d["superseded"] == ids and len(d["queued"]) == 2
          and all(row(i)["status"] == "superseded" for i in ids)
          and row(ids[0])["reason_code"] == "queue_superseded"
          and row(ids[0])["decided_by"] == "snapshot", (out, rows()))
    check("rows for a target the snapshot does not mention stay",
          all(row(i)["status"] == "pending" for i in other), other)
    rc, out, _ = add(prop(target="SKU-4", units=5, basis="b1"))
    d = one_doc(out)
    check("the old proposal again: its superseded row is re-opened "
          "(pending, queue_revived), the one it replaced superseded",
          d["revived"] == [ids[0]] and row(ids[0])["status"] == "pending"
          and row(ids[0])["reason_code"] == "queue_revived"
          and row(d["superseded"][0])["target_ref"] == "SKU-4", out)


# ---- 5 ---------------------------------------------------------------------

def test_approve_gate() -> None:
    print("[5] approve is the gate")
    rc, out, _ = add(prop(target="SKU-10"))
    i = one_doc(out)["queued"][0]
    rc, out, _ = run(["approve", str(i), "--json"], tty=["approve"])
    check("no --reason: reason_required, nothing written",
          rc == 2 and codeof(out) == "reason_required"
          and row(i)["status"] == "pending", out)
    rc, out, _ = run(["approve", str(i), "--reason", "ok", "--json"])
    check("off a TTY without the secret: confirm_needs_human, nothing "
          "written", rc == 2 and codeof(out) == "confirm_needs_human"
          and row(i)["status"] == "pending", out)
    rc, out, _ = run(["approve", str(i), "--reason", "ok", "--json"],
                     tty=["yes"])
    check("at a TTY, a wrong word: confirm_typed_mismatch, nothing written",
          rc == 2 and codeof(out) == "confirm_typed_mismatch"
          and row(i)["status"] == "pending", out)
    rc, out, err = run(["approve", str(i), "--reason", "ok", "--json"],
                       tty=["approve"])
    r = row(i)
    check("at a TTY, retyping 'approve': approved @tty with the reason",
          rc == 0 and r["status"] == "approved"
          and r["decided_by"].endswith("@tty") and r["reason"] == "ok"
          and one_doc(out)["message_code"]["code"] == "queue_approved",
          (out, err, r))

    rc, out, _ = add(prop(target="SKU-11"), prop(target="SKU-12"))
    a, b = one_doc(out)["queued"]
    rc, out, _ = run(["approve", str(b), str(a), "--reason", "go", "--json"],
                     secret=True)
    d = one_doc(out)
    with closing(sqlite3.connect(DB)) as c:
        rs = [q.fetch(c, a), q.fetch(c, b)]
    check("off a TTY with the secret: the challenge as data, subject bound "
          "to both ids and their proposals, nothing written",
          rc == 2 and d["code"] == "confirm_code_required"
          and d["subject"]["verb"] == "queue approve"
          and d["subject"]["items"] == q.approve_items(rs)
          and d["subject"]["market"] == "US"
          and d["next"][0].startswith("shop queue approve")
          and "--relay-user <sender_id>" in d["next"][0]
          and all(r["status"] == "pending" for r in rs), d)
    c6 = d["params"]["confirm_code"]
    rc, out, _ = run(["approve", str(a), "--reason", "go", "--code", c6,
                      *RELAY, "--json"], secret=True)
    check("the code for {a, b} does not approve a alone: "
          "confirm_code_mismatch", rc == 2 and codeof(out) ==
          "confirm_code_mismatch" and row(a)["status"] == "pending", out)
    rc, out, _ = run(["approve", str(a), str(b), "--reason", "go", "--code",
                      c6, "--json"], secret=True)
    check("--code without the relay audit: confirm_relay_audit_missing",
          rc == 2 and codeof(out) == "confirm_relay_audit_missing"
          and row(a)["status"] == "pending", out)
    rc, out, _ = run(["approve", str(a), str(b), f"--code={c6}",
                      "--reason=go", "--relay-user=web:ann",
                      "--relay-at=2026-09-23T10:00:00Z", "--json"],
                     secret=True)
    d = one_doc(out)
    check("the code relayed back (--name=value form): both approved @relay, "
          "the audit on the reason",
          rc == 0 and [x["status"] for x in d["items"]] == ["approved"] * 2
          and all(x["decided_by"].endswith("@relay") for x in d["items"])
          and d["items"][0]["reason"] == "go [relay user=web:ann at="
          "2026-09-23T10:00:00Z]", out)
    rc, out, _ = run(["approve", str(a), str(b), "--reason", "go", "--code",
                      c6, *RELAY, "--json"], secret=True)
    check("the same code again: refused (the rows are approved now), "
          "nothing changed", rc == 2 and codeof(out) ==
          "queue_item_wrong_status", out)
    rc, out, _ = add(prop(target="SKU-13"))
    x = one_doc(out)["queued"][0]
    rc, out, _ = run(["approve", str(x), "--reason", "go", "--json"],
                     secret=True)
    old = one_doc(out)["params"]["confirm_code"]
    add(prop(target="SKU-13", units=9))          # supersedes x …
    add(prop(target="SKU-13"))                   # … and re-opens it
    rc, out, _ = run(["approve", str(x), "--reason", "go", "--code", old,
                      *RELAY, "--json"], secret=True)
    check("a code issued before the row moved (superseded, then re-opened: "
          "pending again, a new decided_at) is refused: confirm_code_mismatch",
          row(x)["status"] == "pending" and rc == 2
          and codeof(out) == "confirm_code_mismatch", out)
    rc, out, _ = run(["approve", "999", "--reason", "x", "--json"],
                     tty=["approve"])
    check("an unknown id: queue_item_not_found",
          rc == 2 and codeof(out) == "queue_item_not_found", out)


# ---- 6 ---------------------------------------------------------------------

def test_expired() -> None:
    print("[6] expired, superseded and decided rows are refused")
    rc, out, _ = add(prop(target="SKU-20"))
    i = one_doc(out)["queued"][0]
    run(["approve", str(i), "--reason", "ok"], tty=["approve"])
    later = dates.now() + timedelta(hours=25)
    with mock.patch.object(dates, "now", lambda: later):
        rc, out, _ = run(["approve", str(i), "--reason", "again", "--json"],
                         tty=["approve"])
        rc2, out2, _ = run(["list", "--json"])
    check("an approval older than ttl_hours: queue_item_expired, nothing "
          "written", rc == 2 and codeof(out) == "queue_item_expired"
          and one_doc(out)["params"]["ttl_hours"] == 24.0
          and row(i)["reason"] == "ok", out)
    item = [x for x in one_doc(out2)["items"] if x["id"] == i][0]
    check("list marks it expired", rc2 == 0 and item["expired"] is True,
          item)
    with closing(sqlite3.connect(DB)) as c:
        c.execute("UPDATE action_queue SET status='expired' WHERE id=?", (i,))
        c.commit()
    rc, out, _ = run(["approve", str(i), "--reason", "x", "--json"],
                     tty=["approve"])
    check("status expired: queue_item_expired", rc == 2 and codeof(out) ==
          "queue_item_expired", out)
    rc, out, _ = add(prop(target="SKU-20"))
    check("a fresh add of the identical proposal renews it: re-opened "
          "pending", one_doc(out)["revived"] == [i]
          and row(i)["status"] == "pending", out)
    sup = [r["id"] for r in rows() if r["status"] == "superseded"][0]
    rc, out, _ = run(["approve", str(sup), "--reason", "x", "--json"],
                     tty=["approve"])
    check("a superseded row: queue_item_superseded", rc == 2 and codeof(out)
          == "queue_item_superseded", out)
    rej = [r["id"] for r in rows() if r["status"] == "rejected"][0]
    rc, out, _ = run(["approve", str(rej), "--reason", "x", "--json"],
                     tty=["approve"])
    check("a rejected row: queue_item_wrong_status", rc == 2 and codeof(out)
          == "queue_item_wrong_status", out)


# ---- 7 ---------------------------------------------------------------------

def test_reject() -> None:
    print("[7] reject: a no with its reason code")
    rc, out, _ = add(prop(target="SKU-30"))
    i = one_doc(out)["queued"][0]
    for argv, want in (
            (["--reason-code", "too_soon"], "reason_required"),
            (["--reason", "r"], "queue_reason_code_invalid"),
            (["--reason", "r", "--reason-code", "Too Soon"],
             "queue_reason_code_invalid")):
        rc, out, _ = run(["reject", str(i), *argv, "--json"])
        check(f"reject {argv}: {want}, nothing written",
              rc == 2 and codeof(out) == want
              and row(i)["status"] == "pending", out)
    rc, out, _ = run(["reject", str(i), "--reason", "owner said wait",
                      "--reason-code", "too_soon", "--json"])
    r = row(i)
    check("an agent (no TTY, no code) may reject: rejected @cli with the "
          "reason code", rc == 0 and r["status"] == "rejected"
          and r["decided_by"].endswith("@cli") and r["reason_code"] ==
          "too_soon" and one_doc(out)["message_code"]["code"] ==
          "queue_rejected", out)
    with closing(sqlite3.connect(DB)) as c:
        got = q.rejected(c, "US", "SKU-30")
    check("rejected() returns it for the next proposal run",
          [g["reason_code"] for g in got] == ["too_soon"], got)
    rc, out, _ = add(prop(target="SKU-31"))
    j = one_doc(out)["queued"][0]
    closed = lambda a: q.main(a, spec=SPEC, snapshot=lambda m: {},  # noqa
                              validate=validate, ttl_hours=lambda c, m: 24,
                              reason_codes=["too_soon", "too_big"])
    rc, out, _ = run(["reject", str(j), "--reason", "r", "--reason-code",
                      "whatever", "--json"], main=closed)
    check("a closed reason-code set refuses another code",
          rc == 2 and codeof(out) == "queue_reason_code_unknown", out)
    rc, out, _ = run(["reject", str(i), "--reason", "r", "--reason-code",
                      "too_big", "--json"])
    check("a rejected row cannot be rejected again (not live)",
          rc == 2 and codeof(out) == "queue_item_wrong_status", out)


# ---- 8 ---------------------------------------------------------------------

def test_read_only_and_frozen() -> None:
    print("[8b] list is read-only; a queued proposal is frozen")
    before = DB.stat().st_mtime_ns
    rc, out, _ = run(["list", "--status", "all", "--json"])
    d = one_doc(out)
    check("list --status all: every row, JSON columns parsed",
          rc == 0 and len(d["items"]) == len(rows())
          and d["items"][0]["payload"] == {"units": 5}, out)
    rc, out, _ = run(["list"])
    check("list (text): one line per live row", rc == 0 and "#" in out, out)
    check("list writes nothing", DB.stat().st_mtime_ns == before)
    with closing(sqlite3.connect(DB)) as c:
        e = raises(lambda: c.execute("UPDATE action_queue SET payload='{}' "
                                     "WHERE id=1"), sqlite3.DatabaseError)
        e2 = raises(lambda: c.execute("DELETE FROM action_queue WHERE id=1"),
                    sqlite3.DatabaseError)
    check("the triggers refuse changing a proposal and deleting a row",
          e is not None and e2 is not None, (e, e2))


def test_no_snapshot() -> None:
    print("[8b] no_snapshot: the hook of a harness with nothing proposing")
    e = raises(lambda: q.no_snapshot("US"))
    check("queue add without --from is refused, coded, with the way that "
          "works", e is not None and e.message.code == "queue_no_snapshot"
          and e.next == ["shop queue add --from <proposals.json>"], e)


def test_closure() -> None:
    print("[9] queue.py keeps its fragment closed")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "queue.tsv"}
    probs = messages.check_registry_closed(_shop.KIT, ["queue.py"], mine,
                                           strict_kit=True)
    check("every msg() in queue.py is literal and registered; every "
          "queue.tsv code is emitted", probs == [] and len(mine) >= 18,
          probs)


test_no_db()
test_add_once()
test_assumed_refused()
test_validate()
test_supersede()
test_approve_gate()
test_expired()
test_reject()
test_read_only_and_frozen()
test_no_snapshot()
test_closure()
raise SystemExit(finish())
