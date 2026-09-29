#!/usr/bin/env python3
"""The closed registries (kit/registry.py) hold each of their guards.

  1. read_tsv is strict: missing file, empty file, a missing or repeated
     header column, a row with a stray tab, a carriage return inside a
     cell, an empty or duplicate id (one column or a tuple) are coded
     refusals; CRLF endings and blank lines are fine.
  2. FactKeys: settable/keys/groups/bounds; validate() canonicalises
     numbers and refuses a non-number, a value outside min..max, a value
     not in the choices, a non-ISO date, an empty text, an unknown key
     (listing the keys); threshold_<name> routes to Thresholds; a
     malformed or reserved row fails the load.
  3. DecisionRegistry: every domain of the grammar (int:a..b incl. an open
     side, number:a..b, a|b|c, date, json, text, sha256) accepts its
     canonical form and refuses with its code; a harness-registered domain
     works and its refusals are coded; confirm_exempt / harness_written;
     entity ids; unknown entity type or key; malformed rows fail the load.
  4. Thresholds: the default, overridden per market by a CONFIRMED
     threshold_<name> fact only (pending or non-numeric ones are not in
     effect); --assume goes over both for this run, is never stored, and
     is reported (assumed(), meta.assumed_thresholds, the ASSUMED
     warning); a bad --assume is refused with `next`; listing, bounds,
     superseded names.
  5. The registries resolve their files from harness.toml, with one
     label column per declared language.
  6. registry.py's msg() calls are closed over its fragment.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, contract, messages, registry  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.messages import msg  # noqa: E402
from kit.registry import (DecisionRegistry, FactKeys, Thresholds,  # noqa: E402
                          read_tsv)
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

DIR = Path(tmp_dir("registry-"))
FACT_HEAD = "key group type unit min max label_en label_zh why"
DEC_HEAD = "entity_type key domain confirm label_en label_zh story why"
CONST_HEAD = "name default unit min max group label_en label_zh explain why"
FACTS_SQL = ("CREATE TABLE client_facts (market TEXT NOT NULL, key TEXT NOT "
             "NULL, value TEXT, is_assumption INTEGER NOT NULL DEFAULT 1, "
             "source TEXT, updated_at TEXT, changed_by TEXT, "
             "PRIMARY KEY (market, key))")
_n = 0


def tsv(head: str, *rows: str, raw: str | None = None) -> Path:
    """A TSV file: space-separated `head`, rows given with `|t|` for tab
    ("" cells allowed), or `raw` text as is."""
    global _n
    _n += 1
    p = DIR / f"t{_n}.tsv"
    if raw is None:
        raw = "\t".join(head.split()) + "\n" + "".join(
            r.replace("|t|", "\t") + "\n" for r in rows)
    p.write_text(raw, encoding="utf-8")
    return p


def coded(fn) -> dict | None:
    e = raises(fn, HarnessError)
    return None if e is None else {**messages.code(e.message), "next": e.next}


def load_fails(label: str, fn) -> None:
    r = coded(fn)
    check(f"load refused: {label}",
          r is not None and r["code"] == "registry_file_invalid", r)


# ---- 1 ---------------------------------------------------------------------

def test_read_tsv() -> None:
    print("[1] read_tsv is strict")
    r = coded(lambda: read_tsv(DIR / "nope.tsv", ["a"]))
    check("a missing file: registry_file_missing",
          r and r["code"] == "registry_file_missing"
          and r["params"] == {"path": str(DIR / "nope.tsv")}, r)
    ok = tsv("", raw="a\tb\r\n\r\n x |t| y \n".replace("|t|", "\t")
             + "\n2\t3\r\n")
    check("CRLF endings and blank lines are fine; cells are stripped",
          read_tsv(ok, ["a", "b"], id_col="a") == [
              {"a": "x", "b": "y"}, {"a": "2", "b": "3"}],
          read_tsv(ok, ["a", "b"]))
    for label, path, kw in (
            ("an empty file", tsv("", raw="\n\n"), {}),
            ("a required column missing", tsv("a b", "1|t|2"), {}),
            ("a repeated header column", tsv("a a b", "1|t|2|t|3"), {}),
            ("a stray tab in a row", tsv("a b c", "1|t|2|t|3|t|4"), {}),
            ("a carriage return inside a cell", tsv("", raw="a\tb\tc\n1\t2\r2\t3\n"), {}),
            ("a duplicate id", tsv("a b c", "1|t|2|t|3", "1|t|5|t|6"),
             {"id_col": "a"}),
            ("a duplicate compound id", tsv("a b c", "1|t|2|t|3", "1|t|2|t|6"),
             {"id_col": ("a", "b")}),
            ("an empty id", tsv("a b c", "|t|2|t|3"), {"id_col": "a"}),
            ("an id column not in the header", tsv("a b c", "1|t|2|t|3"),
             {"id_col": "z"})):
        load_fails(label, lambda: read_tsv(path, ["a", "b", "c"], **kw))
    check("a compound id that differs in one part is fine",
          len(read_tsv(tsv("a b c", "1|t|2|t|3", "1|t|3|t|3"), ["a"],
                       id_col=("a", "b"))) == 2)


# ---- 2 ---------------------------------------------------------------------

FACTS = tsv(FACT_HEAD,
            "monthly_spend_cap|t|required|t|number|t|currency|t|0|t||t|monthly "
            "spend cap|t|每月花费上限|t|the budget reads it",
            "fee_share|t|profit|t|number|t|ratio|t|0|t|1|t|fee share|t|费率|t|",
            "brand_voice|t|profile|t|text|t||t||t||t|brand voice|t|品牌语气|t|",
            "launch_on|t|profile|t|date|t||t||t||t|launch day|t|上线日|t|",
            "tier|t|profile|t|choice:gold|silver|t||t||t||t|tier|t|等级|t|")
CONSTS = tsv(CONST_HEAD,
             "min_orders|t|3|t|orders|t|0|t||t|evidence|t|min orders|t|最少订单"
             "|t|orders before a verdict|t|why",
             "approval_ttl_hours|t|24|t|hours|t|1|t|168|t|write path|t|approval "
             "ttl|t|审批有效时长|t|hours an approval lives|t|P01",
             "max_step|t|0.2|t|ratio|t|0|t|1|t|write path|t|max step|t|最大步长"
             "|t|largest change per step|t|learning phase")


def test_fact_keys() -> None:
    print("[2] FactKeys")
    fk = FactKeys(FACTS)
    th = Thresholds(CONSTS)
    check("settable() in file order; with thresholds their keys follow",
          fk.settable() == ("monthly_spend_cap", "fee_share", "brand_voice",
                            "launch_on", "tier")
          and fk.settable(th)[-3:] == ("threshold_min_orders",
                                       "threshold_approval_ttl_hours",
                                       "threshold_max_step"), fk.settable(th))
    check("keys(group), groups(), row()",
          fk.keys("profile") == ("brand_voice", "launch_on", "tier")
          and fk.groups() == ("required", "profit", "profile")
          and fk.row("tier")["type"] == "choice:gold|silver"
          and fk.row("nope") is None)
    check("bounds(): (min, max) with None = unbounded, every row",
          fk.bounds()["monthly_spend_cap"] == (0, None)
          and fk.bounds()["fee_share"] == (0, 1)
          and fk.bounds()["brand_voice"] == (None, None), fk.bounds())
    for key, raw, want in (("monthly_spend_cap", " 1500.00 ", "1500"),
                           ("monthly_spend_cap", "1e3", "1000"),
                           ("monthly_spend_cap", "１２", "12"),
                           ("fee_share", "0.150", "0.15"),
                           ("fee_share", "1", "1"),
                           ("fee_share", "-0", "0"),
                           ("tier", "gold", "gold"),
                           ("launch_on", " 2026-09-28 ", "2026-09-28"),
                           ("brand_voice", "  warm, plain  ", "warm, plain")):
        got = fk.validate(key, raw)
        check(f"{key} {raw!r} -> {want!r}", got == want, got)
    cases = (
        ("monthly_spend_cap", "abc", "fact_value_not_number", {}),
        ("monthly_spend_cap", "nan", "fact_value_not_number", {}),
        ("monthly_spend_cap", "inf", "fact_value_not_number", {}),
        ("monthly_spend_cap", "1_000", "fact_value_not_number", {}),
        ("monthly_spend_cap", "", "fact_value_not_number", {}),
        ("monthly_spend_cap", "-1", "fact_value_out_of_range",
         {"min": 0, "max": None}),
        ("fee_share", "1.5", "fact_value_out_of_range", {"min": 0, "max": 1}),
        ("fee_share", "15", "fact_value_out_of_range", {"min": 0, "max": 1}),
        ("tier", "bronze", "fact_value_not_choice",
         {"choices": ["gold", "silver"]}),
        ("tier", "Gold", "fact_value_not_choice",
         {"choices": ["gold", "silver"]}),
        ("launch_on", "28/09/2026", "fact_value_not_date", {}),
        ("launch_on", "2026-02-29", "fact_value_not_date", {}))
    for key, raw, code, extra in cases:
        r = coded(lambda: fk.validate(key, raw))
        check(f"{key} {raw!r}: {code}",
              r == {"code": code, "params": {"key": key, "value": raw, **extra},
                    "next": []}, r)
    r = coded(lambda: fk.validate("brand_voice", "   "))
    check("an empty text: fact_value_empty",
          r == {"code": "fact_value_empty", "params": {"key": "brand_voice"},
                "next": []}, r)
    r = coded(lambda: fk.validate("tacos_target", "1"))
    check("an unknown key: fact_key_unknown listing the keys",
          r and r["code"] == "fact_key_unknown"
          and r["params"] == {"key": "tacos_target",
                              "keys": list(fk.settable())}, r)
    check("threshold_<name> routes to Thresholds (its bounds)",
          fk.validate("threshold_max_step", "0.30", thresholds=th) == "0.3")
    r = coded(lambda: fk.validate("threshold_max_step", "3", thresholds=th))
    check("… and its refusals", r and r["code"] == "fact_value_out_of_range"
          and r["params"] == {"key": "threshold_max_step", "value": "3",
                              "min": 0, "max": 1}, r)
    r = coded(lambda: fk.validate("threshold_max_step", "0.3"))
    check("without thresholds a threshold key is unknown",
          r and r["code"] == "fact_key_unknown", r)
    r = coded(lambda: fk.validate("nope", "1", thresholds=th))
    check("fact_key_unknown lists the threshold keys too",
          r and r["params"]["keys"] == list(fk.settable(th)), r)

    row = "x|t|g|t|number|t||t||t||t|x|t|x|t|"
    for label, rows in (
            ("an unknown type", ["x|t|g|t|percent|t||t||t||t|x|t|x|t|"]),
            ("a one-sided choice list", ["x|t|g|t|choice:|t||t||t||t|x|t|x|t|"]),
            ("min on a text key", ["x|t|g|t|text|t||t|0|t||t|x|t|x|t|"]),
            ("min > max", ["x|t|g|t|number|t||t|5|t|1|t|x|t|x|t|"]),
            ("a bound that is not a number", ["x|t|g|t|number|t||t|a|t||t|x|t|x|t|"]),
            ("the reserved market_declared", [row.replace("x|t|g", "market_declared|t|g", 1)]),
            ("a threshold_* key", [row.replace("x|t|g", "threshold_x|t|g", 1)]),
            ("a key not snake_case", [row.replace("x|t|g", "Fee|t|g", 1)]),
            ("an empty group", ["x|t||t|number|t||t||t||t|x|t|x|t|"]),
            ("an empty label_zh", ["x|t|g|t|number|t||t||t||t|x|t||t|"]),
            ("a key listed twice", [row, row])):
        load_fails(f"fact_keys: {label}", lambda: FactKeys(tsv(FACT_HEAD, *rows)))
    load_fails("fact_keys: no label_zh column",
               lambda: FactKeys(tsv("key group type unit min max label_en why",
                                    "x|t|g|t|text|t||t||t||t|x|t|")))


# ---- 3 ---------------------------------------------------------------------

def scenario(v: str) -> str:
    if v == "coded":
        raise ValueError(msg("decision_value_empty", "coded by the harness"))
    if v == "harness":
        raise HarnessError(msg("decision_value_empty", "its own error"))
    if v.lower() not in ("launch", "scale"):
        raise ValueError("not a scenario of this harness")
    return v.lower()


DECS = tsv(DEC_HEAD,
           "product|t|hands_off|t|yes|no|t|human|t|hands off|t|不自动调整|t|S01|t|",
           "product|t|priority|t|int:1..5|t|human|t|priority|t|优先级|t||t|",
           "product|t|budget_share|t|number:0..1|t|human|t|share|t|份额|t||t|",
           "product|t|launch_on|t|date|t|human|t|launch|t|上线日|t||t|",
           "product|t|snapshot|t|json|t|harness|t|snapshot|t|快照|t||t|",
           "product|t|brief_hash|t|sha256|t|human|t|brief|t|脚本校验|t||t|",
           "product|t|note|t|text|t|none|t|note|t|备注|t||t|",
           "creator|t|scenario|t|scenario|t|human|t|scenario|t|场景|t||t|",
           "creator|t|rating|t|int:0..|t|none|t|rating|t|评分|t||t|",
           "creator|t|weight|t|number:..10|t|human|t|weight|t|权重|t||t|")
HEX = "AB" * 32


def test_decision_registry() -> None:
    print("[3] DecisionRegistry")
    reg = DecisionRegistry(DECS, domains={"scenario": scenario},
                           entity_ids={"product": r"P\d+"})
    check("entity_types(), keys(), domain(), row()",
          reg.entity_types() == ["product", "creator"]
          and reg.keys("creator") == ["scenario", "rating", "weight"]
          and reg.domain("product", "priority") == "int:1..5"
          and reg.row("product", "note")["confirm"] == "none")
    for et, key, raw, want in (
            ("product", "hands_off", "yes", "yes"),
            ("product", "priority", " 3 ", "3"),
            ("product", "priority", "05", "5"),
            ("product", "budget_share", "0.50", "0.5"),
            ("product", "budget_share", "1", "1"),
            ("product", "launch_on", "2026-09-28", "2026-09-28"),
            ("product", "snapshot", '{"b": 1, "a": "é"}', '{"a":"é","b":1}'),
            ("product", "brief_hash", HEX, HEX.lower()),
            ("product", "note", "  call first  ", "call first"),
            ("creator", "scenario", "Launch", "launch"),
            ("creator", "rating", "1000000", "1000000"),
            ("creator", "weight", "-3.5", "-3.5")):
        got = reg.validate(et, key, raw)
        check(f"{key} {raw!r} -> {want!r}", got == want, got)
    for et, key, raw, code, params in (
            ("product", "hands_off", "maybe", "decision_value_not_choice",
             {"value": "maybe", "choices": ["yes", "no"]}),
            ("product", "priority", "6", "decision_value_out_of_range",
             {"value": 6, "min": 1, "max": 5}),
            ("product", "priority", "0", "decision_value_out_of_range",
             {"value": 0, "min": 1, "max": 5}),
            ("product", "priority", "2.5", "decision_value_not_integer",
             {"value": "2.5"}),
            ("product", "priority", "x", "decision_value_not_integer",
             {"value": "x"}),
            ("product", "budget_share", "1.5", "decision_value_out_of_range",
             {"value": 1.5, "min": 0, "max": 1}),
            ("product", "budget_share", "abc", "decision_value_not_number",
             {"value": "abc"}),
            ("product", "budget_share", "nan", "decision_value_not_number",
             {"value": "nan"}),
            ("product", "launch_on", "2026-02-29", "decision_value_not_date",
             {"value": "2026-02-29"}),
            ("product", "snapshot", "[1, 2]", "decision_value_not_json",
             {"value": "[1, 2]"}),
            ("product", "snapshot", "nope", "decision_value_not_json",
             {"value": "nope"}),
            ("product", "brief_hash", "abc", "decision_value_not_sha256",
             {"value": "abc"}),
            ("product", "brief_hash", "g" * 64, "decision_value_not_sha256",
             {"value": "g" * 64}),
            ("product", "note", "   ", "decision_value_empty", {}),
            ("creator", "rating", "-1", "decision_value_out_of_range",
             {"value": -1, "min": 0, "max": None}),
            ("creator", "weight", "11", "decision_value_out_of_range",
             {"value": 11, "min": None, "max": 10}),
            ("creator", "scenario", "exit", "decision_value_invalid",
             {"value": "exit", "domain": "scenario",
              "detail": "not a scenario of this harness"}),
            ("creator", "scenario", "coded", "decision_value_empty", {}),
            ("creator", "scenario", "harness", "decision_value_empty", {})):
        r = coded(lambda: reg.validate(et, key, raw))
        check(f"{key} {raw!r}: {code}",
              r == {"code": code, "params": params, "next": []}, r)
    r = coded(lambda: reg.validate("campaign", "wallet", "x"))
    check("an unknown entity type: decision_entity_type_unknown",
          r == {"code": "decision_entity_type_unknown",
                "params": {"entity_type": "campaign",
                           "entity_types": ["product", "creator"]},
                "next": []}, r)
    r = coded(lambda: reg.check_key("creator", "stage"))
    check("an unknown key: decision_key_unknown with the allowed keys",
          r == {"code": "decision_key_unknown",
                "params": {"key": "stage", "entity_type": "creator",
                           "allowed": ["rating", "scenario", "weight"]},
                "next": []}, r)
    check("confirm_exempt: only confirm = none; an unknown key never",
          reg.confirm_exempt("product", "note")
          and reg.confirm_exempt("creator", "rating")
          and not reg.confirm_exempt("product", "hands_off")
          and not reg.confirm_exempt("product", "retired"))
    check("harness_written: only confirm = harness",
          reg.harness_written("product", "snapshot")
          and not reg.harness_written("product", "note"))
    check("entity ids: the harness's pattern, else any non-blank id",
          reg.check_entity_id("product", "P12") == "P12"
          and reg.check_entity_id("creator", "anna.lee") == "anna.lee")
    for et, eid in (("product", "X1"), ("product", "P1 "), ("creator", ""),
                    ("creator", " a")):
        r = coded(lambda: reg.check_entity_id(et, eid))
        check(f"entity id {eid!r} for {et}: decision_entity_id_invalid",
              r == {"code": "decision_entity_id_invalid",
                    "params": {"entity_id": eid, "entity_type": et},
                    "next": []}, r)

    base = "product|t|k|t|{d}|t|{c}|t|k|t|键|t||t|"
    for label, domain, confirm in (
            ("an unknown domain", "lifecycle", "human"),
            ("a one-word choice", "yes", "human"),
            ("int bounds reversed", "int:5..1", "human"),
            ("int bounds not integers", "int:a..b", "human"),
            ("int without ..", "int:5", "human"),
            ("a number bound not a number", "number:0..x", "human"),
            ("a repeated choice", "yes|yes", "human"),
            ("an empty choice", "yes||no", "human"),
            ("confirm not human|none|harness", "text", "maybe")):
        load_fails(f"decision_keys: {label}", lambda: DecisionRegistry(
            tsv(DEC_HEAD, base.format(d=domain, c=confirm))))
    load_fails("decision_keys: a (entity_type, key) listed twice",
               lambda: DecisionRegistry(tsv(
                   DEC_HEAD, base.format(d="text", c="human"),
                   base.format(d="date", c="human"))))
    load_fails("decision_keys: a harness domain named like a built-in",
               lambda: DecisionRegistry(DECS, domains={"date": scenario,
                                                       "scenario": scenario}))
    load_fails("decision_keys: a harness domain the TSV needs but the "
               "harness did not register", lambda: DecisionRegistry(DECS))


# ---- 4 ---------------------------------------------------------------------

def facts_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(FACTS_SQL)
    con.executemany(
        "INSERT INTO client_facts (market, key, value, is_assumption, source) "
        "VALUES (?, ?, ?, ?, 'test')",
        [("US", "threshold_min_orders", "5", 0),
         ("US", "threshold_max_step", "0.3", 1),
         ("US", "threshold_approval_ttl_hours", "abc", 0),
         ("US", "threshold_old_orders", "7", 0),
         ("CA", "threshold_min_orders", "7", 1)])
    con.commit()
    return con


def dump(con: sqlite3.Connection) -> list[tuple]:
    return con.execute("SELECT * FROM client_facts ORDER BY market, key"
                       ).fetchall()


def test_thresholds() -> None:
    print("[4] Thresholds")
    th = Thresholds(CONSTS, superseded={"old_orders": "min_orders"})
    con = facts_db()
    before, changes = dump(con), con.total_changes
    check("names/keys/overridable/bounds from the TSV",
          th.names() == ("min_orders", "approval_ttl_hours", "max_step")
          and th.keys()[0] == "threshold_min_orders"
          and th.overridable() == {"min_orders": 3.0,
                                   "approval_ttl_hours": 24.0,
                                   "max_step": 0.2}
          and th.bounds("max_step") == (0, 1)
          and th.bounds("min_orders") == (0, None))
    check("without a connection: the default",
          th.get("min_orders") == 3.0 and th.load() == th.overridable())
    check("US: the confirmed fact overrides the default",
          th.get("min_orders", con, "US") == 5.0)
    check("US: a pending fact is not in effect",
          th.get("max_step", con, "US") == 0.2)
    check("US: a confirmed fact that is not a number is not in effect",
          th.get("approval_ttl_hours", con, "US") == 24.0)
    check("CA: its own (pending) fact is not in effect; US's never leaks",
          th.get("min_orders", con, "CA") == 3.0)
    check("overridden(): the confirmed overrides, per market",
          th.overridden(con, "US") == {"min_orders": {"value": 5.0,
                                                      "default": 3.0}}
          and th.overridden(con, "CA") == {}, th.overridden(con, "US"))
    empty = sqlite3.connect(":memory:")
    check("a DB without client_facts: the defaults",
          th.load(empty, "US") == th.overridable())

    got = th.assume_pairs(["threshold_min_orders=9",
                           " threshold_max_step = 0.25 "])
    check("assume_pairs parses threshold_<name>=<value>",
          got == {"min_orders": 9.0, "max_step": 0.25}, got)
    check("--assume goes over the confirmed fact and the default, any market",
          th.get("min_orders", con, "US") == 9.0
          and th.get("min_orders", con, "CA") == 9.0
          and th.get("max_step", con, "US") == 0.25
          and th.get("approval_ttl_hours", con, "US") == 24.0)
    check("without a connection it is still the plain default (reference "
          "parity)", th.get("min_orders") == 3.0)
    want = {"min_orders": {"value": 9.0, "default": 3.0, "client_value": 5.0},
            "max_step": {"value": 0.25, "default": 0.2, "client_value": None}}
    check("assumed(): value, default, and the client's own confirmed value",
          th.assumed(con, "US") == want, th.assumed(con, "US"))
    check("overridden() stays the stored truth",
          th.overridden(con, "US") == {"min_orders": {"value": 5.0,
                                                      "default": 3.0}})
    check("--assume is never stored: client_facts unchanged, no write",
          dump(con) == before and con.total_changes == changes)
    m = contract.meta(window=None, sources=[], stale=[],
                      thresholds_overridden=th.overridden(con, "US"),
                      assumed_thresholds=th.assumed(con, "US"))
    w = contract.stale_warning(m)
    check("reported: meta.assumed_thresholds and the ASSUMED warning",
          m["assumed_thresholds"] == want and w is not None
          and w.code == "contract_assumed_thresholds"
          and "min_orders=9.0 (in effect was 5.0)" in w
          and "max_step=0.25 (in effect was 0.2)" in w, (m, w))

    nxt = ["shop facts list --thresholds"]
    for pair, code, params in (
            ("min_orders=9", "assume_unknown_threshold", {"pair": "min_orders=9"}),
            ("threshold_nope=1", "assume_unknown_threshold",
             {"pair": "threshold_nope=1"}),
            ("threshold_min_orders", "assume_unknown_threshold",
             {"pair": "threshold_min_orders"}),
            ("threshold_max_step=2", "assume_bad_value",
             {"key": "threshold_max_step", "value": "2"}),
            ("threshold_min_orders=nan", "assume_bad_value",
             {"key": "threshold_min_orders", "value": "nan"}),
            ("threshold_min_orders=", "assume_bad_value",
             {"key": "threshold_min_orders", "value": ""})):
        th.assume({"min_orders": 1})
        r = coded(lambda: th.assume_pairs([pair]))
        check(f"--assume {pair!r}: {code} with next, earlier assumptions "
              f"cleared", r == {"code": code, "params": params, "next": nxt}
              and th.assumed() == {}, (r, th.assumed()))
    r = coded(lambda: th.assume({"nope": 1}))
    check("assume() of an unknown name: threshold_unknown",
          r and r["code"] == "threshold_unknown", r)
    r = coded(lambda: th.get("nope"))
    check("get() of an unknown name: threshold_unknown with the names",
          r == {"code": "threshold_unknown",
                "params": {"name": "nope", "names": list(th.names())},
                "next": []}, r)
    th.assume({})
    check("assume({}) ends the what-if", th.assumed(con, "US") == {}
          and th.get("min_orders", con, "US") == 5.0)

    p = argparse.ArgumentParser()
    registry.add_assume_arg(p)
    a = p.parse_args(["--assume", "threshold_min_orders=4",
                      "--assume=threshold_max_step=0.1"])
    check("add_assume_arg: repeatable, default []",
          a.assume == ["threshold_min_orders=4", "threshold_max_step=0.1"]
          and p.parse_args([]).assume == [], a)
    check("validate(): a threshold fact's canonical value",
          th.validate("threshold_min_orders", "4.0") == "4"
          and th.validate("max_step", ".5") == "0.5")

    rows = {r["name"]: r for r in th.listing(con, "US")}
    check("listing(): the stored fact, pending or confirmed, and the value "
          "in effect from stored facts",
          rows["min_orders"]["value"] == 5.0
          and rows["min_orders"]["in_effect"] == "client"
          and rows["min_orders"]["effective"] == 5.0
          and rows["max_step"]["value"] == 0.3
          and rows["max_step"]["is_assumption"] == 1
          and rows["max_step"]["in_effect"] == "default"
          and rows["max_step"]["effective"] == 0.2
          and rows["approval_ttl_hours"]["value"] == "abc"
          and rows["approval_ttl_hours"]["min"] == 1
          and rows["max_step"]["label_zh"] == "最大步长"
          and rows["max_step"]["key"] == "threshold_max_step", rows)
    check("superseded_overrides(): a fact under a retired name",
          th.superseded_overrides(con) == [{
              "market": "US", "key": "threshold_old_orders", "value": "7",
              "is_assumption": 0, "new_key": "threshold_min_orders"}]
          and Thresholds(CONSTS).superseded_overrides(con) == [])

    row = "n|t|{d}|t|u|t|{lo}|t|{hi}|t|g|t|n|t|名|t|e|t|w"
    for label, d, lo, hi in (("a default outside min..max", "5", "0", "1"),
                             ("a default that is not a number", "x", "", ""),
                             ("min > max", "1", "2", "1")):
        load_fails(f"constants: {label}", lambda: Thresholds(
            tsv(CONST_HEAD, row.format(d=d, lo=lo, hi=hi))))
    load_fails("constants: a name listed twice", lambda: Thresholds(tsv(
        CONST_HEAD, row.format(d="1", lo="", hi=""),
        row.format(d="2", lo="", hi=""))))


# ---- 5 ---------------------------------------------------------------------

def test_harness_paths() -> None:
    print("[5] files from harness.toml; labels per language")
    r = coded(lambda: FactKeys())
    check("the shop harness declares ssot/fact_keys.tsv but has none: "
          "registry_file_missing",
          r and r["code"] == "registry_file_missing"
          and r["params"]["path"].endswith("fake_harness/ssot/fact_keys.tsv"),
          r)
    d = Path(tmp_dir("reg-harness-"))
    (d / "ssot").mkdir()
    (d / "harness.toml").write_text(
        '[harness]\nname = "one"\ncli = "one"\nenv_prefix = "ONE"\n'
        'languages = ["en"]\nmarkets = ["HK"]\n[ssot]\n'
        'fact_keys = "ssot/fact_keys.tsv"\nconstants = "ssot/c.tsv"\n',
        encoding="utf-8")
    (d / "ssot" / "fact_keys.tsv").write_text(
        "key\tgroup\ttype\tunit\tmin\tmax\tlabel_en\twhy\n"
        "fee\tg\tnumber\tpct\t0\t100\tfee\t\n", encoding="utf-8")
    config.use(d)
    try:
        fk = FactKeys()
        check("FactKeys() reads [ssot].fact_keys; a harness declaring only "
              "en needs only label_en", fk.settable() == ("fee",))
        r = coded(lambda: DecisionRegistry())
        check("a registry harness.toml does not declare: "
              "registry_file_missing naming the [ssot] key",
              r == {"code": "registry_file_missing",
                    "params": {"path": "[ssot].decision_keys"}, "next": []}, r)
        r = coded(lambda: Thresholds())
        check("a declared file that is missing: registry_file_missing",
              r and r["code"] == "registry_file_missing", r)
    finally:
        _shop.use()



# ---- 6 ---------------------------------------------------------------------

def test_closure() -> None:
    print("[6] registry.py keeps its registry fragment closed")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "registry.tsv"}
    probs = messages.check_registry_closed(_shop.KIT, ["registry.py"], mine,
                                           strict_kit=True)
    check("every msg() in registry.py is literal, registered with exact "
          "params, and every registry.tsv code is emitted", probs == [], probs)
    check("the SPEC's FactKeys codes are among them",
          {"fact_key_unknown", "fact_value_not_number",
           "fact_value_out_of_range", "fact_value_not_choice"} <= set(mine))


def main() -> int:
    _shop.use()
    for fn in (test_read_tsv, test_fact_keys, test_decision_registry,
               test_thresholds, test_harness_paths, test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
