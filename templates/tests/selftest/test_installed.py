#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The kit as a harness gets it: installed in a sample harness (_sample.py),
every test passes; then each gate fails on the one change it exists to catch,
and says where. A gate that stays green on its broken case fails here.
"""

import os
import sys

SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SELF))          # the kit: _check, run

import _sample  # noqa: E402
from _check import check, child_env, finish, tmp_dir  # noqa: E402

ENV = child_env()
C = _sample.CODE
LEAK = ("import sys, tempfile\nfrom _check import check, finish\n"
        "tempfile.mkdtemp()\ncheck('fine', True)\nsys.exit(finish())\n")
CLOCK_TEST = _sample.read(os.path.join(_sample.KIT, "test_clock.py"))
LAYER_TEST = _sample.read(os.path.join(_sample.KIT, "test_layering.py"))
ATTRS = _sample.read(os.path.join(_sample.TEMPLATES, "gitattributes"))
STRAY = "\nimport datetime\n\n\ndef stamp():\n    return datetime.date.today()\n"
ALLOW = CLOCK_TEST.replace("ALLOWED: dict[tuple[str, str], int] = {",
                           'ALLOWED: dict[tuple[str, str], int] = {("src/compute_kpi.py", "stamp"): 1,')

BROKEN = [  # (what changed, the test file, changes, what its output must say)
    ("pull reaches the database through a helper", "test_layering.py",
     {"src/lib/helpers.py": "from lib import db\n",
      "src/pull_ads.py": "from lib import helpers\n" + C["src/pull_ads.py"]},
     ["FAIL  b: pull_ads: pull reaches no database", "lib.db"]),
    ("compute imports subprocess inside a function", "test_layering.py",
     {"src/compute_kpi.py": C["src/compute_kpi.py"] + "\n\ndef spawn():\n    import subprocess\n"},
     ["FAIL  e: compute_kpi: compute spawns nothing", "subprocess"]),
    ("a second module imports the writer", "test_layering.py",
     {"src/facts.py": "from api import writer\n" + C["src/facts.py"]},
     ["FAIL  a: only the executor imports the one writer", "facts"]),
    ("pull_ads.py renamed: the pull rule would check nothing", "test_layering.py",
     {"src/pull_ads.py": None, "src/fetch_ads.py": C["src/pull_ads.py"]},
     ["FAIL  b: the layer pull_* has modules"]),
    ("the console runs a write verb as argv", "test_layering.py",
     {"webconsole/serve.py": C["webconsole/serve.py"]
      + '\n\ndef reject(qid):\n    return subprocess.run([TOOL, "queue", "reject", qid])\n'},
     ["FAIL  j: the console runs no write verb", "webconsole/serve.py:11"]),
    ("the core names the console", "test_layering.py",
     {"src/lib/rules.py": "# pages: webconsole/pages.py\n" + C["src/lib/rules.py"]},
     ["FAIL  i: the core never names the console", "src/lib/rules.py:1"]),
    ("a text rule's pattern matches nothing", "test_layering.py",
     {"tests/test_layering.py": LAYER_TEST.replace(r're.compile(r"\bsqlite3?\b|\.db\b")',
                                                   r're.compile(r"\bsqlite4\b")')},
     ["FAIL  k: the pattern catches its samples", "misses 'import sqlite3'"]),
    ("a stray calendar read", "test_clock.py",
     {"src/compute_kpi.py": C["src/compute_kpi.py"] + STRAY},
     ["FAIL  no calendar read outside the clock", "src/compute_kpi.py:11 (stamp)"]),
    ("a `from lib.dates import now` binding", "test_clock.py",
     {"src/ingest_ads.py": "from lib.dates import now\n" + C["src/ingest_ads.py"]},
     ["FAIL  no calendar read outside the clock", "from lib.dates import now"]),
    ("an ALLOWED row whose read has moved", "test_clock.py",
     {"tests/test_clock.py": ALLOW},
     ["FAIL  ALLOWED lists only reads still there", "src/compute_kpi.py:stamp (1 moved)"]),
    ("the clock module renamed: the lint would read nothing of it", "test_clock.py",
     {"src/lib/dates.py": None, "src/lib/clock.py": _sample.read(os.path.join(_sample.REPO, "core", "dates.py"))},
     ["FAIL  src hold code to read, src/lib/dates.py among it"]),
    ("an internal file that ships", "test_release_archive.py",
     {".gitattributes": ATTRS.replace("/CLAUDE.md export-ignore\n", "")},
     ["FAIL  no internal file ships", "CLAUDE.md"]),
    ("an export-ignore entry that names nothing", "test_release_archive.py",
     {".gitattributes": ATTRS + "/docs export-ignore\n"},
     ["FAIL  every export-ignore entry in .gitattributes names a tracked path", "/docs"]),
    ("an INTERNAL path that is gone", "test_release_archive.py",
     {"evals/README.md": None},
     ["FAIL  every INTERNAL path exists", "evals"]),
    ("a registry the index names a reader for, left out", "test_release_archive.py",
     {".gitattributes": ATTRS + "/ssot/constants.tsv export-ignore\n"},
     ["FAIL  ssot/index.tsv: every file it names a reader for ships", "ssot/constants.tsv"]),
    ("a planning file that ships", "test_release_archive.py",
     {".gitattributes": ATTRS.replace("/ssot/glossary.tsv export-ignore\n", "")},
     ["FAIL  ssot/index.tsv: the files it names no reader for stay out", "ssot/glossary.tsv"]),
    ("code opens a registry that is not in the release", "test_release_archive.py",
     {"src/lib/extra.py": 'NAME = "extra_rules.tsv"\n'},
     ["FAIL  every .tsv runtime code opens by name ships", "extra_rules.tsv"]),
    ("runtime code left out", "test_release_archive.py",
     {".gitattributes": ATTRS + "/src/lib export-ignore\n"},
     ["FAIL  nothing but the internal files is left out", "src/lib/db.py"]),
]


def sample(changes=None) -> str:
    return _sample.build(tmp_dir(prefix="sample-"), changes)


def main() -> int:
    print("installed as the README says, every test of the kit passes")
    code, out = _sample.run(sample(), ENV, "tests/run.py")
    ran = sorted(ln.split()[2] for ln in out.splitlines() if ln.startswith("ok "))
    check("tests/run.py passes the whole sample", code == 0
          and out.rstrip().splitlines()[-1].startswith("RESULT: "), out)
    check("...running each test file of the kit",
          ran == ["test_clock.py", "test_layering.py", "test_release_archive.py",
                  "test_run_tests.py"], ran)
    code, out = _sample.run(sample({"src/compute_kpi.py": C["src/compute_kpi.py"] + STRAY,
                                    "tests/test_clock.py": ALLOW}), ENV, "tests/test_clock.py")
    check("a read listed in ALLOWED passes until it moves", code == 0, out)

    print("each gate fails on its broken case, and names it")
    for what, test, changes, says in BROKEN:
        code, out = _sample.run(sample(changes), ENV, f"tests/{test}")
        check(f"{test}: {what}", code == 1 and all(s in out for s in says),
              "\n".join(out.splitlines()[-12:]))
    code, out = _sample.run(sample({"tests/test_leak.py": LEAK}), ENV, "tests/run.py", "leak")
    check("run.py: a harness test that leaves a temp dir fails the run",
          code == 1 and "test_leak.py" in out and "left 1 temp entry" in out, out)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
