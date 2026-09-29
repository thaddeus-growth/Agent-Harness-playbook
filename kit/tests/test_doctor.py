#!/usr/bin/env python3
"""kit.doctor: the built-in checks, the harness's own, and the exit rules,
on the fake "shop" harness.

  [1] a fresh install: every built-in line; no DB is info, no market warns;
      "writes: off" is ok; a secret's value is never shown
  [2] the write switches: off (ok), kill env and STOP file (ok, off), ON
      (info), a value that is not 1 (warn)
  [3] the data dir: unset, relative, inside the checkout (warn, with the
      fix); the token source; KIT_TTY outside a test
  [4] env files: a loose file warns (chmod 600), a 600 one does not; a
      secret or the data dir from the HOME-level file warns
  [5] the DB: current (ok), stale, unknown tables, newer than the harness,
      unreadable; opened read-only (nothing changes, nothing is created)
  [6] markets: pending only (warn + the confirm command), confirmed (ok),
      `markets = []` (ok)
  [7] hooks: the platform lock (set / unknown / none), a verb whose script
      is missing, the harness's own checks (warn, fail, --live), a check
      that crashes or reports a plain str
  [8] exit: 0 with warnings, 1 with --strict, 1 on a failure; --json is one
      document whose every message is coded
"""

import os
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import _shop  # noqa: E402
from kit import config, db, doctor, facts  # noqa: E402
from kit.guards import json_contract  # noqa: E402
from kit.messages import msg  # noqa: E402
from kit.registry import FactKeys, Thresholds  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, tmp_dir)
from kit.verbs import Verb, load  # noqa: E402

CFG = _shop.use()
SCRIPTS = _shop.SHOP / "scripts"
SPEC = db.with_human({
    "stock": {"columns": {"market": "TEXT", "sku": "TEXT", "units": "INTEGER"},
              "pk": ["market", "sku"], "doc": "units on hand per product"},
}, version=1)
VERBS = load(SCRIPTS / "verbs.py")


def env(data: Path | str | None, **extra: str) -> dict:
    e = clean_env()
    if data is not None:
        e[CFG.env("DATA_DIR")] = str(data)
    e[CFG.env("AUTH_ENV_PATHS")] = "none"
    e.update(extra)
    return e


def run(data, *argv, checks=(), lock=None, verbs=VERBS, spec=SPEC,
        root=None, load_chain=True, **extra):
    """doctor.main in this process, after the env chain is loaded the way
    the dispatcher loads it: (rc, out, err, document-or-None)."""
    config.use(root or _shop.SHOP)

    def go(argv):
        if load_chain:
            from kit import env as kenv
            kenv.chain().load_into_environ()
        return doctor.main(argv, checks=checks, spec=spec, verbs=verbs,
                           scripts_dir=SCRIPTS, platform_lock=lock)
    try:
        rc, out, err = capture(go, list(argv), env=env(data, **extra))
    finally:
        _shop.use()
    return rc, out, err, one_doc(out)


def found(doc: dict, check_id: str, level: str | None = None) -> list[dict]:
    return [c for c in doc["checks"] if c["check"] == check_id
            and (level is None or c["level"] == level)]


def codes(doc: dict, check_id: str) -> list[str]:
    return [c["message_code"]["code"] for c in found(doc, check_id)]


def init(data: Path, market: str = "US") -> None:
    config.use(_shop.SHOP)
    rc, out, err = capture(
        lambda a: facts.main(a, spec=SPEC, keys=FactKeys(),
                             thresholds=Thresholds()),
        ["init", "--json"], env=env(data), stdin=f"{market}\n\n")
    assert rc == 0, (out, err)


# ------------------------------------------------------------------ [1]

def test_fresh() -> None:
    print("[1] a fresh install")
    data = Path(tmp_dir("doc-fresh-"))
    rc, out, err, doc = run(data, "--json",
                            **{CFG.env("CONFIRM_CODE_SECRET"): "hunter2x"})
    check("one document, exit 0 (warnings are not fatal by default)",
          rc == 0 and doc is not None and not err, (rc, out[:200], err))
    ids = [c["check"] for c in doc["checks"]]
    check("the built-in checks, in order",
          [i for i in dict.fromkeys(ids)] == ["harness", "env_chain",
                                             "token_source", "data_dir", "db",
                                             "markets", "writes", "verbs"],
          ids)
    check("the harness line", codes(doc, "harness") == ["doctor_harness"]
          and doc["harness"]["name"] == "shop-harness")
    check("process env only, each harness variable with its source",
          codes(doc, "env_chain")[0] == "doctor_env_process_only"
          and "SHOP_DATA_DIR = " + str(data) + " [process env]"
          in [c["message"] for c in found(doc, "env_chain")])
    check("a secret's value is never shown, only that it is set",
          "hunter2x" not in out and "SHOP_CONFIRM_CODE_SECRET = set "
          "[process env]" in out)
    check("no DB yet: info with the command that makes one",
          found(doc, "db", "info") and found(doc, "db")[0]["next"]
          == ["shop facts init"] and not data.joinpath("shop.db").exists())
    check("no market declared: a warning naming facts init",
          codes(doc, "markets") == ["doctor_not_onboarded"]
          and found(doc, "markets", "warn"))
    w = found(doc, "writes")
    check("writes: off is the healthy default (ok, not a warning)",
          [c["level"] for c in w] == ["ok"]
          and w[0]["message_code"]["code"] == "doctor_writes_off"
          and w[0]["message_code"]["params"]["reason"]["code"] == "write_off")
    check("every verb's script is present",
          codes(doc, "verbs") == ["doctor_verbs_ok"])
    check("summary and healthy", doc["summary"]["warn"] == 1
          and doc["healthy"] is True and doc["message_code"]["code"]
          == "doctor_summary_warnings")
    check("every message coded (the json contract's own walker)",
          json_contract.uncoded(doc) == [], json_contract.uncoded(doc))
    rc, out, err, _ = run(data)
    check("text: a line per finding, WARN marked, next under it, the summary",
          rc == 0 and "=== shop doctor ===" in out and "  WARN  no market "
          "declared" in out and "next: shop facts init" in out
          and out.rstrip().endswith("each names its fix above"), out)


# ------------------------------------------------------------------ [2]

def test_writes() -> None:
    print("\n[2] the write switches")
    data = Path(tmp_dir("doc-writes-"))
    on = CFG.env("ALLOW_WRITES")
    _, _, _, doc = run(data, "--json", **{on: "1"})
    check("writes ON: info, with how to turn them off",
          [c["level"] for c in found(doc, "writes")] == ["info"]
          and found(doc, "writes")[0]["next"] == [f"unset {on}"])
    _, _, _, doc = run(data, "--json", **{on: "true"})
    check("a value that is not 1: a warning (it enables nothing)",
          codes(doc, "writes") == ["doctor_writes_bad_value"]
          and found(doc, "writes", "warn"))
    _, _, _, doc = run(data, "--json", **{on: "1", CFG.env("KILL"): "1"})
    w = found(doc, "writes")
    check("the kill switch: writes off, ok, the reason named",
          [c["level"] for c in w] == ["ok"]
          and w[0]["message_code"]["params"]["reason"]["code"]
          == "write_killed")
    (data / "STOP").write_text("", encoding="utf-8")
    _, _, _, doc = run(data, "--json", **{on: "1"})
    check("the STOP file: writes off, ok",
          found(doc, "writes")[0]["message_code"]["params"]["reason"]["code"]
          == "write_stop_file" and found(doc, "writes", "ok"))


# ------------------------------------------------------------------ [3]

def test_paths() -> None:
    print("\n[3] the data dir, the token source, KIT_TTY")
    _, _, _, doc = run(None, "--json")
    d = found(doc, "data_dir")
    check("no data dir: a warning, data_dir_unset, with the export",
          [c["message_code"]["code"] for c in d] == ["data_dir_unset"]
          and d[0]["level"] == "warn"
          and d[0]["next"][0].startswith("export SHOP_DATA_DIR="))
    check("and the DB is not looked for", found(doc, "db") == [])
    _, _, _, doc = run("rel/data", "--json")
    check("a relative data dir: data_dir_not_absolute",
          codes(doc, "data_dir") == ["data_dir_not_absolute"])
    inside = _shop.SHOP / "scripts"
    _, _, _, doc = run(inside, "--json")
    check("a data dir inside the checkout: a warning per path",
          codes(doc, "data_dir").count("doctor_inside_repo") == 2
          and found(doc, "data_dir", "warn"))
    _, _, _, doc = run(Path(tmp_dir("doc-t-")), "--json",
                       **{CFG.env("TOKEN_SOURCE"): "vault"})
    check("an unknown token source: a warning (auth_bad_token_source)",
          codes(doc, "token_source") == ["auth_bad_token_source"])
    _, _, _, doc = run(Path(tmp_dir("doc-t-")), "--json",
                       **{CFG.env("TOKEN_SOURCE"): "command"})
    check("token source command: ok", codes(doc, "token_source")
          == ["doctor_token_source"] and found(doc, "token_source", "ok"))
    _, _, _, doc = run(Path(tmp_dir("doc-t-")), "--json", KIT_TTY="/x/tty")
    check("KIT_TTY outside a test: a warning", codes(doc, "kit_tty")
          == ["doctor_kit_tty"])


# ------------------------------------------------------------------ [4]

def home_harness(home: Path) -> Path:
    root = Path(tmp_dir("doc-home-harness-")) / "shop"
    shutil.copytree(_shop.SHOP, root, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"))
    toml = root / "harness.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'home_env_file = ""', f'home_env_file = "{home / ".shop.env"}"'),
        encoding="utf-8")
    return root


def test_env_files() -> None:
    print("\n[4] env files")
    data = Path(tmp_dir("doc-env-"))
    f = data / ".env"
    f.write_text("SHOP_API_KEY=k\n", encoding="utf-8")
    f.chmod(0o644)
    paths_var = CFG.env("AUTH_ENV_PATHS")
    _, _, _, doc = run(data, "--json", **{paths_var: str(f)})
    lf = found(doc, "env_files")
    check("a chain file others can read: a warning with chmod 600",
          [c["message_code"]["code"] for c in lf] == ["doctor_env_file_loose"]
          and lf[0]["next"] == [f"chmod 600 {f}"])
    check("the chain is listed with its file",
          codes(doc, "env_chain")[0] == "doctor_env_chain"
          and str(f) in found(doc, "env_chain")[0]["message"])
    f.chmod(0o600)
    _, _, _, doc = run(data, "--json", **{paths_var: str(f)})
    check("a 600 file: no warning", found(doc, "env_files") == [])

    home = Path(tmp_dir("doc-home-"))
    (home / ".shop.env").write_text(
        f"SHOP_DATA_DIR={data}\nSHOP_API_TOKEN=t0ken\nSHOP_MODE=x\n",
        encoding="utf-8")
    (home / ".shop.env").chmod(0o600)
    root = home_harness(home)
    e = {k: v for k, v in env(None).items() if k != paths_var}
    config.use(root)
    try:
        def go(argv):
            from kit import env as kenv
            kenv.chain().load_into_environ()
            return doctor.main(argv, spec=SPEC)
        rc, out, err = capture(go, ["--json"], env=e)
    finally:
        _shop.use()
    doc = one_doc(out)
    hs = found(doc, "home_secrets")
    check("a secret and the data dir from the HOME-level file: a warning "
          "naming both (not the plain setting)",
          [c["message_code"]["code"] for c in hs] == ["doctor_home_secrets"]
          and hs[0]["message_code"]["params"]["names"]
          == ["SHOP_API_TOKEN", "SHOP_DATA_DIR"], hs)
    check("the data dir from the HOME-level file: its own warning",
          "doctor_data_dir_from_home" in codes(doc, "data_dir"))
    check("the token's value is never shown", "t0ken" not in out)


# ------------------------------------------------------------------ [5]

def fingerprint(path: Path) -> tuple:
    return (path.stat().st_mtime_ns, path.read_bytes())


def test_db() -> None:
    print("\n[5] the database")
    data = Path(tmp_dir("doc-db-"))
    init(data)
    dbf = data / "shop.db"
    before = fingerprint(dbf)
    _, _, _, doc = run(data, "--json")
    check("a current DB: ok, schema v1",
          codes(doc, "db") == ["doctor_db_ok"] and found(doc, "db", "ok"))
    check("read-only: the file is unchanged", fingerprint(dbf) == before)
    with closing(sqlite3.connect(dbf)) as c:
        c.execute("CREATE TABLE leftover (x TEXT)")
        c.execute("DROP TABLE stock")
        c.execute("CREATE TABLE stock (market TEXT, sku TEXT, "
                  "PRIMARY KEY (market, sku))")
        c.commit()
    _, _, _, doc = run(data, "--json")
    check("a stale cache table and an unknown table: one warning each",
          codes(doc, "db") == ["doctor_db_stale", "doctor_db_unknown"]
          and found(doc, "db")[0]["message_code"]["params"]["tables"]
          == ["stock"], codes(doc, "db"))
    with closing(sqlite3.connect(dbf)) as c:
        c.execute("PRAGMA user_version = 9")
        c.commit()
    _, _, _, doc = run(data, "--json")
    check("a DB stamped by a newer harness: schema_too_new, a warning",
          codes(doc, "db") == ["schema_too_new"])
    check("…and the market check does not read it",
          codes(doc, "markets") == ["doctor_not_onboarded"])
    bad = Path(tmp_dir("doc-bad-"))
    (bad / "shop.db").write_bytes(b"this is not a database at all" * 50)
    _, _, _, doc = run(bad, "--json")
    check("an unreadable file: a warning, never a traceback",
          codes(doc, "db") == ["db_unreadable"]
          and found(doc, "db")[0]["next"][0].startswith("sqlite3 "))
    _, _, _, doc = run(data, "--json", spec=None)
    check("no schema given: the file is only named",
          codes(doc, "db") == ["doctor_db_present"])


# ------------------------------------------------------------------ [6]

def test_markets() -> None:
    print("\n[6] markets")
    data = Path(tmp_dir("doc-mk-"))
    init(data)
    _, _, _, doc = run(data, "--json")
    m = found(doc, "markets")
    check("a pending declaration: a warning with the confirm command",
          [c["message_code"]["code"] for c in m] == ["doctor_markets_pending"]
          and m[0]["next"] == ["shop facts confirm market_declared --market "
                               "US --reason <why>"])
    with closing(sqlite3.connect(data / "shop.db")) as c:
        c.execute("UPDATE client_facts SET is_assumption = 0 WHERE key = "
                  "'market_declared'")
        c.commit()
    _, _, _, doc = run(data, "--json")
    check("a confirmed declaration: ok, the market named",
          codes(doc, "markets") == ["doctor_markets_declared"]
          and found(doc, "markets", "ok")[0]["message_code"]["params"]
          == {"markets": ["US"]})
    flat = Path(tmp_dir("doc-flat-")) / "shop"
    shutil.copytree(_shop.SHOP, flat, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"))
    toml = flat / "harness.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'markets = ["US", "CA"]', "markets = []"), encoding="utf-8")
    _, _, _, doc = run(Path(tmp_dir("doc-flat-data-")), "--json", root=flat)
    check("markets = []: nothing to declare, ok",
          codes(doc, "markets") == ["doctor_no_partition"])


# ------------------------------------------------------------------ [7]

def test_hooks() -> None:
    print("\n[7] hooks")
    data = Path(tmp_dir("doc-hooks-"))
    _, _, _, doc = run(data, "--json", lock=lambda ctx: "set")
    check("platform lock set: ok", codes(doc, "platform_lock")
          == ["doctor_platform_lock_set"] and found(doc, "platform_lock", "ok"))
    _, _, _, doc = run(data, "--json",
                       lock=lambda ctx: ("unknown", "(admin rule for budget "
                                                    "changes)"))
    pl = found(doc, "platform_lock")
    check("platform lock unknown: a warning, with the detail",
          [c["level"] for c in pl] == ["warn"]
          and "(admin rule for budget changes)" in pl[0]["message"])
    _, _, _, doc = run(data, "--json")
    check("no hook: no platform line", found(doc, "platform_lock") == [])
    _, _, _, doc = run(data, "--json",
                       verbs=[*VERBS, Verb(("gone",), "gone.py", "read")])
    check("a verb whose script is missing: a warning naming it",
          codes(doc, "verbs") == ["doctor_verb_script_missing"]
          and found(doc, "verbs")[0]["message_code"]["params"]
          == {"verb": "gone", "script": "gone.py"})
    seen = {}

    def mine(ctx):
        seen["live"] = ctx.live
        ctx.warn("shop_prices", msg("shop_price_missing", "No price on file "
                                    "for A1", sku="A1"), ["shop prices pull"])
        if ctx.live:
            ctx.fail("shop_api", msg("shop_stock_low", "A1 has only 0 unit(s) "
                                     "left", sku="A1", units=0))

    def crashes(ctx):
        raise RuntimeError("boom")

    def plain(ctx):
        ctx.warn("plain", "an uncoded sentence")

    rc, _, _, doc = run(data, "--json", checks=[mine, crashes, plain])
    check("a harness check reports in its own words, after the built-ins",
          doc["checks"][-3]["check"] == "shop_prices"
          and found(doc, "shop_prices")[0]["next"] == ["shop prices pull"]
          and seen["live"] is False)
    check("a check that crashes, or reports a plain str: a warning, no "
          "traceback", [c["message_code"]["code"] for c in doc["checks"][-2:]]
          == ["doctor_check_crashed", "doctor_check_crashed"]
          and "RuntimeError: boom" in doc["checks"][-2]["message"], rc)
    check("warnings alone: exit 0", rc == 0)
    rc, _, _, doc = run(data, "--json", "--live", checks=[mine])
    check("--live reaches the harness's checks; a rejected probe fails: "
          "exit 1", seen["live"] is True and rc == 1
          and found(doc, "shop_api", "fail") and doc["healthy"] is False)


# ------------------------------------------------------------------ [8]

def test_exit() -> None:
    print("\n[8] exit rules and --json")
    data = Path(tmp_dir("doc-exit-"))
    rc, _, _, doc = run(data, "--json")
    check("warnings, no --strict: exit 0", rc == 0 and doc["summary"]["warn"])
    rc, _, _, doc = run(data, "--json", "--strict")
    check("--strict: any warning is fatal (exit 1)",
          rc == 1 and doc["strict"] is True and doc["healthy"] is False)
    init(data)
    with closing(sqlite3.connect(data / "shop.db")) as c:
        c.execute("UPDATE client_facts SET is_assumption = 0 WHERE key = "
                  "'market_declared'")
        c.commit()
    rc, out, _, doc = run(data, "--json", "--strict")
    check("a healthy install passes --strict: exit 0, OK",
          rc == 0 and doc["healthy"] and doc["summary"]["warn"] == 0
          and doc["message_code"]["code"] == "doctor_summary_ok", out[-400:])
    rc, out, err, doc = run(data, "--nope", "--json")
    check("a usage error is one coded document too",
          rc == 2 and doc and doc["code"] == "usage", (out, err))


def main() -> int:
    test_fresh()
    test_writes()
    test_paths()
    test_env_files()
    test_db()
    test_markets()
    test_hooks()
    test_exit()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
