"""The golden diff's hook: what one harness runs, and how. Fill it once.

engine.py, beside this file, reads only the names below. Every value here is
an example: it fits the fake harness the tests build (tests/fake_harness.py),
shaped the way the playbook recommends. Replace each with your harness's.

What the engine needs from a harness:
  * One dispatcher (ENTRY) and a verb table (VERB_TABLE) in a file (VERB_FILE,
    the dispatcher itself unless you name another): a dict whose keys are
    tuples of words, e.g. {("report", "summary"): ...}, or a list of
    Verb(words, ...) calls, e.g. [Verb(("report", "summary"), "x.py", "read")].
    The file is read with ast, never imported: importing a dispatcher may load
    the operator's env files and credentials. Cases follow the table, so a new
    verb gets a case without an edit here.
  * Every clock read goes through a seam (SEAMS): a function returning an
    aware UTC datetime ("now") or a date ("today"). A clock read anywhere
    else is not pinned: under --today it still reads the real day.
  * One function starts every child script (RUNNER): (script, args) -> the
    argv to run. It is patched, so a verb's own children run pinned too.
  * The data folder comes from one env variable (DATA_ENV), never the cwd.

Every case runs in the engine's own interpreter (that is how the seams get
patched), so list your harness's dependencies in engine.py's PEP 723 header.

A harness that scaffold/new_harness.py wrote (the playbook's kit vendored at
scripts/kit/) already has all four. Its verbs are the list in scripts/verbs.py,
not a dict in the dispatcher, so name both files. kit.dates.now is its one
clock (patching it pins today, host_today and market_day too),
kit.runner.script_cmd starts every child verb, and the data folder is
<PREFIX>_DATA_DIR:

    ENTRY = "scripts/<cli>.py"          VERB_FILE = "scripts/verbs.py"
    VERB_TABLE = "VERBS"                SYS_PATH = ("scripts",)
    SEAMS = (("kit.dates", "now", "now"), ("kit.dates", "today", "today"))
    RUNNER = ("kit.runner", "script_cmd")
    DATA_ENV = "<PREFIX>_DATA_DIR"      REFUSE_ARGS = ("--apply",)
"""

from __future__ import annotations

import json
from pathlib import Path

# ------------------------------------------------------------ the harness
ENTRY = "tool.py"                   # tree-relative; every case runs it
VERB_FILE = ENTRY                   # tree-relative; the file holding VERB_TABLE
VERB_TABLE = "VERBS"                # its dict or list of verbs, read with ast
SYS_PATH = (".",)                   # tree-relative, first on a case's sys.path
SEAMS = (("lib.clock", "now", "now"),        # (module, function, now|today)
         ("lib.clock", "today", "today"))
RUNNER = ("lib.runner", "script_cmd")        # (module, function) or None
FIXTURES = "fixtures"               # tree-relative; BASE's seed both sides
DATA_ENV = "DATA_DIR"               # the env variable naming the data folder

# The whole env of a case. Nothing else passes: no credential, no write
# switch, no confirm secret. PYTHONHASHSEED never passes, whatever is here.
# HOME is never the operator's, whatever is here: each side gets an empty
# folder of its own, so a home-level env file, a tool's config or a credential
# store under HOME never reaches a case. A tool that caches under HOME caches
# there; point it elsewhere with its own variable if the run gets slow.
ENV_KEEP = ("PATH", "TMPDIR", "LANG")
ENV_PREFIXES = ("LC_",)
ENV_SET = {"TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1"}
REFUSE_ARGS = ("--apply",)          # a case carrying one is refused

# -------------------------------------------------------------- the cases
# Every ("report", x) verb runs as `x --json`, plus these. "{entity}" is a
# slot: filled by learn() from an earlier case; the engine fills "{today}",
# "{month}" (the pin's) and "{work}" (this side's scratch folder).
VARIANTS = {("report", "summary"): [["--by", "day"]],
            ("report", "entity"): [["--id", "{entity}"]]}
FAILURES = {("report", "entity"): [["--id", "no-such-entity"]],  # its error doc
            ("report", "summary"): [["--by", "no-such-key"]]}
READS = {"facts_list": ("facts", "list", "--", "--json")}
DRY_RUNS = {"execute_dry_run": ("execute", "--", "--dry-run", "--json")}
INIT = "demo\n"                     # what `init` reads on stdin: the scope


def seed(run, fixtures: Path) -> None:
    """Build the sandbox from `fixtures` with this side's own verbs.
    run(argv, stdin=None) runs ENTRY pinned, in the case env."""
    for argv, stdin in ((("ingest", "--", "--from", str(fixtures)), None),
                        (("init",), INIT)):
        p = run(argv, stdin)
        if p.returncode:
            raise SystemExit(f"seed step failed: {argv[0]}\n"
                             f"{(p.stdout + p.stderr)[-1500:]}")


def _tag(extra: list[str]) -> str:
    return "_".join(a.strip("-{}").replace("-", "_") for a in extra)


def plan(verbs: set[tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """{case name: ENTRY argv}, from this side's verb table. A case whose
    verb the side lacks is skipped there, so it shows as added or removed."""
    out: dict[str, tuple[str, ...]] = {}
    for verb in sorted(v for v in verbs if v[0] == "report"):
        name, base = "_".join(verb), (*verb, "--", "--json")
        out[name] = base
        for extra in VARIANTS.get(verb, []):
            out[f"{name}__{_tag(extra)}"] = (*base, *extra)
        for extra in FAILURES.get(verb, []):
            out[f"{name}__FAIL_{_tag(extra)}"] = (*base, *extra)
    return out | READS | DRY_RUNS


def learn(name: str, result: dict, slots: dict) -> None:
    """After each case: fill the slots later cases need. result = {rc, doc,
    stdout}; a file a later case reads goes under slots["{work}"]."""
    if name == "report_summary":
        rows = result["doc"].get("rows") if result["rc"] == 0 else None
        slots["{entity}"] = rows[0]["entity"] if rows else None


def pages(tree: Path, run, slots: dict):
    """[(page name, render)] for every page in every language; render()
    returns the HTML. Runs in a child under the pin, the tree first on
    sys.path; run(argv) runs ENTRY there, pinned too. [] for a tree with no
    console yet."""
    if not (tree / "console.py").is_file():
        return []
    import console                  # the tree's own

    def read(argv):
        return json.loads(run(argv).stdout)

    return [(f"page_{page}_{lang}",
             lambda page=page, lang=lang: console.render(
                 page, lang, read, entity=slots.get("{entity}")))
            for page in console.PAGES for lang in console.LANGS]
