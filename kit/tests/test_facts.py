#!/usr/bin/env python3
"""The client facts verb (kit/facts.py) holds each of its guards, on the
fake "shop" harness (markets US/CA; the fact keys and thresholds are
fixture TSVs written here). Ported from the reference harness's
tests/test_facts.py, test_gate_refusal_codes.py (the facts part) and
test_facts_numeric_contract.py (the write-time contract; its compute
banner is harness code and stays there).

  1. Read verbs never create the DB (no_db); every usage error is coded.
  2. init, piped: declares the market PENDING, answers pending (canonical
     form), provenance agent_interview_<date>; a pending declaration does
     not open the door (market_not_onboarded).
  3. The declaration is confirmed through the gate by a relayed code
     (challenge as data, then the code with its audit); init at a
     terminal (the KIT_TTY seam) confirms it by retyping the market.
  4. set is pending; a changed value is pending again; a same-value set
     writes no history and keeps updated_at; --source / --reason needed.
  5. confirm: TTY retype; a mistype, no terminal, no secret are refused;
     a relayed code needs its audit, confirms exactly the value it was
     issued for, and a second use is refused because the version (the
     key's last history id) moved; confirm --value creates or replaces.
  6. unconfirm lowers trust with no gate; already pending / confirmed.
  7. rollback writes the old value as pending; other key, unknown row,
     same value are refused or no-ops.
  8. Numeric bounds, choices, dates, thresholds: refused where typed,
     coded with their bound, nothing written; init re-asks, then aborts
     with nothing written; init never overwrites an answer on disk.
  9. Gate refusal codes: each is ONE JSON document {error, next, code,
     params} (exit 2), the same line alone on stderr without --json, and
     writes nothing.
 10. Markets: several declared or waiting = fact_market_ambiguous with
     reruns; list / get / history / list --thresholds.
 11. restore is TTY only (a secret and a code never unlock it), shows
     the diff, replays a kit backup or a SQL dump additively, twice =
     nothing; unusable, missing and hostile backups are refused.
 12. A fact that moved while the human was asked is not overwritten;
     human-table triggers; readers confirmed / effective / pending.
 13. A harness with markets = [] keeps every fact under "_".
 14. facts.py's msg() calls are closed over its fragment.
"""

import getpass
import io
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, db, facts, human, messages  # noqa: E402
from kit.registry import FactKeys, Thresholds  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, raises, tmp_dir)

CFG = _shop.use()
DATA = _shop.data_dir()
SECRET = CFG.env("CONFIRM_CODE_SECRET")
USER = getpass.getuser()
TODAY = human.now()[:10]
RELAY = ["--relay-user", "chat:U1", "--relay-at", "2026-09-23T10:00:00Z"]
AUDIT = " [relay user=chat:U1 at=2026-09-23T10:00:00Z]"

FIX = Path(tmp_dir("facts-fixtures-"))
(FIX / "fact_keys.tsv").write_text(
    "key\tgroup\ttype\tunit\tmin\tmax\tlabel_en\tlabel_zh\twhy\n"
    "unit_cost\tcost\tnumber\tUSD\t0\t\tUnit cost\t单位成本\tmargin\n"
    "monthly_cap\tbudget\tnumber\tUSD\t0\t\tMonthly cap\t月度上限\tpacing\n"
    "fee_pct\tcost\tnumber\t%\t0\t100\tFee percent\t费率\tmargin\n"
    "channel\tprofile\tchoice:web|store\t\t\t\tChannel\t渠道\trouting\n"
    "launch_date\tprofile\tdate\t\t\t\tLaunch date\t上线日期\tage\n",
    encoding="utf-8")
(FIX / "constants.tsv").write_text(
    "name\tdefault\tunit\tmin\tmax\tgroup\tlabel_en\tlabel_zh\texplain\twhy\n"
    "max_step\t0.2\tratio\t0\t1\tpacing\tMax step\t最大步长\tone change's "
    "size\tsafety\n", encoding="utf-8")
KEYS = FactKeys(FIX / "fact_keys.tsv")
TH = Thresholds(FIX / "constants.tsv")
SPEC = db.with_human({}, version=1)
QUESTIONS = [{"key": "unit_cost", "prompt": "Unit cost per item, USD"},
             {"key": "monthly_cap"}, {"key": "fee_pct"}]


def MAIN(argv):
    return facts.main(argv, spec=SPEC, keys=KEYS, thresholds=TH,
                      init_questions=QUESTIONS)


def env(data: Path, secret: bool = False) -> dict:
    extra = {SECRET: "s3cret"} if secret else {}
    return clean_env(**{CFG.env("DATA_DIR"): str(data),
                        CFG.env("AUTH_ENV_PATHS"): "none", **extra})


def run(argv, *, data=None, tty=None, secret=False, stdin=None, main=None):
    """facts.main(argv) in-process: (rc, out, err). tty=None: off a
    terminal (an agent); tty=[answers]: a human at one (KIT_TTY)."""
    return capture(main or MAIN, list(argv), env=env(data or DATA, secret),
                   stdin=stdin, tty_answers=tty)


def coded(out: str) -> tuple | None:
    d = one_doc(out)
    return d and (d.get("code"), d.get("params"))


def con(data=None) -> sqlite3.Connection:
    return sqlite3.connect((data or DATA) / CFG.db_file)


def fact(key, market="US", data=None):
    """(value, is_assumption, source, changed_by) or None."""
    with closing(con(data)) as c:
        return c.execute("SELECT value, is_assumption, source, changed_by "
                         "FROM client_facts WHERE market=? AND key=?",
                         (market, key)).fetchone()


def top(data=None) -> int:
    with closing(con(data)) as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM "
                         "client_facts_history").fetchone()[0]


def key_top(key, market="US", data=None) -> int:
    """The version a confirm of `key` binds: its last history id."""
    with closing(con(data)) as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM "
                         "client_facts_history WHERE market=? AND key=?",
                         (market, key)).fetchone()[0]


def hist(since=0, data=None) -> list[tuple]:
    """(market, key, action, old, new, is_assumption, reason, changed_by)."""
    with closing(con(data)) as c:
        return c.execute(
            "SELECT market, key, action, old_value, new_value, is_assumption, "
            "reason, changed_by FROM client_facts_history WHERE id > ? "
            "ORDER BY id", (since,)).fetchall()


def rows(data=None) -> dict:
    """Every human facts row, to prove a refusal wrote nothing."""
    with closing(con(data)) as c:
        return {t: c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
                for t in ("client_facts", "client_facts_history")}


def code_of(out: str) -> str | None:
    d = one_doc(out) or {}
    return (d.get("params") or {}).get("confirm_code")


def fresh(prefix="facts-") -> Path:
    return Path(tmp_dir(prefix))


# ---- 1 ---------------------------------------------------------------------

def test_reads_create_nothing() -> None:
    print("[1] read verbs never create the DB; usage errors are coded")
    for argv in (["list"], ["get", "unit_cost"], ["history"]):
        rc, out, err = run([*argv, "--json"])
        d = one_doc(out)
        check(f"{argv[0]} --json on no DB: one no_db document, exit 2",
              rc == 2 and d and d["code"] == "no_db"
              and d["next"] == ["shop facts init"], (rc, out, err))
    check("...and no DB file was created",
          not (DATA / CFG.db_file).exists())
    rc, out, err = run(["rollback", "unit_cost", "--reason", "r", "--json"])
    d = one_doc(out)
    check("a usage error (missing --to) is one coded document (fact_usage)",
          rc == 2 and d and d["code"] == "fact_usage"
          and "--to" in d["params"]["detail"]
          and d["next"] == ["shop facts rollback --help"], (rc, out, err))
    rc, out, err = run(["confirm", "unit_cost", "--yes", "--json"])
    check("there is no bypass flag (--yes is a usage error)",
          rc == 2 and coded(out)[0] == "fact_usage", (rc, out, err))
    rc, out, err = run(["bogus", "--json"])
    check("an unknown verb is a usage error too",
          rc == 2 and coded(out)[0] == "fact_usage", (rc, out, err))
    rc, out, err = run(["--help"])
    check("--help lists every verb, exit 0",
          rc == 0 and all(v in out for v in (
              "init", "list", "get", "set", "confirm", "unconfirm",
              "history", "rollback", "restore")), out)
    check("a malformed init question list is a harness bug (ValueError)",
          all(isinstance(raises(lambda q=q: facts.main(
              ["list"], spec=SPEC, keys=KEYS, thresholds=TH,
              init_questions=q), ValueError), ValueError)
              for q in ([{"key": "nope"}], [{"key": "unit_cost"}] * 2,
                        [{"key": "unit_cost", "extra": 1}], ["unit_cost"])))
    check("a spec without the kit's human tables is refused (ValueError)",
          isinstance(raises(lambda: facts.main(
              ["list"], spec=db.SchemaSpec(tables={}, human_tables=(),
                                           version=1),
              keys=KEYS, thresholds=TH), ValueError), ValueError))


# ---- 2 ---------------------------------------------------------------------

def test_init_piped() -> None:
    print("[2] init, piped: the declaration and every answer are pending")
    rc, out, err = run(["init", "--json"], stdin="us\n4.20\n\n15\n\n")
    d = one_doc(out)
    check("init --json: rc 0, one document, the declaration pending",
          rc == 0 and d and d["market"] == "US"
          and d["declaration"] == "pending"
          and d["message_code"]["code"] == "fact_init_done"
          and d["message_code"]["params"] == {
              "market": "US", "recorded": 2, "declaration": "pending"}
          and d["kept"] == ["monthly_cap"]
          and d["next"] == ["shop facts confirm market_declared --market "
                            "US --reason <why>"], (rc, out, err))
    check("the prompts went to stderr, the answers echoed there",
          "market (one of US, CA)" in err and "Unit cost per item" in err
          and "[2/3 monthly_cap] Monthly cap" in err and "→ 4.20" in err,
          err)
    src = f"agent_interview_{TODAY}"
    check("market_declared = US, pending, piped provenance",
          fact("market_declared") == ("US", 1, src, f"{USER}@cli"),
          fact("market_declared"))
    check("answers stored in canonical form (4.20 -> 4.2), pending",
          fact("unit_cost") == ("4.2", 1, src, f"{USER}@cli")
          and fact("fee_pct") == ("15", 1, src, f"{USER}@cli")
          and fact("monthly_cap") is None,
          (fact("unit_cost"), fact("fee_pct")))
    h = hist()
    check("one history row per write, action init, the onboarding reason",
          [r[1:6] for r in h] == [
              ("market_declared", "init", None, "US", 1),
              ("unit_cost", "init", None, "4.2", 1),
              ("fee_pct", "init", None, "15", 1)]
          and {r[6] for r in h} == {"onboarding (shop facts init)"}, h)
    before = rows()
    rc, out, err = run(["set", "unit_cost", "5", "--source", "s",
                        "--reason", "r", "--json"])
    check("a PENDING declaration does not open the door: "
          "market_not_onboarded, next facts init",
          rc == 2 and coded(out) == ("market_not_onboarded",
                                     {"market": "US"})
          and one_doc(out)["next"] == ["shop facts init"], (rc, out))
    rc, out, err = run(["confirm", "unit_cost", "--reason", "r", "--json"],
                       secret=True)
    check("...and no confirm of another key either",
          rc == 2 and coded(out)[0] == "market_not_onboarded", (rc, out))
    check("...nothing written", rows() == before)
    rc, out, err = run(["init"], stdin="US\n4.2\n\n15\n\n")
    check("a second piped init with the same answers: rc 0, nothing new",
          rc == 0 and rows() == before and "left as-is" in out, (rc, out))


# ---- 3 ---------------------------------------------------------------------

def test_declaration_gate() -> None:
    print("[3] the declaration: relayed code; init at a terminal")
    cmd = ["confirm", "market_declared", "--reason", "owner said so",
           "--json"]
    rc, out, err = run(cmd)
    check("off a TTY, no secret: confirm_needs_human",
          rc == 2 and coded(out)[0] == "confirm_needs_human", (rc, out))
    last = top()
    rc, out, err = run(cmd, secret=True)
    d = one_doc(out)
    check("with the secret: the challenge as data (confirm_code_required)",
          rc == 2 and d["code"] == "confirm_code_required"
          and len(d["params"]["confirm_code"]) == 6
          and d["subject"] == {"verb": "facts confirm", "market": "US",
                               "entity_type": "fact",
                               "items": {"market_declared": "US"},
                               "version": key_top("market_declared")}
          and d["next"] == [
              "shop facts confirm market_declared --reason 'owner said so' "
              f"--json --code {d['params']['confirm_code']} --relay-user "
              "<sender_id> --relay-at <iso_time>"], d)
    code = code_of(out)
    rc, out, err = run([*cmd, "--code", code, *RELAY], secret=True)
    d = one_doc(out)
    check("the relayed code confirms the declaration; changed_by @relay",
          rc == 0 and d["confirmed"][0]["is_assumption"] == 0
          and d["changed_by"] == f"{USER}@relay"
          and d["reason"] == "owner said so" + AUDIT
          and fact("market_declared")[1:] == (0, f"agent_interview_{TODAY}",
                                              f"{USER}@relay"), (rc, out))
    check("history: one confirm row with the audit",
          hist(last) == [("US", "market_declared", "confirm", "US", "US", 0,
                          "owner said so" + AUDIT, f"{USER}@relay")],
          hist(last))

    other = fresh("facts-tty-")
    rc, out, err = run(["init"], data=other, stdin="CA\n1\n2\n3\n\n",
                       tty=["CA"])
    check("init at a terminal: the retyped market confirms the declaration",
          rc == 0 and fact("market_declared", "CA", other)
          == ("CA", 0, f"owner_interview_{TODAY}", f"{USER}@tty"),
          (rc, out, err, fact("market_declared", "CA", other)))
    check("...history: init then confirm (by @tty); answers still pending",
          [r[1:3] + (r[7],) for r in hist(0, other)][:2] == [
              ("market_declared", "init", f"{USER}@cli"),
              ("market_declared", "confirm", f"{USER}@tty")]
          and fact("unit_cost", "CA", other)[1] == 1, hist(0, other))
    other2 = fresh("facts-tty2-")
    rc, out, err = run(["init"], data=other2, stdin="CA\n\n\n\n\n",
                       tty=["US"])
    check("a mistyped market at init: the declaration stays pending",
          rc == 0 and fact("market_declared", "CA", other2)[1] == 1
          and "confirm_typed_mismatch" not in out and "expected" in err,
          (rc, out, err))


# ---- 4 ---------------------------------------------------------------------

def test_set() -> None:
    print("[4] set: pending; a changed value is pending again")
    last = top()
    rc, out, err = run(["set", "unit_cost", "5.00", "--source",
                        "owner_interview_2026-09-22", "--reason", "new quote",
                        "--json"])
    d = one_doc(out)
    check("set: a changed value, pending, canonical",
          rc == 0 and d["change"] == "changed"
          and d["message_code"] == {"code": "fact_set_changed", "params": {
              "key": "unit_cost", "market": "US", "old": "4.2", "new": "5"}}
          and fact("unit_cost") == ("5", 1, "owner_interview_2026-09-22",
                                    f"{USER}@cli"), (rc, out))
    check("history: action set, old -> new, reason as typed",
          hist(last) == [("US", "unit_cost", "set", "4.2", "5", 1,
                          "new quote", f"{USER}@cli")], hist(last))
    with closing(con()) as c:
        at = c.execute("SELECT updated_at FROM client_facts WHERE "
                       "key='unit_cost'").fetchone()[0]
    last = top()
    rc, out, err = run(["set", "unit_cost", "5", "--source", "invoice_42",
                        "--reason", "same", "--json"])
    with closing(con()) as c:
        at2 = c.execute("SELECT updated_at FROM client_facts WHERE "
                        "key='unit_cost'").fetchone()[0]
    check("a same-value set: no history row, no updated_at bump, the new "
          "source kept",
          rc == 0 and one_doc(out)["change"] == "source" and top() == last
          and at2 == at and fact("unit_cost")[2] == "invoice_42", (out,))
    rc, out, err = run(["set", "unit_cost", "5", "--source", "invoice_42",
                        "--reason", "same", "--json"])
    check("...again: change none",
          rc == 0 and one_doc(out)["change"] == "none" and top() == last)
    rc, out, err = run(["set", "channel", "web", "--source", "s", "--reason",
                        "r"])
    check("set a new key (text mode): rc 0, pending, the note on stdout",
          rc == 0 and fact("channel")[:2] == ("web", 1)
          and "pending until a person confirms it" not in err
          and "channel [US] = web" in out, (rc, out, err))
    before = rows()
    for argv, want in (
            (["set", "unit_cost", "6", "--reason", "r"],
             ("fact_source_required", {})),
            (["set", "unit_cost", "6", "--source", " ", "--reason", "r"],
             ("fact_source_required", {})),
            (["set", "unit_cost", "6", "--source", "s"],
             ("reason_required", {})),
            (["set", "unit_cost", "6", "--source", "s", "--reason", "  "],
             ("reason_required", {}))):
        rc, out, err = run([*argv, "--json"])
        check(f"{' '.join(argv[3:])!r}: {want[0]}",
              rc == 2 and coded(out) == want, (rc, out))
    check("...nothing written", rows() == before)


# ---- 5 ---------------------------------------------------------------------

def test_confirm() -> None:
    print("[5/6] confirm: the gate, at a terminal or relayed; unconfirm")
    before = rows()
    base = ["confirm", "unit_cost", "--reason", "owner checked"]
    rc, out, err = run([*base, "--json"], tty=["6"])
    check("a mistyped retype: confirm_typed_mismatch, nothing written",
          rc == 2 and coded(out) == ("confirm_typed_mismatch", {
              "what": "`shop facts confirm`", "typed": "6", "expected": "5"})
          and rows() == before, (rc, out))
    rc, out, err = run([*base, "--json"], tty=[])
    check("no answer at the terminal (EOF): confirm_tty_eof",
          rc == 2 and coded(out)[0] == "confirm_tty_eof" and rows() == before)
    rc, out, err = run(base)
    check("off a TTY, no secret, text mode: refused on stderr only",
          rc == 2 and out == "" and err.startswith("error: ")
          and "interactive terminal" in err and rows() == before, (out, err))
    last = top()
    rc, out, err = run([*base, "--json"], tty=["5"])
    d = one_doc(out)
    check("the value retyped at the terminal: confirmed, @tty",
          rc == 0 and d["changed_by"] == f"{USER}@tty"
          and d["message_code"]["code"] == "fact_confirmed"
          and fact("unit_cost") == ("5", 0, "invoice_42", f"{USER}@tty"),
          (rc, out, err))
    check("history: one confirm row, reason as typed (no relay audit)",
          hist(last) == [("US", "unit_cost", "confirm", "5", "5", 0,
                          "owner checked", f"{USER}@tty")], hist(last))
    rc, out, err = run([*base, "--json"])
    d = one_doc(out)
    check("confirming a confirmed value: fact_already_confirmed, no gate",
          rc == 0 and d["confirmed"] == []
          and d["message_code"]["code"] == "fact_already_confirmed"
          and top() == last + 1, (rc, out))

    print("    a relayed code: bound to the value and the version")
    run(["set", "fee_pct", "12", "--source", "s", "--reason", "r"])
    fee = ["confirm", "fee_pct", "--reason", "relayed", "--json"]
    rc, out, err = run(fee, secret=True)
    code = code_of(out)
    check("the challenge is issued", rc == 2 and code, out)
    before = rows()
    rc, out, err = run([*fee, "--code", code], secret=True)
    check("a code without --relay-user/--relay-at: "
          "confirm_relay_audit_missing, nothing written",
          rc == 2 and coded(out) == ("confirm_relay_audit_missing", {})
          and rows() == before, (rc, out))
    rc, out, err = run(["confirm", "fee_pct", "--value", "20", "--reason",
                        "r", "--code", code, *RELAY, "--json"], secret=True)
    check("the code issued for 12 never confirms --value 20",
          rc == 2 and coded(out)[0] == "confirm_code_mismatch"
          and rows() == before, (rc, out))
    rc, out, err = run([*fee, "--code", "000000" if code != "000000"
                        else "111111", *RELAY], secret=True)
    check("a wrong code: confirm_code_mismatch",
          rc == 2 and coded(out)[0] == "confirm_code_mismatch")
    last = top()
    rc, out, err = run([*fee, "--code", code, *RELAY], secret=True)
    check("the right code with its audit confirms 12, @relay",
          rc == 0 and fact("fee_pct")[:2] == ("12", 0)
          and fact("fee_pct")[3] == f"{USER}@relay"
          and hist(last)[0][2:4] == ("confirm", "12")
          and hist(last)[0][6] == "relayed" + AUDIT, (rc, out, err))
    rc, out, err = run(["unconfirm", "fee_pct", "--reason", "in doubt",
                        "--json"])
    check("unconfirm (no gate, off a TTY): pending again, same value",
          rc == 0 and fact("fee_pct")[:2] == ("12", 1)
          and hist(top() - 1) == [("US", "fee_pct", "unconfirm", "12", "12",
                                   1, "in doubt", f"{USER}@cli")], (rc, out))
    before = rows()
    rc, out, err = run([*fee, "--code", code, *RELAY], secret=True)
    check("a second use of the same code is refused: the version moved",
          rc == 2 and coded(out)[0] == "confirm_code_mismatch"
          and rows() == before and fact("fee_pct")[1] == 1, (rc, out))
    rc, out, err = run(["unconfirm", "fee_pct", "--reason", "x", "--json"])
    check("unconfirm on a pending fact: fact_already_pending, no row",
          rc == 0 and one_doc(out)["message_code"]["code"]
          == "fact_already_pending" and rows() == before)
    rc, out, err = run(["unconfirm", "fee_pct", "--json"])
    check("unconfirm needs --reason", rc == 2
          and coded(out) == ("reason_required", {}))

    print("    confirm --value: the human's own value")
    last = top()
    rc, out, err = run(["confirm", "monthly_cap", "--value", "3000.0",
                        "--reason", "owner", "--json"], tty=["3000"])
    check("a key with no row: created confirmed, canonical, "
          "human_confirmed_<date>",
          rc == 0 and fact("monthly_cap") == ("3000", 0,
                                              f"human_confirmed_{TODAY}",
                                              f"{USER}@tty")
          and hist(last) == [("US", "monthly_cap", "confirm", None, "3000",
                              0, "owner", f"{USER}@tty")], (rc, out, err))
    rc, out, err = run(["confirm", "fee_pct", "--value", "20", "--reason",
                        "owner", "--json"], tty=["20"])
    d = one_doc(out)
    check("a pending 12 replaced by the human's 20, confirmed",
          rc == 0 and fact("fee_pct")[:3] == ("20", 0,
                                              f"human_confirmed_{TODAY}")
          and d["message_code"]["params"]["was"] == "12", (rc, out))
    rc, out, err = run(["confirm", "fee_pct", "--value", "20", "--reason",
                        "r", "--json"], secret=True)
    check("confirm --value of the value already confirmed: no gate, "
          "fact_already_confirmed",
          rc == 0 and one_doc(out)["message_code"]["code"]
          == "fact_already_confirmed", out)


# ---- 6/7 -------------------------------------------------------------------

def test_rollback() -> None:
    print("[7] rollback: the old value back as a pending value")
    with closing(con()) as c:
        init_id = c.execute("SELECT id FROM client_facts_history WHERE "
                            "key='unit_cost' AND action='init'").fetchone()[0]
        fee_id = c.execute("SELECT MIN(id) FROM client_facts_history WHERE "
                           "key='fee_pct'").fetchone()[0]
    last = top()
    rc, out, err = run(["rollback", "unit_cost", "--to", str(init_id),
                        "--reason", "quote was wrong", "--json"])
    d = one_doc(out)
    check("rollback to the init row: 4.2, pending, rollback_to_<id>_<date>",
          rc == 0 and fact("unit_cost")[:3] == (
              "4.2", 1, f"rollback_to_{init_id}_{TODAY}")
          and d["next"] == ["shop facts confirm unit_cost --market US "
                            "--reason <why>"]
          and d["message_code"]["params"] == {
              "key": "unit_cost", "market": "US", "old": "5", "new": "4.2",
              "id": init_id}, (rc, out))
    check("history: action rollback",
          hist(last) == [("US", "unit_cost", "rollback", "5", "4.2", 1,
                          "quote was wrong", f"{USER}@cli")], hist(last))
    before = rows()
    rc, out, err = run(["rollback", "unit_cost", "--to", str(init_id),
                        "--reason", "again", "--json"])
    check("rollback to the value it already holds: nothing written",
          rc == 0 and one_doc(out)["message_code"]["code"]
          == "fact_rollback_nothing" and rows() == before, (rc, out))
    for argv, want in (
            (["--to", str(fee_id)], ("fact_history_other", {
                "id": fee_id, "key": "unit_cost", "market": "US",
                "row_key": "fee_pct", "row_market": "US"})),
            (["--to", "9999"], ("fact_history_not_found", {"id": 9999}))):
        rc, out, err = run(["rollback", "unit_cost", *argv, "--reason", "r",
                            "--json"])
        check(f"rollback {argv[1]}: {want[0]}, nothing written",
              rc == 2 and coded(out) == want and rows() == before, (rc, out))
    rc, out, err = run(["rollback", "unit_cost", "--to", str(init_id),
                        "--json"])
    check("rollback needs --reason", rc == 2
          and coded(out) == ("reason_required", {}))
    # A history row that wrote no value (a hand-planted one: every verb
    # writes a value) cannot be rolled back to.
    with closing(con()) as c:
        c.execute("INSERT INTO client_facts_history (market, key, action, "
                  "old_value, new_value, reason, changed_by, at) VALUES "
                  "('US', 'unit_cost', 'set', '1', NULL, 'r', 't', 'x')")
        c.commit()
        empty = c.execute("SELECT MAX(id) FROM client_facts_history"
                          ).fetchone()[0]
    before = rows()
    rc, out, err = run(["rollback", "unit_cost", "--to", str(empty),
                        "--reason", "r", "--json"])
    check("a history row with no value: fact_history_no_value",
          rc == 2 and coded(out) == ("fact_history_no_value", {"id": empty})
          and rows() == before, (rc, out))


# ---- 8 ---------------------------------------------------------------------

def test_numeric_contract() -> None:
    print("[8] the value contract: refused where typed, nothing written")
    before = rows()
    cases = (
        ("unit_cost", "abc", "fact_value_not_number",
         {"key": "unit_cost", "value": "abc"}),
        ("unit_cost", "-5", "fact_value_out_of_range",
         {"key": "unit_cost", "value": "-5", "min": 0, "max": None}),
        ("fee_pct", "150", "fact_value_out_of_range",
         {"key": "fee_pct", "value": "150", "min": 0, "max": 100}),
        ("fee_pct", "nan", "fact_value_not_number",
         {"key": "fee_pct", "value": "nan"}),
        ("channel", "mail", "fact_value_not_choice",
         {"key": "channel", "value": "mail", "choices": ["web", "store"]}),
        ("launch_date", "last week", "fact_value_not_date",
         {"key": "launch_date", "value": "last week"}),
        ("threshold_max_step", "3", "fact_value_out_of_range",
         {"key": "threshold_max_step", "value": "3", "min": 0, "max": 1}),
    )
    for key, value, code, params in cases:
        for verb in (["set", key, value, "--source", "s"],
                     ["confirm", key, "--value", value]):
            rc, out, err = run([*verb, "--reason", "r", "--json"],
                               secret=True)
            check(f"{verb[0]} {key} {value!r}: {code}",
                  rc == 2 and coded(out) == (code, params), (rc, out))
    check("...nothing written by any of them", rows() == before)
    rc, out, err = run(["set", "fee_pct", "150", "--source", "s", "--reason",
                        "r"])
    check("text mode names the key and the value, on stderr only",
          rc == 2 and out == "" and "fee_pct" in err and "150" in err, err)
    rc, out, err = run(["set", "threshold_max_step", "0.30", "--source", "s",
                        "--reason", "r", "--json"])
    check("a threshold within bounds: stored canonical (0.30 -> 0.3)",
          rc == 0 and fact("threshold_max_step")[:2] == ("0.3", 1), out)
    for value in ("0", "100", "0.5"):
        rc, out, err = run(["set", "fee_pct", value, "--source", "s",
                            "--reason", "r"])
        check(f"fee_pct {value} (bounds inclusive) accepted", rc == 0, err)
    check("every settable key is validated (the registry is closed)",
          set(KEYS.settable(TH)) == {"unit_cost", "monthly_cap", "fee_pct",
                                     "channel", "launch_date",
                                     "threshold_max_step"})

    print("    init: a bad answer is asked again, then aborts cleanly")
    d2 = fresh("facts-init-")
    rc, out, err = run(["init"], data=d2, stdin="US\n0.2\nabc\n5000\n\n\n")
    check("a bad answer is re-asked, the wizard recovers (rc 0)",
          rc == 0 and "must be a number" in err and "try again" in err,
          (rc, out, err))
    check("...the recovered value is what was written",
          fact("monthly_cap", data=d2)[:2] == ("5000", 1))
    for label, stdin, code in (
            ("three bad answers", "US\n0.2\nabc\nabc\nabc\n\n\n",
             "fact_init_bad_answer"),
            ("an out-of-range answer three times",
             "US\n\n\n150\n-1\n101\n\n", "fact_init_bad_answer"),
            ("end of input", "US\n0.2\n", "fact_init_eof"),
            ("no market", "\n", "fact_init_no_market"),
            ("three bad markets", "GB\nXX\nzz\n", "fact_init_bad_market")):
        d3 = fresh("facts-abort-")
        rc, out, err = run(["init", "--json"], data=d3, stdin=stdin)
        check(f"init, {label}: {code}, one document, nothing written",
              rc == 2 and coded(out)[0] == code
              and (not (d3 / CFG.db_file).exists()
                   or rows(d3) == {"client_facts": [],
                                   "client_facts_history": []}),
              (rc, out, err))
    rc, out, err = run(["init", "--json"], data=fresh(), stdin="GB\nXX\nzz\n")
    check("fact_init_bad_market names the valid markets",
          coded(out) == ("fact_init_bad_market",
                         {"attempts": 3, "markets": ["US", "CA"]}), out)

    def interrupted(argv):
        sys.stdin = _Interrupt()
        return MAIN(argv)
    rc, out, err = run(["init", "--json"], data=fresh(), main=interrupted)
    check("init, Ctrl-C: fact_init_interrupted",
          rc == 2 and coded(out)[0] == "fact_init_interrupted", (rc, out))

    print("    init never overwrites an answer on disk")
    before = rows(d2)
    rc, out, err = run(["init", "--json"], data=d2,
                       stdin="US\n0.3\n\n\n\n")
    d = one_doc(out)
    check("a different answer for unit_cost: fact_init_would_overwrite, "
          "next = the set to run instead, nothing written",
          rc == 2 and d["code"] == "fact_init_would_overwrite"
          and d["params"] == {"key": "unit_cost", "market": "US",
                              "current": "0.2", "typed": "0.3"}
          and d["next"] == ["shop facts set unit_cost 0.3 --market US "
                            "--source <where the value came from> --reason "
                            "<why>"] and rows(d2) == before, (rc, out))
    rc, out, err = run(["init", "--json"], data=d2,
                       stdin="US\n0.20\n5000.0\n\n\n")
    check("the same answers in another form (0.20, 5000.0) keep them",
          rc == 0 and rows(d2) == before
          and one_doc(out)["kept"] == ["unit_cost", "monthly_cap",
                                       "fee_pct"], (rc, out, err))


class _Interrupt(io.StringIO):
    def readline(self, *a):
        raise KeyboardInterrupt


# ---- 9 ---------------------------------------------------------------------

def test_gate_refusal_codes() -> None:
    print("[9] each gate refusal: one coded document, the line on stderr")
    keys = list(KEYS.settable(TH))
    cases = (
        ("an empty --reason", ["confirm", "unit_cost", "--reason", " "],
         "reason_required", {}),
        ("no such fact", ["confirm", "launch_date", "--reason", "r"],
         "fact_not_found", {"key": "launch_date", "market": "US"}),
        ("--value for a malformed key",
         ["confirm", "UnitCost", "--value", "1", "--reason", "r"],
         "fact_key_malformed", {"key": "UnitCost"}),
        ("--value for an unknown key",
         ["confirm", "tax_rate", "--value", "1", "--reason", "r"],
         "fact_key_unknown", {"key": "tax_rate", "keys": keys}),
        ("--value not a number",
         ["confirm", "unit_cost", "--value", "abc", "--reason", "r"],
         "fact_value_not_number", {"key": "unit_cost", "value": "abc"}),
        ("--value below its minimum",
         ["confirm", "fee_pct", "--value", "-1", "--reason", "r"],
         "fact_value_out_of_range",
         {"key": "fee_pct", "value": "-1", "min": 0, "max": 100}),
        ("an undeclared market",
         ["confirm", "unit_cost", "--market", "CA", "--reason", "r"],
         "market_not_onboarded", {"market": "CA"}),
        ("not a market",
         ["confirm", "unit_cost", "--market", "GB", "--reason", "r"],
         "market_not_a_market", {"market": "GB", "markets": ["US", "CA"]}),
        ("set an unknown key",
         ["set", "tax_rate", "1", "--source", "s", "--reason", "r"],
         "fact_key_unknown", {"key": "tax_rate", "keys": keys}),
        ("set the market door itself",
         ["set", "market_declared", "CA", "--source", "s", "--reason", "r"],
         "fact_key_unknown", {"key": "market_declared", "keys": keys}),
        ("unconfirm: no such fact",
         ["unconfirm", "launch_date", "--reason", "r"],
         "fact_not_found", {"key": "launch_date", "market": "US"}),
        ("get: no such fact", ["get", "launch_date"],
         "fact_not_found", {"key": "launch_date", "market": "US"}),
        ("rollback an unknown key",
         ["rollback", "tax_rate", "--to", "1", "--reason", "r"],
         "fact_key_unknown", {"key": "tax_rate", "keys": keys}),
    )
    before = rows()
    for label, argv, code, params in cases:
        rc, out, err = run([*argv, "--json"])
        d = one_doc(out)
        check(f"{label}: {code}",
              rc == 2 and code != messages.UNCLASSIFIED and d
              and set(d) == {"error", "next", "code", "params"}
              and (d["code"], d["params"]) == (code, params)
              and err == "", (rc, out, err))
        text = d and d["error"]
        rc, out, err = run(argv)
        check(f"{label} (text): the same line on stderr only",
              rc == 2 and out == "" and text and err.startswith(
                  f"error: {text}\n"), (rc, out, err))
    check("no refusal wrote a human-table row", rows() == before)


# ---- 10 --------------------------------------------------------------------

def test_markets_and_reads() -> None:
    print("[10] markets and the read verbs")
    rc, out, err = run(["init"], stdin="CA\n\n\n\n\n")
    check("init CA, piped: pending declaration", rc == 0
          and fact("market_declared", "CA")[1] == 1, (out, err))
    rc, out, err = run(["set", "unit_cost", "1", "--source", "s", "--reason",
                        "r", "--json"])
    d = one_doc(out)
    check("--market omitted with US declared and CA waiting: "
          "fact_market_ambiguous, one rerun per market",
          rc == 2 and d["code"] == "fact_market_ambiguous"
          and d["params"] == {"markets": ["CA", "US"]}
          and d["next"] == [
              "shop facts set unit_cost 1 --source s --reason r --json "
              "--market CA",
              "shop facts set unit_cost 1 --source s --reason r --json "
              "--market US"], d)
    rc, out, err = run(["set", "unit_cost", "1", "--market", "ca",
                        "--source", "s", "--reason", "r", "--json"])
    check("an explicit CA (any case) is still behind its door",
          rc == 2 and coded(out) == ("market_not_onboarded",
                                     {"market": "CA"}), out)
    rc, out, err = run(["list", "--json"])
    d = one_doc(out)
    us = {f["key"]: f for f in d["facts"] if f["market"] == "US"}
    check("list --json: declared, waiting, facts with labels, settable keys",
          rc == 0 and d["declared_markets"] == ["US"]
          and d["pending_markets"] == ["CA"]
          and us["unit_cost"]["label_zh"] == "单位成本"
          and us["unit_cost"]["group"] == "cost"
          and us["threshold_max_step"]["label_en"] == "Max step"
          and d["settable_keys"] == list(KEYS.settable(TH)), d)
    rc, out, err = run(["list", "--pending", "--market", "US", "--json"])
    d = one_doc(out)
    check("list --pending --market US: only pending US rows",
          rc == 0 and d["facts"] and all(
              f["is_assumption"] == 1 and f["market"] == "US"
              for f in d["facts"]), d)
    rc, out, err = run(["list"])
    check("list (text): * marks pending, the waiting declaration named",
          rc == 0 and "declared market(s): US" in out
          and "waiting for a human to confirm the declaration: CA" in out
          and " * US" in out, out)
    rc, out, err = run(["list", "--thresholds", "--market", "US", "--json"])
    d = one_doc(out)
    check("list --thresholds: default, the pending client value, in effect "
          "= default",
          rc == 0 and len(d["thresholds"]) == 1
          and d["thresholds"][0]["value"] == 0.3
          and d["thresholds"][0]["is_assumption"] == 1
          and d["thresholds"][0]["effective"] == 0.2
          and d["thresholds"][0]["in_effect"] == "default", d)
    rc, out, err = run(["get", "unit_cost", "--market", "US", "--json"])
    d = one_doc(out)
    check("get --json: the row", rc == 0 and d["value"] == "4.2"
          and d["key"] == "unit_cost", d)
    rc, out, err = run(["get", "market_declared", "--market", "CA",
                        "--json"])
    check("get reads a market whose declaration is pending",
          rc == 0 and one_doc(out)["is_assumption"] == 1, out)
    rc, out, err = run(["get", "unit_cost", "--json"])
    check("get with the market omitted and two waiting: ambiguous",
          rc == 2 and coded(out)[0] == "fact_market_ambiguous", out)
    rc, out, err = run(["history", "unit_cost", "--market", "US", "--json"])
    d = one_doc(out)
    check("history KEY --json: every row of the key, in order",
          rc == 0 and [r["action"] for r in d["history"]][:4]
          == ["init", "set", "confirm", "rollback"]
          and d["market"] == "US", d)
    rc, out, err = run(["history", "--market", "US"])
    check("history (every key, text)", rc == 0 and "market_declared" in out
          and "fee_pct" in out, out)
    rc, out, err = run(["list", "--market", "GB", "--json"])
    check("list --market GB: market_not_a_market",
          rc == 2 and coded(out)[0] == "market_not_a_market", out)


# ---- 11 --------------------------------------------------------------------

def test_restore() -> None:
    print("[11] restore: TTY only, additive, twice = nothing")
    backup = db.backup_human_tables(SPEC, DATA / CFG.db_file)
    check("a kit human backup to restore from", backup is not None
          and backup.exists())
    dump = DATA / "dump.sql"
    with closing(con()) as c:
        dump.write_text("\n".join(c.iterdump()), encoding="utf-8")
        dumped = c.execute("SELECT market, key, value, is_assumption FROM "
                           "client_facts ORDER BY market, key").fetchall()
    for key, value in (("unit_cost", "7"), ("monthly_cap", "9")):
        run(["set", key, value, "--market", "US", "--source", "s",
             "--reason", "oops"])
    check("two facts moved since the backup",
          fact("unit_cost")[0] == "7" and fact("monthly_cap")[:2] == ("9", 1))
    before = rows()
    rc, out, err = run(["restore", "--from", str(backup), "--reason",
                        "undo", "--json"], secret=True)
    check("off a TTY, even with the secret: confirm_needs_human, nothing "
          "written (a code never unlocks restore)",
          rc == 2 and coded(out)[0] == "confirm_needs_human"
          and "subject" not in one_doc(out) and rows() == before, (rc, out))
    rc, out, err = run(["restore", "--from", str(backup), "--reason", "undo",
                        "--code", "123456", "--json"], secret=True)
    check("restore takes no --code at all", rc == 2
          and coded(out)[0] == "fact_usage", out)
    rc, out, err = run(["restore", "--from", str(backup), "--reason",
                        "undo", "--json"], tty=["1"])
    check("the wrong count typed: confirm_typed_mismatch, nothing written",
          rc == 2 and coded(out)[0] == "confirm_typed_mismatch"
          and "~ US unit_cost: '7' → '4.2'" in err
          and "~ US monthly_cap: '9' → '3000' (confirmed)" in err
          and rows() == before, (rc, out, err))
    expected = coded(out)[1]["expected"]
    last = top()
    rc, out, err = run(["restore", "--from", str(backup), "--reason",
                        "undo", "--json"], tty=[expected])
    d = one_doc(out)
    check("the count typed at the terminal: restored",
          rc == 0 and d["restored"]["client_facts"]["rows"] == 2
          and fact("unit_cost")[:2] == ("4.2", 1)
          and fact("monthly_cap")[:2] == ("3000", 0), (rc, out, err))
    h = hist(last)
    check("history: set rows the DB lacked? none; one restore row per fact, "
          "by @tty, the reason",
          {(r[1], r[2], r[3], r[4], r[6], r[7]) for r in h} == {
              ("unit_cost", "restore", "7", "4.2", "undo", f"{USER}@tty"),
              ("monthly_cap", "restore", "9", "3000", "undo",
               f"{USER}@tty")}, h)
    before = rows()
    rc, out, err = run(["restore", "--from", str(backup), "--reason",
                        "again", "--json"], tty=["0"])
    check("a second restore finds nothing to do (no prompt)",
          rc == 0 and one_doc(out)["message_code"]["code"]
          == "fact_restore_nothing" and rows() == before, (rc, out))

    print("    a SQL dump, into a fresh DB: the lost history comes back")
    d2 = fresh("facts-restore-")
    run(["init"], data=d2, stdin="US\n\n\n\n\n")
    n_before = len(rows(d2)["client_facts_history"])
    rc, out, err = run(["restore", "--from", str(dump), "--reason",
                        "rebuild", "--json"], data=d2, tty=["x"])
    total = coded(out)[1]["expected"]
    rc, out, err = run(["restore", "--from", str(dump), "--reason",
                        "rebuild", "--json"], data=d2, tty=[total])
    r2 = rows(d2)
    check("restored from the dump: every fact of the dump, history appended",
          rc == 0 and sorted((r[0], r[1], r[2], r[3]) for r in r2["client_facts"])
          == dumped and fact("market_declared", data=d2)[:2] == ("US", 0)
          and len(r2["client_facts_history"]) > n_before, (rc, out, err))
    rc, out, err = run(["restore", "--from", str(dump), "--reason",
                        "rebuild", "--json"], data=d2, tty=["0"])
    check("...twice = nothing", rc == 0 and one_doc(out)["message_code"][
        "code"] == "fact_restore_nothing" and rows(d2) == r2, out)

    print("    unusable backups")
    bad = DATA / "bad.sql"
    bad.write_text("CREATE TABLE x (a);", encoding="utf-8")
    victim = DATA / "planted.db"
    hostile = DATA / "hostile.sql"
    hostile.write_text(f"ATTACH DATABASE '{victim}' AS p; "
                       f"CREATE TABLE p.t (a);", encoding="utf-8")
    binary = DATA / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00garbage")
    for label, path, code in (
            ("a missing file", DATA / "nope.db", "fact_backup_missing"),
            ("a dump without the human tables", bad, "fact_backup_unusable"),
            ("a dump that ATTACHes a file", hostile, "fact_backup_unusable"),
            ("a binary that is not SQLite", binary, "fact_backup_unusable")):
        rc, out, err = run(["restore", "--from", str(path), "--reason", "r",
                            "--json"], tty=["1"])
        check(f"{label}: {code}", rc == 2 and coded(out)[0] == code,
              (rc, out))
    check("...the hostile dump created no file", not victim.exists())
    rc, out, err = run(["restore", "--from", str(backup), "--json"],
                       tty=["1"])
    check("restore needs --reason", rc == 2
          and coded(out) == ("reason_required", {}))


# ---- 12 --------------------------------------------------------------------

def test_protection_and_readers() -> None:
    print("[12] a moved fact is not overwritten; triggers; readers")
    run(["unconfirm", "unit_cost", "--market", "US", "--reason", "r"])
    real = human.confirm

    def meddle(*a, **k):
        channel = real(*a, **k)
        with closing(con()) as c:     # someone writes while the human types
            c.execute("UPDATE client_facts SET value='8', changed_by='x' "
                      "WHERE market='US' AND key='unit_cost'")
            c.commit()
        return channel
    before = fact("unit_cost")
    with mock.patch.object(human, "confirm", meddle):
        rc, out, err = run(["confirm", "unit_cost", "--market", "US",
                            "--reason", "r", "--json"], tty=[before[0]])
    d = one_doc(out)
    check("the fact changed while the human was asked: "
          "fact_changed_meanwhile, next = get, the other write kept",
          rc == 2 and d["code"] == "fact_changed_meanwhile"
          and d["next"] == ["shop facts get unit_cost --market US"]
          and fact("unit_cost")[:2] == ("8", 1), (rc, out, err))

    with closing(con()) as c:
        e = raises(lambda: c.execute("DELETE FROM client_facts"),
                   sqlite3.DatabaseError)
        e2 = raises(lambda: c.execute(
            "UPDATE client_facts_history SET reason='x'"),
            sqlite3.DatabaseError)
        e3 = raises(lambda: c.execute("DELETE FROM client_facts_history"),
                    sqlite3.DatabaseError)
    check("triggers: no DELETE of facts, history never updated or deleted",
          all(isinstance(x, sqlite3.DatabaseError) for x in (e, e2, e3)))

    with closing(con()) as c:
        check("effective: (value, confirmed?)",
              facts.effective(c, "US", "unit_cost") == ("8", False)
              and facts.effective(c, "US", "monthly_cap") == ("3000", True)
              and facts.effective(c, "US", "launch_date") is None)
        check("confirmed: only a confirmed value",
              facts.confirmed(c, "US", "unit_cost") is None
              and facts.confirmed(c, "US", "monthly_cap") == "3000")
        p = facts.pending(c, "US")
        check("pending(market): the pending rows, ordered by key",
              [r["key"] for r in p] == sorted(r["key"] for r in p)
              and "unit_cost" in [r["key"] for r in p]
              and all(r["is_assumption"] == 1 and r["market"] == "US"
                      for r in p), p)
        check("pending(None): every market (CA's declaration included)",
              ("CA", "market_declared") in {
                  (r["market"], r["key"]) for r in facts.pending(c, None)})
    empty = sqlite3.connect(":memory:")
    check("readers on a DB without client_facts (or none): None / []",
          facts.effective(empty, "US", "x") is None
          and facts.confirmed(None, "US", "x") is None
          and facts.pending(empty, "US") == [])
    empty.close()


# ---- 13 --------------------------------------------------------------------

def test_unpartitioned() -> None:
    print("[13] a harness with markets = [] keeps every fact under '_'")
    root = Path(tmp_dir("facts-flat-"))
    shutil.copytree(_shop.SHOP, root, dirs_exist_ok=True)
    toml = root / "harness.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'markets = ["US", "CA"]', "markets = []"), encoding="utf-8")
    try:
        config.use(root)
        d = fresh("facts-flat-data-")
        rc, out, err = run(["init", "--json"], data=d, stdin="7\n\n\n\n")
        doc = one_doc(out)
        check("init asks no market, declares nothing, answers under '_'",
              rc == 0 and doc["market"] == "_"
              and doc["declaration"] == "none"
              and "market (" not in err
              and fact("unit_cost", "_", d)[:2] == ("7", 1)
              and fact("market_declared", "_", d) is None, (rc, out, err))
        rc, out, err = run(["set", "fee_pct", "3", "--source", "s",
                            "--reason", "r", "--json"], data=d)
        check("set needs no declaration", rc == 0
              and fact("fee_pct", "_", d)[:2] == ("3", 1), out)
        rc, out, err = run(["confirm", "fee_pct", "--reason", "r", "--json"],
                           data=d, tty=["3"])
        check("confirm under '_'", rc == 0
              and fact("fee_pct", "_", d)[:2] == ("3", 0), out)
        rc, out, err = run(["get", "fee_pct", "--market", "US", "--json"],
                           data=d)
        check("a market argument is refused: market_unpartitioned",
              rc == 2 and coded(out)[0] == "market_unpartitioned", out)
    finally:
        _shop.use()


# ---- 14 --------------------------------------------------------------------

def test_closure() -> None:
    print("[14] facts.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "facts.tsv"}
    check("the fragment holds the fact_* verb codes",
          own and all(c.startswith("fact_") for c in own), sorted(own))
    problems = messages.check_registry_closed(
        _shop.KIT, ["facts.py"], registry=own, strict_kit=True)
    check("every code emitted by facts.py is registered with exact params, "
          "and every code of facts.tsv is emitted", problems == [], problems)
    check("facts.tsv shares no code with another fragment",
          not any(Path(r["file"]).name != "facts.tsv" and c in own
                  for c, r in reg.items()))


if __name__ == "__main__":
    test_reads_create_nothing()
    test_init_piped()
    test_declaration_gate()
    test_set()
    test_confirm()
    test_rollback()
    test_numeric_contract()
    test_gate_refusal_codes()
    test_markets_and_reads()
    test_restore()
    test_protection_and_readers()
    test_unpartitioned()
    test_closure()
    raise SystemExit(finish())
