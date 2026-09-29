"""The decisions verb: every human judgement about one entity, in one
generic table (schema_base `decisions` + `decisions_history`), keyed per
market. Ported from the reference harness's scripts/decisions.py and the
readers of its _lib/decisions.py; the registry (which entity types and
keys exist, their value domains, their `confirm` column) is
kit.registry.DecisionRegistry, and anything it does not list is refused.

What it guards:

  * Trust lives in the data. `set` (anyone, agents included) writes a
    PENDING value; only `confirm` (the human gate: a retype at /dev/tty, or
    a relayed --code) puts it in force. Until then `confirmed_value` keeps
    the value a human last confirmed, so a proposal never changes a result
    (`effective`). A key the registry marks `confirm = none` is in force
    the moment it is set (never one that can move money); a key marked
    `harness` is written only by a hook inside another confirm, so `set`
    and `confirm` refuse it.
  * `confirm ET ID… KEY`: several ids of one type and key pass ONE
    challenge (the human retypes the word `confirm`, or relays back one
    code bound to exactly that set of ids and values); one id retypes the
    value. All or nothing: an unknown id, an already confirmed one (in a
    batch) or a withdrawn one refuses the whole batch before any code is
    issued. `--value V` (one id) is the human stating a value, validated
    like `set`, bound into the same challenge; it needs no pending row and
    writes a `set` then a `confirm` history row, both under the gate's
    channel, with source human_confirmed_<date>. The code's subject
    version is the newest history id of the rows the confirm moves, so a
    used code is stale; the version is checked again under the write
    lock, so a value that changed while the human was answering is
    refused (decision_changed_meanwhile), not overwritten.
  * `withdraw` takes back pending values (anyone: it only lowers trust):
    all or nothing, one `withdraw` history row each (old = the pending
    value, new = the value still in force); the value in force is
    untouched. A later `set` of the same value proposes again.
  * Every write runs inside db.keep_human_rows, appends history with
    changed_by = `<os user>@cli|tty|relay` (derived, kit.human) and the
    required --reason; the file's triggers refuse DELETE and history
    rewrites whatever code path tries.
  * Read verbs (get, list, history) open the DB read-only and never create
    it. Under --json every verb prints one document, a refusal included
    (kit.contract.fail; a challenge carries `subject` and `next`).

Hooks (the reference's stage rules, generalised):

  * set_guard(con, *, market, entity_type, entity_id, key, value, row,
    code) -> "tty" | "relay" | "cli" | None. Called for every `set` after
    validation, outside the write. It may refuse (raise) or run a human
    challenge itself (kit.human.confirm, e.g. moving a stage back) and
    return its channel; the kit then requires --relay-user/--relay-at for
    "relay" and writes the history under that channel.
  * on_confirm(con, *, market, entity_type, entity_id, key, value, row,
    changed) -> Follow | None. Called for every id before the challenge:
    `Follow.bind` puts other values into the subject and the prompt (a
    code shown for one of them refuses another), `note` goes on the prompt
    line, `keys` move the subject version too, `apply(con, reason=,
    channel=)` runs inside the confirm's own write (use write_set /
    write_confirm) and returns report lines; `confirmed` / `recorded`
    name the keys listed under those names in --json.

Readers for harness code: effective(), pending(), row(), last_change(),
set_history(); plan_confirm() is the exact challenge `confirm` would run
(pending asks copy its subject rather than guess it).

Deviations from the reference / SPEC, each written here and in the build
report:
  * The kit's table holds `status` (pending | confirmed | withdrawn) and
    `confirmed_value` instead of is_assumption + a history lookup: the
    value in force is `confirmed_value`, so effective() needs no registry.
    A `confirm = none` key is therefore written in force by `set` (status
    confirmed, no `confirm` history row, changed_by @cli): the registry's
    exemption is applied when the value is written, not when it is read.
    Changing a key from none to human later does not distrust the values
    already in force.
  * `withdraw` marks the row's status `withdrawn` (value and value in
    force unchanged) where the reference only appended history.
  * confirm and withdraw also set the row's updated_at and changed_by.
  * Coded refusals where the reference raised plain text:
    decision_source_required, decision_harness_written (the reference's
    decision_with_stage_only). New: decision_changed_meanwhile.
  * --reason / --source are refused as coded messages (reason_required,
    decision_source_required), not argparse usage errors; every verb takes
    --json; set also takes --code/--relay-user/--relay-at for its guard.
  * `confirm` of an unknown key is decision_key_unknown (the reference
    said "no decision"); list/history --entity-type are checked against
    the registry with decision_entity_type_unknown.
  * Domain logic not ported: stage order, stage_entered_on, the stage
    snapshot, relevance tiers, keyword caps. They are what the hooks are
    for (kit/tests/test_decisions.py builds a stage example from them).

Test: kit/tests/test_decisions.py.
"""

from __future__ import annotations

import argparse
import shlex
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from kit import db, human
from kit import market as mk
from kit.config import config
from kit.contract import HarnessError, add_json_arg, run_main
from kit.db import SchemaSpec
from kit.messages import joined, msg
from kit.registry import DecisionRegistry

TABLE, HISTORY = "decisions", "decisions_history"
PENDING, CONFIRMED, WITHDRAWN = "pending", "confirmed", "withdrawn"
SET, CONFIRM, WITHDRAW = "set", "confirm", "withdraw"   # history actions
READS = frozenset({"get", "list", "history"})           # opened read-only
CHANNELS = ("cli", "tty", "relay")
CONFIRM_WORD = "confirm"        # what a human retypes to confirm several ids


@dataclass(frozen=True)
class Follow:
    """What confirming one id also does (the on_confirm hook's plan)."""
    bind: Mapping[str, str] = field(default_factory=dict)
    note: str = ""
    keys: tuple[str, ...] = ()
    confirmed: tuple[str, ...] = ()
    recorded: tuple[str, ...] = ()
    apply: Callable[..., "list[str] | None"] | None = None


SetGuard = Callable[..., "str | None"]
OnConfirm = Callable[..., "Follow | None"]


# ---- readers ---------------------------------------------------------------

def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                       "name=?", (name,)).fetchone() is not None


def _dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def row(con: sqlite3.Connection, market: str, entity_type: str,
        entity_id: str, key: str) -> dict | None:
    """The stored decision row, None when there is none (or no table: a DB
    not yet opened for write)."""
    if not _has_table(con, TABLE):
        return None
    rows = _dicts(con.execute(
        f"SELECT * FROM {TABLE} WHERE market=? AND entity_type=? AND "
        f"entity_id=? AND key=?", (market, entity_type, entity_id, key)))
    return rows[0] if rows else None


def effective(con: sqlite3.Connection, market: str, entity_type: str,
              entity_id: str, key: str) -> str | None:
    """The value in force: the one a human last confirmed (or a
    `confirm = none` key's value), None when nothing is. A pending value is
    never returned."""
    r = row(con, market, entity_type, entity_id, key)
    return None if r is None else r["confirmed_value"]


def _with_effective(r: dict) -> dict:
    return {**r, "effective": r["confirmed_value"]}


def pending(con: sqlite3.Connection, market: str, *,
            entity_type: str | None = None, entity_id: str | None = None,
            key: str | None = None) -> list[dict]:
    """Every value of `market` awaiting a human confirm (not confirmed, not
    withdrawn, not a `confirm = none` key), sorted; each row carries
    `effective`, the value in force meanwhile."""
    if not _has_table(con, TABLE):
        return []
    where, params = ["market=?", "status=?"], [market, PENDING]
    for col, val in (("entity_type", entity_type), ("entity_id", entity_id),
                     ("key", key)):
        if val is not None:
            where.append(f"{col}=?")
            params.append(val)
    return [_with_effective(r) for r in _dicts(con.execute(
        f"SELECT * FROM {TABLE} WHERE {' AND '.join(where)} "
        f"ORDER BY entity_type, entity_id, key", params))]


def last_change(con: sqlite3.Connection, market: str, entity_type: str,
                entity_id: str, keys: Iterable[str]) -> int:
    """The newest history id among `keys` of one entity (0 = none). Every
    gated write appends history, so a code bound to it is single use."""
    keys = list(dict.fromkeys(keys))
    if not keys or not _has_table(con, HISTORY):
        return 0
    return con.execute(
        f"SELECT COALESCE(MAX(id), 0) FROM {HISTORY} WHERE market=? AND "
        f"entity_type=? AND entity_id=? AND key IN "
        f"({','.join('?' * len(keys))})",
        (market, entity_type, entity_id, *keys)).fetchone()[0]


def set_history(con: sqlite3.Connection, market: str, entity_type: str,
                entity_id: str, key: str) -> list[str]:
    """Every value the decision was set to, newest first (its `set`
    history rows)."""
    if not _has_table(con, HISTORY):
        return []
    return [v for (v,) in con.execute(
        f"SELECT new_value FROM {HISTORY} WHERE market=? AND entity_type=? "
        f"AND entity_id=? AND key=? AND action=? ORDER BY id DESC",
        (market, entity_type, entity_id, key, SET))]


# ---- writers (inside the caller's keep_human_rows) -------------------------

def _history(con: sqlite3.Connection, market: str, entity_type: str,
             entity_id: str, key: str, action: str, old: str | None,
             new: str | None, status: str, reason: str, channel: str) -> None:
    con.execute(
        f"INSERT INTO {HISTORY} (market, entity_type, entity_id, key, action, "
        f"old_value, new_value, status, reason, changed_by, at) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (market, entity_type, entity_id, key, action, old, new, status, reason,
         human.changed_by(channel), human.now()))


def _channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"channel {channel!r} is not one of {CHANNELS}")
    return channel


def write_set(con: sqlite3.Connection, market: str, entity_type: str,
              entity_id: str, key: str, value: str, *, source: str,
              reason: str, channel: str, in_force: bool = False) -> None:
    """Write `value` as the decision's value, pending or (in_force) in force,
    and one `set` history row (old = the previous value). For hooks: call
    it inside the confirm's write, with the reason and channel it gave."""
    _channel(channel)
    old = row(con, market, entity_type, entity_id, key)
    status, now, who = (CONFIRMED if in_force else PENDING), human.now(), \
        human.changed_by(channel)
    in_force_value = value if in_force else (old or {}).get("confirmed_value")
    if old is None:
        con.execute(
            f"INSERT INTO {TABLE} (market, entity_type, entity_id, key, value, "
            f"status, confirmed_value, source, updated_at, changed_by) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?)",
            (market, entity_type, entity_id, key, value, status,
             in_force_value, source, now, who))
    else:
        con.execute(
            f"UPDATE {TABLE} SET value=?, status=?, confirmed_value=?, "
            f"source=?, updated_at=?, changed_by=? WHERE market=? AND "
            f"entity_type=? AND entity_id=? AND key=?",
            (value, status, in_force_value, source, now, who, market,
             entity_type, entity_id, key))
    _history(con, market, entity_type, entity_id, key, SET,
             old["value"] if old else None, value, status, reason, channel)


def write_confirm(con: sqlite3.Connection, market: str, entity_type: str,
                  entity_id: str, key: str, *, reason: str,
                  channel: str) -> None:
    """Put the decision's stored value in force and append its `confirm`
    history row. For hooks, like write_set."""
    _channel(channel)
    r = row(con, market, entity_type, entity_id, key)
    if r is None:
        raise ValueError(f"write_confirm: no decision {market} {entity_type} "
                         f"{entity_id} {key}")
    con.execute(
        f"UPDATE {TABLE} SET status=?, confirmed_value=value, updated_at=?, "
        f"changed_by=? WHERE market=? AND entity_type=? AND entity_id=? AND "
        f"key=?", (CONFIRMED, human.now(), human.changed_by(channel), market,
                   entity_type, entity_id, key))
    _history(con, market, entity_type, entity_id, key, CONFIRM, r["value"],
             r["value"], CONFIRMED, reason, channel)


def _write_withdraw(con: sqlite3.Connection, r: dict, reason: str) -> None:
    ident = (r["market"], r["entity_type"], r["entity_id"], r["key"])
    con.execute(f"UPDATE {TABLE} SET status=?, updated_at=?, changed_by=? "
                f"WHERE market=? AND entity_type=? AND entity_id=? AND key=?",
                (WITHDRAWN, human.now(), human.changed_by("cli"), *ident))
    _history(con, *ident, WITHDRAW, r["value"], r["confirmed_value"],
             WITHDRAWN, reason, "cli")


# ---- refusals --------------------------------------------------------------

def _refuse_harness_written(registry: DecisionRegistry, et: str,
                            key: str) -> None:
    if registry.harness_written(et, key):
        raise human.Refused(msg(
            "decision_harness_written",
            f"{et} {key} is written only by the harness, inside another "
            f"human confirm — it cannot be set or confirmed by itself",
            entity_type=et, key=key))


def _not_found(m: str, et: str, eid: str, key: str):
    return msg("decision_not_found", f"no decision {m} {et} {eid} {key}",
               market=m, entity_type=et, entity_id=eid, key=key)


def _check_entity_type(registry: DecisionRegistry, et: str | None) -> None:
    if et is not None and et not in registry.entity_types():
        raise HarnessError(msg(
            "decision_entity_type_unknown",
            f"unknown entity_type {et!r} "
            f"({', '.join(registry.entity_types())})",
            entity_type=et, entity_types=registry.entity_types()))


def _check_confirm_args(registry: DecisionRegistry, et: str,
                        eids: list[str], key: str, value: str | None) -> None:
    registry.check_key(et, key)
    _refuse_harness_written(registry, et, key)
    if value is not None and len(eids) != 1:
        raise human.Refused(msg("decision_value_one_id",
                                "--value confirms exactly one id"))


# ---- the confirm challenge -------------------------------------------------

def _follow(on_confirm: OnConfirm | None, con: sqlite3.Connection, m: str,
            et: str, eid: str, key: str, value: str, r: dict | None,
            changed: bool) -> Follow | None:
    if on_confirm is None:
        return None
    f = on_confirm(con, market=m, entity_type=et, entity_id=eid, key=key,
                   value=value, row=r, changed=changed)
    if f is None:
        return None
    if not isinstance(f, Follow) or key in f.bind:
        raise ValueError(f"on_confirm must return a Follow that does not "
                         f"re-bind {key!r}, got {f!r}")
    return f


def _version_keys(key: str, f: Follow | None) -> list[str]:
    return [key, *((*f.bind, *f.keys) if f else ())]


def _version(con: sqlite3.Connection, m: str, et: str, key: str,
             follows: Mapping[str, Follow | None]) -> int:
    return max((last_change(con, m, et, eid, _version_keys(key, f))
                for eid, f in follows.items()), default=0)


def plan_confirm(con: sqlite3.Connection, market: str, entity_type: str,
                 entity_ids: Iterable[str], key: str, *,
                 registry: DecisionRegistry, value: str | None = None,
                 on_confirm: OnConfirm | None = None) -> dict:
    """The challenge `confirm` runs for these ids, or its refusal.

    {"rows", "new", "already"} and, unless the one id is already confirmed
    with that value (already=True: nothing to do), {"follows", "items",
    "version", "subject", "what", "prompt", "expected"}. `subject` is what
    a relayed code is bound to (kit.human.subject); `expected` what a
    human retypes."""
    m, et = market, entity_type
    eids = list(dict.fromkeys(entity_ids))
    _check_confirm_args(registry, et, eids, key, value)
    typed = None
    if value is not None:
        registry.check_entity_id(et, eids[0])
        typed = registry.validate(et, key, value)
    rows = {eid: row(con, m, et, eid, key) for eid in eids}
    new = {e: typed if typed is not None else (r and r["value"])
           for e, r in rows.items()}
    done = [e for e, r in rows.items()
            if r and r["status"] == CONFIRMED and new[e] == r["value"]]
    if len(eids) == 1 and done:
        return {"rows": rows, "new": new, "already": True}
    bad = [_not_found(m, et, e, key) for e, r in rows.items()
           if r is None and typed is None]
    bad += [msg("decision_already_confirmed",
                f"{e} already confirmed ({rows[e]['value']!r})",
                entity_id=e, value=rows[e]["value"]) for e in done]
    bad += [msg("decision_withdrawn",
                f"{e}: its pending value was withdrawn — nothing to confirm "
                f"(`set` proposes again; a human may `confirm --value`)",
                entity_id=e)
            for e, r in rows.items()
            if r and typed is None and r["status"] == WITHDRAWN]
    if bad:
        if len(eids) == 1:
            raise human.Refused(bad[0])
        reasons = joined(bad)
        raise human.Refused(msg(
            "decision_confirm_refused",
            f"nothing confirmed ({len(bad)} of {len(eids)} ids cannot be): "
            f"{reasons}", refused=len(bad), total=len(eids), reasons=reasons))
    follows, items, lines = {}, {}, []
    for eid, r in rows.items():
        changed = r is None or new[eid] != r["value"]
        f = follows[eid] = _follow(on_confirm, con, m, et, eid, key, new[eid],
                                   r, changed)
        items[eid] = {key: new[eid], **(dict(f.bind) if f else {})}
        was = ("" if not changed else
               f" (replacing {r['value']})" if r else " (new)")
        also = f" {f.note}" if f and f.note else ""
        lines.append(f"{m} {et} {eid} {key} = {new[eid]}{was}{also}")
    version = _version(con, m, et, key, follows)
    what = f"`{config().cli} decisions confirm`"
    if len(eids) == 1:
        prompt = f"{lines[0]} — retype the value to confirm it: "
        expected = new[eids[0]]
    else:
        what += f" of {len(eids)} {et} {key} decisions"
        prompt = ("\n".join(f"  {ln}" for ln in lines)
                  + f"\n  type '{CONFIRM_WORD}' to confirm these "
                    f"{len(eids)} decisions: ")
        expected = CONFIRM_WORD
    return {"rows": rows, "new": new, "already": False, "follows": follows,
            "items": items, "version": version,
            "subject": human.subject("decisions confirm", m, et, items,
                                     version),
            "what": what, "prompt": prompt, "expected": expected}


# ---- verbs -----------------------------------------------------------------

def _cli() -> str:
    return config().cli


def _market_flag(m: str) -> str:
    return f" --market {m}" if config().markets else ""


def _confirm_hint(m: str, et: str, eid: str, key: str) -> str:
    return (f"{_cli()} decisions confirm {et} {eid} {key}{_market_flag(m)} "
            f"--reason <why>")


def _source(text: str | None) -> str:
    if not text or not text.strip():
        raise human.Refused(msg(
            "decision_source_required",
            "--source is required (convention: <who>_<status>_<YYYY-MM-DD>)"))
    return text.strip()


def cmd_set(con: sqlite3.Connection, a: argparse.Namespace, ctx: dict) -> Any:
    et, eid, key = a.entity_type, a.entity_id, a.key
    m, stored = ctx["market"], ctx["stored"]
    exempt = ctx["registry"].confirm_exempt(et, key)
    existing = row(con, m, et, eid, key)
    channel, why = "cli", ctx["why"]
    if ctx["set_guard"] is not None:
        channel = _channel(ctx["set_guard"](
            con, market=m, entity_type=et, entity_id=eid, key=key,
            value=stored, row=existing, code=a.code) or "cli")
        why += human.relay_audit(channel, a.relay_user, a.relay_at)
    status = "in force, no confirm needed" if exempt else "pending"
    with db.keep_human_rows(ctx["spec"], con):
        existing = row(con, m, et, eid, key)
        if existing is None or existing["value"] != stored \
                or existing["status"] == WITHDRAWN \
                or (exempt and existing["status"] != CONFIRMED):
            # a withdrawn value set again is a new proposal, same value or not
            write_set(con, m, et, eid, key, stored, source=ctx["source"],
                      reason=why, channel=channel, in_force=exempt)
            change = "new" if existing is None else "changed"
            text = (f"set {m} {et} {eid} {key} = {stored!r} (new, {status})"
                    if existing is None else
                    f"set {m} {et} {eid} {key}: {existing['value']!r} → "
                    f"{stored!r} ({status})")
        else:
            con.execute(f"UPDATE {TABLE} SET source=? WHERE market=? AND "
                        f"entity_type=? AND entity_id=? AND key=?",
                        (ctx["source"], m, et, eid, key))
            change = "unchanged"
            text = f"set {m} {et} {eid} {key}: value unchanged ({stored!r})"
    after = row(con, m, et, eid, key)
    waits = after["status"] == PENDING
    nxt = [_confirm_hint(m, et, eid, key)] if waits else []
    if a.json:
        return {"set": _with_effective(after), "change": change,
                "awaits_confirm": waits, "next": nxt}
    print(text)
    for n in nxt:
        print(f"  in force only after a human runs: {n}")
    return None


def cmd_confirm(con: sqlite3.Connection, a: argparse.Namespace,
                ctx: dict) -> Any:
    m, et, key, spec = ctx["market"], a.entity_type, a.key, ctx["spec"]
    plan = plan_confirm(con, m, et, a.entity_id, key,
                        registry=ctx["registry"], value=a.value,
                        on_confirm=ctx["on_confirm"])
    rows, new = plan["rows"], plan["new"]
    if plan["already"]:
        (eid, r), = rows.items()
        if a.json:
            return {"confirmed": [], "recorded": [],
                    "already_confirmed": [_with_effective(r)]}
        print(f"{m} {et} {eid} {key}: already confirmed ({r['value']!r})")
        return None
    channel = human.confirm(plan["what"], plan["prompt"], plan["expected"],
                            subj=plan["subject"], code=a.code)
    why = ctx["why"] + human.relay_audit(channel, a.relay_user, a.relay_at)
    follows, extras = plan["follows"], {}
    with db.keep_human_rows(spec, con):
        if _version(con, m, et, key, follows) != plan["version"]:
            raise human.Refused(msg(
                "decision_changed_meanwhile",
                f"{m} {et} {key}: the decision changed while the confirm "
                f"waited for its answer — nothing was written; rerun to see "
                f"the current value", market=m, entity_type=et, key=key),
                [shlex.join(ctx["cmd"])])
        for eid, r in rows.items():
            v = new[eid]
            if r is None or v != r["value"]:
                write_set(con, m, et, eid, key, v,
                          source=human.typed_source(), reason=why,
                          channel=channel, in_force=True)
            write_confirm(con, m, et, eid, key, reason=why, channel=channel)
            f = follows[eid]
            if f and f.apply:
                extras[eid] = list(f.apply(con, reason=why, channel=channel)
                                   or [])
    if a.json:
        def listed(attr: str, base: tuple[str, ...]) -> list[dict]:
            return [_with_effective(r2) for eid, f in follows.items()
                    for k in dict.fromkeys((*base, *(getattr(f, attr)
                                                     if f else ())))
                    if (r2 := row(con, m, et, eid, k)) is not None]
        return {"confirmed": listed("confirmed", (key,)),
                "recorded": listed("recorded", ()),
                "changed_by": human.changed_by(channel), "reason": why}
    for eid, r in rows.items():
        was = "" if r and r["value"] == new[eid] else \
            f"{(r and r['value'])!r} → "
        print(f"{m} {et} {eid} {key}: {was}{new[eid]!r} confirmed (in force)")
        for line in extras.get(eid, []):
            print(f"  {line}")
    return None


def cmd_withdraw(con: sqlite3.Connection, a: argparse.Namespace,
                 ctx: dict) -> Any:
    m, et, key = ctx["market"], a.entity_type, a.key
    eids = list(dict.fromkeys(a.entity_id))
    with db.keep_human_rows(ctx["spec"], con):    # read under the write lock
        rows = {eid: row(con, m, et, eid, key) for eid in eids}
        bad = [e for e, r in rows.items()
               if r is None or r["status"] != PENDING]
        if bad:
            raise human.Refused(msg(
                "decision_nothing_pending",
                f"nothing withdrawn: no pending {m} {et} {key} for "
                f"{', '.join(bad)} ({len(bad)} of {len(eids)} ids) — only a "
                f"value awaiting a confirm can be withdrawn", market=m,
                entity_type=et, key=key, entity_ids=bad, refused=len(bad),
                total=len(eids)))
        for r in rows.values():
            _write_withdraw(con, r, ctx["why"])
    out = [_with_effective(row(con, m, et, eid, key)) for eid in eids]
    if a.json:
        return {"withdrawn": out, "changed_by": human.changed_by("cli"),
                "reason": ctx["why"]}
    for r in out:
        print(f"{m} {et} {r['entity_id']} {key}: pending {r['value']!r} "
              f"withdrawn (in force: {r['effective']!r})")
    return None


def cmd_get(con: sqlite3.Connection, a: argparse.Namespace, ctx: dict) -> Any:
    m = ctx["market"]
    r = row(con, m, a.entity_type, a.entity_id, a.key)
    if r is None:
        raise HarnessError(_not_found(m, a.entity_type, a.entity_id, a.key))
    r = _with_effective(r)
    if a.json:
        return r
    for col, v in r.items():
        print(f"{col:<16} {v}")
    return None


def _filtered(con: sqlite3.Connection, table: str, filters: dict,
              order: str) -> list[dict]:
    if not _has_table(con, table):
        return []
    where = [f"{c}=?" for c, v in filters.items() if v is not None]
    params = [v for v in filters.values() if v is not None]
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _dicts(con.execute(f"{sql} ORDER BY {order}", params))


def cmd_list(con: sqlite3.Connection, a: argparse.Namespace, ctx: dict) -> Any:
    rows = [_with_effective(r) for r in _filtered(
        con, TABLE, {"market": ctx["market"], "entity_type": a.entity_type,
                     "entity_id": a.entity_id, "key": a.key},
        "market, entity_type, entity_id, key")
        if not a.pending or r["status"] == PENDING]
    if a.json:
        return {"decisions": rows}
    print(f"{len(rows)} decision(s)")
    for r in rows:
        mark = {PENDING: "*", WITHDRAWN: "-"}.get(r["status"], " ")
        eff = "" if r["effective"] == r["value"] else \
            f"  (in force: {r['effective']})"
        print(f" {mark} {r['market']:<4} {r['entity_type']:<10} "
              f"{r['entity_id']:<14} {r['key']:<24} {r['value']!s:<14} "
              f"src={r['source']} updated={r['updated_at']}{eff}")
    print(f"(* = pending — a human runs `{_cli()} decisions confirm <type> "
          f"<id>... <key>{_market_flag('<M>')} --reason <why>`; "
          f"- = withdrawn)")
    return None


def cmd_history(con: sqlite3.Connection, a: argparse.Namespace,
                ctx: dict) -> Any:
    rows = _filtered(con, HISTORY, {
        "market": ctx["market"], "entity_type": a.entity_type,
        "entity_id": a.entity_id, "key": a.key}, "id")
    if a.json:
        return {"history": rows}
    print(f"{len(rows)} history row(s)")
    for r in rows:
        print(f"  #{r['id']:<4} {r['at']}  {r['action']:<8} {r['market']} "
              f"{r['entity_type']} {r['entity_id']} {r['key']}: "
              f"{r['old_value']!s} → {r['new_value']!s} "
              f"by={r['changed_by']}  why={r['reason']}")
    return None


# ---- entry -----------------------------------------------------------------

def _parser(registry: DecisionRegistry) -> argparse.ArgumentParser:
    cli = _cli()
    p = argparse.ArgumentParser(
        prog=f"{cli} decisions",
        description="Human decisions about one entity: set proposes "
                    "(pending), only a human confirm puts a value in force.",
        epilog=f"Registry: {registry.path}. This verb is the only writer of "
               f"the decisions tables.")
    sub = p.add_subparsers(dest="verb", required=True)
    market_help = "the market; omit when exactly one is declared"

    def target(sp: argparse.ArgumentParser, many: bool = False) -> None:
        sp.add_argument("entity_type",
                        help=f"one of {', '.join(registry.entity_types())}")
        sp.add_argument("entity_id", nargs="+" if many else None,
                        help="the entity's id" + ("; several ids of this "
                        "type take the same key together, all or nothing"
                        if many else ""))
        sp.add_argument("key", help="a key the decision registry lists")
        sp.add_argument("--market", help=market_help)
        add_json_arg(sp)

    def filters(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--entity-type")
        sp.add_argument("--entity-id")
        sp.add_argument("--key")
        add_json_arg(sp)

    sp = sub.add_parser("set", help="write a PENDING value (anyone; a "
                        "`confirm = none` key is in force at once)")
    target(sp)
    sp.add_argument("value")
    sp.add_argument("--source", help="<who>_<status>_<YYYY-MM-DD> (required)")
    human.add_gate_args(sp)
    sp = sub.add_parser("confirm", help="put the pending value(s) in force: "
                        "a human retypes at the terminal, or relays a code")
    target(sp, many=True)
    sp.add_argument("--value", help="exactly one id: the value the human "
                    "confirms instead of the pending one (or with none)")
    human.add_gate_args(sp)
    sp = sub.add_parser("withdraw", help="take back PENDING values (anyone: "
                        "it only lowers trust); all or nothing")
    target(sp, many=True)
    sp.add_argument("--reason", help="why (required; kept in history)")
    sp = sub.add_parser("get", help="one decision, with the value in force")
    target(sp)
    sp = sub.add_parser("list", help="decisions (* = pending, - = withdrawn)")
    filters(sp)
    sp.add_argument("--market", help="one market; omit for all")
    sp.add_argument("--pending", action="store_true",
                    help="only values awaiting a human confirm")
    sp = sub.add_parser("history", help="the append-only change log")
    filters(sp)
    sp.add_argument("--market", help=market_help)
    return p


VERBS = {"set": cmd_set, "confirm": cmd_confirm, "withdraw": cmd_withdraw,
         "get": cmd_get, "list": cmd_list, "history": cmd_history}


def _run(argv: list[str], *, spec: SchemaSpec, registry: DecisionRegistry,
         on_confirm: OnConfirm | None, set_guard: SetGuard | None,
         cmd: list[str]) -> Any:
    a = _parser(registry).parse_args(argv)
    ctx: dict = {"spec": spec, "registry": registry, "on_confirm": on_confirm,
                 "set_guard": set_guard, "cmd": cmd}
    # what needs no DB is refused before one is opened (or created)
    if a.verb in ("set", "confirm", "withdraw", "get"):
        registry.check_key(a.entity_type, a.key)
    if a.verb in ("list", "history"):
        _check_entity_type(registry, a.entity_type)
    if a.verb == "set":
        _refuse_harness_written(registry, a.entity_type, a.key)
        registry.check_entity_id(a.entity_type, a.entity_id)
        ctx["source"] = _source(a.source)
    if a.verb == "confirm":
        _check_confirm_args(registry, a.entity_type, a.entity_id, a.key,
                            a.value)
    if a.verb in ("set", "confirm", "withdraw"):
        ctx["why"] = human.why(a.reason)
    if a.verb == "set":
        ctx["stored"] = registry.validate(a.entity_type, a.key, a.value)
    con = db.connect(spec, read_only=a.verb in READS)
    try:
        if a.verb == "list":
            ctx["market"] = a.market and mk.validate(a.market)
        else:
            ctx["market"] = mk.resolve(con, a.market,
                                       must_be_declared=a.verb not in READS,
                                       cmd=cmd)
        return VERBS[a.verb](con, a, ctx)
    finally:
        con.close()


def main(argv: list[str] | None = None, *, spec: SchemaSpec,
         registry: DecisionRegistry, on_confirm: OnConfirm | None = None,
         set_guard: SetGuard | None = None) -> int:
    """`<cli> decisions set|confirm|withdraw|get|list|history …`; the exit
    code (0, 2 for a named refusal, 1 otherwise)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = [_cli(), "decisions", *argv]
    return run_main(lambda av: _run(av, spec=spec, registry=registry,
                                    on_confirm=on_confirm,
                                    set_guard=set_guard, cmd=cmd),
                    argv, cmd=cmd)
