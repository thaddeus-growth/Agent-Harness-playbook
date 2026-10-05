#!/usr/bin/env python3
"""kit.verbs + kit.cli: the verb table and the dispatcher, on the fake
"shop" harness (kit/tests/fake_harness: scripts/verbs.py, scripts/shop.py).

  [1] the verb table: a malformed verb, a built-in word, a duplicate or an
      empty table fails at load; load() reads a harness's verbs.py; of_kind,
      targets, match (longest prefix), group, script_args (a shared script
      gets its sub-verb, a single one none), answers (docstring fallback);
      examples and keys: tuples, each example starting with the verb's own
      words, keys without spaces, both in as_dict (cli-prefixed examples)
  [2] help: every verb grouped by kind with what it answers, the built-ins
      and the env chain; `<cli>` alone is usage on stderr, exit 2; a group's
      --help lists that group; --version; `verbs --json` is the table,
      examples and keys included
  [3] refusals: an unlisted verb (coded, one document under --json, the
      candidates named), no data dir, a relative data dir, a missing
      script; --help needs no data dir; a verb's own --help ends with its
      Examples block and JSON keys line after the script's help (exit 0
      only; none when the verb declares neither; not on a group's --help)
  [4] forwarding: the first `--` stripped wherever it sits, a second kept;
      flags pass without `--`; the sub-verb of a shared script; the `+ cmd`
      trace on stderr with a --code masked, stdout the child's alone; the
      child's exit code, a signal as 128+n; the env chain loaded once into
      the child's env, the process env winning
  [5] through the real CLI (a subprocess): a read verb never creates the
      DB; init, list, pending; the kit's guards read the harness's
      verbs.py (json contract, boundary patterns); `shop facts list
      --help` ends with its examples, and each example of a read verb run
      through the CLI returns a document with the keys it declares
  [6] cli.tsv / doctor.tsv: every code emitted, exact params, en + zh
"""

import json
import os
import shutil
import signal
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import _shop  # noqa: E402
from kit import config, messages, verbs  # noqa: E402
from kit.cli import Dispatcher, masked, strip_dashdash  # noqa: E402
from kit.guards import boundary, json_contract  # noqa: E402
from kit.testing.check import (capture, check, clean_env, finish,  # noqa: E402
                               one_doc, raises, tmp_dir)
from kit.testing.sandbox import run as sandbox_run  # noqa: E402
from kit.testing.sandbox import sandbox_env  # noqa: E402
from kit.verbs import Verb, VerbTableError  # noqa: E402

CFG = _shop.use()
SCRIPTS = _shop.SHOP / "scripts"
CLI = SCRIPTS / "shop.py"
TABLE = verbs.load(SCRIPTS / "verbs.py")

ECHO = '''"""Echo: what this script was given."""
import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "script": os.path.basename(__file__),
                  "env": {k: v for k, v in os.environ.items()
                          if k.startswith(("SHOP_", "FROM_"))}}))
code = os.environ.get("ECHO_EXIT")
if code == "signal":
    os.kill(os.getpid(), signal.SIGTERM)
raise SystemExit(int(code or 0))
'''.replace("import json, os, sys", "import json, os, signal, sys")


TABLE_SRC = """[Verb(("things", "list"), "multi.py", "read"),
 Verb(("things", "set"), "multi.py", "human"),
 Verb(("compute", "sales"), "compute_sales.py", "read"),
 Verb(("compute", "sales", "deep"), "compute_sales_deep.py", "read"),
 Verb(("import", "codes"), "import_file.py", "ingest"),
 Verb(("notes",), "notes.py", "read", False, False,
      answers="Notes, no data dir", examples=("notes x", "notes  --json"),
      keys=("argv", "script")),
 Verb(("gone",), "missing.py", "read", False, False)]"""
ECHO_TABLE = eval(TABLE_SRC)
DRIVER = f'''"""A dispatcher over the echo scripts (kit.cli, as a harness CLI)."""
import sys
from pathlib import Path
from kit.cli import Dispatcher
from kit.verbs import Verb
HERE = Path(__file__).resolve().parent
raise SystemExit(Dispatcher({TABLE_SRC}, scripts_dir=HERE).main(sys.argv[1:]))
'''


def echo_dir() -> Path:
    d = Path(tmp_dir("cli-scripts-"))
    for name in ("multi.py", "compute_sales.py", "compute_sales_deep.py",
                 "notes.py", "import_file.py"):
        (d / name).write_text(ECHO, encoding="utf-8")
    (d / "cli.py").write_text(DRIVER, encoding="utf-8")
    return d


def drive(scripts: Path, argv: list[str], e: dict,
          cwd: Path | None = None) -> tuple[int, str, str]:
    """The echo dispatcher as a real process (its child writes to the real
    stdout, which capture() cannot see)."""
    e = {**e, "PYTHONPATH": str(_shop.PLAYBOOK), "PYTHONDONTWRITEBYTECODE": "1",
         config.ROOT_ENV: str(_shop.SHOP)}
    return sandbox_run([sys.executable, str(scripts / "cli.py"), *argv], e,
                       cwd=cwd)


def env(data: Path | None = None, **extra: str) -> dict:
    e = clean_env(**({CFG.env("DATA_DIR"): str(data)} if data else {}))
    e[CFG.env("AUTH_ENV_PATHS")] = "none"
    e.update(extra)
    return e


# ------------------------------------------------------------------ [1]

def test_table() -> None:
    print("[1] the verb table")
    v = Verb(["facts", "set"], "facts.py", "human")
    check("words become a tuple; command joins them; defaults True/True/''",
          v.words == ("facts", "set") and v.command == "facts set"
          and v.takes_market and v.needs_data_dir and v.answers == "")
    check("a string of words is split", Verb("a b", "x.py", "read").words
          == ("a", "b"))
    for label, make in (
            ("no words", lambda: Verb((), "x.py", "read")),
            ("an upper-case word", lambda: Verb(("Facts",), "x.py", "read")),
            ("a flag as a word", lambda: Verb(("--x",), "x.py", "read")),
            ("a built-in word", lambda: Verb(("doctor",), "x.py", "read")),
            ("the built-in `verbs`", lambda: Verb(("verbs", "x"), "x.py",
                                                  "read")),
            ("an unknown kind", lambda: Verb(("a",), "x.py", "write")),
            ("a script that is not .py", lambda: Verb(("a",), "x.sh", "read")),
            ("an absolute script", lambda: Verb(("a",), "/x/y.py", "read")),
            ("a script leaving the dir twice",
             lambda: Verb(("a",), "../../y.py", "read")),
            ("a .. inside the path", lambda: Verb(("a",), "b/../../y.py",
                                                 "read")),
            ("takes_market not a bool", lambda: Verb(("a",), "x.py", "read",
                                                     "yes"))):
        check(f"refused at construction: {label}",
              isinstance(raises(make), VerbTableError))
    check("../tests/run.py is a valid dev script",
          Verb(("test",), "../tests/run.py", "dev", False, False).kind
          == "dev")
    check("a duplicate verb fails check()", isinstance(raises(
        lambda: verbs.check([v, Verb(("facts", "set"), "f.py", "read")])),
        VerbTableError))
    check("an empty table fails check()",
          isinstance(raises(lambda: verbs.check([])), VerbTableError))
    check("an example that routes to a longer verb fails check()",
          "routes to 'a b c'" in str(raises(lambda: verbs.check([
              Verb(("a", "b"), "a.py", "read", examples=("a b c --json",)),
              Verb(("a", "b", "c"), "a_b_c.py", "read")]))))
    check("load(): the shop harness's verbs.py, checked, in order",
          len(TABLE) == 23 and TABLE[0].words == ("facts", "init")
          and all(isinstance(x, Verb) for x in TABLE))
    config.use(_shop.SHOP)
    check("load() without a path reads the bound harness's table",
          verbs.load() == TABLE)
    d = Path(tmp_dir("verbs-file-"))
    (d / "verbs.py").write_text("X = 1\n", encoding="utf-8")
    check("a verbs.py without VERBS is refused",
          "defines no VERBS" in str(raises(lambda: verbs.load(d / "verbs.py"))))
    check("a missing verbs.py is refused",
          "no verb table" in str(raises(lambda: verbs.load(d / "nope.py"))))
    kinds = {k for k in verbs.KINDS}
    check("the six kinds, closed", kinds == {"read", "ingest", "human",
                                             "gated", "external", "dev"})
    gated = verbs.of_kind(TABLE, "gated")
    check("of_kind: the gated verbs, in table order",
          [x.command for x in gated] == ["facts confirm", "facts restore",
                                         "decisions confirm", "queue approve"])
    check("of_kind refuses an unknown kind",
          isinstance(raises(lambda: verbs.of_kind(TABLE, "writes")),
                     VerbTableError))
    t = verbs.targets(TABLE)
    check("targets: {words: script}", t[("compute", "steer")]
          == "compute_steer.py" and t[("facts", "confirm")] == "facts.py")
    m = verbs.match(["compute", "sales", "deep", "x"], ECHO_TABLE)
    check("match: the longest listed prefix", m.words == ("compute", "sales",
                                                          "deep"))
    check("match: none for an unlisted verb",
          verbs.match(["things"], ECHO_TABLE) is None
          and verbs.match(["thing", "list"], ECHO_TABLE) is None)
    check("group: the verbs under the typed words",
          [x.command for x in verbs.group(["things", "--help"], ECHO_TABLE)]
          == ["things list", "things set"]
          and verbs.group(["zzz"], ECHO_TABLE) == [])
    check("script_args: a family script gets the words after the first",
          verbs.script_args(ECHO_TABLE[0]) == ["list"]
          and verbs.script_args(Verb(("execute", "apply"),
                                     "execute_actions.py", "external"))
          == ["apply"]
          and verbs.script_args(Verb(("import", "codes"), "import_file.py",
                                     "ingest")) == ["codes"])
    check("script_args: a script named after the whole verb gets none",
          verbs.script_args(ECHO_TABLE[2]) == []
          and verbs.script_args(Verb(("compute", "price-ladder"),
                                     "compute_price_ladder.py", "read")) == []
          and verbs.script_args(Verb(("pending",), "pending.py", "read"))
          == [])
    check("answers: the verb's own line first",
          verbs.answers(ECHO_TABLE[5], SCRIPTS) == "Notes, no data dir")
    check("answers: else the script docstring's first line",
          verbs.answers(Verb(("x",), "facts.py", "read"), SCRIPTS)
          == "Client facts: what the owner told us, pending until a person "
             "confirms.")
    check("answers: '' for a missing script",
          verbs.answers(Verb(("x",), "nope.py", "read"), SCRIPTS) == "")
    check("examples / keys default to (); a list is kept as a tuple",
          v.examples == () and v.keys == ()
          and Verb(("a",), "x.py", "read", examples=["a --json"],
                   keys=["rows"]).examples == ("a --json",)
          and Verb(("a",), "x.py", "read", keys=["rows"]).keys == ("rows",))
    for label, make in (
            ("examples as one string",
             lambda: Verb(("a", "b"), "x.py", "read", examples="a b")),
            ("an example that is not a string",
             lambda: Verb(("a",), "x.py", "read", examples=(1,))),
            ("a blank example",
             lambda: Verb(("a",), "x.py", "read", examples=("  ",))),
            ("an example of another verb",
             lambda: Verb(("a", "b"), "x.py", "read", examples=("a c",))),
            ("an example with the cli name",
             lambda: Verb(("a",), "x.py", "read", examples=("shop a",))),
            ("keys as one string",
             lambda: Verb(("a",), "x.py", "read", keys="rows")),
            ("a key with a space",
             lambda: Verb(("a",), "x.py", "read", keys=("the rows",))),
            ("an empty key", lambda: Verb(("a",), "x.py", "read", keys=("",)))):
        check(f"refused at construction: {label}",
              isinstance(raises(make), VerbTableError))
    n = ECHO_TABLE[5]
    check("examples(): whitespace collapsed; help_tail: the Examples block "
          "(cli-prefixed) and the JSON keys line",
          verbs.examples(n) == ["notes x", "notes --json"]
          and verbs.help_tail(n, "shop") == "\nExamples:\n  shop notes x\n"
          "  shop notes --json\n\nJSON keys: argv, script\n"
          and verbs.help_tail(ECHO_TABLE[0], "shop") == ""
          and verbs.help_tail(Verb(("a",), "x.py", "read", keys=("k",)),
                              "c") == "\nJSON keys: k\n")
    row = verbs.as_dict(n, "shop")
    check("as_dict: examples (cli-prefixed) and keys; [] when none",
          row["examples"] == ["shop notes x", "shop notes --json"]
          and row["keys"] == ["argv", "script"]
          and verbs.as_dict(ECHO_TABLE[0], "shop")["examples"] == []
          and verbs.as_dict(ECHO_TABLE[0], "shop")["keys"] == [], row)


# ------------------------------------------------------------------ [2]

def test_help() -> None:
    print("\n[2] help, version, the verb table")
    d = Dispatcher(TABLE, scripts_dir=SCRIPTS, epilog="EPILOG LINE")
    rc, out, err = capture(d.main, ["--help"], env=env())
    check("--help: exit 0 on stdout", rc == 0 and out and not err, err)
    for v in TABLE:
        check(f"--help lists `shop {v.command}` with what it answers",
              f"shop {v.command}" in out and verbs.answers(v, SCRIPTS) in out)
    heads = [ln.split(" — ")[0] for ln in out.splitlines() if " — " in ln
             and not ln.startswith(" ")]
    check("grouped by kind, in kind order, then the built-ins",
          heads == ["read", "human", "gated", "external"]
          and out.index("gated —") < out.index("built-in")
          and "shop doctor [--live] [--strict] [--json]" in out
          and "shop verbs [--json]" in out, heads)
    check("the env chain and the epilog", "SHOP_AUTH_ENV_PATHS=none" in out
          and out.rstrip().endswith("EPILOG LINE"))
    check("`help` is --help", capture(d.main, ["help"], env=env())[1] == out)
    rc, out2, err2 = capture(d.main, [], env=env())
    check("no verb: the same usage on stderr, exit 2",
          rc == 2 and not out2 and err2 == out)
    rc, out, _ = capture(d.main, ["facts", "--help"], env=env())
    check("a group's --help lists that group only, exit 0",
          rc == 0 and "shop facts confirm" in out and "shop queue" not in out
          and "built-in" not in out, out)
    rc, out, _ = capture(d.main, ["--version"], env=env())
    check("--version: harness and kit versions",
          rc == 0 and out.startswith("shop-harness ") and "(kit 0.8.1)" in out,
          out)
    rc, out, _ = capture(d.main, ["verbs", "--json"], env=env())
    doc = one_doc(out)
    check("verbs --json: one document, every verb with its kind",
          rc == 0 and doc and [r["words"] for r in doc["verbs"]]
          == [list(v.words) for v in TABLE]
          and all(r["kind"] == v.kind and r["script"] == v.script
                  and r["command"] == f"shop {v.command}"
                  for r, v in zip(doc["verbs"], TABLE)), out[:300])
    fl = next(r for r in doc["verbs"] if r["words"] == ["facts", "list"])
    check("verbs --json: each verb's examples (cli-prefixed) and JSON keys",
          fl["examples"] == ["shop facts list --json",
                             "shop facts list --market US --pending --json"]
          and fl["keys"] == ["declared_markets", "pending_markets", "facts"]
          and all(isinstance(r["examples"], list) and isinstance(r["keys"],
                                                                  list)
                  for r in doc["verbs"]), fl)
    check("verbs --json: the kinds and the built-ins",
          set(doc["kinds"]) == set(verbs.KINDS)
          and [b["words"] for b in doc["builtins"]] == [["doctor"], ["verbs"]]
          and doc["cli"] == "shop")
    rc, out, _ = capture(d.main, ["verbs"], env=env())
    check("verbs (text): the help listing", rc == 0 and "shop pending" in out)
    check("a malformed table fails when the dispatcher is built",
          isinstance(raises(lambda: Dispatcher(
              [*TABLE, TABLE[0]], scripts_dir=SCRIPTS)), VerbTableError))


# ------------------------------------------------------------------ [3]

def test_refusals() -> None:
    print("\n[3] refusals")
    scripts = echo_dir()
    d = Dispatcher(ECHO_TABLE, scripts_dir=scripts)
    data = Path(tmp_dir("cli-data-"))
    rc, out, err = capture(d.main, ["frobnicate", "--json"], env=env(data))
    doc = one_doc(out)
    check("an unlisted verb: one coded document, exit 2, nothing run",
          rc == 2 and doc and doc["code"] == "cli_unknown_verb"
          and doc["params"]["words"] == "frobnicate"
          and doc["next"] == ["shop --help"] and "+ " not in err, (out, err))
    rc, out, err = capture(d.main, ["things", "delete", "x"], env=env(data))
    check("a near miss names the candidates (text: error/next on stderr)",
          rc == 2 and not out and "shop things list" in err
          and "next: shop things --help" in err, err)
    rc, out, err = capture(d.main, ["things", "--json"], env=env(data))
    check("a group word alone is not a verb", rc == 2
          and one_doc(out)["code"] == "cli_unknown_verb")
    rc, out, err = capture(d.main, ["things", "list", "--json"], env=env())
    doc = one_doc(out)
    check("no data dir: data_dir_unset, exit 2, the script never runs",
          rc == 2 and doc["code"] == "data_dir_unset"
          and doc["next"][0].startswith("export SHOP_DATA_DIR=")
          and "+ " not in err, (out, err))
    rc, out, err = capture(d.main, ["things", "list"],
                           env=env(**{CFG.env("DATA_DIR"): "rel/dir"}))
    check("a relative data dir: data_dir_not_absolute (text on stderr)",
          rc == 2 and "relative path" in err and not out, err)
    rc, out, err = drive(scripts, ["things", "list", "--help"], env())
    check("--help reaches the script without a data dir",
          rc == 0 and (one_doc(out) or {}).get("argv") == ["list", "--help"],
          (out, err))
    rc, out, err = drive(scripts, ["notes", "--help"], env())
    head, _, tail = out.partition("\nExamples:\n")
    check("a verb's own --help: the script's help, then its examples "
          "(cli-prefixed) and JSON keys",
          rc == 0 and (one_doc(head) or {}).get("argv") == ["--help"]
          and tail == "  shop notes x\n  shop notes --json\n\n"
                      "JSON keys: argv, script\n", (out, err))
    rc, out, err = drive(scripts, ["notes", "--", "-h"], env())
    check("-h after `--` too", rc == 0 and "\nExamples:\n" in out, out)
    rc, out, err = drive(scripts, ["notes", "--help"],
                         env(ECHO_EXIT="3"))
    check("a script whose --help fails: its exit code, no examples",
          rc == 3 and "Examples:" not in out, out)
    rc, out, err = drive(scripts, ["notes", "x"], env())
    check("no --help: no examples after the document",
          rc == 0 and "Examples:" not in out and "JSON keys" not in out, out)
    rc, out, err = drive(scripts, ["things", "list", "--help"], env())
    check("a verb that declares neither: nothing after the script's help",
          rc == 0 and out.rstrip().endswith("}") and "Examples" not in out,
          out)
    rc, out, err = capture(d.main, ["things", "--help"], env=env())
    check("a group's --help stays the listing (no examples)",
          rc == 0 and "Examples" not in out and "shop things list" in out,
          out)
    rc, out, err = drive(scripts, ["notes", "x"], env())
    check("a verb with needs_data_dir=False runs without one",
          rc == 0 and (one_doc(out) or {}).get("argv") == ["x"], (out, err))
    rc, out, err = capture(d.main, ["gone", "--json"], env=env())
    check("a listed verb with no script: cli_script_missing",
          rc == 2 and one_doc(out)["code"] == "cli_script_missing", out)


# ------------------------------------------------------------------ [4]

def test_forwarding() -> None:
    print("\n[4] forwarding")
    check("strip_dashdash: the first `--` only, wherever it sits",
          strip_dashdash(["a", "--", "b", "--", "c"]) == ["a", "b", "--", "c"]
          and strip_dashdash(["a"]) == ["a"])
    check("masked: a --code value never shows",
          masked(["x", "--code", "123456", "--code=654321", "--codex"])
          == ["x", "--code", "***", "--code=***", "--codex"])
    scripts = echo_dir()
    data = Path(tmp_dir("cli-data-"))

    def go(argv, e=None, cwd=None):
        rc, out, err = drive(scripts, argv, e or env(data), cwd)
        return rc, one_doc(out) or {"stdout": out}, err

    rc, doc, err = go(["things", "set", "k", "v", "--", "--x", "--", "y"])
    check("a shared script: its sub-verb, then the args; the first `--` "
          "stripped, the second kept",
          rc == 0 and doc.get("script") == "multi.py"
          and doc.get("argv") == ["set", "k", "v", "--x", "--", "y"], doc)
    rc, doc, err = go(["compute", "sales", "--by", "week", "--json"])
    check("a script named after the verb: no verb words; flags pass "
          "without `--`",
          doc.get("argv") == ["--by", "week", "--json"]
          and doc.get("script") == "compute_sales.py", doc)
    rc, doc, err = go(["import", "codes", "f.csv"])
    check("a script of one verb not named after it (import_file.py for "
          "`import codes`): its sub-verb too",
          doc.get("script") == "import_file.py"
          and doc.get("argv") == ["codes", "f.csv"], doc)
    rc, doc, err = go(["compute", "--", "sales", "--tree"])
    check("`--` right after a word: stripped before the verb is matched",
          doc.get("argv") == ["--tree"], doc)
    rc, doc, err = go(["compute", "sales", "deep", "z"])
    check("the longest prefix wins", doc.get("script") == "compute_sales_deep.py"
          and doc.get("argv") == ["z"], doc)
    rc, out, err = drive(scripts, ["things", "set", "--code", "123456",
                                   "--code=654321"], env(data))
    check("stdout is the child's document alone; the `+ cmd` trace is on "
          "stderr with the code masked",
          one_doc(out) is not None and err.startswith("+ ")
          and "multi.py set --code '***' '--code=***'" in err
          and "123456" not in err and "654321" not in err, err)
    check("the child still got the code",
          one_doc(out)["argv"] == ["set", "--code", "123456", "--code=654321"])
    rc, doc, err = go(["things", "list"], env(data, ECHO_EXIT="3"))
    check("the child's exit code is the dispatcher's", rc == 3, rc)
    rc, doc, err = go(["things", "list"], env(data, ECHO_EXIT="signal"))
    check("a child killed by a signal: 128 + its number",
          rc == 128 + signal.SIGTERM, rc)

    envfile = Path(tmp_dir("cli-env-")) / "client.env"
    envfile.write_text("FROM_FILE=file\nFROM_BOTH=file\nSHOP_TOKEN_SOURCE="
                       "command\n", encoding="utf-8")
    e = env(data, FROM_BOTH="process")
    e[CFG.env("AUTH_ENV_PATHS")] = str(envfile)
    rc, doc, err = go(["things", "list"], e)
    got = doc.get("env", {})
    check("the env chain reaches the child: file values fill what the "
          "process lacks, the process env wins",
          got.get("FROM_FILE") == "file" and got.get("FROM_BOTH") == "process"
          and got.get("SHOP_TOKEN_SOURCE") == "command", doc)
    (data / ".env").write_text("FROM_DATA=data\n", encoding="utf-8")
    e = env(data)
    del e[CFG.env("AUTH_ENV_PATHS")]
    rc, doc, err = go(["things", "list"], e)
    check("with no override the chain reads <DATA_DIR>/.env",
          doc.get("env", {}).get("FROM_DATA") == "data", doc)
    cwd = Path(tmp_dir("cli-cwd-"))
    (cwd / ".env").write_text("FROM_CWD=cwd\n", encoding="utf-8")
    rc, doc, err = go(["things", "list"], e, cwd)
    check("never the cwd's .env", rc == 0 and "FROM_CWD" not in doc.get(
        "env", {"FROM_CWD": 1}), doc)


# ------------------------------------------------------------------ [5]

def real_env(data: Path, **extra) -> dict:
    return sandbox_env(data, PYTHONPATH=str(_shop.PLAYBOOK),
                       PYTHONDONTWRITEBYTECODE="1", **extra)


def shop(data: Path, *argv: str, stdin: str | None = None, **extra):
    return sandbox_run([sys.executable, str(CLI), *argv], real_env(data, **extra),
                       stdin=stdin)


def test_real_cli() -> None:
    print("\n[5] through the harness CLI (a subprocess)")
    data = Path(tmp_dir("cli-real-"))
    rc, out, err = shop(data, "facts", "list", "--", "--json")
    check("a read verb without a DB: no_db, nothing created",
          rc == 2 and one_doc(out)["code"] == "no_db"
          and list(data.iterdir()) == [], (out, err))
    check("the trace names the script and its sub-verb",
          "facts.py list --json" in err, err)
    rc, out, err = shop(data, "facts", "init", "--json", stdin="US\n4.2\n\n")
    doc = one_doc(out)
    check("init through the CLI: the declaration pending, the answer pending",
          rc == 0 and doc["declaration"] == "pending"
          and [w["key"] for w in doc["written"]] == ["market_declared",
                                                     "unit_cost"], (out, err))
    rc, out, err = shop(data, "facts", "list", "--json")
    check("facts list --json (no `--`): one document",
          rc == 0 and one_doc(out)["pending_markets"] == ["US"], out)
    rc, out, err = shop(data, "facts", "list", "--help")
    check("`shop facts list --help`: the script's usage, then its examples "
          "and JSON keys", rc == 0 and out.index("usage:")
          < out.index("\nExamples:\n  shop facts list --json\n  shop facts "
                      "list --market US --pending --json\n")
          and out.endswith("\nJSON keys: declared_markets, pending_markets, "
                           "facts\n"), (out, err))
    for v in TABLE:
        if v.kind != "read" or not v.keys:
            continue
        for ex in v.examples:
            rc, out, err = shop(data, *ex.split())
            doc = one_doc(out) or {}
            check(f"the example `shop {ex}` runs and has its JSON keys",
                  rc == 0 and set(v.keys) <= set(doc), (out, err[-300:]))
    rc, out, err = shop(data, "pending", "--json")
    check("pending --json: the declaration and the fact as asks",
          rc == 0 and len(one_doc(out)["asks"]) == 2, out[:300])
    rc, out, err = shop(data, "compute", "steer", "--json", "--market", "US")
    check("a compute the market is not confirmed for: coded refusal",
          rc == 2 and one_doc(out)["code"] == "market_not_onboarded"
          if one_doc(out) else False, (out, err))
    config.use(_shop.SHOP)
    problems = json_contract.check_read_verbs(
        data, run=lambda argv: sandbox_run([sys.executable, str(CLI), *argv],
                                           real_env(data)),
        skip=("compute steer",))
    check("the json contract guard, over the table verbs.py lists",
          problems == [], problems)
    write, gated = boundary.patterns("shop", verbs.load())
    check("the boundary guard reads the kinds from verbs.py",
          write.search("shop facts set x 1") and gated.search(
              "shop queue approve 1") and not write.search("shop facts list"))
    check("harness.toml gives the sample arguments of the verbs that need "
          "some", json_contract.sample_args(config.config())
          == {"facts get": ["unit_cost"],
              "decisions get": ["product", "SKU-1", "stage"]})
    empty = Path(tmp_dir("cli-empty-"))
    problems = json_contract.check_read_verbs_no_db(
        empty, run=lambda argv: sandbox_run([sys.executable, str(CLI), *argv],
                                            real_env(empty)))
    check("every read verb on an empty data dir, `facts get` and "
          "`decisions get` with their sample arguments: no_db, nothing "
          "created", problems == [], problems)
    problems = json_contract.check_read_verbs_no_db(
        empty, run=lambda argv: sandbox_run([sys.executable, str(CLI), *argv],
                                            real_env(empty)), args={})
    check("…without them `facts get` fails on its usage (the samples are "
          "what makes it pass)", any(p.startswith("facts get:")
                                     for p in problems), problems)
    for argv, code in ((["decisions", "get", "--json"], "usage"),
                       (["queue", "approve", "--json"], "usage"),
                       (["execute", "apply", "--nope", "--json"], "usage"),
                       (["pending", "--nope", "--json"], "usage"),
                       (["compute", "steer", "--nope", "--json"], "usage"),
                       (["facts", "get", "--json"], "fact_usage")):
        rc, out, err = shop(data, *argv)
        doc = one_doc(out)
        check(f"`shop {' '.join(argv)}`: one coded usage document, no "
              f"argparse text", rc == 2 and doc and doc.get("code") == code
              and "usage:" not in err, (out, err[-300:]))
    rc, out, err = shop(data, "doctor", "--json")
    doc = one_doc(out)
    check("the built-in doctor through the CLI: one document",
          rc == 0 and doc and doc["checks"] and "summary" in doc, out[:300])
    rc, out, err = shop(data, "verbs", "--json")
    check("verbs --json through the CLI", rc == 0
          and len(one_doc(out)["verbs"]) == len(TABLE))


# ------------------------------------------------------------------ [6]

def test_codes() -> None:
    print("\n[6] the cli.tsv and doctor.tsv fragments")
    reg = messages.registry()
    for mod in ("cli", "doctor"):
        own = {c: r for c, r in reg.items()
               if Path(r["file"]).name == f"{mod}.tsv"}
        check(f"{mod}.tsv: every code prefixed {mod}_",
              own and all(c.startswith(f"{mod}_") for c in own), sorted(own))
        check(f"{mod}.tsv: en + zh meanings",
              all(r["meaning_en"] and r["meaning_zh"] for r in own.values()))
        problems = messages.check_registry_closed(
            _shop.KIT, [f"{mod}.py"], registry=own, strict_kit=True)
        check(f"every code {mod}.py emits is in {mod}.tsv with exact params, "
              f"and every row is emitted", problems == [], problems)


def main() -> int:
    test_table()
    test_help()
    test_refusals()
    test_forwarding()
    test_real_cli()
    test_codes()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
