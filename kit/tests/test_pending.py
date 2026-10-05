#!/usr/bin/env python3
"""`<cli> pending` (kit/pending.py) turns every row waiting on a human into
one valid console ask, on the fake "shop" harness.

  1. A read verb: no DB = no_db, nothing created; nothing waiting = no asks.
  2. Every pending fact (number / choice / date / threshold), decision
     (number / choice / text / sha256) and queue item becomes exactly one
     ask of the right step, with evidence from the harness's own rows
     (source "harness …"), recommend.because left null and flagged; the
     console refuses them as they are and, once the agent writes
     `because`, `console/ask.py add --dry-run` accepts every one.
  3. The gate block is what the kit's gate binds: every gated ask,
     answered through console/relay.py (in a child process, against a
     harness command that runs kit.facts / kit.decisions / kit.queue),
     passes relay's subject check and writes exactly that row, @relay
     (a provided value other than the recommendation too).
  4. No gate where the relayed call could not reach the row, each with a
     coded warning: several markets declared (the console names no
     market), a verb word the console cannot send, a binding longer than
     the console holds.
  5. Ask ids are stable while nothing moves and new when the value does;
     pending.tsv is closed over pending.py.
"""

import copy
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import db, decisions, facts, messages, pending  # noqa: E402
from kit import queue as q  # noqa: E402
from kit.registry import DecisionRegistry, FactKeys, Thresholds  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, tmp_dir)
from kit.testing.sandbox import sandbox_env  # noqa: E402

CFG = _shop.use()
DATA = _shop.data_dir()
DB = DATA / CFG.db_file
SECRET = CFG.env("CONFIRM_CODE_SECRET")
CONSOLE = _shop.PLAYBOOK / "console"
SPEC = db.with_human({}, version=1)
SHA = "ab" * 32

FIX = Path(tmp_dir("pending-fixtures-"))
(FIX / "fact_keys.tsv").write_text(
    "key\tgroup\ttype\tunit\tmin\tmax\tlabel_en\tlabel_zh\twhy\n"
    "unit_cost\tcost\tnumber\tUSD\t0\t50\tUnit cost\t单位成本\tmargin\n"
    "channel\tprofile\tchoice:web|store\t\t\t\tChannel\t渠道\trouting\n"
    "launch_date\tprofile\tdate\t\t\t\tLaunch date\t上线日期\tage\n",
    encoding="utf-8")
(FIX / "constants.tsv").write_text(
    "name\tdefault\tunit\tmin\tmax\tgroup\tlabel_en\tlabel_zh\texplain\twhy\n"
    "max_step\t0.2\tratio\t0\t1\tpacing\tMax step\t最大步长\tsize\tsafety\n",
    encoding="utf-8")
KEYS = FactKeys(FIX / "fact_keys.tsv")
TH = Thresholds(FIX / "constants.tsv")
IDS = {"product": r"SKU-[A-Z0-9]+", "campaign": r"[0-9]+"}
REG = DecisionRegistry(entity_ids=IDS)

# the harness command console/relay.py runs: `<cmd> facts|decisions|queue …`
HARNESS = r'''
import os, sys
sys.path.insert(0, os.environ["PLAYBOOK"])
from pathlib import Path
from kit import db, decisions, facts, queue
from kit.registry import DecisionRegistry, FactKeys, Thresholds
fix = Path(os.environ["FIX"])
spec = db.with_human({}, version=1)
group, argv = sys.argv[1], sys.argv[2:]
if group == "facts":
    rc = facts.main(argv, spec=spec, keys=FactKeys(fix / "fact_keys.tsv"),
                    thresholds=Thresholds(fix / "constants.tsv"))
elif group == "decisions":
    rc = decisions.main(argv, spec=spec, registry=DecisionRegistry(
        entity_ids={"product": r"SKU-[A-Z0-9]+", "campaign": r"[0-9]+"}))
else:
    rc = queue.main(argv, spec=spec, snapshot=lambda m: {},
                    validate=lambda c, p: None, ttl_hours=lambda c, m: 24)
raise SystemExit(rc)
'''

RELAY = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import core, relay
cfg = json.loads(sys.argv[2])
ask, errs = core.validate_ask(cfg["ask"])
assert not errs, errs
print(json.dumps(relay.run(ask, cfg["value"], cmd=cfg["cmd"], verbs=cfg["verbs"],
                           user="alice", reason="owner answered",
                           timeout=60.0, env=cfg["env"])))
'''


def PEND(argv, registry=REG):
    return pending.main(argv, spec=SPEC, keys=KEYS, registry=registry,
                        thresholds=TH)


def env() -> dict:
    return clean_env(**{CFG.env("DATA_DIR"): str(DATA),
                        CFG.env("AUTH_ENV_PATHS"): "none"})


def pend(registry=REG) -> dict:
    rc, out, err = capture(lambda a: PEND(a, registry), ["--json"],
                           env=env())
    assert rc == 0, (rc, out, err)
    return one_doc(out)


def fset(*argv):
    rc, out, err = capture(lambda a: facts.main(a, spec=SPEC, keys=KEYS,
                                                thresholds=TH),
                           ["set", *argv, "--source", "agent_2026-09-28",
                            "--reason", "from the invoice", "--json"],
                           env=env())
    assert rc == 0, (argv, out, err)


def dset(*argv, registry=REG):
    rc, out, err = capture(lambda a: decisions.main(a, spec=SPEC,
                                                    registry=registry),
                           ["set", *argv, "--source", "agent_2026-09-28",
                            "--reason", "proposed", "--json"], env=env())
    assert rc == 0, (argv, out, err)


def queue_add(*props):
    with closing(db.connect(SPEC)) as c:
        return q.add(SPEC, c, {"meta": {}, "proposals": list(props)},
                     lambda con, p: None)


def prop(target, payload, **kw):
    return {"kind": "restock", "market": "US", "target_ref": target,
            "payload": payload, "basis": "b1", "evidence": {"stock": 2},
            "expected": {"effect": f"{target} gets {payload.get('units')} "
                                   f"more units"}, **kw}


def filled(asks: list[dict]) -> list[dict]:
    return [{**a, "recommend": {**a["recommend"],
                                "because": "the harness's own numbers"}}
            for a in asks]


def console_add(asks: list[dict]) -> dict:
    d = Path(tmp_dir("console-"))
    f = d / "asks.json"
    f.write_text(json.dumps(asks, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(CONSOLE / "ask.py"), "--dir",
                        str(d / "log"), "add", str(f), "--dry-run"],
                       capture_output=True, text=True, timeout=60,
                       env={**sandbox_env(DATA),
                            "PYTHONDONTWRITEBYTECODE": "1"})
    return one_doc(r.stdout) or {"stdout": r.stdout, "stderr": r.stderr}


def relay(ask: dict, value: str) -> dict:
    d = Path(tmp_dir("harness-"))
    script = d / "shop_harness.py"
    script.write_text(HARNESS, encoding="utf-8")
    child_env = sandbox_env(DATA, PLAYBOOK=str(_shop.PLAYBOOK), FIX=str(FIX),
                            PYTHONDONTWRITEBYTECODE="1",
                            **{SECRET: "s3cret"})
    cfg = json.dumps({"ask": ask, "value": value,
                      "cmd": [sys.executable, str(script)],
                      "verbs": [["facts", "confirm"],
                                ["decisions", "confirm"],
                                ["queue", "approve"]], "env": child_env})
    r = subprocess.run([sys.executable, "-c", RELAY, str(CONSOLE), cfg],
                       env=child_env, capture_output=True, text=True,
                       timeout=180)
    return one_doc(r.stdout) or {"stdout": r.stdout, "stderr": r.stderr}


def fact_row(key, market="US"):
    with closing(sqlite3.connect(DB)) as c:
        return c.execute("SELECT value, is_assumption, changed_by FROM "
                         "client_facts WHERE market=? AND key=?",
                         (market, key)).fetchone()


def queue_status(i: int) -> str:
    with closing(sqlite3.connect(DB)) as c:
        return q.fetch(c, i)["status"]


def by_ref(doc: dict) -> dict:
    """ask by a readable handle: fact:<key>, decision:<eid>:<key>, queue:<id>."""
    out = {}
    for a, b in zip(doc["asks"], doc["about"]):
        r = b["ref"]
        h = (f"fact:{r['key']}" if b["table"] == "client_facts" else
             f"decision:{r['entity_id']}:{r['key']}"
             if b["table"] == "decisions" else f"queue:{r['id']}")
        out[h] = (a, b)
    return out


def wcodes(doc: dict) -> list[str]:
    return [w["code"] for w in doc["warning_codes"]]


# ---- 1 ---------------------------------------------------------------------

def test_read_only() -> None:
    print("[1] a read verb")
    rc, out, _ = capture(PEND, ["--json"], env=env())
    check("no DB: no_db, exit 2, nothing created",
          rc == 2 and one_doc(out)["code"] == "no_db" and not DB.exists(),
          out)
    db.connect(SPEC).close()
    with closing(sqlite3.connect(DB)) as c:
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "('US', 'market_declared', 'US', 0, 't', "
                  "'2026-09-22', 't@tty')")
        c.commit()
    d = pend()
    check("nothing waiting: no asks, the coded summary, no warnings",
          d["asks"] == [] and d["about"] == [] and d["warnings"] == []
          and d["message_code"]["code"] == "pending_summary", d)
    rc, out, _ = capture(PEND, ["--market", "XX", "--json"], env=env())
    check("--market outside the closed set: market_not_a_market",
          rc == 2 and one_doc(out)["code"] == "market_not_a_market", out)


# ---- 2 + 3 -------------------------------------------------------------------

def test_asks_and_relay() -> None:
    print("[2] one valid console ask per pending row")
    fset("unit_cost", "4.20")
    fset("channel", "web")
    fset("launch_date", "2026-01-15")
    fset("threshold_max_step", "0.3")
    dset("product", "SKU-1", "floor_price", "2.50")
    dset("product", "SKU-1", "stage", "live")
    dset("product", "SKU-2", "note", "hand wash only")
    dset("product", "SKU-3", "artwork", SHA)
    rep = queue_add(prop("SKU-1", {"units": 40}))
    qid = rep["queued"][0]
    mtime = DB.stat().st_mtime_ns
    doc = pend()
    asks, about = doc["asks"], doc["about"]
    refs = by_ref(doc)
    check("nine asks: 4 facts, 4 decisions, 1 queue item, in that order",
          len(asks) == 9 and [b["table"] for b in about] ==
          ["client_facts"] * 4 + ["decisions"] * 4 + ["action_queue"]
          and doc["message_code"]["params"] == {"facts": 4, "decisions": 4,
                                                "queue": 1}, about)
    check("pending opened the DB read-only (the file is unchanged)",
          DB.stat().st_mtime_ns == mtime)
    steps = {h: a["step"] for h, (a, _) in refs.items()}
    check("steps by value kind: number/date/threshold provide, choice "
          "choose, text provide, sha256 confirm, queue approve",
          steps == {"fact:unit_cost": "provide", "fact:channel": "choose",
                    "fact:launch_date": "provide",
                    "fact:threshold_max_step": "provide",
                    "decision:SKU-1:floor_price": "provide",
                    "decision:SKU-1:stage": "choose",
                    "decision:SKU-2:note": "provide",
                    "decision:SKU-3:artwork": "confirm",
                    f"queue:{qid}": "approve"}, steps)
    uc = refs["fact:unit_cost"][0]
    check("a number fact: input number with the registry's bounds and "
          "unit; recommend = the pending value",
          uc["input"] == {"type": "number", "min": 0, "max": 50,
                          "unit": "USD"}
          and uc["recommend"] == {"value": "4.2", "because": None}, uc)
    check("a threshold fact: the thresholds registry's bounds",
          refs["fact:threshold_max_step"][0]["input"] ==
          {"type": "number", "min": 0, "max": 1, "unit": "ratio"},
          refs["fact:threshold_max_step"][0])
    check("a choice: its options", [o["value"] for o in
                                    refs["fact:channel"][0]["options"]] ==
          ["web", "store"])
    qa = refs[f"queue:{qid}"][0]
    check("the queue ask: approve, effect from expected, the payload and "
          "evidence as tables", qa["effect"].endswith("SKU-1 gets 40 more "
                                                     "units")
          and qa["evidence"][0]["table"]["rows"] == [["units", "40"]]
          and qa["recommend"] == {"value": "yes", "because": None}, qa)
    sources = [e.get("source") or e["table"]["caption"]
               for a in asks for e in a["evidence"]]
    check("every evidence item is sourced from the harness",
          sources and all("harness" in s for s in sources), sources)
    check("every ask is flagged: recommend.because null, about.fill, the "
          "coded pending_fill_because warning; the texts coded",
          all(a["recommend"]["because"] is None for a in asks)
          and all(b["fill"] == ["recommend.because"] for b in about)
          and wcodes(doc) == ["pending_fill_because"]
          and all(b["title_code"]["code"].startswith("pending_title_")
                  and b["why_code"] and b["if_no_code"] for b in about)
          and about[-1]["effect_code"]["code"] == "pending_effect_queue",
          doc["warning_codes"])
    check("every ask is gated here (one market declared)",
          all(b["gate"] for b in about) and all("gate" in a for a in asks))
    got = console_add(asks[:1])
    check("as emitted, the console refuses an ask (because is required)",
          got.get("ok") is False and got.get("code") == "invalid_ask"
          and any(e["field"] == "recommend.because"
                  for e in got["params"]["errors"]), got)
    got = console_add(filled(asks))
    check("with because written: console/ask.py add --dry-run accepts all "
          "nine", got.get("ok") is True and len(got.get("posted", [])) == 9,
          got)

    print("[3] each gate passes console/relay.py's subject check")
    bad = copy.deepcopy(filled([refs[f"queue:{qid}"][0]])[0])
    items = bad["gate"]["expect"]["items"]
    bad["gate"]["expect"]["items"] = {
        k: [*v[:3], '{"units":41}', v[4]] for k, v in items.items()}
    got = relay(bad, "yes")
    check("control: an expect that is not what the gate binds is "
          "gate_changed, nothing written",
          got.get("code") == "gate_changed" and queue_status(qid) ==
          "pending", got)
    answers = {"fact:unit_cost": "4.5", "fact:channel": "web",
               "fact:launch_date": "2026-01-15",
               "fact:threshold_max_step": "0.3",
               "decision:SKU-1:floor_price": "2.50",
               "decision:SKU-1:stage": "live",
               "decision:SKU-2:note": "hand wash only",
               "decision:SKU-3:artwork": "yes", f"queue:{qid}": "yes"}
    for h, value in answers.items():
        ask = filled([refs[h][0]])[0]
        got = relay(ask, value)
        check(f"{h} answered {value!r}: ok through the kit's gate",
              got.get("ok") is True and got.get("code") is None, got)
    check("the facts confirmed @relay, the provided value (4.5, not the "
          "recommended 4.2) is the one written",
          fact_row("unit_cost")[:2] == ("4.5", 0)
          and fact_row("unit_cost")[2].endswith("@relay")
          and fact_row("channel")[:2] == ("web", 0)
          and fact_row("threshold_max_step")[:2] == ("0.3", 0),
          [fact_row(k) for k in ("unit_cost", "channel")])
    with closing(sqlite3.connect(DB)) as c:
        st = c.execute("SELECT entity_id, key, status, confirmed_value FROM "
                       "decisions ORDER BY entity_id, key").fetchall()
        qr = q.fetch(c, qid)
    check("the decisions confirmed", all(s[2] == "confirmed" for s in st)
          and ("SKU-1", "floor_price", "confirmed", "2.5") in st, st)
    check("the queue item approved @relay", qr["status"] == "approved"
          and qr["decided_by"].endswith("@relay"), qr)
    check("nothing is waiting any more", pend()["asks"] == [])


# ---- 4 ---------------------------------------------------------------------

def test_no_gate() -> None:
    print("[4] no gate where the relayed call cannot reach the row")
    capture(lambda a: facts.main(a, spec=SPEC, keys=KEYS, thresholds=TH),
            ["unconfirm", "unit_cost", "--reason", "new invoice"], env=env())
    fset("unit_cost", "5")          # a confirmed value is never set over
    dset("product", "SKU-1", "floor_price", "3")
    with closing(sqlite3.connect(DB)) as c:        # a second market waits
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "('CA', 'market_declared', 'CA', 1, 'agent', "
                  "'2026-09-28', 'a@cli')")
        c.commit()
    doc = pend()
    refs = by_ref(doc)
    check("two markets declared or waiting: the CA declaration (listed "
          "first) and the US fact carry no gate, each with "
          "pending_gate_market_ambiguous",
          doc["about"][0]["ref"] == {"key": "market_declared"}
          and doc["about"][0]["market"] == "CA"
          and not doc["about"][0]["gate"]
          and "gate" not in refs["fact:unit_cost"][0]
          and wcodes(doc).count("pending_gate_market_ambiguous") == 2,
          doc["warning_codes"])
    check("the decision (only US confirmed) keeps its gate",
          "gate" in refs["decision:SKU-1:floor_price"][0])
    got = console_add(filled(doc["asks"]))
    check("the gateless asks are still valid console asks",
          got.get("ok") is True, got)

    loose = DecisionRegistry()
    dset("product", "SKU 9", "note", "fragile", registry=loose)
    doc = pend(loose)
    a, b = by_ref(doc)["decision:SKU 9:note"]
    check("an entity id the console cannot send as a verb word: no gate, "
          "pending_gate_bad_word", "gate" not in a and not b["gate"]
          and "pending_gate_bad_word" in wcodes(doc)
          and a["id"].startswith("decision-us-product-sku-9-note-"), a)
    rep = queue_add(prop("SKU-8", {"units": 1, "note": "x" * 2100}))
    doc = pend()
    a, b = by_ref(doc)[f"queue:{rep['queued'][0]}"]
    check("a binding longer than the console's expect limit: no gate, "
          "pending_gate_too_long", "gate" not in a
          and "pending_gate_too_long" in wcodes(doc), doc["warning_codes"])
    got = console_add(filled([a]))
    check("that ask is still valid (evidence cells clipped)",
          got.get("ok") is True, got)


# ---- 5 ---------------------------------------------------------------------

def test_ids_and_closure() -> None:
    print("[5] stable ids; closure")
    first = [a["id"] for a in pend()["asks"]]
    check("the same rows: the same ids", [a["id"] for a in pend()["asks"]]
          == first)
    fset("unit_cost", "6", "--market", "US")
    again = by_ref(pend())["fact:unit_cost"][0]["id"]
    check("a new pending value: a new id (an answered id is never reused)",
          again not in first and again.startswith("fact-us-unit_cost-"),
          again)
    rc, out, _ = capture(PEND, [], env=env())
    check("text mode: one line per ask and the summary",
          rc == 0 and "fact-us-unit_cost-" in out and "ask(s) waiting" in out,
          out)
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "pending.tsv"}
    probs = messages.check_registry_closed(_shop.KIT, ["pending.py"], own,
                                           strict_kit=True)
    check("every msg() in pending.py is literal and registered; every "
          "pending.tsv code is emitted", probs == [], probs)


test_read_only()
test_asks_and_relay()
test_no_gate()
test_ids_and_closure()
raise SystemExit(finish())
