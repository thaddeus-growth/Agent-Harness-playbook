"""`<cli> pending`: everything waiting on a human, as ready console asks.

A read verb (the DB is opened read-only and never created). Every row
that only a human can move on becomes one ask in the playbook console's
format (console/README.md "A clear ask"; console/examples/*.json):

  * a pending market declaration (client_facts market_declared,
    is_assumption = 1) -> step `confirm` (listed first: nothing else can
    be confirmed for a market before it);
  * a pending client fact -> `provide` (number / date / text, with the
    registry's bounds and unit), `choose` (a choice key) or `confirm`;
  * a pending decision -> `provide` / `choose` / `confirm` by its domain;
  * a pending queue item -> `approve`, its `effect` from the proposal's
    `expected`.

What it guards:

  * The gate block is built from the SAME subject the gate binds, never
    guessed: facts confirm binds {key: value} (kit.facts), decisions
    confirm binds decisions.plan_confirm()'s items (on_confirm's extra
    values included), queue approve binds queue.approve_items(). `expect`
    = {"market": M, "items": …}; "$value" stands for the owner's answer
    where `value_arg` passes it. So console/relay.py's subject check
    passes for exactly what the owner was shown, and nothing else.
  * A gate is attached only when the relayed call can reach this row: the
    console appends no --market, so facts/decisions asks carry a gate only
    when the verb resolves to this row's market without one (exactly one
    market declared, or the harness has none), every verb word is a plain
    token, `expect` fits the console's limit and the confirm is not
    already refused. Otherwise the ask has no gate and a coded warning
    says why (a person confirms at the terminal).
  * Evidence comes from the harness's own rows, each item's source
    labelled "harness (<table>)"; the agent's argument goes in
    `recommend.because`, which pending leaves null: every ask is listed
    in `about[].fill`, and the console refuses the ask until the agent
    writes it.
  * An ask id carries a digest of the subject (value and version), so a
    new pending value is a new question, never a reused id.

Every text in an ask is a coded Msg: `about[i]` carries the
{title,why,if_no,effect}_code of asks[i] (the console format has no room
for codes inside the ask).

Deviation (SPEC §pending): the entry point is `main` (`pending_main` is
an alias), with keyword hooks thresholds= (threshold_<name> facts) and
on_confirm= (the decisions hook, so its bound values are in `expect`);
asks, about and warnings are also returned by `asks()` for harness code.

Test: kit/tests/test_pending.py.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from decimal import Decimal
from typing import Any, Callable

from kit import contract, db, decisions, facts, human
from kit import market as markets
from kit import queue as q
from kit.config import config
from kit.contract import HarnessError
from kit.messages import Msg, code, coded, msg
from kit.registry import (FACT_PREFIX, DecisionRegistry, FactKeys,
                          Thresholds)

# the console's limits (console/core.py LIMITS, TOKEN_RE, ID_RE)
LIMITS = {"id": 64, "kind": 40, "group": 60, "title": 120, "why": 400,
          "if_no": 200, "effect": 200, "label": 40, "value": 120,
          "source": 120, "caption": 80, "cell": 80, "option_label": 60,
          "unit": 12, "evidence": 12, "rows": 30, "options": 8,
          "expect": 2000, "answer": 500}
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,79}\Z")
FILL = ["recommend.because"]
SKIP_EVIDENCE = ("because", "because_code")


# ---- small helpers ---------------------------------------------------------

def _clip(text: Any, n: int) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _cut(m: Msg, n: int) -> Msg:
    """The message clipped to the console's limit, code and params kept."""
    return m if len(m) <= n else type(m)(m.code, _clip(m, n), m.params)


def _cell(v: Any) -> str:
    return _clip(v if isinstance(v, str) else q.canonical(v), LIMITS["cell"])


def _ask_id(prefix: str, parts: list[str], subject: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", "-".join(parts).lower()).strip("-._")
    digest = hashlib.sha256(subject.encode()).hexdigest()[:8]
    head = f"{prefix}-{slug}"[:LIMITS["id"] - 9].rstrip("-._")
    return f"{head}-{digest}"


def _fact(label: str, value: Any, table: str) -> dict | None:
    if value is None or str(value).strip() == "":
        return None
    return {"label": _clip(label, LIMITS["label"]),
            "value": _clip(value, LIMITS["value"]),
            "source": f"harness ({table})"}


def _table(caption: str, d: dict, table: str) -> dict | None:
    rows = [[_cell(k), _cell(v)] for k, v in d.items()
            if k not in SKIP_EVIDENCE][:LIMITS["rows"]]
    if not rows:
        return None
    return {"table": {"caption": _clip(f"{caption}. Source: harness "
                                       f"({table})", LIMITS["caption"]),
                      "columns": ["Field", "Value"], "rows": rows}}


def _evidence(*items: dict | None) -> list[dict]:
    return [i for i in items if i][:LIMITS["evidence"]]


def _number(v: Any) -> int | float | None:
    if v is None or v == "":
        return None
    d = Decimal(str(v))
    return int(d) if d == d.to_integral_value() else float(d)


def _shape(kind: str, *, choices: list[str] | None = None,
           lo: Any = None, hi: Any = None, unit: str = "") -> dict:
    """The ask's step and its step fields for a value of `kind`."""
    if kind == "choice" and choices and 2 <= len(choices) <= LIMITS["options"]:
        return {"step": "choose", "options": [
            {"value": c, "label": _clip(c, LIMITS["option_label"])}
            for c in choices]}
    if kind in ("number", "date", "text"):
        spec: dict = {"type": kind}
        if kind == "number":
            for k, v in (("min", _number(lo)), ("max", _number(hi))):
                if v is not None:
                    spec[k] = v
            if unit and len(unit) <= LIMITS["unit"]:
                spec["unit"] = unit
        return {"step": "provide", "input": spec}
    return {"step": "confirm"}


def _fact_shape(key: str, keys: FactKeys | None,
                thresholds: Thresholds | None) -> tuple[dict, str, str]:
    """(step fields, label, why) of a fact key from its registry row."""
    lang = config().languages[0]
    row = keys.row(key) if keys is not None else None
    if row is not None:
        kind, _, rest = row["type"].partition(":")
        lo, hi = keys.bounds()[key]
        shape = _shape(kind, choices=rest.split("|") if rest else None,
                       lo=lo, hi=hi, unit=row.get("unit") or "")
        return shape, row.get(f"label_{lang}") or key, row.get("why") or ""
    if thresholds is not None and key.startswith(FACT_PREFIX):
        name = key[len(FACT_PREFIX):]
        trow = thresholds.row(name)
        if trow is not None:
            lo, hi = thresholds.bounds(name)
            return (_shape("number", lo=lo, hi=hi, unit=trow.get("unit") or ""),
                    trow.get(f"label_{lang}") or key, trow.get("why") or "")
    return {"step": "confirm"}, key, ""


def _decision_shape(domain: str) -> dict:
    for kind in ("int", "number"):
        if domain.startswith(kind + ":"):
            lo, _, hi = domain[len(kind) + 1:].partition("..")
            return _shape("number", lo=lo or None, hi=hi or None)
    if domain in ("date", "text"):
        return _shape(domain)
    if "|" in domain and ":" not in domain:
        return _shape("choice", choices=domain.split("|"))
    return _shape("confirm")


def _recommend(shape: dict, value: str) -> dict:
    return {"value": "yes" if shape["step"] in ("confirm", "approve")
            else value, "because": None}


def _gate(ask_id: str, verb: list[str], expect: dict, value_arg: bool,
          warnings: list[Msg]) -> dict | None:
    """The gate block, or None with a coded warning saying why not."""
    for w in verb:
        if not TOKEN_RE.match(w):
            warnings.append(msg(
                "pending_gate_bad_word",
                f"{ask_id}: no gate, {w!r} cannot be a console verb word "
                f"(letters, digits, _ . : @ / -); confirm it at the terminal",
                id=ask_id, word=w))
            return None
    size = len(q.canonical(expect))
    if size > LIMITS["expect"]:
        warnings.append(msg(
            "pending_gate_too_long",
            f"{ask_id}: no gate, what it binds is {size} characters (the "
            f"console holds {LIMITS['expect']}); confirm it at the terminal",
            id=ask_id, length=size))
        return None
    return {"verb": verb, "value_arg": value_arg, "expect": expect}


def _reachable(ask_id: str, m: str, relay_markets: list[str],
               confirmed: list[str], door: bool,
               warnings: list[Msg]) -> bool:
    """Can a relayed facts/decisions call (no --market) reach market m?"""
    if relay_markets != [m]:
        warnings.append(msg(
            "pending_gate_market_ambiguous",
            f"{ask_id}: no gate, the console's call names no market and "
            f"{len(relay_markets)} are declared ({', '.join(relay_markets)}); "
            f"confirm it at the terminal with --market {m}", id=ask_id,
            markets=relay_markets))
        return False
    if door and m not in confirmed:
        warnings.append(msg(
            "pending_gate_not_onboarded",
            f"{ask_id}: no gate yet, market {m} is not confirmed; answer its "
            f"market declaration first", id=ask_id, market=m))
        return False
    return True


def _about(ask_id: str, table: str, m: str, ref: dict, step: str,
           gate: bool, **texts: Msg) -> dict:
    return {"id": ask_id, "table": table, "market": m, "ref": ref,
            "step": step, "gate": gate, "fill": list(FILL),
            **{f"{k}_code": code(v) for k, v in texts.items()}}


# ---- one ask per row -------------------------------------------------------

def _fact_ask(con: sqlite3.Connection, r: dict, *, keys: FactKeys | None,
              thresholds: Thresholds | None, relay_markets: list[str],
              confirmed: list[str], warnings: list[Msg]) -> tuple[dict, dict]:
    m, key, value = r["market"], r["key"], r["value"]
    version = facts._last_id(con, m, key)
    subj = human.subject("facts confirm", m, "fact", {key: value}, version)
    by, at, src = r["changed_by"], (r["updated_at"] or "")[:16], r["source"]
    declared = key == markets.MARKET_KEY
    if declared:
        shape, label, why_row = {"step": "confirm"}, "Market", ""
        ask_id = _ask_id("market", [m], subj)
        title = msg("pending_title_declared",
                    f"Do we work on market {m} for you?", market=m)
        why = msg("pending_why_declared",
                  f"{by} declared market {m} on {at}. Until a person "
                  f"confirms it, nothing (no fact, decision or action) can "
                  f"be confirmed for {m}.", market=m, by=by, at=at)
        if_no = msg("pending_if_no_declared",
                    f"{m} stays undeclared and nothing is written for it; "
                    f"the agent asks which markets are right.", market=m)
    else:
        shape, label, why_row = _fact_shape(key, keys, thresholds)
        ask_id = _ask_id("fact", [m, key], subj)
        title = msg("pending_title_fact",
                    f"{label} for {m}: is {value} right?", label=label,
                    market=m, value=value)
        why = msg("pending_why_fact",
                  f"{key} [{m}] = {value} waits for a person: {by} set it on "
                  f"{at} (source {src}). Nothing uses it until it is "
                  f"confirmed." + (f" It is used for: {why_row}."
                                   if why_row else ""),
                  key=key, market=m, value=value, by=by, at=at, source=src,
                  why=why_row)
        if_no = msg("pending_if_no_fact",
                    f"{key} stays pending and nothing uses it; the agent "
                    f"sets the right value or asks again.", key=key)
    title, why, if_no = (_cut(title, LIMITS["title"]),
                         _cut(why, LIMITS["why"]), _cut(if_no, LIMITS["if_no"]))
    ask = {"id": ask_id, "step": shape["step"],
           "kind": "market" if declared else "fact",
           "group": _clip(f"Facts, {m}", LIMITS["group"]),
           "title": title, "why": why,
           "evidence": _evidence(
               _fact("Value waiting", value, "client_facts"),
               _fact("Set by", by, "client_facts"),
               _fact("Set on", at, "client_facts"),
               _fact("Its source", src, "client_facts"),
               _fact("Unit", (keys.row(key) or {}).get("unit")
                     if keys is not None and not declared else None,
                     "fact registry")),
           "recommend": _recommend(shape, value), "if_no": if_no,
           **{k: v for k, v in shape.items() if k != "step"}}
    gate = None
    if _reachable(ask_id, m, relay_markets, confirmed, not declared,
                  warnings):
        value_arg = shape["step"] in ("provide", "choose")
        gate = _gate(ask_id, ["facts", "confirm", key],
                     {"market": m, "items": {key: "$value" if value_arg
                                             else value}},
                     value_arg, warnings)
    if gate:
        ask["gate"] = gate
    return ask, _about(ask_id, "client_facts", m, {"key": key},
                       shape["step"], gate is not None, title=title, why=why,
                       if_no=if_no)


def _decision_ask(con: sqlite3.Connection, r: dict, *,
                  registry: DecisionRegistry, on_confirm: Callable | None,
                  relay_markets: list[str], warnings: list[Msg]
                  ) -> tuple[dict, dict]:
    m, et, eid, key, value = (r["market"], r["entity_type"], r["entity_id"],
                              r["key"], r["value"])
    try:
        shape = _decision_shape(registry.domain(et, key))
    except HarnessError:                 # a key the registry no longer has
        shape = {"step": "confirm"}
    row = registry.row(et, key) or {}
    label = row.get(f"label_{config().languages[0]}") or key
    by, at, src = r["changed_by"], (r["updated_at"] or "")[:16], r["source"]
    eff = r.get("effective")
    plan, refused = None, None
    try:
        plan = decisions.plan_confirm(con, m, et, [eid], key,
                                      registry=registry, on_confirm=on_confirm)
    except HarnessError as e:
        refused = e.message
    subj = plan["subject"] if plan and not plan["already"] else \
        human.subject("decisions confirm", m, et, {eid: {key: value}},
                      decisions.last_change(con, m, et, eid, [key]))
    ask_id = _ask_id("decision", [m, et, eid, key], subj)
    title = _cut(msg("pending_title_decision",
                     f"{label} of {et} {eid}: is {value} right?",
                     label=label, entity_type=et, entity_id=eid, value=value),
                 LIMITS["title"])
    in_force = eff if eff is not None else "no value"
    why = _cut(msg("pending_why_decision",
                   f"{et} {eid} {key} = {value} waits for a person: {by} set "
                   f"it on {at} (source {src}). Until it is confirmed, "
                   f"{in_force} stays in force." + (
                       f" It is used for: {row['why']}." if row.get("why")
                       else ""),
                   entity_type=et, entity_id=eid, key=key, value=value,
                   by=by, at=at, source=src, effective=in_force,
                   why=row.get("why") or ""), LIMITS["why"])
    if_no = _cut(msg("pending_if_no_decision",
                     f"{in_force} stays in force; the agent withdraws the "
                     f"proposal or sets another value.", effective=in_force),
                 LIMITS["if_no"])
    ask = {"id": ask_id, "step": shape["step"], "kind": "decision",
           "group": _clip(f"Decisions, {m}", LIMITS["group"]),
           "title": title, "why": why,
           "evidence": _evidence(
               _fact("Value waiting", value, "decisions"),
               _fact("In force now", in_force, "decisions"),
               _fact("Set by", by, "decisions"),
               _fact("Set on", at, "decisions"),
               _fact("Its source", src, "decisions")),
           "recommend": _recommend(shape, value), "if_no": if_no,
           **{k: v for k, v in shape.items() if k != "step"}}
    gate = None
    if refused is not None:
        warnings.append(msg(
            "pending_gate_refused",
            f"{ask_id}: no gate, the confirm would be refused: {refused}",
            id=ask_id, reason=refused))
    elif plan and not plan["already"] and _reachable(
            ask_id, m, relay_markets, relay_markets, False, warnings):
        value_arg = shape["step"] in ("provide", "choose")
        items = {str(k): dict(v) for k, v in plan["items"].items()}
        if value_arg:
            items[eid][key] = "$value"
        gate = _gate(ask_id, ["decisions", "confirm", et, eid, key],
                     {"market": m, "items": items}, value_arg, warnings)
    if gate:
        ask["gate"] = gate
    return ask, _about(ask_id, "decisions", m,
                       {"entity_type": et, "entity_id": eid, "key": key},
                       shape["step"], gate is not None, title=title, why=why,
                       if_no=if_no)


def _queue_ask(r: dict, warnings: list[Msg]) -> tuple[dict, dict]:
    m, kind, target = r["market"], r["kind"], r["target_ref"]
    row = q.row_dict(r)
    ev, expected = row["evidence"] or {}, row["expected"] or {}
    ask_id = _ask_id("action", [m, str(r["id"])], q.approve_subject([r]))
    title = _cut(msg("pending_title_queue",
                     f"Approve {kind} on {target} ({m})?", kind=kind,
                     target_ref=target, market=m), LIMITS["title"])
    because = str(ev.get("because") or "")
    at, basis = (r["created_at"] or "")[:16], r["basis"]
    why = _cut(msg("pending_why_queue",
                   f"The harness proposed this on {at} from the data it read "
                   f"(basis {basis[:12]}). Nothing is sent until a person "
                   f"approves, and execute refuses it if that data moves."
                   + (f" The rule says: {because}" if because else ""),
                   created_at=at, basis=basis, because=because),
               LIMITS["why"])
    said = expected.get("effect") if isinstance(expected.get("effect"),
                                                str) else None
    shown = said or ", ".join(f"{k} {_cell(v)}" for k, v in expected.items()) \
        or _cell(row["payload"])
    effect = _cut(msg("pending_effect_queue",
                      f"{kind} on {target}: {shown}", kind=kind,
                      target_ref=target, expected=shown), LIMITS["effect"])
    if_no = _cut(msg("pending_if_no_queue",
                     "Nothing is sent. The agent records the no with a "
                     "reason code, and the next proposal run reads it."),
                 LIMITS["if_no"])
    ask = {"id": ask_id, "step": "approve", "kind": "action",
           "group": _clip(f"Actions, {m}", LIMITS["group"]),
           "title": title, "why": why,
           "evidence": _evidence(
               _table("What would be written", row["payload"], "action_queue"),
               _table("Expected effect", {k: v for k, v in expected.items()
                                          if k != "effect"}, "action_queue"),
               _table("The rule's evidence", ev, "action_queue"),
               _fact("Proposed on", at, "action_queue"),
               _fact("Basis", basis, "action_queue")),
           "recommend": {"value": "yes", "because": None},
           "effect": effect, "if_no": if_no}
    gate = _gate(ask_id, ["queue", "approve", str(r["id"])],
                 {"market": m, "items": q.approve_items([r])}, False, warnings)
    if gate:
        ask["gate"] = gate
    return ask, _about(ask_id, "action_queue", m, {"id": r["id"]}, "approve",
                       gate is not None, title=title, why=why, effect=effect,
                       if_no=if_no)


# ---- all of them -----------------------------------------------------------

def asks(con: sqlite3.Connection, *, keys: FactKeys | None,
         registry: DecisionRegistry | None,
         thresholds: Thresholds | None = None,
         on_confirm: Callable | None = None,
         market: str | None = None) -> dict:
    """{asks, about, warnings, message} for every row waiting on a human
    (of `market`, or of every market): declarations first, then facts,
    decisions and queue items."""
    warnings: list[Msg] = []
    out: list[tuple[dict, dict]] = []
    fact_markets = markets.declared(con, include_pending=True)
    confirmed = markets.declared(con)
    rows = facts.pending(con, market)
    rows.sort(key=lambda r: (r["key"] != markets.MARKET_KEY, r["market"],
                             r["key"]))
    for r in rows:
        out.append(_fact_ask(con, r, keys=keys, thresholds=thresholds,
                             relay_markets=fact_markets, confirmed=confirmed,
                             warnings=warnings))
    n_facts = len(out)
    if registry is not None:
        for m in ([market] if market else confirmed):
            for r in decisions.pending(con, m):
                if registry.harness_written(r["entity_type"], r["key"]):
                    continue
                out.append(_decision_ask(con, r, registry=registry,
                                         on_confirm=on_confirm,
                                         relay_markets=confirmed,
                                         warnings=warnings))
    n_dec = len(out) - n_facts
    for r in q.pending(con, market):
        out.append(_queue_ask(r, warnings))
    n_q = len(out) - n_facts - n_dec
    summary = msg("pending_summary",
                  f"{len(out)} ask(s) waiting on a person: {n_facts} fact(s), "
                  f"{n_dec} decision(s), {n_q} queue item(s)",
                  facts=n_facts, decisions=n_dec, queue=n_q)
    if out:
        warnings.insert(0, msg(
            "pending_fill_because",
            f"write recommend.because in each of the {len(out)} ask(s) (your "
            f"argument, apart from the harness's evidence) before `ask.py "
            f"add`; the console refuses an ask without it", count=len(out)))
    return {**coded("message", summary), "market": market,
            "asks": [a for a, _ in out], "about": [b for _, b in out],
            **coded("warnings", warnings)}


def main(argv: list[str] | None = None, *, spec: db.SchemaSpec,
         keys: FactKeys | None, registry: DecisionRegistry | None,
         thresholds: Thresholds | None = None,
         on_confirm: Callable | None = None) -> int:
    """`<cli> pending [--market M] [--json]`; the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = config()
    cmd = [cfg.cli, "pending", *argv]
    p = argparse.ArgumentParser(prog=f"{cfg.cli} pending",
                                description=__doc__.splitlines()[0])
    p.add_argument("--market")
    contract.add_json_arg(p)

    def run(argv: list[str]) -> Any:
        a = p.parse_args(argv)
        m = markets.validate(a.market) if a.market else None
        if (e := contract.missing_db_error()) is not None:
            raise e
        con = db.connect(spec, read_only=True)
        try:
            doc = asks(con, keys=keys, registry=registry,
                       thresholds=thresholds, on_confirm=on_confirm, market=m)
        finally:
            con.close()
        if a.json:
            return doc
        for ask, about in zip(doc["asks"], doc["about"]):
            print(f"  {ask['id']}  [{ask['step']}{' gated' if about['gate'] else ''}]"
                  f"  {ask['title']}")
        print(doc["message"])
        for w in doc["warnings"]:
            print(f"note: {w}", file=sys.stderr)
        return None

    return contract.run_main(run, argv, cmd=cmd)


pending_main = main
