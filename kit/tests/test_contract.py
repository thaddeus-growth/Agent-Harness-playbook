#!/usr/bin/env python3
"""kit.contract: one stdout document, errors included; never a traceback.

  * emit() prints one JSON line (non-ASCII kept, key order kept);
  * fail() under --json prints exactly {error, next, code, params} on
    stdout and nothing on stderr; exit 2 for a HarnessError, 1 otherwise;
    text mode prints error:/next: on stderr only; a gate challenge adds
    `subject` and the rerun command with --code/--relay-user/--relay-at;
    a broken registry still yields a coded document;
  * missing_db_error(): None with a DB; no_db with the harness's next
    (hook, or the default) without one; never creates the file;
  * meta(): every required key always present, malformed parts refused;
  * stale_warning()/coverage_warning(): coded Msgs, None when all is well;
  * run_main(): doc / int / None / exception paths.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, contract  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.messages import code, msg  # noqa: E402
from kit.testing.check import (capture, check, finish, one_doc, raises,  # noqa: E402
                               tmp_dir)

FAILURE_KEYS = {"error", "next", "code", "params"}


class Challenge(HarnessError):
    """Shaped like kit.human.CodeRequired (confirm_code + subject)."""

    def __init__(self, message, *, confirm_code, subject):
        super().__init__(message)
        self.confirm_code, self.subject = confirm_code, subject


def run_fail(e, as_json, cmd=("shop", "facts", "list")):
    return capture(lambda _: contract.fail(e, cmd=list(cmd), as_json=as_json),
                   [])


def main() -> int:
    _shop.use()
    data = _shop.data_dir()

    print("[1] emit: one JSON document on stdout")
    rc, out, err = capture(lambda _: contract.emit(
        {"z": 1, "a": "价格", "p": Path("/x"), "m": msg(
            "no_db", "No database at y", path="y")}), [])
    check("one line, key order kept, non-ASCII kept, default=str",
          out == '{"z": 1, "a": "价格", "p": "/x", "m": "No database at y"}\n'
          and err == "", out)

    print("\n[2] fail(): the one failure output")
    e = HarnessError(msg("no_db", "No database at /d", path="/d"),
                     ["shop facts init"])
    check("HarnessError keeps message and next",
          e.message == "No database at /d" and e.next == ["shop facts init"]
          and str(e) == "No database at /d")
    rc, out, err = run_fail(e, True)
    doc = one_doc(out)
    check("--json: exactly {error, next, code, params}, stderr empty, exit 2",
          rc == 2 and err == "" and set(doc) == FAILURE_KEYS
          and doc == {"error": "No database at /d", "next": ["shop facts init"],
                      "code": "no_db", "params": {"path": "/d"}}, (rc, out, err))
    rc, out, err = run_fail(RuntimeError("disk on fire"), True)
    doc = one_doc(out)
    check("--json: any other exception -> unclassified_error, exit 1",
          rc == 1 and doc == {"error": "disk on fire", "next": [],
                              "code": "unclassified_error",
                              "params": {"detail": "disk on fire"}}, out)
    rc, out, err = run_fail(e, False)
    check("text: error:/next: on stderr, nothing on stdout, no traceback",
          rc == 2 and out == "" and err == "error: No database at /d\n"
          "next: shop facts init\n", (rc, out, err))
    ch = Challenge(msg("no_db", "relay me", path="p"), confirm_code="042317",
                   subject={"verb": "facts confirm", "market": "US",
                            "items": {"k": "v"}, "version": 3})
    rc, out, err = run_fail(ch, True, ("shop", "facts", "confirm", "k",
                                        "--reason", "two words"))
    doc = one_doc(out)
    check("a gate challenge: subject + the rerun with --code and relay audit",
          rc == 2 and doc["subject"]["items"] == {"k": "v"}
          and doc["next"] == ["shop facts confirm k --reason 'two words' "
                              "--code 042317 --relay-user <sender_id> "
                              "--relay-at <iso_time>"]
          and set(doc) == FAILURE_KEYS | {"subject"}, doc)
    bad = Path(tmp_dir("broken-"))
    (bad / "harness.toml").write_text(
        '[harness]\nname = "x"\ncli = "x"\nenv_prefix = "X"\n'
        '[ssot]\nmessage_codes = "missing.tsv"\n')
    config.use(bad)
    rc, out, err = run_fail(RuntimeError("boom"), True)
    check("a registry that cannot load still gives a coded document",
          rc == 1 and one_doc(out) == {"error": "boom", "next": [],
                                       "code": "unclassified_error",
                                       "params": {"detail": "boom"}}, out)
    _shop.use()
    _shop.data_dir()
    data = Path(os.environ["SHOP_DATA_DIR"])

    print("\n[3] missing_db_error(): a read never creates the DB")
    e = contract.missing_db_error()
    check("no DB: no_db, path, the harness's no_db_next hook",
          isinstance(e, HarnessError) and code(e.message) == {
              "code": "no_db", "params": {"path": str(data / "shop.db")}}
          and e.next == ["shop facts init"], e and e.next)
    check("… and nothing was created", not (data / "shop.db").exists())
    (data / "shop.db").write_bytes(b"")
    check("a DB file: None", contract.missing_db_error() is None)
    os.environ["SHOP_DB"] = str(data / "other.db")
    check("SHOP_DB is the file looked at",
          contract.missing_db_error().message.params["path"]
          == str(data / "other.db"))
    del os.environ["SHOP_DB"]
    plain = Path(tmp_dir("plain-"))
    (plain / "harness.toml").write_text(
        '[harness]\nname = "p"\ncli = "pl"\nenv_prefix = "SHOP"\n'
        'version = "1.2.3"\n')
    config.use(plain)
    check("no hook: next = <cli> doctor, <cli> facts init",
          contract.missing_db_error().next == ["pl doctor", "pl facts init"])
    check("harness version from [harness].version",
          contract.harness_version() == "1.2.3")
    _shop.use()
    saved = os.environ.pop("SHOP_DATA_DIR")
    e = raises(contract.missing_db_error, HarnessError)
    check("no data dir: data_dir_unset, not a cwd default",
          e is not None and e.message.code == "data_dir_unset", e)
    os.environ["SHOP_DATA_DIR"] = saved

    print("\n[4] meta(): required keys always present")
    w = contract.window("2026-09-01", "2026-09-07",
                        ["2026-09-01", "2026-09-02", "2026-09-05"])
    check("window(): requested span, days found",
          w == {"start": "2026-09-01", "end": "2026-09-07",
                "days_requested": 7, "days_found": 3}, w)
    miss = contract.missing_days("2026-09-01", "2026-09-07",
                                 ["2026-09-01", "2026-09-02", "2026-09-05"])
    check("missing_days()", miss == ["2026-09-03", "2026-09-04",
                                     "2026-09-06", "2026-09-07"], miss)
    m = contract.meta(window=None, sources=[], stale=[])
    check("a bare meta has every required key, n/a values",
          tuple(m) == contract.REQUIRED_KEYS and m["window"] is None
          and m["sources"] == [] and m["stale"] == [] and m["coverage"] is None
          and m["thresholds_overridden"] == {} and m["assumed_thresholds"] == {}
          and m["evidence_level"] is None, m)
    check("harness block: name, version, kit_version",
          m["harness"] == {"name": "shop-harness", "version": None,
                           "kit_version": "0.1.0"}, m["harness"])
    full = contract.meta(
        window=w, sources=[{"table": "orders", "pulled_on": None}],
        stale=[{"table": "orders", "last_date": "2026-09-05", "lag_days": 5,
                "max_lag_days": 2}],
        coverage={"missing_days": miss, "next": ["shop pull orders"]},
        evidence_level="attributed", extra={"settled": True})
    check("a full meta keeps what it is given, extra appended",
          full["settled"] is True and full["coverage"]["next"]
          == ["shop pull orders"] and json.loads(json.dumps(full)) == full)
    for label, kw in (
            ("a window without days_found",
             {"window": {"start": "a", "end": "b", "days_requested": 1}}),
            ("a source without pulled_on", {"sources": [{"table": "t"}]}),
            ("a stale row without max_lag_days",
             {"stale": [{"table": "t", "last_date": "d", "lag_days": 1}]}),
            ("coverage without next", {"coverage": {"missing_days": []}}),
            ("extra replacing a required key", {"extra": {"window": 1}})):
        args = {"window": None, "sources": [], "stale": [], **kw}
        check(f"refused: {label}",
              raises(lambda: contract.meta(**args), ValueError) is not None)

    print("\n[5] stale_warning() / coverage_warning()")
    check("all fine -> None",
          contract.stale_warning(m) is None
          and contract.coverage_warning(m) is None)
    s = contract.stale_warning(
        {**m, "stale": full["stale"]})
    check("stale -> contract_stale_data, text names the lag",
          s.code == "contract_stale_data" and "orders ends 2026-09-05, 5 days "
          "behind (max 2)" in s, s)
    c = contract.coverage_warning(full)
    check("short window -> contract_short_window with ranges and backfill",
          c.code == "contract_short_window"
          and "only 3 of 7 requested days (2026-09-01..2026-09-07)" in c
          and "no rows for 2026-09-03..2026-09-04, 2026-09-06..2026-09-07" in c
          and "backfill: `shop pull orders`" in c, c)
    part = {**m, "window": {**w, "days_found": 7}, "coverage": {
        "missing_days": [], "next": [],
        "missing_days_by_source": {"refunds": ["2026-09-02"], "orders": []}}}
    c = contract.coverage_warning(part)
    check("a source with gaps -> contract_partial_source",
          c.code == "contract_partial_source"
          and c.params["gaps"] == {"refunds": "2026-09-02"}
          and "refunds has no rows for 2026-09-02" in c, c)
    both = contract.stale_warning({**full, "assumed_thresholds": {
        "min_spend": {"value": 5, "default": 10, "client_value": None}}})
    check("several warnings -> one joined message, ASSUMED first",
          both.code == "joined" and [p.code for p in both.params["parts"]]
          == ["contract_assumed_thresholds", "contract_stale_data",
              "contract_short_window"]
          and both.startswith("⚠ ASSUMED (not stored): min_spend=5 (in effect "
                              "was 10)"), both)
    check("every warning's code is JSON-safe",
          json.loads(json.dumps(code(both))) == code(both))

    print("\n[6] run_main()")
    cmd = ["shop", "x"]
    rc, out, err = capture(lambda a: contract.run_main(
        lambda argv: {"ok": argv}, a, cmd=cmd), ["--json"])
    check("a returned doc is emitted, exit 0",
          rc == 0 and one_doc(out) == {"ok": ["--json"]}, out)
    rc, _, _ = capture(lambda a: contract.run_main(lambda argv: 3, a, cmd=cmd),
                       [])
    check("a returned int is the exit code", rc == 3)
    rc, out, _ = capture(lambda a: contract.run_main(lambda argv: None, a,
                                                     cmd=cmd), [])
    check("None = 0, nothing printed", rc == 0 and out == "")

    def refuse(argv):
        raise HarnessError(msg("no_db", "No database at q", path="q"), ["n"])
    rc, out, err = capture(lambda a: contract.run_main(refuse, a, cmd=cmd),
                           ["--json"])
    check("an exception under --json -> the failure doc, exit 2",
          rc == 2 and one_doc(out)["code"] == "no_db" and err == "", out)
    rc, out, err = capture(lambda a: contract.run_main(refuse, a, cmd=cmd),
                           ["--", "--json"])
    check("--json after a literal -- is not the flag: text failure",
          rc == 2 and out == "" and err.startswith("error: "), (out, err))
    rc, out, err = capture(lambda a: contract.run_main(
        refuse, a, cmd=cmd, as_json_default=True), [])
    check("as_json_default", rc == 2 and one_doc(out)["code"] == "no_db")
    p = argparse.ArgumentParser()
    contract.add_json_arg(p)
    check("add_json_arg: --json flag", p.parse_args(["--json"]).json is True
          and p.parse_args([]).json is False)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
