#!/usr/bin/env python3
"""The decisions verb (kit/decisions.py) holds each of its guards, on the
fake "shop" harness (ssot/decision_keys.tsv: product stage / stage_since /
stage_record (harness) / floor_price / artwork (sha256) / agent_score
(confirm = none) / note, campaign channel). Ported from the reference
harness's tests/test_decisions.py; its domain rules (stage order, the
entry date, the snapshot) come back here as the hooks a harness passes.

  1. The registry is closed: an unknown key, entity type, id or value
     (every domain, sha256 included) is refused, coded, before any DB is
     opened or created; --reason and --source are required; a `harness`
     key cannot be set or confirmed; read verbs never create the DB.
  2. set = pending; only the gate (a TTY retype, or a relayed --code with
     its audit) puts it in force; a confirmed value stays in force while a
     new pending one waits; a same-value set writes no history.
  3. A `confirm = none` key is in force at once, never pending, never
     withdrawable.
  4. A relayed code confirms exactly what it was issued for, once.
  5. Several ids: one challenge (retype 'confirm'), one code bound to the
     exact set of ids and values; all or nothing; plan_confirm() is that
     same challenge for anyone building an ask.
  6. confirm --json: the challenge as data, then what was written; a
     refusal is still one document.
  7. confirm --value: the human's own value, validated, bound, retyped;
     set + confirm history; needs no pending row.
  8. withdraw: pending values only, all or nothing, history old = pending
     / new = in force; afterwards nothing pending, confirm refuses it,
     confirm --value and a new set still work.
  9. Hooks: set_guard gates a stage rollback (TTY or relay); on_confirm
     binds a pending entry date into the code, writes the date and a
     frozen record inside the confirm's own write, and a failing hook
     rolls the whole confirm back.
 10. Markets: writes need a declared market, several declared = ambiguous
     with reruns, one entity per market; a harness with markets = [] keeps
     everything under "_".
 11. Readers and protection: effective / pending / row / last_change /
     set_history; triggers; a value that changed while the human answered
     is refused, not overwritten; hook contract errors.
 12. decisions.py's msg() calls are closed over its fragment.
"""

import getpass
import json
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, db, decisions, human, messages  # noqa: E402
from kit.registry import DecisionRegistry  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, raises, tmp_dir)

CFG = _shop.use()
DATA = _shop.data_dir()
DB = DATA / CFG.db_file
SECRET = CFG.env("CONFIRM_CODE_SECRET")
USER = getpass.getuser()
TODAY = human.now()[:10]
RELAY = ["--relay-user", "chat:U1", "--relay-at", "2026-09-23T10:00:00Z"]
AUDIT = " [relay user=chat:U1 at=2026-09-23T10:00:00Z]"
SHA = "AB" * 32

REG = DecisionRegistry(entity_ids={"product": r"SKU-[A-Z0-9]+",
                                   "campaign": r"[0-9]+"})
SPEC = db.with_human({}, version=1)


def main_with(**hooks):
    return lambda argv: decisions.main(argv, spec=SPEC, registry=REG, **hooks)


MAIN = main_with()


def env(secret: bool = False) -> dict:
    extra = {SECRET: "s3cret"} if secret else {}
    return clean_env(**{CFG.env("DATA_DIR"): str(DATA),
                        CFG.env("AUTH_ENV_PATHS"): "none", **extra})


def run(argv, *, tty=None, secret=False, main=None, reason=True,
        source=True):
    """decisions.main(argv) in-process: (rc, out, err). tty=None: off a
    terminal (an agent); tty=[answers]: a human at one (the KIT_TTY seam).
    set/confirm/withdraw get --reason and set gets --source unless given."""
    argv = list(argv)
    if reason and argv[0] in ("set", "confirm", "withdraw") \
            and not any(a.startswith("--reason") for a in argv):
        argv += ["--reason", "test"]
    if source and argv[0] == "set" \
            and not any(a.startswith("--source") for a in argv):
        argv += ["--source", "agent_proposed_2026-09-22"]
    return capture(main or MAIN, argv, env=env(secret), tty_answers=tty)


def code_in(text: str) -> str | None:
    m = re.search(r"code: (\d{6})", text)
    return m.group(1) if m else None


def con() -> sqlite3.Connection:
    return sqlite3.connect(DB)


def eff(et, eid, key, market="US"):
    with closing(con()) as c:
        return decisions.effective(c, market, et, eid, key)


def stored(et, eid, key, market="US"):
    """(value, status, confirmed_value) of the row, None when absent."""
    with closing(con()) as c:
        r = decisions.row(c, market, et, eid, key)
    return r and (r["value"], r["status"], r["confirmed_value"])


def top() -> int:
    with closing(con()) as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM "
                         "decisions_history").fetchone()[0]


def hist(since: int = 0) -> list[tuple]:
    """History rows after id `since`: (entity_id, key, old, new,
    changed_by, action, reason, status)."""
    with closing(con()) as c:
        return c.execute(
            "SELECT entity_id, key, old_value, new_value, changed_by, action, "
            "reason, status FROM decisions_history WHERE id > ? ORDER BY id",
            (since,)).fetchall()


def declare(market: str) -> None:
    with closing(con()) as c:
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "(?, 'market_declared', ?, 0, 'test', '2026-09-22', "
                  "'test@tty')", (market, market))
        c.commit()


def coded(out: str) -> tuple | None:
    d = one_doc(out)
    return d and (d.get("code"), d.get("params"))


# ---- 1 ---------------------------------------------------------------------

def test_registry_closed() -> None:
    print("[1] the registry is closed; refusals open no DB")
    cases = (
        ("an unknown key", ["set", "product", "SKU-1", "price", "5"],
         "decision_key_unknown"),
        ("an unknown entity type", ["set", "brand", "SKU-1", "stage", "live"],
         "decision_entity_type_unknown"),
        ("a product id that is not SKU-…",
         ["set", "product", "sku-1", "stage", "live"],
         "decision_entity_id_invalid"),
        ("a campaign id that is not numeric",
         ["set", "campaign", "abc", "channel", "paid"],
         "decision_entity_id_invalid"),
        ("a stage outside draft|live|retired",
         ["set", "product", "SKU-1", "stage", "growth"],
         "decision_value_not_choice"),
        ("a score above int:0..10",
         ["set", "product", "SKU-1", "agent_score", "11"],
         "decision_value_out_of_range"),
        ("a word as a number", ["set", "product", "SKU-1", "floor_price",
                                "cheap"], "decision_value_not_number"),
        ("a date that is not ISO", ["set", "product", "SKU-1", "stage_since",
                                    "last week"], "decision_value_not_date"),
        ("a sha256 that is not 64 hex digits",
         ["set", "product", "SKU-1", "artwork", "ab12"],
         "decision_value_not_sha256"),
        ("an empty text", ["set", "product", "SKU-1", "note", "  "],
         "decision_value_empty"),
        ("a `harness` key set by hand",
         ["set", "product", "SKU-1", "stage_record", '{"a": 1}'],
         "decision_harness_written"),
        ("a `harness` key confirmed by hand",
         ["confirm", "product", "SKU-1", "stage_record", "--value", "{}"],
         "decision_harness_written"),
        ("--value with two ids",
         ["confirm", "product", "SKU-1", "SKU-2", "stage", "--value", "live"],
         "decision_value_one_id"),
        ("a blank --reason", ["set", "product", "SKU-1", "stage", "live",
                              "--reason", "  "], "reason_required"),
        ("a blank --source", ["set", "product", "SKU-1", "stage", "live",
                              "--source", " "], "decision_source_required"),
        ("list of an unknown entity type", ["list", "--entity-type", "brand"],
         "decision_entity_type_unknown"),
    )
    for label, argv, want in cases:
        rc, out, _ = run([*argv, "--json"])
        got = coded(out)
        check(f"refused, coded {want}: {label}",
              rc == 2 and got and got[0] == want, (rc, out))
    rc, out, _ = run(["set", "product", "SKU-1", "stage", "live", "--json"],
                     source=False)
    check("a missing --source: decision_source_required",
          rc == 2 and coded(out)[0] == "decision_source_required", out)
    rc, out, _ = run(["withdraw", "product", "SKU-1", "stage", "--json"],
                     reason=False)
    check("a missing --reason: reason_required (coded, not a usage error)",
          rc == 2 and coded(out)[0] == "reason_required", out)
    rc, out, _ = run(["set", "product", "SKU-1", "stage", "growth"])
    check("text mode: `error:` on stderr, nothing on stdout",
          rc == 2 and out == "", out)
    check("...and no refusal above created the DB", not DB.exists())
    for verb in (["get", "product", "SKU-1", "stage"], ["list"],
                 ["history"]):
        rc, out, _ = run([*verb, "--json"])
        d = one_doc(out)
        check(f"read verb `{verb[0]}` on no DB: no_db with next, one "
              f"document", rc == 2 and d and d["code"] == "no_db"
              and d["next"] == ["shop facts init"], out)
    check("...and read verbs never create it", not DB.exists())
    check("the registry's sha256 domain stores lower-case hex",
          REG.validate("product", "artwork", SHA) == SHA.lower())
    db.connect(SPEC).close()
    declare("US")


# ---- 2 ---------------------------------------------------------------------

def test_pending_then_confirm() -> None:
    print("[2] set = pending; only the gate puts a value in force")
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "live"])
    check("an agent (off a TTY) may set: pending, with the confirm to run",
          rc == 0 and "(new, pending)" in out
          and "shop decisions confirm product SKU-A stage --market US" in out,
          out)
    check("...not in force yet", eff("product", "SKU-A", "stage") is None)
    check("...history: set by <user>@cli, why = --reason, status pending",
          hist()[-1] == ("SKU-A", "stage", None, "live", f"{USER}@cli", "set",
                         "test", "pending"), hist())
    n = top()
    rc, out, _ = run(["confirm", "product", "SKU-A", "stage", "--json"])
    check("confirm off a TTY, no secret: confirm_needs_human",
          rc == 2 and coded(out)[0] == "confirm_needs_human", out)
    rc, _, err = run(["confirm", "product", "SKU-A", "stage"], tty=[])
    check("no answer at the terminal (EOF) is refused",
          rc == 2 and "EOF" in err, err)
    rc, out, err = run(["confirm", "product", "SKU-A", "stage"],
                       tty=["retired"])
    check("a wrong retype is refused", rc == 2 and "expected" in err, err)
    check("...nothing written, still not in force",
          top() == n and eff("product", "SKU-A", "stage") is None)
    rc, out, err = run(["confirm", "product", "SKU-A", "stage"],
                       tty=["live"])
    check("a human at a terminal retypes it: in force",
          rc == 0 and eff("product", "SKU-A", "stage") == "live"
          and "confirmed (in force)" in out, (out, err))
    check("...history: confirm by <user>@tty, status confirmed",
          hist(n) == [("SKU-A", "stage", "live", "live", f"{USER}@tty",
                       "confirm", "test", "confirmed")], hist(n))

    print("[2b] the relayed --code")
    run(["set", "product", "SKU-R", "stage", "live"])
    rc, _, err = run(["confirm", "product", "SKU-R", "stage"])
    check("secret unset: refused as at a pipe, no code offered",
          rc == 2 and "TTY" in err and code_in(err) is None, err)
    rc, _, err = run(["confirm", "product", "SKU-R", "stage"], secret=True)
    code = code_in(err)
    check("secret set, no --code: refused, a code and the summary to relay",
          rc == 2 and code and "rerun with --code" in err
          and "US product SKU-R stage = live" in err, err)
    n = top()
    rc, _, err = run(["confirm", "product", "SKU-R", "stage", "--code",
                      "000000", *RELAY], secret=True)
    check("a wrong code is refused", rc == 2 and "does not match" in err, err)
    rc, _, err = run(["confirm", "product", "SKU-R", "stage", "--code", code],
                     secret=True)
    check("the right code without --relay-user/--relay-at is refused",
          rc == 2 and "--relay-user" in err, err)
    check("...nothing written by any refusal", top() == n)
    rc, out, err = run(["confirm", "product", "SKU-R", "stage", "--reason",
                        "client approved over chat", "--code", code, *RELAY],
                       secret=True)
    check("the right code + the relay audit: in force",
          rc == 0 and eff("product", "SKU-R", "stage") == "live", err)
    check("...history: <user>@relay, the reason carries the audit",
          hist(n) == [("SKU-R", "stage", "live", "live", f"{USER}@relay",
                       "confirm", "client approved over chat" + AUDIT,
                       "confirmed")], hist(n))

    print("[2c] a confirmed value stays in force while a new one waits")
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "retired",
                      "--reason", "sold out for good"])
    check("an agent proposes the next stage: pending",
          rc == 0 and "'live' → 'retired' (pending)" in out, out)
    check("...the confirmed stage stays in force",
          eff("product", "SKU-A", "stage") == "live"
          and stored("product", "SKU-A", "stage")
          == ("retired", "pending", "live"))
    rc, out, _ = run(["list", "--pending", "--json"])
    rows = one_doc(out)["decisions"]
    check("list --pending: exactly that row, value and value in force",
          rc == 0 and [(r["entity_id"], r["value"], r["effective"],
                        r["status"]) for r in rows]
          == [("SKU-A", "retired", "live", "pending")], rows)
    rc, out, _ = run(["get", "product", "SKU-A", "stage", "--json"])
    check("get --json: one document, the row plus `effective`",
          rc == 0 and one_doc(out)["effective"] == "live"
          and one_doc(out)["value"] == "retired", out)
    run(["confirm", "product", "SKU-A", "stage"], tty=["retired"])
    check("...confirmed: now retired is in force",
          eff("product", "SKU-A", "stage") == "retired")
    n = top()
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "retired",
                      "--source", "owner_rechecked_2026-09-23"])
    with closing(con()) as c:
        src = decisions.row(c, "US", "product", "SKU-A", "stage")["source"]
    check("a same-value set: no history, only the source is refreshed",
          rc == 0 and "unchanged" in out and top() == n
          and src == "owner_rechecked_2026-09-23"
          and eff("product", "SKU-A", "stage") == "retired", out)
    rc, out, _ = run(["set", "product", "SKU-A", "floor_price", "2.50",
                      "--json"])
    d = one_doc(out)
    check("set --json: one document, the canonical value, awaiting a "
          "confirm, with the command that confirms it",
          rc == 0 and d["set"]["value"] == "2.5" and d["change"] == "new"
          and d["awaits_confirm"] is True
          and d["next"] == ["shop decisions confirm product SKU-A "
                            "floor_price --market US --reason <why>"], d)


# ---- 3 ---------------------------------------------------------------------

def test_confirm_exempt() -> None:
    print("[3] a `confirm = none` key is in force at once")
    n = top()
    rc, out, _ = run(["set", "product", "SKU-A", "agent_score", "8"])
    check("set says in force, no confirm hint",
          rc == 0 and "in force, no confirm needed" in out
          and "pending" not in out and "decisions confirm" not in out, out)
    check("...in force immediately, stored confirmed (no human needed)",
          eff("product", "SKU-A", "agent_score") == "8"
          and stored("product", "SKU-A", "agent_score")
          == ("8", "confirmed", "8"))
    check("...history: one `set` by @cli, no `confirm` row",
          hist(n) == [("SKU-A", "agent_score", None, "8", f"{USER}@cli",
                       "set", "test", "confirmed")], hist(n))
    run(["set", "product", "SKU-A", "agent_score", "10"])
    check("a rescore is in force at once, both in history",
          eff("product", "SKU-A", "agent_score") == "10"
          and [h[2:4] for h in hist(n)] == [(None, "8"), ("8", "10")],
          hist(n))
    rc, out, _ = run(["list", "--pending", "--json"])
    check("...never listed as pending",
          all(r["key"] != "agent_score" for r in one_doc(out)["decisions"]),
          out)
    rc, out, _ = run(["withdraw", "product", "SKU-A", "agent_score",
                      "--json"])
    check("...nothing to withdraw", rc == 2
          and coded(out)[0] == "decision_nothing_pending", out)
    rc, out, _ = run(["confirm", "product", "SKU-A", "agent_score"])
    check("confirming it: already confirmed, rc 0, no gate",
          rc == 0 and "already confirmed" in out, out)


# ---- 4 ---------------------------------------------------------------------

def test_code_bound() -> None:
    print("[4] a relayed code confirms exactly what it was shown for, once")
    a, b = "SKU-BA", "SKU-BB"
    for p in (a, b):
        run(["set", "product", p, "stage", "live"])
    _, _, err = run(["confirm", "product", a, "stage"], secret=True)
    code_a = code_in(err)
    rc, _, err = run(["confirm", "product", b, "stage", "--code", code_a,
                      *RELAY], secret=True)
    check("A's code does not confirm B (same pending value)",
          rc == 2 and "does not match" in err
          and eff("product", b, "stage") is None, err)
    rc, _, err = run(["confirm", "product", a, "stage", "--code", code_a,
                      *RELAY], secret=True)
    check("...it confirms A", rc == 0 and eff("product", a, "stage")
          == "live", err)
    rc, _, _ = run(["confirm", "product", b, "stage", "--code", code_a,
                    *RELAY], secret=True)
    check("...and never a second decision",
          rc == 2 and eff("product", b, "stage") is None)
    run(["set", "product", a, "stage", "retired"])
    _, _, err = run(["confirm", "product", a, "stage"], secret=True)
    used = code_in(err)
    rc, _, _ = run(["confirm", "product", a, "stage", "--code", used,
                    *RELAY], secret=True)
    check("a code for A → retired confirms it",
          rc == 0 and eff("product", a, "stage") == "retired")
    run(["set", "product", a, "stage", "draft"])
    run(["set", "product", a, "stage", "retired"])
    rc, _, err = run(["confirm", "product", a, "stage", "--code", used,
                      *RELAY], secret=True)
    check("...replayed on the identical pending value: refused (the write "
          "moved the version)", rc == 2 and "does not match" in err, err)


# ---- 5 ---------------------------------------------------------------------

def test_confirm_many() -> None:
    print("[5] several ids: one challenge, one code, all or nothing")

    def pend(values: dict) -> None:
        for eid, v in values.items():
            run(["set", "campaign", eid, "channel", v])

    def effs(eids) -> list:
        return [eff("campaign", e, "channel") for e in eids]

    tty = {"9001": "organic", "9002": "organic", "9003": "paid"}
    pend(tty)
    n = top()
    rc, _, err = run(["confirm", "campaign", "9001", "9002", "9003",
                      "channel"], tty=["organic"])
    check("TTY: every (id, pending value) in one prompt, retype 'confirm'",
          all(f"campaign {e} channel = {v}" in err for e, v in tty.items())
          and "type 'confirm'" in err, err)
    check("...retyping a value instead is refused, nothing written",
          rc == 2 and top() == n and effs(tty) == [None] * 3, err)
    rc, out, _ = run(["confirm", "campaign", "9003", "9001", "9002",
                      "channel", "--reason", "owner ok'd the channels"],
                     tty=["confirm"])
    check("...one retype of 'confirm' confirms all three",
          rc == 0 and effs(tty) == list(tty.values()), out)
    check("...one confirm row each, same reason, same changed_by",
          sorted(hist(n)) == [(e, "channel", v, v, f"{USER}@tty", "confirm",
                               "owner ok'd the channels", "confirmed")
                              for e, v in sorted(tty.items())], hist(n))

    print("  -- all or nothing")
    run(["set", "campaign", "9011", "channel", "organic"])
    n = top()
    rc, out, _ = run(["confirm", "campaign", "9011", "9012", "9001",
                      "channel", "--json"], tty=["confirm"])
    d = one_doc(out)
    check("one unknown + one already-confirmed id: decision_confirm_refused "
          "naming both, each reason coded",
          rc == 2 and d["code"] == "decision_confirm_refused"
          and d["params"]["refused"] == 2 and d["params"]["total"] == 3
          and [p["code"] for p in d["params"]["reasons"]["params"]["parts"]]
          == ["decision_not_found", "decision_already_confirmed"]
          and "9012" in d["error"] and "9001 already confirmed" in d["error"],
          d)
    check("...nothing written, the pending one stays pending",
          top() == n and eff("campaign", "9011", "channel") is None)
    rc, _, err = run(["confirm", "campaign", "9011", "9012", "channel"],
                     secret=True)
    check("...and off a TTY no code is issued for a bad set",
          rc == 2 and code_in(err) is None and "9012" in err, err)

    print("  -- one relayed code bound to exactly the set")
    rel = {"9101": "organic", "9102": "organic", "9103": "paid"}
    pend({**rel, "9104": "organic"})
    ids = list(rel)
    rc, _, err = run(["confirm", "campaign", *ids, "channel"], secret=True)
    code = code_in(err)
    check("off a TTY: ONE code, the summary lists every id and its value",
          rc == 2 and code and err.count("code: ") == 1
          and all(f"campaign {e} channel = {v}" in err
                  for e, v in rel.items()), err)
    n = top()
    for label, argv in (("a subset", ["9101", "9102"]),
                        ("a superset", [*ids, "9104"]),
                        ("another set of the same size",
                         ["9101", "9102", "9104"])):
        rc, _, err = run(["confirm", "campaign", *argv, "channel", "--code",
                          code, *RELAY], secret=True)
        check(f"the set's code refuses {label}",
              rc == 2 and "does not match" in err, err)
    check("...nothing written by any refusal",
          top() == n and effs(rel) == [None] * 3)
    run(["set", "campaign", "9103", "channel", "organic"])
    rc, _, err = run(["confirm", "campaign", *ids, "channel", "--code", code,
                      *RELAY], secret=True)
    check("a value changed after the code was shown: refused",
          rc == 2 and "does not match" in err, err)
    run(["set", "campaign", "9103", "channel", "paid"])
    _, _, err = run(["confirm", "campaign", *ids, "channel"], secret=True)
    code = code_in(err)
    n = top()
    rc, out, err = run(["confirm", "campaign", *reversed(ids), "channel",
                        "--reason", "client ok'd 3 channels", "--code", code,
                        *RELAY], secret=True)
    check("the right set, in any order, + the audit: all in force",
          rc == 0 and effs(rel) == list(rel.values()), err)
    check("...one @relay confirm row per id with the audit reason",
          sorted(hist(n)) == [(e, "channel", v, v, f"{USER}@relay",
                               "confirm", "client ok'd 3 channels" + AUDIT,
                               "confirmed") for e, v in sorted(rel.items())],
          hist(n))
    for e in ids:           # the identical pending set again
        run(["set", "campaign", e, "channel",
             "paid" if rel[e] == "organic" else "organic"])
        run(["set", "campaign", e, "channel", rel[e]])
    rc, _, err = run(["confirm", "campaign", *ids, "channel", "--code", code,
                      *RELAY], secret=True)
    check("...the used code is refused on the identical pending set",
          rc == 2 and "does not match" in err, err)

    print("  -- one id; plan_confirm is the same challenge")
    run(["set", "campaign", "9201", "channel", "organic"])
    rc, _, err = run(["confirm", "campaign", "9201", "channel"],
                     tty=["organic"])
    check("one id: retype the value itself",
          rc == 0 and err == "US campaign 9201 channel = organic — retype "
          "the value to confirm it: ", err)
    run(["set", "campaign", "9202", "channel", "paid"])
    with closing(con()) as c:
        last = decisions.last_change(c, "US", "campaign", "9202", ["channel"])
        plan = decisions.plan_confirm(c, "US", "campaign", ["9202"],
                                      "channel", registry=REG)
    subj = human.subject("decisions confirm", "US", "campaign",
                         {"9202": {"channel": "paid"}}, last)
    _, _, err = run(["confirm", "campaign", "9202", "channel"], secret=True)
    with mock.patch.dict("os.environ", {SECRET: "s3cret"}):
        same = human.issue_code(subj)
    check("one id: the code is bound to {id: {key: value}} and the last "
          "history id", code_in(err) == same, err)
    check("plan_confirm gives that exact subject, prompt and expected value",
          plan["subject"] == subj and plan["expected"] == "paid"
          and plan["prompt"].startswith("US campaign 9202 channel = paid"),
          plan)
    rc, _, err = run(["confirm", "campaign", "9202", "channel", "--code",
                      same, *RELAY], secret=True)
    check("...a code issued from plan_confirm's subject confirms it",
          rc == 0 and eff("campaign", "9202", "channel") == "paid", err)
    rc, out, _ = run(["confirm", "campaign", "9201", "channel"])
    check("one already-confirmed id: a no-op, rc 0, no gate",
          rc == 0 and "already confirmed" in out, out)


# ---- 6 ---------------------------------------------------------------------

def test_confirm_json() -> None:
    print("[6] confirm --json: the challenge as data, then what was written")
    ids = ["9301", "9302"]
    for e in ids:
        run(["set", "campaign", e, "channel", "organic"])
    base = ["confirm", "campaign", *ids, "channel", "--reason", "web ok",
            "--json"]
    rc, out, err = run(base, secret=True)
    d = one_doc(out) or {}
    shown = d.get("params", {}).get("confirm_code", "")
    check("off a TTY, no --code: one document, confirm_code_required, the "
          "code and the summary as params, nothing on stderr",
          rc == 2 and d.get("code") == "confirm_code_required"
          and re.fullmatch(r"\d{6}", shown) and err == ""
          and all(f"campaign {e} channel = organic" in d["params"]["summary"]
                  for e in ids), (out, err))
    check("...subject: market, entity type, every id with its value",
          {k: d["subject"][k] for k in ("verb", "market", "entity_type",
                                        "items")}
          == {"verb": "decisions confirm", "market": "US",
              "entity_type": "campaign",
              "items": {e: {"channel": "organic"} for e in ids}}, d)
    check("...next reruns the same command with --code and the relay flags",
          d["next"] == ["shop decisions confirm campaign 9301 9302 channel "
                        f"--reason 'web ok' --json --code {shown} "
                        "--relay-user <sender_id> --relay-at <iso_time>"]
          and eff("campaign", "9301", "channel") is None, d)
    rc, out, _ = run([*base, "--code", shown, *RELAY], secret=True)
    d = one_doc(out) or {}
    check("with the code: rc 0, the rows now in force, nothing recorded",
          rc == 0 and d["recorded"] == []
          and [(r["entity_id"], r["value"], r["status"], r["effective"])
               for r in d["confirmed"]]
          == [(e, "organic", "confirmed", "organic") for e in ids]
          and d["changed_by"] == human.changed_by("relay")
          and d["reason"] == "web ok" + AUDIT, d)
    rc, out, _ = run([*base, "--code", shown, *RELAY], secret=True)
    d = one_doc(out) or {}
    check("a refusal under --json is still one document",
          rc == 2 and d.get("error") and d.get("code")
          and set(d) == {"error", "next", "code", "params"}, out)
    rc, out, _ = run(["confirm", "campaign", "9301", "channel", "--json"])
    d = one_doc(out) or {}
    check("an already-confirmed id under --json: one document naming it",
          rc == 0 and d["confirmed"] == []
          and [r["entity_id"] for r in d["already_confirmed"]] == ["9301"], d)


# ---- 7 ---------------------------------------------------------------------

def test_confirm_value() -> None:
    print("[7] confirm --value: the human's own value, the same gate")
    cid = "9401"
    run(["set", "campaign", cid, "channel", "organic"])
    n = top()
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "maybe", "--json"], tty=["maybe"])
    check("an invalid --value is refused by the registry domain",
          rc == 2 and coded(out)[0] == "decision_value_not_choice"
          and top() == n, out)
    rc, _, err = run(["confirm", "campaign", cid, "channel", "--value",
                      "paid"], tty=["organic"])
    check("at a TTY the human retypes the typed value, not the pending one",
          rc == 2 and top() == n and "(replacing organic)" in err, err)
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "paid", "--reason", "owner wants it"], tty=["paid"])
    check("the typed value is written and in force",
          rc == 0 and eff("campaign", cid, "channel") == "paid"
          and "'organic' → 'paid'" in out, out)
    check("...history: set organic → paid, then its confirm, both @tty",
          hist(n) == [(cid, "channel", "organic", "paid", f"{USER}@tty",
                       "set", "owner wants it", "confirmed"),
                      (cid, "channel", "paid", "paid", f"{USER}@tty",
                       "confirm", "owner wants it", "confirmed")], hist(n))
    with closing(con()) as c:
        src = decisions.row(c, "US", "campaign", cid, "channel")["source"]
    check("...source records the human's confirm",
          src == f"human_confirmed_{TODAY}", src)
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "paid"], tty=["paid"])
    check("--value equal to the value in force: already confirmed",
          rc == 0 and "already confirmed" in out, out)

    print("  -- relayed: the code binds the typed value")
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "organic", "--json"], secret=True)
    d = one_doc(out)
    check("nothing pending: --value still issues a challenge for the typed "
          "value", rc == 2 and d["params"]["confirm_code"]
          and d["subject"]["items"] == {cid: {"channel": "organic"}}, d)
    run(["set", "campaign", cid, "channel", "organic"])
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "paid", "--json"], secret=True)
    d = one_doc(out)
    code = d["params"]["confirm_code"]
    check("an agent's pending organic, the human types paid: the subject "
          "binds paid", d["subject"]["items"] == {cid: {"channel": "paid"}},
          d)
    for label, extra in (("the pending value (no --value)", []),
                         ("another --value", ["--value", "organic"])):
        rc, out, _ = run(["confirm", "campaign", cid, "channel", *extra,
                          "--code", code, *RELAY, "--json"], secret=True)
        check(f"...that code does not confirm {label}",
              rc == 2 and coded(out)[0] == "confirm_code_mismatch"
              and stored("campaign", cid, "channel")
              == ("organic", "pending", "paid"), out)
    n = top()
    rc, out, _ = run(["confirm", "campaign", cid, "channel", "--value",
                      "paid", "--code", code, *RELAY, "--json"], secret=True)
    check("the code for the typed value confirms it over the pending one",
          rc == 0 and eff("campaign", cid, "channel") == "paid"
          and one_doc(out)["changed_by"] == f"{USER}@relay"
          and hist(n)[0][2:6] == ("organic", "paid", f"{USER}@relay", "set"),
          (out, hist(n)))
    n = top()
    rc, out, _ = run(["confirm", "campaign", "9403", "channel", "--value",
                      "organic"], tty=["organic"])
    check("--value needs no row at all",
          rc == 0 and eff("campaign", "9403", "channel") == "organic"
          and [h[2:6] for h in hist(n)]
          == [(None, "organic", f"{USER}@tty", "set"),
              ("organic", "organic", f"{USER}@tty", "confirm")], out)
    rc, out, _ = run(["confirm", "product", "SKU-A", "artwork", "--value",
                      SHA], tty=[SHA.lower()])
    check("a sha256 --value is stored canonical and retyped that way",
          rc == 0 and eff("product", "SKU-A", "artwork") == SHA.lower(), out)
    rc, out, _ = run(["confirm", "product", "SKU-Q", "stage", "--json"])
    check("no row and no --value: decision_not_found",
          rc == 2 and coded(out) == ("decision_not_found", {
              "market": "US", "entity_type": "product",
              "entity_id": "SKU-Q", "key": "stage"}), out)


# ---- 8 ---------------------------------------------------------------------

def test_withdraw() -> None:
    print("[8] withdraw: take back pending values, never a confirmed one")
    ids = ["9501", "9502", "9503"]
    for e in ids:
        run(["set", "campaign", e, "channel", "paid"])
    run(["set", "campaign", "9504", "channel", "organic"])
    run(["confirm", "campaign", "9504", "channel"], tty=["organic"])
    run(["set", "campaign", "9504", "channel", "paid"])
    run(["set", "campaign", "9505", "channel", "organic"])
    run(["confirm", "campaign", "9505", "channel"], tty=["organic"])
    before = [stored("campaign", e, "channel") for e in [*ids, "9504"]]
    n = top()
    rc, out, _ = run(["withdraw", "campaign", "9501", "9505", "9599",
                      "channel", "--reason", "x", "--json"])
    d = one_doc(out)
    check("a confirmed value or an unknown id refuses the whole batch",
          rc == 2 and d["code"] == "decision_nothing_pending"
          and d["params"]["entity_ids"] == ["9505", "9599"]
          and d["params"]["refused"] == 2 and d["params"]["total"] == 3
          and top() == n, d)
    rc, out, _ = run(["withdraw", "campaign", *ids, "9504", "channel",
                      "--reason", "web ok", "--json"])
    d = one_doc(out)
    check("an agent (no challenge) withdraws several pending values",
          rc == 0 and [r["entity_id"] for r in d["withdrawn"]]
          == [*ids, "9504"]
          and all(r["status"] == "withdrawn" for r in d["withdrawn"])
          and d["reason"] == "web ok"
          and d["changed_by"] == f"{USER}@cli", d)
    check("...one withdraw row each: old = the pending value, new = the "
          "value in force",
          [h[1:] for h in hist(n)] == [
              ("channel", "paid", None, f"{USER}@cli", "withdraw", "web ok",
               "withdrawn")] * 3 + [
              ("channel", "paid", "organic", f"{USER}@cli", "withdraw",
               "web ok", "withdrawn")], hist(n))
    check("...values and values in force are untouched",
          [stored("campaign", e, "channel")[::2] for e in [*ids, "9504"]]
          == [b[::2] for b in before]
          and [eff("campaign", e, "channel") for e in [*ids, "9504"]]
          == [None, None, None, "organic"])
    with closing(con()) as c:
        left = {r["entity_id"] for r in decisions.pending(
            c, "US", entity_type="campaign", key="channel")}
    rc, out, _ = run(["list", "--pending", "--entity-type", "campaign",
                      "--key", "channel", "--json"])
    listed = {r["entity_id"] for r in one_doc(out)["decisions"]}
    check("...nothing pending for them (pending(), list --pending)",
          not left & {*ids, "9504"} and not listed & {*ids, "9504"},
          (left, listed))
    n = top()
    rc, _, err = run(["withdraw", "campaign", "9501", "channel", "--reason",
                      "x"])
    check("withdrawing again is refused", rc == 2
          and "nothing withdrawn" in err and top() == n, err)
    rc, out, _ = run(["confirm", "campaign", "9501", "channel", "--json"],
                     tty=["paid"])
    check("confirm refuses a withdrawn value, even at a TTY",
          rc == 2 and coded(out)[0] == "decision_withdrawn" and top() == n
          and eff("campaign", "9501", "channel") is None, out)
    rc, out, _ = run(["confirm", "campaign", "9502", "channel", "--value",
                      "organic"], tty=["organic"])
    check("...a human may still confirm a value of their own",
          rc == 0 and eff("campaign", "9502", "channel") == "organic", out)
    rc, out, _ = run(["set", "campaign", "9501", "channel", "paid"])
    with closing(con()) as c:
        again = decisions.pending(c, "US", entity_id="9501")
    check("a later set of the same value proposes again (pending, history)",
          rc == 0 and [r["value"] for r in again] == ["paid"]
          and hist()[-1][5] == "set", (out, again))


# ---- 9 ---------------------------------------------------------------------

ORDER = ("draft", "live", "retired")
FAIL_APPLY: list = []


def stage_guard(con, *, market, entity_type, entity_id, key, value, row,
                code):
    """A harness rule: a product never moves back to an earlier stage
    unless a human says so (the reference's stage rollback)."""
    if (entity_type, key) != ("product", "stage"):
        return None
    cur = decisions.effective(con, market, entity_type, entity_id, key)
    if cur is None or ORDER.index(value) >= ORDER.index(cur):
        return None
    return human.confirm(
        f"moving product {entity_id} back from {cur} to {value}",
        f"product {entity_id} [{market}]: {cur} → {value} is a rollback — "
        f"retype the earlier stage to allow it: ", value, code=code,
        subj=human.subject("decisions set stage rollback", market,
                           entity_type, {entity_id: {key: [cur, value]}},
                           decisions.last_change(con, market, entity_type,
                                                 entity_id, [key])))


def stage_follow(con, *, market, entity_type, entity_id, key, value, row,
                 changed):
    """A harness rule: confirming a stage settles its stage_since (a
    pending date is confirmed with it and bound into the code; else today
    is recorded) and freezes a stage_record when the stage changes."""
    if (entity_type, key) != ("product", "stage"):
        return None
    today = human.now()[:10]
    since = decisions.row(con, market, "product", entity_id, "stage_since")
    since = since if since and since["status"] == "pending" else None
    before = decisions.effective(con, market, "product", entity_id, "stage")
    record = None if before == value else REG.validate(
        "product", "stage_record",
        json.dumps({"from": before, "to": value, "on": today}))

    def apply(c, *, reason, channel):
        lines = []
        if since:
            decisions.write_confirm(
                c, market, "product", entity_id, "stage_since",
                reason=f"{reason} [confirmed with stage → {value}]",
                channel=channel)
            lines.append(f"stage_since {since['value']!r} confirmed with it")
        else:
            why = f"{reason} [set by the stage confirm: stage → {value}]"
            decisions.write_set(c, market, "product", entity_id,
                                "stage_since", today,
                                source=f"stage_confirm_{today}", reason=why,
                                channel=channel, in_force=True)
            decisions.write_confirm(c, market, "product", entity_id,
                                    "stage_since", reason=why,
                                    channel=channel)
            lines.append(f"stage_since = {today!r} recorded (in force)")
        if record:
            decisions.write_set(
                c, market, "product", entity_id, "stage_record", record,
                source=f"stage_confirm_{today}",
                reason=f"{reason} [frozen by the stage confirm: {before} → "
                       f"{value}]", channel=channel, in_force=True)
            lines.append("stage_record recorded")
        if FAIL_APPLY:
            raise RuntimeError("the hook failed after writing")
        return lines

    return decisions.Follow(
        bind={"stage_since": since["value"]} if since else {},
        note=(f"(and its pending stage_since = {since['value']})" if since
              else f"(stage_since will be recorded as {today}, today UTC)"),
        keys=("stage_since",), confirmed=("stage_since",),
        recorded=("stage_record",) if record else (), apply=apply)


HOOKED = main_with(set_guard=stage_guard, on_confirm=stage_follow)


def test_hooks() -> None:
    print("[9] hooks: set_guard gates a rollback; on_confirm follows")
    p = "SKU-H1"
    run(["set", "product", p, "stage", "live"], main=HOOKED)
    n = top()
    rc, out, err = run(["confirm", "product", p, "stage", "--reason",
                        "went live"], tty=["live"], main=HOOKED)
    check("a stage confirm with no pending date: the prompt says today's "
          "date will be recorded",
          rc == 0 and f"stage_since will be recorded as {TODAY}" in err, err)
    check("...stage_since is today, in force, and stage_record frozen in "
          "force, from → to",
          eff("product", p, "stage") == "live"
          and stored("product", p, "stage_since")
          == (TODAY, "confirmed", TODAY)
          and json.loads(eff("product", p, "stage_record"))
          == {"from": None, "to": "live", "on": TODAY}
          and "stage_record recorded" in out, out)
    note = "went live [set by the stage confirm: stage → live]"
    check("...all in the confirm's own write, under its channel: stage "
          "confirm, stage_since set + confirm, stage_record set only",
          [(h[1], h[5], h[4], h[6]) for h in hist(n)] == [
              ("stage", "confirm", f"{USER}@tty", "went live"),
              ("stage_since", "set", f"{USER}@tty", note),
              ("stage_since", "confirm", f"{USER}@tty", note),
              ("stage_record", "set", f"{USER}@tty",
               "went live [frozen by the stage confirm: None → live]")],
          hist(n))

    print("  -- the rollback guard")
    n = top()
    rc, _, err = run(["set", "product", p, "stage", "draft"], main=HOOKED)
    check("an agent cannot move a product back (live → draft)",
          rc == 2 and "back from live to draft" in err, err)
    rc, _, err = run(["set", "product", p, "stage", "draft"], tty=["live"],
                     main=HOOKED)
    check("...nor a human who retypes the wrong stage", rc == 2, err)
    check("...nothing written", top() == n)
    rc, out, _ = run(["set", "product", p, "stage", "draft", "--reason",
                      "relaunch"], tty=["draft"], main=HOOKED)
    check("a human at a terminal may, with a reason: pending, by @tty",
          rc == 0 and "(pending)" in out
          and hist(n) == [(p, "stage", "live", "draft", f"{USER}@tty",
                           "set", "relaunch", "pending")], (out, hist(n)))
    rc, out, _ = run(["set", "product", p, "stage", "retired"], main=HOOKED)
    check("moving forward needs no guard", rc == 0, out)
    run(["set", "product", p, "stage", "draft", "--reason", "back again"],
        tty=["draft"], main=HOOKED)
    run(["confirm", "product", p, "stage"], tty=["draft"], main=HOOKED)
    check("...the earlier stage is in force only after its confirm",
          eff("product", p, "stage") == "draft")

    q = "SKU-H2"
    run(["set", "product", q, "stage", "live"], main=HOOKED)
    run(["confirm", "product", q, "stage"], tty=["live"], main=HOOKED)
    n = top()
    rc, _, err = run(["set", "product", q, "stage", "draft"], secret=True,
                     main=HOOKED)
    code = code_in(err)
    check("relay: a rollback off a TTY gets a code and its summary",
          rc == 2 and code and "live → draft is a rollback" in err, err)
    rc, _, err = run(["set", "product", q, "stage", "draft", "--code",
                      code], secret=True, main=HOOKED)
    check("...the code without the relay audit is refused",
          rc == 2 and "--relay-user" in err and top() == n, err)
    rc, out, _ = run(["set", "product", q, "stage", "draft", "--reason",
                      "client relaunched", "--code", code, *RELAY],
                     secret=True, main=HOOKED)
    check("...with the audit: written pending, by @relay, audit in reason",
          rc == 0 and hist(n) == [(q, "stage", "live", "draft",
                                   f"{USER}@relay", "set",
                                   "client relaunched" + AUDIT, "pending")]
          and eff("product", q, "stage") == "live", (out, hist(n)))
    n = top()
    rc, _, err = run(["set", "product", q, "stage", "draft", "--code", code,
                      *RELAY], secret=True, main=HOOKED)
    check("...the used code is stale: the same rollback set again (live "
          "still in force) needs a new challenge",
          rc == 2 and "does not match" in err and top() == n, err)
    rc, out, err = run(["set", "product", q, "stage", "retired", "--code",
                        code, *RELAY], secret=True, main=HOOKED)
    check("...a set that needs no guard ignores --code: written by @cli",
          rc == 0 and hist(n) == [(q, "stage", "draft", "retired",
                                   f"{USER}@cli", "set", "test", "pending")],
          (out, err, hist(n)))

    print("  -- on_confirm binds what it confirms into the code")
    r = "SKU-H3"
    run(["set", "product", r, "stage", "live"], main=HOOKED)
    run(["set", "product", r, "stage_since", "2026-09-02"], main=HOOKED)
    rc, _, err = run(["confirm", "product", r, "stage"], secret=True,
                     main=HOOKED)
    stale = code_in(err)
    check("the summary to relay shows the pending date",
          rc == 2 and "(and its pending stage_since = 2026-09-02)" in err
          and stale, err)
    run(["set", "product", r, "stage_since", "2026-08-01"], main=HOOKED)
    rc, _, err = run(["confirm", "product", r, "stage", "--code", stale,
                      *RELAY], secret=True, main=HOOKED)
    check("...a code issued with one pending date does not confirm another",
          rc == 2 and "does not match" in err
          and eff("product", r, "stage") is None, err)
    rc, out, _ = run(["confirm", "product", r, "stage", "--json"],
                     secret=True, main=HOOKED)
    d = one_doc(out)
    check("...the subject carries the bound date",
          d["subject"]["items"] == {r: {"stage": "live",
                                        "stage_since": "2026-08-01"}}, d)
    rc, out, _ = run(["confirm", "product", r, "stage", "--json", "--code",
                      d["params"]["confirm_code"], *RELAY], secret=True,
                     main=HOOKED)
    d = one_doc(out) or {}
    check("...the code for the date on screen confirms both, by @relay; "
          "--json lists stage + stage_since as confirmed, stage_record as "
          "recorded",
          rc == 0 and eff("product", r, "stage") == "live"
          and eff("product", r, "stage_since") == "2026-08-01"
          and [x["key"] for x in d["confirmed"]] == ["stage", "stage_since"]
          and [(x["key"], x["status"]) for x in d["recorded"]]
          == [("stage_record", "confirmed")]
          and hist()[-1][4] == f"{USER}@relay", d)

    print("  -- a Follow's keys move the code's version too")
    k = "SKU-H4"
    run(["set", "product", k, "stage", "live"], main=HOOKED)
    _, _, err = run(["confirm", "product", k, "stage"], secret=True,
                    main=HOOKED)
    kcode = code_in(err)
    run(["set", "product", k, "stage_since", "2026-09-01"], main=HOOKED)
    run(["withdraw", "product", k, "stage_since"], main=HOOKED)
    rc, _, err = run(["confirm", "product", k, "stage", "--code", kcode,
                      *RELAY], secret=True, main=HOOKED)
    check("a stage_since set and withdrawn after the code was shown (same "
          "items on screen): the code is stale",
          rc == 2 and "does not match" in err
          and eff("product", k, "stage") is None, err)

    print("  -- several ids, and a failing hook")
    many = ["SKU-M1", "SKU-M2"]
    for x in many:
        run(["set", "product", x, "stage", "live"], main=HOOKED)
    rc, out, _ = run(["confirm", "product", *many, "stage"],
                     tty=["confirm"], main=HOOKED)
    check("a batch of stage confirms settles each stage_since",
          rc == 0 and all(eff("product", x, "stage") == "live"
                          and eff("product", x, "stage_since") == TODAY
                          for x in many), out)
    s = "SKU-F1"
    run(["set", "product", s, "stage", "live"], main=HOOKED)
    n = top()
    FAIL_APPLY.append(True)
    try:
        rc, out, err = run(["confirm", "product", s, "stage", "--json"],
                           tty=["live"], main=HOOKED)
    finally:
        FAIL_APPLY.clear()
    check("a hook that fails after writing rolls the whole confirm back",
          rc == 1 and one_doc(out)["code"] == "unclassified_error"
          and top() == n and eff("product", s, "stage") is None
          and stored("product", s, "stage_since") is None, (out, err))


# ---- 10 --------------------------------------------------------------------

def test_markets() -> None:
    print("[10] markets: declared, resolved, one entity per market")
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "live",
                      "--market", "CA", "--json"])
    check("a write to an undeclared market is refused",
          rc == 2 and coded(out)[0] == "market_not_onboarded", out)
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "live",
                      "--market", "GB", "--json"])
    check("a market outside the closed set is refused",
          rc == 2 and coded(out)[0] == "market_not_a_market", out)
    declare("CA")
    rc, out, _ = run(["set", "product", "SKU-A", "stage", "live", "--json"])
    d = one_doc(out)
    check("two markets declared: market_ambiguous, next reruns with each",
          rc == 2 and d["code"] == "market_ambiguous"
          and d["next"] == [
              "shop decisions set product SKU-A stage live --json --reason "
              "test --source agent_proposed_2026-09-22 --market CA",
              "shop decisions set product SKU-A stage live --json --reason "
              "test --source agent_proposed_2026-09-22 --market US"], d)
    run(["set", "product", "SKU-A", "stage", "live", "--market", "ca"])
    run(["confirm", "product", "SKU-A", "stage", "--market", "CA"],
        tty=["live"])
    check("the same product holds its own stage per market",
          eff("product", "SKU-A", "stage", "CA") == "live"
          and eff("product", "SKU-A", "stage", "US") == "retired")
    rc, out, _ = run(["history", "--market", "CA", "--json"])
    rows = one_doc(out)["history"]
    check("history carries the market and filters by it",
          rc == 0 and rows and {r["market"] for r in rows} == {"CA"}, rows)
    rc, out, _ = run(["list", "--json"])
    check("list without --market shows every market",
          {r["market"] for r in one_doc(out)["decisions"]} == {"US", "CA"},
          out)
    rc, out, _ = run(["get", "product", "SKU-A", "stage", "--json"])
    check("a read without --market, two declared: ambiguous too",
          rc == 2 and coded(out)[0] == "market_ambiguous", out)

    print("  -- a harness with markets = [] keeps one market, \"_\"")
    root = Path(tmp_dir("solo-"))
    (root / "ssot").mkdir()
    (root / "harness.toml").write_text(
        '[harness]\nname = "solo"\ncli = "solo"\nenv_prefix = "SOLO"\n'
        'languages = ["en", "zh"]\nmarkets = []\n[ssot]\n'
        'decision_keys = "ssot/decision_keys.tsv"\n', encoding="utf-8")
    shutil.copy(_shop.SHOP / "ssot" / "decision_keys.tsv", root / "ssot")
    config.use(root)
    try:
        data = Path(tmp_dir("solo-data-"))
        reg = DecisionRegistry()
        solo = lambda av: decisions.main(av, spec=SPEC, registry=reg)  # noqa: E731
        e = clean_env(SOLO_DATA_DIR=str(data), SOLO_AUTH_ENV_PATHS="none")
        rc1, out1, _ = capture(solo, ["set", "product", "p1", "stage", "live",
                                      "--source", "s", "--reason", "r"],
                               env=e)
        rc2, _, _ = capture(solo, ["confirm", "product", "p1", "stage",
                                   "--reason", "r"], env=e,
                            tty_answers=["live"])
        with closing(sqlite3.connect(data / "solo.db")) as c:
            got = decisions.effective(c, "_", "product", "p1", "stage")
        check("set + confirm with no --market land under \"_\"; the hint "
              "names no --market",
              rc1 == rc2 == 0 and got == "live" and "--market" not in out1,
              (out1, got))
        rc, out, _ = capture(solo, ["get", "product", "p1", "stage",
                                    "--market", "US", "--json"], env=e)
        check("...and a --market other than \"_\" is refused",
              rc == 2 and coded(out)[0] == "market_unpartitioned", out)
    finally:
        _shop.use()


# ---- 11 --------------------------------------------------------------------

def test_readers_and_protection() -> None:
    print("[11] readers, triggers, a value changed meanwhile, hook errors")
    bare = sqlite3.connect(":memory:")
    check("a DB with no decisions tables: nothing in force, nothing pending",
          decisions.effective(bare, "US", "product", "SKU-A", "stage") is None
          and decisions.pending(bare, "US") == []
          and decisions.row(bare, "US", "product", "SKU-A", "stage") is None
          and decisions.last_change(bare, "US", "product", "SKU-A",
                                    ["stage"]) == 0
          and decisions.set_history(bare, "US", "product", "SKU-A",
                                    "stage") == [])
    bare.close()
    with closing(con()) as c:
        c.row_factory = sqlite3.Row
        pend = decisions.pending(c, "US")
        hist_a = decisions.set_history(c, "US", "product", "SKU-A", "stage")
    check("pending(): dict rows (any row_factory), each with `effective`",
          pend and all(isinstance(r, dict) and r["status"] == "pending"
                       and "effective" in r for r in pend), pend)
    check("set_history(): every value set, newest first",
          hist_a == ["retired", "live"], hist_a)
    for sql in ("DELETE FROM decisions",
                "DELETE FROM decisions_history",
                "UPDATE decisions_history SET reason = 'rewritten'"):
        with closing(con()) as c:
            e = raises(lambda: c.execute(sql), sqlite3.IntegrityError)
        check(f"raw `{sql}` is aborted by a trigger", e is not None, e)

    print("  -- a value that changed while the human answered")
    us = ["--market", "US"]         # [10] declared CA too
    run(["set", "campaign", "9601", "channel", "organic", *us])
    real = human.confirm

    def racing(*a, **k):
        channel = real(*a, **k)
        capture(MAIN, ["set", "campaign", "9601", "channel", "paid", *us,
                       "--reason", "meanwhile", "--source", "agent_x"],
                env=env())
        return channel

    n = top()
    with mock.patch.object(human, "confirm", racing):
        rc, out, _ = run(["confirm", "campaign", "9601", "channel", *us,
                          "--json"], tty=["organic"])
    d = one_doc(out) or {}
    check("refused (decision_changed_meanwhile), next = rerun",
          rc == 2 and d.get("code") == "decision_changed_meanwhile"
          and d["next"] == ["shop decisions confirm campaign 9601 channel "
                            "--market US --json --reason test"], d)
    check("...the value shown is not written over the newer proposal",
          stored("campaign", "9601", "channel") == ("paid", "pending", None)
          and [h[5] for h in hist(n)] == ["set"], hist(n))

    print("  -- hook contract errors")
    bad_follow = main_with(on_confirm=lambda con, **k: decisions.Follow(
        bind={"channel": "paid"}))
    rc, out, _ = run(["confirm", "campaign", "9601", "channel", *us,
                      "--json"], tty=["paid"], main=bad_follow)
    check("an on_confirm that re-binds the confirmed key is a bug (rc 1)",
          rc == 1 and one_doc(out)["code"] == "unclassified_error", out)
    bad_guard = main_with(set_guard=lambda con, **k: "sudo")
    rc, out, _ = run(["set", "campaign", "9601", "channel", "organic", *us,
                      "--json"], main=bad_guard)
    check("a set_guard returning an unknown channel is a bug (rc 1)",
          rc == 1 and one_doc(out)["code"] == "unclassified_error", out)
    with closing(con()) as c:
        e = raises(lambda: decisions.write_confirm(
            c, "US", "campaign", "9999", "channel", reason="r",
            channel="cli"), ValueError)
    check("write_confirm of a row that does not exist is a ValueError",
          e is not None, e)


# ---- 12 --------------------------------------------------------------------

def test_closure() -> None:
    print("[12] decisions.py keeps its registry fragment closed")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "decisions.tsv"}
    used = {c: reg[c] for c in ("decision_entity_type_unknown",)}
    probs = messages.check_registry_closed(_shop.KIT, ["decisions.py"],
                                           {**mine, **used}, strict_kit=True)
    check("every msg() in decisions.py is literal and registered with its "
          "exact params; every decisions.tsv code is emitted", probs == [],
          probs)
    check("the fragment holds the verb's codes (the registry's own "
          "decision_* codes stay in registry.tsv)",
          set(mine) == {"decision_not_found", "decision_harness_written",
                        "decision_source_required", "decision_value_one_id",
                        "decision_already_confirmed", "decision_withdrawn",
                        "decision_confirm_refused",
                        "decision_changed_meanwhile",
                        "decision_nothing_pending"}, sorted(mine))


if __name__ == "__main__":
    test_registry_closed()
    test_pending_then_confirm()
    test_confirm_exempt()
    test_code_bound()
    test_confirm_many()
    test_confirm_json()
    test_confirm_value()
    test_withdraw()
    test_hooks()
    test_markets()
    test_readers_and_protection()
    test_closure()
    raise SystemExit(finish())
