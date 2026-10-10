#!/usr/bin/env python3
"""The guards (kit/guards/*), each on a temp copy of the fake "shop"
harness (kit/tests/fake_harness) made whole: an ssot index with owner and
agent files, layered scripts, a stand-in CLI, a consumer console, the kit
vendored into scripts/kit, a git repository with a generated
.gitattributes.

  [0] the clean copy passes every guard
  [1] ssot: a missing index row, an owner file with a flag (and every
      other engineering token), a restated threshold, a sibling that lost
      an id, duplicate ids, a stray tab, a bad owner kind, a missing reader
      or test, a bad id column, a missing code symbol, a banned term, the
      held ratchet, a deleted id (vs git HEAD), a Chinese index header;
      an agent-only row proposed or asked is fine, accepted is not
  [2] release: an archive shipping tests/, a planning file or dropping
      SKILL.md, a registry with a reader left out, an opened .tsv not
      shipped, a stale internal entry, the vendored kit made internal
  [3] layering: a pull importing the db, a compute importing the writer,
      an ingest importing the client, a compute spawning, a library
      importing a script; the engine's own planted self-test
  [3b] clock: a script reading datetime.now(), a time.strftime with no
      time, a name bound by `from kit.dates import now`, a stale allowed
      row, an allowed read, a vendored clock that reads the calendar twice,
      no code to read; the rule's own planted self-test
  [4] json contract: code_ok / uncoded / meta / failure units; then the
      read verbs through the CLI: an uncoded English message in a --json
      doc, an unclassified crash, a read verb that creates the DB
  [5] boundary: the core naming the console; a consumer invoking a write
      verb, a gated verb without the relay flags, --apply, the DB
  [6] drift: a drifted vendored file, an added / removed file, a console
      VERSION that is not the kit's, no vendored kit
  [7] after every plant was undone, the copy is clean again

Every plant is undone before the next, so each proves one rule.
"""

import contextlib
import os
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"       # the verbs run as children
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull      # no user git config decides
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

import _shop  # noqa: E402
from kit import config, contract, messages  # noqa: E402
from kit.guards import (boundary, changelog, clock, drift, json_contract,  # noqa: E402
                        layering, release, ssot)
from kit.messages import coded, msg  # noqa: E402
from kit.testing.check import capture, check, finish, tmp_dir  # noqa: E402
from kit.tools import manifest, vendor  # noqa: E402

V = namedtuple("V", "words script kind takes_market needs_data_dir",
               defaults=(True, True))
VERBS = [V(("compute", "sales"), "compute_sales.py", "read"),
         V(("compute", "stories"), "compute_stories.py", "read"),
         V(("notes", "list"), "notes_list.py", "read", False),
         V(("facts", "list"), "facts.py", "read"),
         V(("facts", "set"), "facts.py", "human"),
         V(("facts", "confirm"), "facts.py", "gated"),
         V(("queue", "approve"), "queue.py", "gated"),
         V(("pull", "orders"), "pull_orders.py", "external"),
         V(("execute", "apply"), "execute_actions.py", "external"),
         V(("test",), "run_tests.py", "dev", False, False)]
READS = [V(("compute", "sales"), "compute_sales.py", "read"),
         V(("notes", "list"), "notes_list.py", "read", False)]
HAS_GIT = shutil.which("git") is not None

GUARDS_TOML = """
[guards.ssot]
cli_verbs = ["compute", "notes", "facts", "queue", "pull", "execute"]
table_prefixes = ["ext_"]
banned_terms = {"variant group" = "product family"}
"""

SHOP_CLI = '''"""A stand-in for the harness CLI (kit.cli) in the kit's guard tests:
routes a verb's words to its script, strips the first literal `--`,
forwards the rest."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTES = {("compute", "sales"): "compute_sales.py",
          ("compute", "bad"): "compute_bad.py",
          ("compute", "crash"): "compute_crash.py",
          ("compute", "creates"): "compute_creates.py",
          ("notes", "list"): "notes_list.py"}


def main(argv):
    for n in (2, 1):
        script = ROUTES.get(tuple(argv[:n]))
        if script:
            rest = argv[n:]
            if "--" in rest:
                rest.remove("--")
            return subprocess.call([sys.executable, str(HERE / script),
                                    *rest], stdin=subprocess.DEVNULL)
    print(f"error: no verb {argv[:2]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

COMPUTE = '''"""A fixture compute of the shop harness."""
import sys

from kit import contract
from kit.messages import coded, msg

THRESHOLD_NAMES = ("low_stock_units",)
DAYS = ["2026-09-01", "2026-09-02"]


def main(argv):
    err = contract.missing_db_error()
    if err is not None:
        raise err
    low = msg("shop_stock_low", "A1 has only 2 unit(s) left", sku="A1",
              units=2)
    m = contract.meta(
        window=contract.window("2026-09-01", "2026-09-03", DAYS),
        sources=[{"table": "orders", "pulled_on": "2026-09-03T08:00:00Z"}],
        stale=[],
        coverage={"missing_days": contract.missing_days(
            "2026-09-01", "2026-09-03", DAYS), "next": ["shop pull orders"]})
    WARN
    return {"meta": m, "rows": [{"sku": "A1", "units": 2,
                                 "status": "low_stock"}],
            **warnings, "argv": argv}


if __name__ == "__main__":
    raise SystemExit(contract.run_main(main, sys.argv[1:],
                                       cmd=["shop", "compute", "sales"]))
'''
GOOD_WARN = 'warnings = coded("warnings", [low])'
BAD_WARN = 'warnings = {"warnings": ["Price is missing for A1"]}'

NOTES_LIST = '''"""A fixture read verb of the shop harness (no meta: not a compute)."""
import sys

from kit import contract


def main(argv):
    err = contract.missing_db_error()
    if err is not None:
        raise err
    return {"items": [{"sku": "A1", "note": "restock_soon"}]}


if __name__ == "__main__":
    raise SystemExit(contract.run_main(main, sys.argv[1:],
                                       cmd=["shop", "notes", "list"]))
'''

CONSOLE_RELAY = '''"""Relays a human's click through the harness's gate."""
import subprocess


def confirm(key, code, user, at):
    argv = ["shop", "facts", "confirm", key]
    argv += [f"--code={code}", f"--relay-user=web:{user}",
             f"--relay-at={at}"]
    return subprocess.run(argv, capture_output=True, text=True)


def report():
    return subprocess.run(["shop", "compute", "sales", "--", "--json"],
                          capture_output=True, text=True)
'''

FILES = {
    "CLAUDE.md": "# For people changing the code\n",
    "README.md": "# The shop harness\n",
    "SKILL.md": "---\nname: shop-harness\n---\n",
    "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n",
    "evals/README.md": "# Agent-behaviour evals\n",
    ".claude/settings.json": "{}\n",
    "tests/test_ssot.py": "# the harness's ssot test\n",
    "ssot/README.md": "Who writes which file.\n",
    "ssot/glossary.tsv": "term\tdefinition\n"
                         "product family\tA parent product and all its "
                         "variants.\n"
                         "stock\tUnits on hand that can be sold today.\n",
    "ssot/glossary.agent.tsv": "term\tstatus\twhere_in_data\n"
                               "product family\taccepted\torders.parent_id\n"
                               "stock\taccepted\tinventory.units\n",
    "ssot/user-stories.tsv": "id\ti_want\tdone_when\n"
                             "S01\tone page listing what waits for my "
                             "decision\teach waiting item shows its "
                             "evidence\n"
                             "S02\ta warning when stock runs low\tthe "
                             "warning names the product and the units left, "
                             "below low_stock_units\n",
    "ssot/user-stories.agent.tsv": "id\tstatus\tcheck\n"
                                   "S01\taccepted\tcompute_sales.py rows>0\n"
                                   "S02\taccepted\t—\n"
                                   "S03\tproposed\t—\n",
    "ssot/constants.tsv": "name\tdefault\tunit\twhy\n"
                          "low_stock_units\t5\tunits\tbelow this a product "
                          "needs a reorder\n",
    "ssot/story_checks.tsv": "story\tverb\texpect\tneeds\tnote\n"
                             "S01\tcompute sales\trows>0\t—\tthe sales "
                             "report lists products\n"
                             "S02\t—\t—\t—\tproved by the test suite\n",
    "scripts/_lib/__init__.py": "",
    "scripts/_lib/shop_api.py": "import json\nimport urllib.request\n",
    "scripts/_lib/writer.py": "from _lib import shop_api\n",
    "scripts/pull_orders.py": "from kit import raw\nfrom _lib import shop_api\n",
    "scripts/ingest_orders.py": "from kit import raw\n",
    "scripts/compute_sales.py": COMPUTE.replace("WARN", GOOD_WARN),
    "scripts/compute_stories.py": "import sys\n\nfrom kit import stories\n\n"
                                  "if __name__ == '__main__':\n"
                                  "    raise SystemExit(stories.main("
                                  "sys.argv[1:]))\n",
    "scripts/execute_actions.py": "from _lib import writer\n",
    "scripts/notes_list.py": NOTES_LIST,
    "scripts/shop.py": SHOP_CLI,
    "console/relay.py": CONSOLE_RELAY,
    "console/README.md": "Relays answers; reads the harness's --json.\n",
}
T = "tests/test_ssot.py"
INDEX = [  # file, owner, reader, test, id, purpose
    ("ssot/index.tsv", "registry", "—", T, "file",
     "every registry and owner file"),
    ("ssot/glossary.tsv", "owner", "—", T, "term",
     "one name per business idea"),
    ("ssot/glossary.agent.tsv", "agent", "—", T, "term",
     "the same terms: status and where they live"),
    ("ssot/user-stories.tsv", "owner", "—", T, "id", "what the owner wants"),
    ("ssot/user-stories.agent.tsv", "agent", "—", T, "id",
     "the same ids: status and checks"),
    ("ssot/constants.tsv", "registry", "scripts/compute_sales.py", T, "name",
     "threshold defaults"),
    ("ssot/message_codes.tsv", "registry", "scripts/notes.py", T, "code",
     "the harness's message codes"),
    ("ssot/story_checks.tsv", "registry", "scripts/compute_stories.py", T,
     "story", "one runnable check per story"),
    ("scripts/compute_sales.py:THRESHOLD_NAMES", "registry",
     "scripts/compute_sales.py", T, "name", "the thresholds a report names"),
]
HEADER = ("file", "owner", "reader", "test", "id_column", "purpose")


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def unique_id(table: list[list[str]]) -> str:
    """The shortest prefix of columns that identifies every row."""
    head = table[0]
    for n in range(1, len(head) + 1):
        ids = [tuple(r[:n]) for r in table[1:]]
        if all(all(i) for i in ids) and len(ids) == len(set(ids)):
            return "+".join(head[:n])
    return head[0]


def index_text(root: Path, rows=None, header=HEADER) -> str:
    """The index: `rows` (default INDEX), plus a planning row for every
    ssot TSV INDEX does not know (another kit module's tests may add
    registries to the fake harness)."""
    rows = list(INDEX if rows is None else rows)
    known = {r[0] for r in INDEX}
    for p in sorted((root / "ssot").rglob("*.tsv")):
        rel = p.relative_to(root).as_posix()
        if rel not in known:
            rows.append((rel, "registry", "—", T,
                         unique_id(ssot.tsv(p)), "another module's registry"))
    return "\n".join("\t".join(r) for r in [header, *rows]) + "\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=kit test",
         "-c", "user.email=kit@example.com", "-c", "commit.gpgsign=false",
         *args], capture_output=True, text=True, check=True).stdout


def build() -> Path:
    root = Path(tmp_dir("guards-")) / "shop"
    shutil.copytree(_shop.SHOP, root, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".DS_Store"))
    toml = root / "harness.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + GUARDS_TOML,
                    encoding="utf-8")
    for rel, text in FILES.items():
        write(root, rel, text)
    write(root, "ssot/index.tsv", index_text(root, INDEX))
    vendor.sync(_shop.KIT, root / "scripts" / "kit", {})
    write(root, "console/VERSION", (_shop.KIT / "VERSION").read_text())
    manifest.write(root / "console")
    write(root, ".gitattributes", release.render_gitattributes(root))
    if HAS_GIT:
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "the clean shop harness")
    return root


@contextlib.contextmanager
def planted(root: Path, rel: str, text: str | None = None, *,
            append: str | None = None, replace: tuple[str, str] | None = None,
            remove: bool = False):
    """Change one file for the duration of the block, then restore it."""
    p = root / rel
    old = p.read_bytes() if p.exists() else None
    try:
        if remove:
            p.unlink()
        else:
            if append is not None:
                text = (old or b"").decode() + append
            elif replace is not None:
                text = (old or b"").decode().replace(*replace)
                assert text != old.decode(), f"nothing to replace in {rel}"
            write(root, rel, text)
        yield p
    finally:
        if old is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(old)


def caught(label: str, problems: list[str], *needles: str) -> None:
    text = "\n".join(problems)
    check(label, bool(needles) and all(n in text for n in needles),
          text or "(nothing found)")


def clean(label: str, problems: list[str]) -> None:
    check(label, problems == [], "\n".join(problems))


def everything(root: Path, data: Path) -> dict[str, list[str]]:
    return {"ssot": ssot.check_index(root),
            "release": release.check_release(root),
            "changelog": changelog.check_changelog(root),
            "layering": layering.check_layers(root),
            "clock": clock.check_reads(root),
            "boundary": boundary.check_boundaries(root, verbs=VERBS),
            "drift": drift.check_harness(root),
            "json contract": json_contract.check_read_verbs(data,
                                                            verbs=READS)}


# ------------------------------------------------------------ [1b] changelog

def test_changelog(root: Path) -> None:
    print("\n[1b] changelog: whole, and SKILL.md's version is its newest release")
    check("the rule's planted self-test: every fault caught, a clean log passes",
          changelog.self_test() == [], changelog.self_test())
    with planted(root, "CHANGELOG.md", remove=True):
        caught("no CHANGELOG.md", changelog.check_changelog(root), "does not exist")
    clean("a SKILL.md with no version is not held to one", changelog.check_changelog(root))
    versioned = "---\nname: shop-harness\nversion: 0.2.0\n---\n"
    with planted(root, "SKILL.md", versioned):
        caught("SKILL.md bumped without an entry", changelog.check_changelog(root),
               "a version bump needs its entry")
        with planted(root, "CHANGELOG.md",
                     "# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2026-01-01\n\n### Added\n- x\n"):
            clean("a log whose newest release is SKILL.md's version", changelog.check_changelog(root))


# ---------------------------------------------------------------- [1] ssot

def test_ssot(root: Path) -> None:
    print("\n[1] ssot: the index, the owner-file lint, ids kept")
    r = ssot.rules(root)
    names = ssot.thresholds(r)
    check("thresholds come from [ssot].constants' first column",
          names == {"low_stock_units"}, names)
    for text in ("see #184", "run shop compute sales", "add --json",
                 "compute_sales.py", "the _lib code", "ssot/constants.tsv",
                 "the ext_orders table", "rows of client_facts",
                 "low_stock_units=20", "low_stock_units（20）",
                 "low_stock_units 20"):
        check(f"the lint catches {text!r}", bool(ssot.lint(text, r, names)))
    for text in ("points ≥ low_stock_units; a score of 8/10",
                 "the shop owner decides", "decisions stay with me",
                 "e.g. 3.5 units", "a store in 2026"):
        check(f"the lint leaves business prose alone: {text!r}",
              not ssot.lint(text, r, names), ssot.lint(text, r, names))

    us, ua = "ssot/user-stories.tsv", "ssot/user-stories.agent.tsv"
    with planted(root, us, replace=("shows its evidence",
                                    "shows its evidence (run with --json)")):
        caught("an owner file with a flag in it",
               ssot.check_index(root),
               "owner file ssot/user-stories.tsv S01 done_when", "'--json'")
    with planted(root, us, replace=("below low_stock_units",
                                    "below low_stock_units 5")):
        caught("an owner file restating a threshold's number",
               ssot.check_index(root), "S02 done_when",
               "'low_stock_units 5'")
    with planted(root, "ssot/index.tsv", index_text(
            root, [x for x in INDEX if x[0] != "ssot/glossary.agent.tsv"])):
        caught("a missing index row", ssot.check_index(root),
               "does not list ['ssot/glossary.agent.tsv']")
    with planted(root, ua, replace=("S02\taccepted\t—\n", "")):
        caught("an agent file that lost an id", ssot.check_index(root),
               "ssot/user-stories.agent.tsv: ids differ",
               "only in the owner file ['S02']")
    with planted(root, ua, append="S04\tproposed\t—\n"):
        clean("an agent-only row with status proposed is fine",
              ssot.check_index(root))
    with planted(root, ua, append="S04\tasked\t—\n"):
        clean("an agent-only row with status asked (sent to the console, "
              "not answered yet) is fine", ssot.check_index(root))
    with planted(root, ua, append="S04\taccepted\t—\n"):
        caught("an agent-only row with status accepted is not",
               ssot.check_index(root), "ssot/user-stories.agent.tsv: ids "
               "differ", "only in the agent file ['S04']")
    with planted(root, us, append="S01\tagain\tagain\n"):
        caught("a duplicate id", ssot.check_index(root),
               "ssot/user-stories.tsv: duplicate id(s) ['S01']")
    with planted(root, "ssot/glossary.tsv", append="price\tWhat we ask.\tx\n"):
        caught("a stray tab (rows of different widths)",
               ssot.check_index(root), "ssot/glossary.tsv: rows of different "
                                       "widths [2, 3]")
    bad_rows = [("ssot/glossary.tsv", "boss", "—", T, "term", "x")
                if x[0] == "ssot/glossary.tsv" else x for x in INDEX]
    with planted(root, "ssot/index.tsv", index_text(root, bad_rows)):
        caught("an owner kind outside owner | agent | registry",
               ssot.check_index(root), "owner is 'boss'")
    bad_rows = [(x[0], x[1], "scripts/gone.py", "—", "nope", "")
                if x[0] == "ssot/constants.tsv" else x for x in INDEX]
    with planted(root, "ssot/index.tsv", index_text(root, bad_rows)):
        caught("a missing reader, no test, a bad id column, no purpose",
               ssot.check_index(root),
               "reader/test paths do not exist: ['scripts/gone.py']",
               "ssot/constants.tsv: names no test",
               "id column 'nope' is not a column",
               "ssot/constants.tsv: purpose is empty")
    with planted(root, "scripts/compute_sales.py",
                 replace=("THRESHOLD_NAMES =", "NAMES =")):
        caught("a code-held registry whose symbol is gone",
               ssot.check_index(root),
               "scripts/compute_sales.py:THRESHOLD_NAMES: the file does not "
               "exist")
    with planted(root, "ssot/glossary.tsv",
                 replace=("A parent product", "A variant group")):
        caught("a banned term", ssot.check_index(root),
               "ssot/glossary.tsv: says 'variant group'; the term is "
               "'product family'")
    toml = (root / "harness.toml").read_text(encoding="utf-8")
    with planted(root, "harness.toml", toml + 'held = [["' + us +
                 '", "S01", "done_when"]]\n'):
        caught("held: an entry that no longer fails is itself a problem",
               ssot.check_index(root), "held ['ssot/user-stories.tsv', "
                                       "'S01', 'done_when'] no longer fails")
        with planted(root, us, replace=("its evidence", "its evidence #12")):
            clean("held: the held cell may still fail",
                  ssot.check_index(root))
    zh = ("文件", "主人", "读它的代码", "绑它的测试", "id列", "说明")
    with planted(root, "harness.toml", toml + "index_columns = {"
                 + ", ".join(f'{k} = "{v}"' for k, v in zip(
                     ssot.ORDER, zh)) + "}\n"):
        caught("index_columns: the default header is now wrong",
               ssot.check_index(root), "header is ['file'")
        zh_rows = [(*x[:4], "文件", x[5]) if x[0] == "ssot/index.tsv" else x
                   for x in INDEX]
        with planted(root, "ssot/index.tsv", index_text(root, zh_rows, zh)):
            clean("index_columns: an index in the configured (Chinese) "
                  "header passes", ssot.check_index(root))
    if HAS_GIT:
        with planted(root, us, replace=("S02\t", "S09\t")), \
                planted(root, ua, replace=("S02\t", "S09\t")):
            caught("a deleted id (renumbered S02 -> S09) vs git HEAD",
                   ssot.check_index(root),
                   "ssot/user-stories.tsv: id(s) ['S02'] were at git HEAD "
                   "and are gone",
                   "ssot/user-stories.agent.tsv: id(s) ['S02'] were at git "
                   "HEAD")
    else:
        check("skipped: the git HEAD comparison (no git)", True)


# ------------------------------------------------------------- [2] release

def test_release(root: Path) -> None:
    print("\n[2] release: what git archive ships")
    if not HAS_GIT:
        check("skipped: git is absent", release.check_release(root) == [])
        return
    tracked, ships = release.shipped(root)
    check("the vendored kit and console, SKILL.md and the registries with a "
          "reader ship; tests/ and planning files don't",
          {"scripts/kit/contract.py", "console/relay.py", "SKILL.md",
           "ssot/constants.tsv", "ssot/story_checks.tsv"} <= ships
          and not {"tests/test_ssot.py", "ssot/glossary.tsv",
                   "ssot/index.tsv", "CLAUDE.md"} & ships,
          sorted(ships)[:40])
    ga = ".gitattributes"
    with planted(root, ga, replace=("/tests export-ignore\n", "")):
        caught("a release archive shipping tests/", release.check_release(root),
               "internal files ship: ['tests/test_ssot.py']",
               ".gitattributes: lacks /tests export-ignore")
    with planted(root, ga, replace=("/ssot/glossary.tsv export-ignore\n", "")):
        caught("a planning file (no reader) shipping",
               release.check_release(root),
               "internal files ship: ['ssot/glossary.tsv']")
    with planted(root, ga, append="/SKILL.md export-ignore\n"):
        caught("SKILL.md left out", release.check_release(root),
               "[release].must_ship paths do not ship: ['SKILL.md']",
               "left out of the release but not internal: ['SKILL.md']",
               ".gitattributes: marks /SKILL.md export-ignore")
    with planted(root, ga, append="/ssot/constants.tsv export-ignore\n"):
        caught("a registry the index names a reader for, left out",
               release.check_release(root),
               "names a reader for do not ship: ['ssot/constants.tsv']")
    with planted(root, "scripts/compute_sales.py",
                 append='\nEXTRA = open("orders_map.tsv")\n'):
        caught("a .tsv runtime code opens by name that does not ship",
               release.check_release(root),
               "runtime code opens by name do not ship: ['orders_map.tsv']")
    with planted(root, "harness.toml", replace=(
            'internal = ["CLAUDE.md"', 'internal = ["docs", "CLAUDE.md"')):
        caught("a stale internal entry", release.check_release(root),
               "does not track (a stale entry hides nothing): ['docs']")
    with planted(root, "harness.toml", replace=(
            'internal = ["CLAUDE.md"', 'internal = ["scripts/kit", '
                                       '"CLAUDE.md"')):
        caught("the vendored kit made internal", release.check_release(root),
               "scripts/kit/ is internal (['scripts/kit']): the vendored "
               "copy must ship")
    text = release.render_gitattributes(root)
    check("render_gitattributes: internal then planning files, one "
          "export-ignore line each",
          "/tests export-ignore" in text and "/ssot/index.tsv export-ignore"
          in text and text.index("/tests") < text.index("/ssot/index.tsv")
          and "THRESHOLD_NAMES" not in text, text)


# ------------------------------------------------------------ [3] layering

def test_layering(root: Path) -> None:
    print("\n[3] layering: the import graph")
    check("the engine's planted self-test: every rule catches its "
          "violation, clean modules pass", layering.self_test() == [],
          layering.self_test())
    mem = layering.members(root)
    check("the layers have their modules (positive controls)",
          mem["pull"] == {"pull_orders"} and mem["ingest"] == {"ingest_orders"}
          and {"compute_sales", "compute_stories"} <= mem["compute"]
          and mem["writer"] == {"_lib.writer"}
          and mem["clients"] == {"_lib.shop_api"}, mem)
    with planted(root, "scripts/pull_orders.py", append="from kit import db\n"):
        caught("a pull importing db", layering.check_layers(root),
               "b: pull_orders reaches", "'kit.db'", "'sqlite3'")
    with planted(root, "scripts/compute_sales.py",
                 append="from _lib import writer\n"):
        caught("a compute importing the writer", layering.check_layers(root),
               "a: _lib.writer (the writer) is imported by ['compute_sales']",
               "d: compute_sales reaches ['_lib.shop_api', '_lib.writer', "
               "'urllib.request']")
    with planted(root, "scripts/ingest_orders.py",
                 append="def f():\n    from _lib import shop_api\n"):
        caught("an ingest importing the API client (even lazily)",
               layering.check_layers(root),
               "c: ingest_orders reaches ['_lib.shop_api'")
    with planted(root, "scripts/compute_sales.py", append="import subprocess\n"):
        caught("a compute that spawns (not spawn_allowed)",
               layering.check_layers(root),
               "f: computes that run another process: ['compute_sales']")
    with planted(root, "scripts/_lib/helper.py", "import compute_sales\n"):
        caught("a library importing a script", layering.check_layers(root),
               "h: _lib.helper imports the script(s) ['compute_sales']")


def test_clock(root: Path) -> None:
    print("\n[3b] clock: one calendar read")
    check("the rule's planted self-test: every forbidden form caught, the "
          "allowed ones not", clock.self_test() == [], clock.self_test())
    clean("the clean copy reads the calendar only through kit.dates",
          clock.check_reads(root))
    with planted(root, "scripts/compute_sales.py",
                 append="import datetime\nT = datetime.datetime.now()\n"):
        caught("a script reading datetime.datetime.now()",
               clock.check_reads(root), "compute_sales.py:", "(<module>)",
               "datetime.datetime.now")
    with planted(root, "scripts/compute_sales.py",
                 append="import time\n\ndef f():\n    return time.strftime"
                        "('%Y')\n"):
        caught("a time.strftime with no time value",
               clock.check_reads(root), "(f) time.strftime('%Y')")
    with planted(root, "scripts/_lib/helper.py",
                 "from kit.dates import now\n"):
        caught("a name bound at import", clock.check_reads(root),
               "from kit.dates import now")
    with planted(root, "scripts/kit/dates.py",
                 append="\n\ndef stray():\n    import time\n    return "
                        "time.ctime()\n"):
        caught("a clock that reads the calendar twice",
               clock.check_reads(root), "must read the stdlib clock once")
    with planted(root, "scripts/compute_sales.py",
                 append="import datetime\nT = datetime.date.today()\n"), \
            planted(root, "harness.toml", append=(
                '\n[guards.clock.allowed]\n"scripts/compute_sales.py::'
                '<module>" = 1\n')):
        clean("an allowed read (a late adopter's row) passes",
              clock.check_reads(root))
    with planted(root, "harness.toml", append=(
            '\n[guards.clock.allowed]\n"scripts/compute_sales.py::f" = 2\n')):
        caught("an allowed row for a read that has moved",
               clock.check_reads(root), "lists 2, 0 left")
    with planted(root, "harness.toml", append=(
            '\n[guards.clock]\ncode = ["nowhere"]\n')):
        caught("a code path that finds nothing is not a pass",
               clock.check_reads(root), "no code to read")


# ------------------------------------------------------- [4] json contract

def test_json_units() -> None:
    print("\n[4a] json contract: the checkers themselves")
    ok = {"code": "shop_stock_low", "params": {"sku": "A1", "units": 2}}
    check("code_ok: a registered code with exactly its params",
          json_contract.code_ok(ok))
    for bad in ({"code": "shop_stock_low", "params": {"sku": "A1"}},
                {**ok, "extra": 1}, {"code": "no_such", "params": {}},
                "shop_stock_low", None):
        check(f"code_ok refuses {bad!r}", not json_contract.code_ok(bad))
    low = msg("shop_stock_low", "A1 low", sku="A1", units=2)
    j = messages.joined([low, msg("shop_price_missing", "no price",
                                  sku="B2")])
    check("code_ok: nested messages (joined parts) are checked too",
          json_contract.code_ok(messages.code(j))
          and not json_contract.code_ok({"code": "joined", "params": {
              "parts": [{"code": "nope", "params": {}}]}}))
    u = json_contract.uncoded
    check("uncoded: English prose without its code is flagged, a "
          "snake_case machine value is not",
          u({"warnings": ["x y"], "rows": [{"reason": "prose here"}],
             "evidence": {"reason": "own_target"}})
          == [".warnings", ".rows[0].reason"],
          u({"warnings": ["x y"], "rows": [{"reason": "prose here"}],
             "evidence": {"reason": "own_target"}}))
    check("uncoded: coded() output passes (a list, a message, a null)",
          u({**coded("warnings", [low]), "row": coded("reason", low),
             "other": coded("note", None)}) == [])
    check("uncoded: a null message needs its null code beside it",
          u({"reason": None}) == [".reason"])
    check("uncoded: a data label path is data, not a message",
          u({"rows": [{"note": "Blue shirt"}]},
            data_labels=(".rows[].note",)) == [])
    check("uncoded: a key the document codes somewhere is a message "
          "everywhere",
          u({"rows": [{"verdict": "Buy more", **{"verdict_code":
                                                 messages.code(low)}},
                      {"verdict": "Hold on"}]}) == [".rows[1].verdict"])
    check("uncoded: a message with a malformed code, and a stray malformed "
          "code, are flagged",
          u({"x": "A1 low", "x_code": {"code": "shop_stock_low",
                                       "params": {}},
             "y_code": {"code": "nope", "params": {}}}) == [".x", ".y_code"],
          u({"x": "A1 low", "x_code": {"code": "shop_stock_low",
                                       "params": {}},
             "y_code": {"code": "nope", "params": {}}}))
    e = contract.HarnessError(msg("no_db", "No database at /x", path="/x"),
                              ["shop facts init"])
    fdoc = contract.failure_doc(e, ["shop", "compute", "sales"])
    check("uncoded: a failure document is coded at the top",
          u(fdoc) == [] and u({**fdoc, "code": "nope"}) == [".code"])

    window = contract.window("2026-09-01", "2026-09-03",
                             ["2026-09-01", "2026-09-02"])
    good = {"meta": contract.meta(
        window=window,
        sources=[{"table": "orders", "pulled_on": "2026-09-03T08:00:00Z"}],
        stale=[], coverage={"missing_days": ["2026-09-03"],
                            "next": ["shop pull orders"]})}
    check("check_meta: kit.contract.meta() passes",
          json_contract.check_meta(good) == [],
          json_contract.check_meta(good))
    m = good["meta"]
    for label, broken, needle in (
            ("no meta", {"rows": []}, "no top-level meta"),
            ("a required key missing",
             {"meta": {k: v for k, v in m.items() if k != "stale"}},
             "meta lacks ['stale']"),
            ("pulled_on in another format",
             {"meta": {**m, "sources": [{"table": "orders",
                                         "pulled_on": "2026-09-03"}]}},
             "meta.sources"),
            ("a stale entry within its lag",
             {"meta": {**m, "stale": [{"table": "orders", "last_date": "x",
                                       "lag_days": 1, "max_lag_days": 3}]}},
             "meta.stale"),
            ("missing days that don't add up",
             {"meta": {**m, "coverage": {"missing_days": [],
                                         "next": []}}},
             "does not match the window"),
            ("assumed thresholds on a plain run",
             {"meta": {**m, "assumed_thresholds": {"x": {"value": 1}}}},
             "assumed_thresholds is not {}"),
            ("another harness's name",
             {"meta": {**m, "harness": {**m["harness"], "name": "other"}}},
             "meta.harness")):
        caught(f"check_meta catches {label}",
               json_contract.check_meta(broken), needle)

    def failing(exc):
        def fn(argv):
            raise exc
        return lambda argv: contract.run_main(fn, argv, cmd=["shop", "x"])
    rc, out, err = capture(failing(e), ["--json"])
    check("check_failure: fail()'s document passes (and is no_db)",
          json_contract.check_failure(rc, out, err, codes={"no_db"}) == [],
          json_contract.check_failure(rc, out, err))
    rc, out, err = capture(failing(ValueError("boom")), ["--json"])
    caught("check_failure: an unclassified crash is flagged",
           json_contract.check_failure(rc, out, err), "unclassified_error")
    caught("check_failure: exit 0, prose on stdout, a traceback",
           json_contract.check_failure(
               0, "error: boom\n", "Traceback (most recent call last):\n"),
           "exit 0 on a failure", "not exactly one JSON document",
           "a traceback on stderr")
    caught("check_failure: another code than the expected one",
           json_contract.check_failure(rc, out, err, codes={"no_db"}),
           "is not one of ['no_db']")
    check("verb_argv: `--` after the words unless the rest has one",
          json_contract.verb_argv(["compute", "sales"], ["--by", "week"],
                                  ["--json"])
          == ["compute", "sales", "--", "--by", "week", "--json"]
          and json_contract.verb_argv(["x"], ["--", "-a"], ["--json"])
          == ["x", "--", "-a", "--json"])


def test_json_verbs(root: Path, data: Path) -> None:
    print("\n[4b] json contract: the read verbs, through the CLI")
    run = json_contract.cli_runner(data)
    rc, out, err = run(["compute", "sales", "--", "--json"])
    check("the stand-in CLI runs a read verb (positive control)",
          rc == 0 and '"warning_codes"' in out, (rc, out, err))
    bad = READS + [V(("compute", "bad"), "compute_bad.py", "read")]
    with planted(root, "scripts/compute_bad.py",
                 COMPUTE.replace("WARN", BAD_WARN)):
        caught("an uncoded English message in a --json doc",
               json_contract.check_read_verbs(data, verbs=bad),
               "compute bad: uncoded message at .warnings")
    crash = READS + [V(("compute", "crash"), "compute_crash.py", "read")]
    with planted(root, "scripts/compute_crash.py", COMPUTE.replace(
            "    WARN", "    raise ValueError('boom')")):
        caught("a read verb whose failure is unclassified",
               json_contract.check_read_verbs(data, verbs=crash),
               "compute crash: unclassified_error")
    empty = Path(tmp_dir("shop-empty-"))
    clean("no DB: every read verb fails no_db and creates nothing",
          json_contract.check_read_verbs_no_db(empty, verbs=READS))
    makes = READS + [V(("compute", "creates"), "compute_creates.py", "read")]
    with planted(root, "scripts/compute_creates.py",
                 "import os\nimport sqlite3\nsqlite3.connect(os.path.join("
                 "os.environ['SHOP_DATA_DIR'], 'shop.db')).close()\n"
                 "print('{}')\n"):
        caught("a read verb that creates the DB",
               json_contract.check_read_verbs_no_db(empty, verbs=makes),
               "compute creates: created ['shop.db']",
               "compute creates: exit 0 on a failure")


# ------------------------------------------------------------ [5] boundary

def test_boundary(root: Path) -> None:
    print("\n[5] boundary: the core names no adapter; a consumer only reads")
    write_re, gated_re = boundary.patterns("shop", VERBS)
    for text in ('run(["shop", "facts", "set", k, v])', "shop facts set k 1",
                 "uv run scripts/shop.py pull orders", '("pull", "orders")',
                 "shop execute apply", "[CLI, 'execute', 'apply']"):
        check(f"the write-verb pattern catches {text}",
              bool(write_re.search(text)))
    for text in ("shop facts list --json", '("facts", "list", "--json")',
                 "shop compute sales -- --json", '("facts", "confirm")',
                 "shop test", "the shop pulls orders"):
        check(f"the write-verb pattern leaves {text} alone",
              not write_re.search(text))
    for text in ("shop facts confirm k", '("queue", "approve")'):
        check(f"the gated-verb pattern catches {text}",
              bool(gated_re.search(text)))
    with planted(root, "scripts/compute_sales.py",
                 append='\nPAGE = open("console/relay.py")\n'):
        caught("the core naming the console", boundary.check_boundaries(
            root, verbs=VERBS),
            "core names the adapter console/: scripts/compute_sales.py")
    with planted(root, "scripts/notes_list.py",
                 append="\n# from console import relay\n"):
        caught("the core importing the console", boundary.check_boundaries(
            root, verbs=VERBS), "scripts/notes_list.py: names 'from console'")
    with planted(root, "ssot/glossary.tsv",
                 append="page\tThe console/ page the owner reads.\n"):
        clean("an owner file may name it (business prose is not scanned)",
              boundary.check_boundaries(root, verbs=VERBS))
    for rel, text, needle in (
            ("console/admin.py", 'run(["shop", "facts", "set", k, v])',
             "console/admin.py: invokes a write verb"),
            ("console/fast.py", 'run(["shop", "facts", "confirm", k])',
             "console/fast.py: invokes the gated"),
            ("console/half.py", 'run(["shop", "queue", "approve", i, '
                                '"--code", c])',
             "console/half.py: invokes the gated"),
            ("console/go.py", 'run(["shop", "compute", "sales", "--apply"])',
             "console/go.py: passes --apply"),
            ("console/peek.py", 'con = sqlite3.connect(os.environ["SHOP_DB"])',
             "console/peek.py: opens the database directly"),
            ("console/file.js", "new Database('data/shop.db')",
             "console/file.js: opens the database directly")):
        with planted(root, rel, text + "\n"):
            caught(f"a consumer: {needle.split(': ', 1)[1]} ({rel})",
                   boundary.check_boundaries(root, verbs=VERBS), needle)
    with planted(root, "console/reads.py", 'open("ssot/constants.tsv")\n'):
        caught("allowed_reads: a consumer reading a registry it may not",
               boundary.check_consumer(root, "console", verbs=VERBS,
                                       allowed_reads={"i18n.py": {
                                           "message_codes.tsv"}}),
               "console/reads.py: reads ssot/")
    with planted(root, "console/tests/test_x.py",
                 'run(["shop", "facts", "set"])\n'):
        clean("a consumer's own tests/ are not scanned",
              boundary.check_boundaries(root, verbs=VERBS))
    with planted(root, "console/rules.tsv",
                 "U01\tthe button never says --apply; no shop facts set\n"), \
            planted(root, "console/examples/ask.json",
                    '{"gate": {"verb": ["facts", "confirm", "k"]}}\n'):
        clean("prose runs nothing, and an example ask's gate is data (the "
              "relay code adds the flags)",
              boundary.check_boundaries(root, verbs=VERBS))
    with planted(root, "console/deploy.json", '{"args": ["--apply"]}\n'):
        caught("...but config that passes --apply is flagged",
               boundary.check_boundaries(root, verbs=VERBS),
               "console/deploy.json: passes --apply")
    real = root / "console-real"
    vendor.sync(_shop.PLAYBOOK / "console", real, {})
    try:
        clean("the playbook's own console/ keeps the consumer rules",
              boundary.check_consumer(root, "console-real", verbs=VERBS))
    finally:
        shutil.rmtree(real)


# --------------------------------------------------------------- [6] drift

def test_drift(root: Path) -> None:
    print("\n[6] drift: the vendored kit and console")
    kit = "scripts/kit/"
    with planted(root, kit + "dates.py", append="# a local edit\n"):
        caught("a drifted vendored file", drift.check_harness(root),
               "kit/: changed: dates.py")
    with planted(root, kit + "extra.py", "X = 1\n"):
        caught("a file added to the vendored kit", drift.check_harness(root),
               "kit/: not in MANIFEST.sha256: extra.py")
    with planted(root, kit + "atomic.py", remove=True):
        caught("a file removed from the vendored kit",
               drift.check_harness(root), "kit/: missing: atomic.py")
    with planted(root, kit + "VERSION", "9.9.9\n"):
        caught("the kit's VERSION edited", drift.check_harness(root),
               "kit/: changed: VERSION")
    with planted(root, "console/relay.py", append="# patched here\n"):
        caught("a drifted vendored console file", drift.check_harness(root),
               "console/: changed: relay.py")
    with planted(root, "console/VERSION", "0.0.9\n"), \
            planted(root, "console/MANIFEST.sha256", "x"):
        manifest.write(root / "console")
        caught("a console vendored from another kit version",
               drift.check_harness(root), "console/VERSION '0.0.9' is not "
                                          "the kit's")
    other = Path(tmp_dir("shop-bare-"))
    write(other, "harness.toml", (_shop.SHOP / "harness.toml").read_text())
    caught("a harness with no vendored kit", drift.check_harness(other),
           "no vendored kit at scripts/kit/")


def main() -> int:
    _shop.use()
    root = build()
    config.use(root)
    data = Path(tmp_dir("shop-data-"))
    (data / "shop.db").write_bytes(b"")

    print("[0] the clean copy of the fake harness passes every guard")
    for name, problems in everything(root, data).items():
        clean(f"clean: {name}", problems)
    rc, out, _ = capture(lambda a: 0 if release.check_release_archive(root)
                         and layering.check_layering(root)
                         and ssot.check_ssot(root)
                         and drift.check_drift(root) else 1, [])
    check("the check_*() conveniences report through kit.testing.check",
          rc == 0 and out.count("  PASS  ") >= 5, out)

    test_ssot(root)
    test_changelog(root)
    test_release(root)
    test_layering(root)
    test_clock(root)
    test_json_units()
    test_json_verbs(root, data)
    test_boundary(root)
    test_drift(root)

    print("\n[7] every plant undone: the copy is clean again")
    for name, problems in everything(root, data).items():
        clean(f"clean again: {name}", problems)
    _shop.use()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
