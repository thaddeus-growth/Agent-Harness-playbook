"""Which market a verb acts on, and the declaration door.

Ported from the reference harness's `_lib/market_gate.py`. A market is
always named, never assumed:

  * validate()          an explicit market must be one of the closed set
                        `[harness].markets` (matched case-insensitively,
                        returned in its declared spelling, so `us` and `US`
                        never become two partitions); anything else is
                        market_not_a_market.
  * declared()          the markets this DB has a CONFIRMED `market_declared`
                        client fact for (a pending one, written by `facts
                        init`, is not a declaration until a human confirms
                        it), limited to the closed set.
  * resolve()           explicit: validated and declared; omitted: the only
                        declared market; none = market_none_declared,
                        several = market_ambiguous, each with `next`.
  * require_declared()  the single door: a write to a market with no
                        confirmed declaration is refused (market_not_onboarded),
                        which makes "recorded into the wrong partition"
                        unreachable rather than merely discouraged.

A harness with `markets = []` keeps no partition: every function returns
or accepts the one pseudo-market "_" (declared() is ["_"] whatever the DB
holds), and an explicit market other than "_" is refused
(market_unpartitioned), the same way everywhere.

Reads use plain SQL against schema_base's client_facts(market, key,
value, is_assumption, …); a DB without that table has declared nothing.

Additions to the SPEC: declared(con, include_pending=True) also lists
pending declarations (facts init / doctor need them), and resolve(…,
must_be_declared=False) validates only, for reads that may show an
undeclared market's rows (the reference's `get --market`); `cmd=` makes
market_ambiguous's `next` the same command rerun with each --market.

No database module is imported: the functions that read one take the
connection they are given, so a puller may call validate() and still
reach no database (kit.guards.layering, rule b).

Test: kit/tests/test_market.py.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from kit.config import config
from kit.contract import HarnessError
from kit.messages import msg

if TYPE_CHECKING:          # a type only: a puller imports validate() and
    import sqlite3         # must not reach the database (the layering rule)

MARKET_KEY = "market_declared"   # the client fact whose confirmed row declares a market
PSEUDO = "_"                     # the one market of a harness with no partition


def _markets() -> tuple[str, ...]:
    return config().markets


def _init_next() -> list[str]:
    return [f"{config().cli} facts init"]


def validate(code: str | None) -> str:
    """The canonical spelling of `code` in the closed set, else
    HarnessError(market_not_a_market) (market_unpartitioned when the
    harness keeps no partition and `code` is not "_")."""
    markets = _markets()
    text = (code or "").strip()
    if not markets:
        if text == PSEUDO:
            return PSEUDO
        raise HarnessError(msg(
            "market_unpartitioned",
            f"{code!r}: this harness keeps no market partition — omit "
            f"--market (its one market is {PSEUDO!r})", market=code))
    canon = {m.casefold(): m for m in markets}.get(text.casefold())
    if canon is None:
        raise HarnessError(msg(
            "market_not_a_market",
            f"{code!r} is not a market of this harness. Valid: "
            f"{', '.join(markets)} (the set is closed: [harness].markets "
            f"in harness.toml)", market=code, markets=list(markets)))
    return canon


def _has_facts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='client_facts'").fetchone() is not None


def declared(con: sqlite3.Connection | None, *,
             include_pending: bool = False) -> list[str]:
    """The declared markets, sorted: those with a confirmed MARKET_KEY fact
    (with `include_pending`, a pending one counts too), limited to the
    closed set. ["_"] for a harness with no partition."""
    markets = _markets()
    if not markets:
        return [PSEUDO]
    if con is None or not _has_facts(con):
        return []
    sql = "SELECT DISTINCT market FROM client_facts WHERE key=?"
    if not include_pending:
        sql += " AND is_assumption=0"
    rows = {r[0] for r in con.execute(sql + " ORDER BY market", (MARKET_KEY,))}
    return [m for m in sorted(rows) if m in markets]


def require_declared(con: sqlite3.Connection | None, market: str) -> None:
    """Refuse a write to a market with no confirmed declaration."""
    if not _markets():
        validate(market)            # "_" passes; anything else: unpartitioned
        return
    if market not in declared(con):
        raise HarnessError(msg(
            "market_not_onboarded",
            f"market {market!r} is not onboarded — no confirmed "
            f"`{MARKET_KEY}` fact for it. Run `{config().cli} facts init` "
            f"first, then have a human confirm the declaration.",
            market=market), _init_next())


def resolve(con: sqlite3.Connection | None, explicit: str | None, *,
            must_be_declared: bool = True,
            cmd: list[str] | None = None) -> str:
    """The market a verb acts on. Explicit wins (validated, and declared
    unless `must_be_declared=False`); omitted, it is read from the DB's own
    declarations, never from a constant: exactly one = that one."""
    if explicit:
        m = validate(explicit)
        if must_be_declared:
            require_declared(con, m)
        return m
    ms = declared(con)
    if len(ms) == 1:
        return ms[0]
    cli = config().cli
    if not ms:
        raise HarnessError(msg(
            "market_none_declared",
            f"no market is declared in this DB — nothing is onboarded yet. "
            f"Run `{cli} facts init` to declare one."), _init_next())
    nxt = ([shlex.join([*cmd, "--market", m]) for m in ms] if cmd
           else [f"{cli} facts list --market {m}" for m in ms])
    raise HarnessError(msg(
        "market_ambiguous",
        f"{len(ms)} markets declared ({', '.join(ms)}) — pass --market to "
        f"say which.", markets=ms), nxt)
