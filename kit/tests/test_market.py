#!/usr/bin/env python3
"""The market door (kit/market.py) holds each of its guards.

  1. validate(): the closed set of [harness].markets, matched
     case-insensitively and returned in its declared spelling; anything
     else is market_not_a_market with the valid set.
  2. declared(): only CONFIRMED market_declared facts count (a pending one
     only with include_pending), limited to the closed set; a DB without
     client_facts, or no DB, has declared nothing.
  3. resolve(): explicit = validated and declared (must_be_declared=False:
     validated only); omitted = the only declared one; none =
     market_none_declared, several = market_ambiguous, each with `next`
     (the same command rerun with each --market when `cmd` is given).
  4. require_declared(): a write to an undeclared market is refused.
  5. A harness with markets = [] keeps one pseudo-market "_": every
     function returns or accepts it, and refuses any other.
  6. market.py's msg() calls are closed over its fragment; failures go
     through kit.contract as one coded document.
"""

import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, contract, market, messages  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

FACTS = ("CREATE TABLE client_facts (market TEXT NOT NULL, key TEXT NOT NULL, "
         "value TEXT, is_assumption INTEGER NOT NULL DEFAULT 1, source TEXT, "
         "updated_at TEXT, changed_by TEXT, PRIMARY KEY (market, key))")


def db(*rows: tuple[str, str, int]) -> sqlite3.Connection:
    """An in-memory DB with client_facts (schema_base's columns) holding
    (market, key, is_assumption) rows."""
    con = sqlite3.connect(":memory:")
    con.execute(FACTS)
    con.executemany("INSERT INTO client_facts (market, key, value, "
                    "is_assumption, source) VALUES (?, ?, ?, ?, 'test')",
                    [(m, k, m, a) for m, k, a in rows])
    return con


def refusal(fn) -> dict | None:
    e = raises(fn, HarnessError)
    if e is None:
        return None
    return {**messages.code(e.message), "next": e.next}


def test_validate() -> None:
    print("[1] validate: the closed set")
    check("the declared spelling comes back, whatever the case or spaces",
          [market.validate(x) for x in ("US", "us", " Ca ")] == ["US", "US",
                                                                 "CA"])
    for bad in ("GB", "", None, "U S", "_"):
        r = refusal(lambda: market.validate(bad))
        check(f"{bad!r}: market_not_a_market with the valid set",
              r == {"code": "market_not_a_market",
                    "params": {"market": bad, "markets": ["US", "CA"]},
                    "next": []}, r)


def test_declared() -> None:
    print("[2] declared: confirmed declarations only")
    con = db(("US", market.MARKET_KEY, 0), ("CA", market.MARKET_KEY, 1),
             ("GB", market.MARKET_KEY, 0), ("US", "unit_cost", 0))
    check("a confirmed declaration counts, a pending one does not, one "
          "outside the closed set never does",
          market.declared(con) == ["US"], market.declared(con))
    check("include_pending lists the pending declaration too",
          market.declared(con, include_pending=True) == ["CA", "US"],
          market.declared(con, include_pending=True))
    check("a DB without client_facts, or none at all, declared nothing",
          market.declared(sqlite3.connect(":memory:")) == []
          and market.declared(None) == [])


def test_resolve() -> None:
    print("[3] resolve / [4] require_declared")
    one = db(("US", market.MARKET_KEY, 0), ("CA", market.MARKET_KEY, 1))
    both = db(("US", market.MARKET_KEY, 0), ("CA", market.MARKET_KEY, 0))
    none = db(("US", market.MARKET_KEY, 1))
    check("omitted: the only declared market", market.resolve(one, None) == "US")
    check("explicit, declared: validated and returned canonical",
          market.resolve(both, "ca") == "CA")
    r = refusal(lambda: market.resolve(one, "ca"))
    check("explicit but not declared (pending only): market_not_onboarded, "
          "next = facts init",
          r == {"code": "market_not_onboarded", "params": {"market": "CA"},
                "next": ["shop facts init"]}, r)
    check("must_be_declared=False: validated only (a read may show it)",
          market.resolve(one, "ca", must_be_declared=False) == "CA")
    r = refusal(lambda: market.resolve(one, "GB", must_be_declared=False))
    check("… and still refuses a market outside the set",
          r and r["code"] == "market_not_a_market", r)
    r = refusal(lambda: market.resolve(none, None))
    check("none declared: market_none_declared, next = facts init",
          r == {"code": "market_none_declared", "params": {},
                "next": ["shop facts init"]}, r)
    r = refusal(lambda: market.resolve(both, None))
    check("several declared: market_ambiguous naming them",
          r == {"code": "market_ambiguous", "params": {"markets": ["CA", "US"]},
                "next": ["shop facts list --market CA",
                         "shop facts list --market US"]}, r)
    r = refusal(lambda: market.resolve(
        both, None, cmd=["shop", "compute", "margin", "--json"]))
    check("… with cmd: next reruns that command with each --market",
          r and r["next"] == ["shop compute margin --json --market CA",
                              "shop compute margin --json --market US"], r)

    check("require_declared: a declared market passes",
          raises(lambda: market.require_declared(one, "US"), HarnessError)
          is None)
    r = refusal(lambda: market.require_declared(one, "CA"))
    check("require_declared: a pending declaration is not onboarded",
          r and r["code"] == "market_not_onboarded", r)
    doc = contract.failure_doc(raises(lambda: market.resolve(none, None),
                                      HarnessError), ["shop", "x"])
    check("a market refusal renders as one coded failure document",
          doc == {"error": doc["error"], "next": ["shop facts init"],
                  "code": "market_none_declared", "params": {}}, doc)


def test_no_partition() -> None:
    print("[5] markets = []: the one pseudo-market")
    d = Path(tmp_dir("flat-"))
    (d / "harness.toml").write_text(
        '[harness]\nname = "flat"\ncli = "flat"\nenv_prefix = "FLAT"\n'
        'markets = []\n', encoding="utf-8")
    config.use(d)
    try:
        con = db(("US", market.MARKET_KEY, 0))
        check("declared() is ['_'] whatever the DB holds",
              market.declared(con) == ["_"] and market.declared(None) == ["_"])
        check("resolve(None) and resolve('_') are '_'; validate('_') too",
              market.resolve(con, None) == "_" and market.resolve(con, "_")
              == "_" and market.validate("_") == "_")
        check("require_declared('_') passes",
              raises(lambda: market.require_declared(None, "_"),
                     HarnessError) is None)
        for bad in ("US", None, ""):
            r = refusal(lambda: market.validate(bad))
            check(f"validate({bad!r}): market_unpartitioned",
                  r == {"code": "market_unpartitioned",
                        "params": {"market": bad}, "next": []}, r)
        r = refusal(lambda: market.resolve(con, "US"))
        check("an explicit market is refused the same way by resolve",
              r and r["code"] == "market_unpartitioned", r)
        r = refusal(lambda: market.require_declared(con, "US"))
        check("… and by require_declared",
              r and r["code"] == "market_unpartitioned", r)
    finally:
        _shop.use()


def test_closure() -> None:
    print("[6] market.py keeps its registry fragment closed")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "market.tsv"}
    probs = messages.check_registry_closed(_shop.KIT, ["market.py"], mine,
                                           strict_kit=True)
    check("every msg() in market.py is literal, registered with exact "
          "params, and every market.tsv code is emitted", probs == [], probs)
    check("the reference names are kept (market_not_a_store renamed "
          "market_not_a_market per the SPEC)",
          {"market_none_declared", "market_ambiguous",
           "market_not_onboarded", "market_not_a_market"} <= set(mine))


def test_puller_safe() -> None:
    print("[6] a puller may import kit.market (it reaches no database)")
    import subprocess
    r = subprocess.run([sys.executable, "-c",
                        "import sys; import kit.market; "
                        "print(sorted(m for m in ('sqlite3', 'kit.db') "
                        "if m in sys.modules))"],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(_shop.PLAYBOOK),
                       env={"PYTHONPATH": str(_shop.PLAYBOOK),
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "PATH": "/usr/bin:/bin"})
    check("importing kit.market loads neither sqlite3 nor kit.db",
          r.returncode == 0 and r.stdout.strip() == "[]", (r.stdout, r.stderr))
    from kit.guards import layering
    import shutil
    root = Path(tmp_dir("market-pull-")) / "shop"
    shutil.copytree(_shop.SHOP, root, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"))
    (root / "scripts" / "pull_orders.py").write_text(
        "from kit import market\n\n\ndef scope(m):\n"
        "    return market.validate(m)\n", encoding="utf-8")
    probs = [p for p in layering.check_layers(root) if "pull_orders" in p]
    check("the layering guard: a pull calling market.validate() is clean",
          probs == [], probs)
    (root / "scripts" / "pull_orders.py").write_text(
        "from kit import market, db\n", encoding="utf-8")
    probs = [p for p in layering.check_layers(root) if "pull_orders" in p]
    check("…and one importing kit.db is still caught", any(
        p.startswith("b: pull_orders") for p in probs), probs)


def main() -> int:
    _shop.use()
    for fn in (test_validate, test_declared, test_resolve, test_no_partition,
               test_puller_safe, test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
