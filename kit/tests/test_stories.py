#!/usr/bin/env python3
"""kit.stories: the story-check engine, on the fake "shop" harness.

  [1] the expect grammar: path, path>0, path=V, !term, `[]`, `*`
  [2] the registry: only read verbs (write, gated, external, unknown,
      unbalanced quotes and the spawning verb itself are refused), only
      well-formed terms, the header and row widths; CI-only rows; needs
  [3] a refused registry refuses the whole run before any verb runs: one
      coded failure document (story_check_verb_refused /
      story_check_bad_term), exit 2
  [4] a run: pass, fail (a term that doesn't hold; a verb that failed,
      its own code nested), skip (needs -> no rows for the market; a
      no-data failure; CI only); each distinct verb runs once; exit 0
      whatever the stories; the document keeps the --json contract; text
      output
  [5] the default has_rows: the market's rows, any row without a market
      column, a view, no table
  [6] through the harness CLI (run_via_cli): `--` then --json, --market
      where the verb takes one, --assume on a compute only, {market} as
      an SQL literal, a failure document, a finding exit; main() end to end
  [7] check_registry(): the registry's own rules
  [8] the stories.tsv fragment: every code emitted, exact params, en + zh
"""

import os
import shutil
import sqlite3
import sys
from collections import namedtuple
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import _shop  # noqa: E402
from kit import config, messages, runner, stories  # noqa: E402
from kit.guards import json_contract  # noqa: E402
from kit.testing.check import (capture, check, finish, one_doc,  # noqa: E402
                               raises, tmp_dir)

V = namedtuple("V", "words script kind takes_market needs_data_dir",
               defaults=(True, True))
VERBS = [V(("compute", "sales"), "compute_sales.py", "read"),
         V(("compute", "broken"), "compute_broken.py", "read"),
         V(("compute", "nodata"), "compute_nodata.py", "read"),
         V(("compute", "finding"), "compute_finding.py", "read"),
         V(("compute", "stories"), "compute_stories.py", "read"),
         V(("notes", "list"), "notes_list.py", "read", False),
         V(("facts", "list"), "facts.py", "read"),
         V(("facts", "set"), "facts.py", "human"),
         V(("facts", "confirm"), "facts.py", "gated"),
         V(("pull", "orders"), "pull_orders.py", "external"),
         V(("execute", "apply"), "execute_actions.py", "external")]
HEAD = "story\tverb\texpect\tneeds\tnote\n"


def registry(rows: list[tuple], head: str = HEAD) -> Path:
    path = Path(tmp_dir("stories-reg-")) / "story_checks.tsv"
    path.write_text(head + "".join("\t".join(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def make_db(data: Path) -> Path:
    """orders (a market column): US rows only; stock (no market column):
    one row; empty: none; a view over orders; US declared, CA not."""
    path = data / "shop.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE orders (market TEXT, day TEXT, units INTEGER);
        INSERT INTO orders VALUES ('US', '2026-09-01', 3);
        CREATE TABLE stock (sku TEXT, units INTEGER);
        INSERT INTO stock VALUES ('A1', 2);
        CREATE TABLE empty (market TEXT);
        CREATE VIEW us_orders AS SELECT * FROM orders;
        CREATE TABLE client_facts (market TEXT, key TEXT, value TEXT,
                                   is_assumption INTEGER);
        INSERT INTO client_facts VALUES ('US', 'market_declared', 'US', 0);
    """)
    con.commit()
    con.close()
    return path


# ------------------------------------------------------------------ [1]

def test_terms() -> None:
    print("[1] expect terms")
    doc = {"a": {"b": 0, "c": None, "d": [], "e": "x"},
           "rows": [{"v": None}, {"v": 2, "s": "executed", "t": True}],
           "fams": {"F1": {"k": [1]}, "F2": {"k": []}}}
    cases = [("a.b", True), ("a.c", False), ("a.zz", False), ("a.d", True),
             ("a.b>0", False), ("a.d>0", False), ("a.e>0", True),
             ("rows>0", True), ("rows[].v", True), ("rows[].v>0", True),
             ("rows[].s=executed", True), ("rows[].t=true", True),
             ("rows[].v=2", True), ("rows[].v=3", False),
             ("!rows[].t=true", False), ("!rows[].s=applied", True),
             ("fams.*.k>0", True), ("fams.*.k[]=1", True), ("!a.c", True),
             ("rows[].t>0", False)]
    for term, want in cases:
        check(f"{term} -> {want}", stories.holds(doc, term) is want)
    check("a leading [] reads a top-level list",
          stories.holds([{"n": 0}], "[].n=0")
          and not stories.holds([], "[].n=0"))
    for term in ("a>1", "rows[].v=", "!", "=x", "a>0>0"):
        check(f"`{term}` is not a term", not stories.TERM.fullmatch(term))


# ------------------------------------------------------------------ [2]

def test_load() -> None:
    print("\n[2] the registry: read verbs only, well-formed terms")
    reg = registry([("S01", "compute sales --by week", "rows>0 argv", "orders;"
                     " stock", "the sales report"),
                    ("S02", "notes list", "items>0", "—", "notes"),
                    ("S03", "—", "—", "—", "proved in CI"),
                    ("S04", "facts list", "facts", "", "empty needs")])
    got = stories.load(reg, verbs=VERBS)
    check("a registry of read verbs loads, in order",
          [c.story for c in got] == ["S01", "S02", "S03", "S04"], got)
    check("verb as typed, expect split on spaces, needs on `;`",
          got[0] == stories.Check("S01", "compute sales --by week",
                                  ("rows>0", "argv"), ("orders", "stock")),
          got[0])
    check("a row whose verb is — is CI-only: no verb, no expect, no needs",
          got[2] == stories.Check("S03", "", (), ()), got[2])
    check("an empty needs cell is no needs", got[3].needs == ())
    for verb in ("pull orders", "facts set k 1", "facts confirm k",
                 "execute apply --apply", "compute stories", "nope list",
                 'compute sales "unbalanced'):
        e = raises(lambda: stories.load(registry([
            ("S01", "compute sales", "rows", "—", "x"),
            ("S02", verb, "x", "—", "x")]), verbs=VERBS))
        check(f"`{verb}` refuses the registry (story_check_verb_refused)",
              e is not None and e.message.code == "story_check_verb_refused"
              and e.message.params == {"story": "S02", "verb": verb}, e)
    for term in ("a>1", "rows[].v=", "!", "=x", "a>0>0"):
        e = raises(lambda: stories.load(registry([
            ("S02", "notes list", term, "—", "x")]), verbs=VERBS))
        check(f"expect `{term}` refuses the registry (story_check_bad_term)",
              e is not None and e.message.code == "story_check_bad_term"
              and e.message.params == {"story": "S02", "term": term}, e)
    e = raises(lambda: stories.load(Path(tmp_dir()) / "none.tsv",
                                    verbs=VERBS))
    check("no registry file: story_check_registry_missing",
          e is not None and e.message.code == "story_check_registry_missing",
          e)
    e = raises(lambda: stories.load(registry([("S01", "notes list", "x")],
                                             head="story\tverb\texpect\n"),
                                    verbs=VERBS))
    check("a header without `needs`: story_check_registry_bad",
          e is not None and e.message.code == "story_check_registry_bad"
          and "needs" in e.message.params["detail"], e)
    e = raises(lambda: stories.load(registry([("S01", "notes list", "x")]),
                                    verbs=VERBS))
    check("a row of another width: story_check_registry_bad",
          e is not None and e.message.code == "story_check_registry_bad"
          and "line 2 has 3 cells" in e.message.params["detail"], e)
    check("match(): the longest verb whose words prefix the typed ones",
          stories.match(["compute", "sales", "--by", "week"],
                        VERBS).script == "compute_sales.py"
          and stories.match(["compute"], VERBS) is None)


# ------------------------------------------------------------------ [3]

def fake_runner(calls: list):
    docs = {
        "compute sales": {"rows": [{"units": 2}]},
        "notes list": {"items": [{"note": "ok"}]},
        "facts list": {"facts": [{"key": "k"}]},
        "compute broken": runner.ChildFailed(
            "No database at /x", ["shop facts init"],
            {"code": "no_db", "params": {"path": "/x"}}),
        "compute nodata": runner.ChildFailed(
            "no rows yet for US", [], {"code": "no_data", "params": {}}),
    }

    def run(verb, market, assume):
        calls.append((verb, market, tuple(assume)))
        return docs[" ".join(verb.split()[:2])]
    return run


def main_(reg: Path, calls: list, argv: list[str], **kw):
    return capture(lambda a: stories.main(
        a, verbs=VERBS, registry=reg, run_verb=fake_runner(calls), **kw),
        argv)


def test_refused_run() -> None:
    print("\n[3] a refused registry refuses the whole run, before any verb")
    calls: list = []
    rc, out, err = main_(registry([("S01", "notes list", "items>0", "—",
                                    "x")]), calls, ["--json"])
    check("positive control: an allowed registry reaches the verb",
          rc == 0 and len(calls) == 1, (rc, out, err, calls))
    for verb in ("pull orders", "facts confirm k", "compute stories"):
        calls.clear()
        rc, out, err = main_(registry([
            ("S01", "compute sales", "rows", "—", "x"),
            ("S02", verb, "x", "—", "x")]), calls, ["--json"])
        doc = one_doc(out) or {}
        check(f"`{verb}` in the registry: exit 2, one coded failure "
              f"document, no verb ran",
              rc == 2 and set(doc) == {"error", "next", "code", "params"}
              and doc["code"] == "story_check_verb_refused"
              and doc["params"] == {"story": "S02", "verb": verb}
              and not calls and err == "", (rc, out, err, calls))
    calls.clear()
    rc, out, err = main_(registry([("S02", "notes list", "a>1", "—", "x")]),
                         calls, [])
    check("a bad term, text mode: `error:` on stderr, stdout empty, no verb "
          "ran", rc == 2 and out == "" and err.startswith("error: ")
          and "a>1" in err and not calls, (rc, out, err))


# ------------------------------------------------------------------ [4]

def test_run(data: Path) -> None:
    print("\n[4] a run on the client's data")
    reg = registry([
        ("S01", "compute sales", "rows>0 rows[].units", "orders", "pass"),
        ("S02", "compute sales", "rows>0 rows[].units=3 nope", "orders",
         "a term fails"),
        ("S03", "notes list", "items>0", "empty", "no rows yet"),
        ("S04", "notes list", "items>0", "no_such_table", "no table"),
        ("S05", "—", "—", "—", "CI only"),
        ("S06", "compute broken", "rows", "—", "the verb fails"),
        ("S07", "compute nodata", "rows", "—", "no data for the verb"),
        ("S08", "notes list", "!items[].note=bad", "stock; us_orders",
         "no market column; a view")])
    calls: list = []
    rc, out, err = main_(reg, calls, ["--json", "--market", "US",
                                      "--assume", "low_stock_units=3"])
    doc = one_doc(out) or {}
    st = {s["id"]: s for s in doc.get("stories", [])}
    check("exit 0, one document {meta, market, stories, summary}, even with "
          "failed stories", rc == 0 and set(doc) == {"meta", "market",
                                                      "stories", "summary"}
          and doc["market"] == "US", (rc, out[-400:], err))
    check("one entry per registry row, in its order, each {id, status, "
          "verb, reason, reason_code}",
          list(st) == [f"S0{i}" for i in range(1, 9)]
          and all(set(s) == {"id", "status", "verb", "reason", "reason_code"}
                  for s in st.values()), list(st))
    status = {i: s["status"] for i, s in st.items()}
    check("pass / fail / skip as the rows say",
          status == {"S01": "pass", "S02": "fail", "S03": "skip",
                     "S04": "skip", "S05": "skip", "S06": "fail",
                     "S07": "skip", "S08": "pass"}, status)
    code = {i: (s["reason_code"] or {}).get("code") for i, s in st.items()}
    check("each reason's code",
          code == {"S01": None, "S02": "story_check_expect_failed",
                   "S03": "story_check_no_data", "S04": "story_check_no_data",
                   "S05": "story_check_ci_only",
                   "S06": "story_check_verb_failed",
                   "S07": "story_check_verb_no_data", "S08": None}, code)
    check("a pass has no reason (null, null code)",
          st["S01"]["reason"] is None and st["S01"]["reason_code"] is None)
    check("a failing term: fail naming just the terms that don't hold",
          st["S02"]["reason_code"]["params"]["expect"] == ["rows[].units=3",
                                                          "nope"], st["S02"])
    check("a needs table without the market's rows: skip, naming it",
          st["S03"]["reason_code"]["params"] == {
              "story": "S03", "verb": "notes list", "table": "empty",
              "market": "US"}, st["S03"])
    err6 = st["S06"]["reason_code"]["params"]["error"]
    check("a verb that fails: fail, the verb's own failure code nested",
          err6 == {"code": "no_db", "params": {"path": "/x"}}, err6)
    check("a verb failing no_data: skip, its failure nested",
          st["S07"]["reason_code"]["params"]["market"] == "US"
          and "error" in st["S07"]["reason_code"]["params"], st["S07"])
    check("the summary counts the statuses",
          doc["summary"] == {"pass": 2, "fail": 2, "skip": 4},
          doc.get("summary"))
    ran = [c[0] for c in calls]
    check("each distinct verb ran once; skipped stories ran nothing",
          sorted(ran) == ["compute broken", "compute nodata",
                          "compute sales", "notes list"], calls)
    check("the hook gets the market and this run's --assume",
          all(c[1] == "US" and c[2] == ("--assume", "low_stock_units=3")
              for c in calls), calls)
    check("the document keeps the --json contract (every message coded, "
          "meta)", json_contract.uncoded(doc) == []
          and json_contract.check_meta(doc) == [],
          (json_contract.uncoded(doc), json_contract.check_meta(doc)))

    rc, out, err = main_(reg, [], ["--market", "US"])
    check("text: the counts, then one line per story",
          rc == 0 and "story checks (market=US): 2 pass, 2 fail, 4 skip"
          in out and "  S01  pass  shop compute sales" in out
          and "  S02  fail  S02: `shop compute sales` ran, but "
              "rows[].units=3 nope does not hold" in out, out)
    rc, out, err = main_(reg, [], ["--json"])
    check("no --market: the one declared market (kit.market.resolve)",
          rc == 0 and (one_doc(out) or {}).get("market") == "US", out[-300:])
    rc, out, err = main_(reg, [], ["--json", "--market", "CA"])
    doc = one_doc(out) or {}
    check("an undeclared market: the market gate's coded refusal",
          rc == 2 and doc.get("code") == "market_not_onboarded", out)
    nodb = Path(tmp_dir("shop-nodb-"))
    rc, out, err = capture(lambda a: stories.main(
        a, verbs=VERBS, registry=reg, run_verb=fake_runner([])),
        ["--json"], env={**os.environ, "SHOP_DATA_DIR": str(nodb)})
    doc = one_doc(out) or {}
    check("no DB: no_db, and nothing created",
          rc == 2 and doc.get("code") == "no_db" and not any(nodb.iterdir()),
          out)


# ------------------------------------------------------------------ [5]

def test_has_rows(db: Path) -> None:
    print("\n[5] the default has_rows")
    con = sqlite3.connect(db)
    try:
        h = stories.table_has_rows
        check("a table with a market column: the market's rows only",
              h(con, "orders", "US") and not h(con, "orders", "CA"))
        check("a table without a market column: any row",
              h(con, "stock", "CA"))
        check("an empty table, a missing table: no rows",
              not h(con, "empty", "US") and not h(con, "nope", "US"))
        check("a view counts like a table", h(con, "us_orders", "US"))
        check("a name that is not an identifier is never queried",
              not h(con, "orders; DROP TABLE stock", "US")
              and h(con, "stock", "US"))
    finally:
        con.close()


# ------------------------------------------------------------------ [6]

CLI = '''import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTES = {("compute", "sales"): "compute_sales.py",
          ("compute", "broken"): "compute_broken.py",
          ("compute", "finding"): "compute_finding.py",
          ("notes", "list"): "notes_list.py"}


def main(argv):
    script = ROUTES.get(tuple(argv[:2]))
    if not script:
        print(f"error: no verb {argv[:2]}", file=sys.stderr)
        return 2
    rest = argv[2:]
    if "--" in rest:
        rest.remove("--")
    return subprocess.call([sys.executable, str(HERE / script), *rest],
                           stdin=subprocess.DEVNULL)


raise SystemExit(main(sys.argv[1:]))
'''
ECHO = ('import json, sys\nprint(json.dumps({"rows": [{"units": 2}], '
        '"items": [1], "argv": sys.argv[1:]}))\n')
BROKEN = ('import json, sys\nprint(json.dumps({"error": "No database at /x",'
          ' "next": ["shop facts init"], "code": "no_db", '
          '"params": {"path": "/x"}}))\nsys.exit(2)\n')
FINDING = ('import json, sys\nprint(json.dumps({"rows": [], "finding": '
           'True}))\nsys.exit(2)\n')


def test_cli(data: Path) -> None:
    print("\n[6] through the harness CLI")
    root = Path(tmp_dir("stories-")) / "shop"
    shutil.copytree(_shop.SHOP, root, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc"))
    for rel, text in (("scripts/shop.py", CLI),
                      ("scripts/compute_sales.py", ECHO),
                      ("scripts/notes_list.py", ECHO),
                      ("scripts/compute_broken.py", BROKEN),
                      ("scripts/compute_finding.py", FINDING)):
        (root / rel).write_text(text, encoding="utf-8")
    config.use(root)
    try:
        run = stories.run_via_cli
        got = run("compute sales --by week", "US", ["--assume", "x=1"],
                  VERBS)
        check("a compute: its words' rest, then --json, --market, --assume "
              "(the `--` stripped by the CLI)",
              got.get("argv") == ["--by", "week", "--json", "--market", "US",
                                  "--assume", "x=1"], got)
        got = run("notes list {market}", "US", ["--assume", "x=1"], VERBS)
        check("a verb without a market: no --market, no --assume (not a "
              "compute); {market} as an SQL literal",
              got.get("argv") == ["'US'", "--json"], got)
        got = run("compute sales -- --tree", "O'K", [], VERBS)
        check("a verb that has its own `--`: no second one",
              got.get("argv") == ["--tree", "--json", "--market", "O'K"],
              got)
        got = run("compute broken", "US", [], VERBS)
        check("a failing verb: runner.ChildFailed with its error, next and "
              "code", isinstance(got, runner.ChildFailed)
              and str(got) == "No database at /x"
              and got.next == ["shop facts init"]
              and got.code == {"code": "no_db", "params": {"path": "/x"}},
              got)
        got = run("compute finding", "US", [], VERBS)
        check("a document without `error` counts whatever the exit",
              got == {"rows": [], "finding": True}, got)
        reg = registry([
            ("S01", "compute sales --by week", "rows>0 argv[]=--by",
             "orders", "x"),
            ("S02", "notes list", "items>0", "—", "x"),
            ("S03", "compute broken", "rows", "—", "x")])
        rc, out, err = capture(lambda a: stories.main(
            a, verbs=VERBS, registry=reg), ["--json"])
        doc = one_doc(out) or {}
        check("main() end to end (default hooks: the CLI, has_rows, "
              "kit.market)",
              rc == 0 and [s["status"] for s in doc.get("stories", [])]
              == ["pass", "pass", "fail"] and doc["summary"]
              == {"pass": 2, "fail": 1, "skip": 0}, (rc, out[-500:], err))
    finally:
        _shop.use()


# ------------------------------------------------------------------ [7]

def test_check_registry() -> None:
    print("\n[7] check_registry(): the registry's own rules")
    good = registry([("S01", "compute sales", "rows>0", "orders", "x"),
                     ("S02", "—", "—", "—", "CI")])
    check("a good registry: no problem",
          stories.check_registry(good, verbs=VERBS, known_tables={"orders"},
                                 required=("S01", "S02")) == [],
          stories.check_registry(good, verbs=VERBS))
    bad = registry([("S01", "compute sales", "—", "orders", "x"),
                    ("S01", "—", "—", "orders", "x"),
                    ("S03", "notes list", "items", "ghost", "")])
    probs = "\n".join(stories.check_registry(
        bad, verbs=VERBS, known_tables={"orders"}, required=("S09",)))
    for needle in ("more than one row for ['S01']",
                   "S01: a row with a verb needs an expect term",
                   "S03: needs unknown tables ['ghost']",
                   "stories without a check row: ['S09']",
                   "rows without a note: ['S03']"):
        check(f"check_registry: {needle}", needle in probs, probs)
    refused = stories.check_registry(registry([
        ("S01", "facts set k 1", "x", "—", "x")]), verbs=VERBS)
    check("check_registry: a refused verb is reported, not raised",
          len(refused) == 1 and "not a read verb" in refused[0], refused)


# ------------------------------------------------------------------ [8]

def test_fragment() -> None:
    print("\n[8] kit/message_codes.d/stories.tsv")
    reg = messages.registry()
    mine = {c: r for c, r in reg.items()
            if Path(r["file"]).name == "stories.tsv"}
    check("the fragment loads (kit origin, en + zh)",
          len(mine) == 9 and all(r["origin"] == "kit" and r["meaning_en"]
                                 and r["meaning_zh"] for r in mine.values()),
          sorted(mine))
    probs = messages.check_registry_closed(_shop.KIT, ["stories.py"], mine,
                                           strict_kit=True)
    check("closed both ways: every stories.py msg() is a literal code of the "
          "fragment with exactly its params, and every fragment code is "
          "emitted", probs == [], probs)
    probs = messages.check_registry_closed(_shop.KIT, ["guards/*.py"], {},
                                           strict_kit=True)
    check("the guards emit no coded message (their problems are for the "
          "developer)", probs == [], probs)


def main() -> int:
    _shop.use()
    data = _shop.data_dir()
    db = make_db(data)
    test_terms()
    test_load()
    test_refused_run()
    test_run(data)
    test_has_rows(db)
    test_cli(data)
    test_check_registry()
    test_fragment()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
