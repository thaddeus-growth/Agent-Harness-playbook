"""The build workflow names only what is there, or says it is not there yet.

BUILD.md, the build-harness skill and the templates README are read as text:
every playbook path and link they name exists, or is in BUILD.md's one list
"Still being built"; every template BUILD.md names exists; every template is
listed; every harness test any of them names is one the scaffolder generates.
The templates themselves are held to the shapes the docs promise: the step
table, the skill and ssot/stages.agent.tsv list the same steps; the ssot files
carry the columns the kit reads and the trail every owner row needs; the
placeholders are the ones explained; harness.toml parses; CODEOWNERS names real
paths; the CI template's title rule and secret scan match what they should; the
dissent ask in team-review.md is one the console accepts. Each check is tried on
a broken input first, so it cannot pass by looking at nothing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import _t  # noqa: E402

BUILD, SKILL, TREADME = "BUILD.md", "skills/build-harness/SKILL.md", "templates/README.md"
DOCS = (BUILD, SKILL, TREADME)
TOP_DIRS = ("templates", "console", "kit", "scaffold", "build", "skills", "hosts")
PATH = re.compile(r"(?<![\w/.$<-])((?:" + "|".join(TOP_DIRS) + r")/[A-Za-z0-9_./-]*)")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
HARNESS_TEST = re.compile(r"(?<![\w/.-])tests/(test_\w+\.py)")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
ACTIONS_EXPR = re.compile(r"\$\{\{[^}]*\}\}")     # a GitHub Actions expression, not a placeholder
STEPS = ["B0", "B0.5", "B0.6", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10"]
GENERATED = {"test_ssot.py", "test_layering.py", "test_clock.py", "test_json_contract.py", "test_release.py", "test_kit_drift.py",
             "test_human_tables.py", "test_gate.py", "test_boundary.py", "test_run_tests.py"}


def doc(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def section(text: str, heading: str) -> str:
    """The body under `## heading` up to the next `## `, or '' when there is none."""
    parts = re.split(r"^## ", text, flags=re.M)
    for p in parts[1:]:
        title, _, body = p.partition("\n")
        if title.strip() == heading:
            return body
    return ""


def named(rel: str, text: str) -> list[str]:
    """Every playbook path `text` names, as a path from the repository root: the
    paths under the playbook's top folders, and every relative link."""
    base = os.path.dirname(rel)
    out = [p.rstrip(".,;:") for p in PATH.findall(LINK.sub("]", text))]     # a link's target is read once, below
    for t in LINK.findall(text):
        if not re.match(r"[a-z]+:", t):
            out.append(os.path.relpath(os.path.normpath(os.path.join(REPO, base, t)), REPO).replace(os.sep, "/"))
    return out


def to_be_built(text: str) -> list[str]:
    return re.findall(r"^- `([^`]+)`", section(text, "Still being built"), re.M)


def missing(rel: str, text: str, listed: list[str]) -> list[str]:
    return [p for p in named(rel, text) if p not in listed and not os.path.exists(os.path.join(REPO, p))]


def template_files() -> list[str]:
    out = []
    for top, dirs, names in os.walk(os.path.join(REPO, "templates")):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__")))
        out += [os.path.relpath(os.path.join(top, n), REPO).replace(os.sep, "/") for n in sorted(names)
                if not n.startswith(".")]
    return out


def tsv(rel: str) -> list[dict]:
    """A TSV as rows; every row has exactly the header's width."""
    lines = doc(rel).rstrip("\n").split("\n")
    head = lines[0].split("\t")
    rows = []
    for n, ln in enumerate(lines[1:], 2):
        cells = ln.split("\t")
        assert len(cells) == len(head), f"{rel}:{n} has {len(cells)} cells, the header {len(head)}"
        rows.append(dict(zip(head, cells)))
    return rows


def header(rel: str) -> list[str]:
    return doc(rel).split("\n", 1)[0].split("\t")


# ------------------------------------------------------------- what is named --

def test_the_path_finder_sees_playbook_paths_and_no_others():
    text = "run `kit/x.py`, not scripts/kit/y.py or $DATA_DIR/build/z; see [a](templates/README.md) and [b](https://x.example.com)."
    assert named("BUILD.md", text) == ["kit/x.py", "templates/README.md"]
    assert named("skills/build-harness/SKILL.md", "[b](../../BUILD.md)") == ["BUILD.md"]
    assert to_be_built("## Still being built\n\n- `kit/`: the kit.\n- words\n\n## Next\n- `x/`") == ["kit/"]


def test_every_path_the_build_docs_name_exists_or_is_listed_as_to_be_built():
    listed = to_be_built(doc(BUILD))
    assert missing(BUILD, "see `kit/nope.py`, [x](nope.md) and `kit/`", ["kit/"]) == ["kit/nope.py", "nope.md"]
    seen = 0
    for rel in DOCS:
        text = doc(rel)
        seen += len(named(rel, text))
        assert not missing(rel, text, listed), (rel, missing(rel, text, listed))
    assert seen > 60, seen                                                    # the check saw something
    for p in listed:
        if os.path.exists(os.path.join(REPO, p)):
            print(f"note: {p} exists now; take it out of BUILD.md's list \"Still being built\"")


def test_every_template_build_md_names_exists_and_none_is_deferred():
    assert [p for p in named(BUILD, "[t](templates/nope.md)") if not os.path.exists(os.path.join(REPO, p))] \
        == ["templates/nope.md"]
    templates = [p for p in named(BUILD, doc(BUILD)) if p.startswith("templates/")]
    assert len(set(templates)) >= 10, templates
    for p in templates:
        assert os.path.exists(os.path.join(REPO, p)), p
    assert not [p for p in to_be_built(doc(BUILD)) if p.startswith("templates/")]


def test_the_to_be_built_list_is_one_list_and_each_entry_is_used():
    assert section(doc(BUILD), "Still being built"), "BUILD.md keeps the list, even when it is empty"
    listed = to_be_built(doc(BUILD))
    assert len(listed) == len(set(listed)), listed
    assert all(p.split("/")[0] in TOP_DIRS for p in listed), listed
    for rel in (SKILL, TREADME):
        assert not section(doc(rel), "Still being built"), f"{rel} keeps its own list"
    body = doc(BUILD).replace(section(doc(BUILD), "Still being built"), "")
    elsewhere = set(named(BUILD, body)) | set(named(SKILL, doc(SKILL))) | set(named(TREADME, doc(TREADME)))
    assert "kit/gone.py" not in elsewhere                                     # the check would notice an unused entry
    for p in listed:
        assert p in elsewhere, f"{p} is listed but no step names it"


def test_every_template_is_listed_in_the_templates_readme():
    listed = set(named(TREADME, doc(TREADME)))
    assert "templates/nope.md" not in listed
    files = [p for p in template_files() if p != TREADME]
    assert len(files) >= 25, files
    for p in files:
        assert p in listed, f"{p} is not in templates/README.md"


def test_every_harness_test_named_is_one_the_scaffolder_generates():
    table = section(doc(TREADME), "The tests a new harness starts with")
    assert set(re.findall(r"`tests/(test_\w+\.py)`", table)) == GENERATED
    own = {n for n in os.listdir(HERE) if n.startswith("test_")}
    assert HARNESS_TEST.findall("see tests/test_write_path.py and console/tests/test_core.py") == ["test_write_path.py"]
    texts = {rel: doc(rel) for rel in (BUILD, SKILL)}
    for rel in template_files():
        try:
            texts[rel] = doc(rel)
        except UnicodeDecodeError:
            continue
    seen = set()
    for rel, text in texts.items():
        for name in HARNESS_TEST.findall(text):
            seen.add(name)
            assert name in GENERATED or name in own, f"{rel} names tests/{name}, which no harness is given"
    assert GENERATED <= seen, sorted(GENERATED - seen)


def test_each_invariant_in_the_agent_instructions_names_a_generated_test():
    text = doc("templates/AGENT_INSTRUCTIONS.md")
    rules = re.findall(r"^- \*\*.+$", section(text, "Invariants (each names the test that enforces it)"), re.M)
    assert len(rules) >= 10, rules
    for rule in rules:
        m = re.search(r"\*\(([^)]*)\)\*\s*$", rule)
        assert m and HARNESS_TEST.search(m.group(1)), f"no test named: {rule[:60]}"
    assert set(HARNESS_TEST.findall(text)) == GENERATED
    for old in ("src/", "test_write_path", "test_rebuild", "test_human_gate", "test_init", "test_contract.py"):
        assert old not in text, old


# ------------------------------------------------------------- the steps --

def after(cell: str) -> list[str]:
    return [s for s in re.split(r"[,\s]+", cell) if s and s not in ("—", "-")]


def step_table(text: str) -> list[list[str]]:
    rows = re.findall(r"^\| (B[\d.]+ .*)\|\s*$", section(text, "The steps"), re.M)
    return [[c.strip() for c in r.split("|")] for r in rows]


def test_the_step_table_the_skill_and_the_stages_file_list_the_same_steps():
    assert [r[0].split()[0] for r in step_table("## The steps\n| B0 x | a | — | b | c |\n")] == ["B0"]
    rows = step_table(doc(BUILD))
    assert [r[0].split()[0] for r in rows] == STEPS, [r[0] for r in rows]
    for r in rows:
        assert len(r) == 5 and all(r), r
    heads = re.findall(r"^## (B[\d.]+) · ", doc(BUILD), re.M)
    assert heads == STEPS, heads
    in_skill = re.findall(r"\*\*(B[\d.]+) ", section(doc(SKILL), "The steps, in order"))
    assert in_skill == STEPS, in_skill
    stages = tsv("templates/ssot/stages.agent.tsv")
    assert [s["stage"] for s in stages] == STEPS
    for r, s in zip(rows, stages):
        assert after(r[2]) == after(s["after"]), (r[0], r[2], s["after"])


# ------------------------------------------------------------- the ssot templates --

KIT_COLUMNS = {
    "constants.tsv": ["name", "default", "unit", "min", "max", "group", "label_en", "label_zh", "explain", "why"],
    "fact_keys.tsv": ["key", "group", "type", "unit", "min", "max", "label_en", "label_zh", "why"],
    "decision_keys.tsv": ["entity_type", "key", "domain", "confirm", "label_en", "label_zh", "story", "why"],
    "message_codes.tsv": ["code", "params", "meaning_en", "meaning_zh"],
    "story_checks.tsv": ["story", "verb", "expect", "needs", "note"],
}
TRAIL = ["status", "source", "ask", "decided"]
DECIDED = re.compile(r"^(console:[a-z0-9][a-z0-9._-]*@\d+|chat:\S.*|merge:[0-9a-f]{7,40}|ci:\S+|check:\S.*)$")
ROW_STATUS = {"proposed", "asked", "accepted", "dropped", "retired"}
STAGE_STATUS = {"todo", "doing", "signed", "blocked"}


def trail_problems(owner: list[dict], sibling: list[dict], key: str) -> list[str]:
    """What breaks the trail between an owner file and its .agent.tsv sibling."""
    out = []
    by_id = {r[key]: r for r in sibling}
    if len(by_id) != len(sibling) or len({r[key] for r in owner}) != len(owner):
        out.append("an id is used twice")
    for r in sibling:
        st = r["status"]
        if st not in ROW_STATUS:
            out.append(f"{r[key]}: status {st!r}")
        if st == "asked" and not r["ask"]:
            out.append(f"{r[key]}: asked, but no ask")
        if st in ("accepted", "dropped", "retired") and not DECIDED.match(r["decided"]):
            out.append(f"{r[key]}: {st}, but no decided answer")
    for r in owner:
        s = by_id.get(r[key])
        if s is None:
            out.append(f"{r[key]}: an owner row with no sibling")
        elif s["status"] not in ("accepted", "retired"):
            out.append(f"{r[key]}: an owner row whose sibling is {s['status']}")
    return out


def stage_problems(stages: list[dict]) -> list[str]:
    out, before, signed = [], set(), set()
    for s in stages:
        if s["status"] not in STAGE_STATUS:
            out.append(f"{s['stage']}: status {s['status']!r}")
        if not set(after(s["after"])) <= before:
            out.append(f"{s['stage']}: after names a later or unknown step")
        if s["status"] == "signed":
            if not DECIDED.match(s["decided"]):
                out.append(f"{s['stage']}: signed with no reference")
            if not set(after(s["after"])) <= signed:
                out.append(f"{s['stage']}: signed before a step it comes after")
            signed.add(s["stage"])
        if not s["signer"]:
            out.append(f"{s['stage']}: nobody signs it")
        before.add(s["stage"])
    return out


def test_the_trail_check_catches_what_it_should():
    ok = {"status": "accepted", "source": "meeting:x", "ask": "b-1", "decided": "console:b-1@3"}
    assert trail_problems([{"id": "S01"}], [{"id": "S01", **ok}], "id") == []
    assert trail_problems([{"id": "S01"}], [], "id") == ["S01: an owner row with no sibling"]
    assert trail_problems([{"id": "S01"}], [{"id": "S01", **ok, "decided": ""}], "id") \
        == ["S01: accepted, but no decided answer"]
    assert trail_problems([], [{"id": "S02", **ok, "status": "asked", "ask": ""}], "id") == ["S02: asked, but no ask"]
    stage = {"after": "", "status": "signed", "decided": "ci:1", "signer": "ci"}
    assert stage_problems([{"stage": "B0", **stage}]) == []
    assert stage_problems([{"stage": "B0", **stage, "status": "todo"}, {"stage": "B1", **stage, "after": "B0"}]) \
        == ["B1: signed before a step it comes after"]
    assert stage_problems([{"stage": "B1", **stage, "after": "B9", "status": "todo"}]) \
        == ["B1: after names a later or unknown step"]


def test_the_ssot_templates_carry_the_columns_the_kit_reads_and_the_trail():
    for name, cols in KIT_COLUMNS.items():
        assert header(f"templates/ssot/{name}") == cols, name
    agents = [p for p in template_files() if p.endswith(".agent.tsv")]
    assert len(agents) == 4, agents
    for rel in agents:
        assert set(TRAIL) <= set(header(rel)), rel
    index = tsv("templates/ssot/index.tsv")
    in_index = {r["file"] for r in index}
    on_disk = {"ssot/" + p.split("templates/ssot/")[1] for p in template_files()
               if p.startswith("templates/ssot/") and p.endswith(".tsv")}
    assert in_index == on_disk, (in_index ^ on_disk)
    for r in index:
        assert r["owner"] in ("owner", "agent", "registry"), r
        for t in r["test"].split(";"):
            assert HARNESS_TEST.fullmatch(t.strip()) and t.strip()[6:] in GENERATED, r
        assert r["id_column"].split("+")[0] in header("templates/" + r["file"]), r
    for owner, key in (("glossary", "term"), ("user-stories", "id"), ("policies", "id")):
        problems = trail_problems(tsv(f"templates/ssot/{owner}.tsv"), tsv(f"templates/ssot/{owner}.agent.tsv"), key)
        assert not problems, (owner, problems)
    assert not stage_problems(tsv("templates/ssot/stages.agent.tsv"))


def test_the_registries_refer_only_to_rows_that_exist():
    stories = {r["id"] for r in tsv("templates/ssot/user-stories.tsv")}
    thresholds = {r["name"] for r in tsv("templates/ssot/constants.tsv")}
    for r in tsv("templates/ssot/policies.tsv"):
        assert set(re.split(r"[;,\s]+", r["params"])) - {"", "—"} <= thresholds, r
        assert set(r["stories"].split()) <= stories, r
    for rel in ("templates/ssot/decision_keys.tsv", "templates/ssot/story_checks.tsv"):
        for r in tsv(rel):
            assert set((r.get("story") or "").split()) <= stories, (rel, r)
    for r in tsv("templates/ssot/decision_keys.tsv"):
        assert r["confirm"] in ("human", "none", "harness"), r
    for r in tsv("templates/ssot/message_codes.tsv"):
        params = {p for p in r["params"].split(",") if p}
        for lang in ("meaning_en", "meaning_zh"):
            assert set(re.findall(r"\{(\w+)\}", r[lang])) <= params, (r["code"], lang)


# ------------------------------------------------------------- the other templates --

def test_every_placeholder_is_one_the_templates_readme_explains():
    explained = set(re.findall(r"^\| `\{\{(\w+)\}\}` \|", section(doc(TREADME), "Placeholders"), re.M))
    assert explained >= {"name", "cli", "env_prefix", "owner"}
    assert PLACEHOLDER.findall("{{cli}} and {{ cli }}") == ["cli"]
    assert ACTIONS_EXPR.sub("", "${{ github.event.pull_request.title }} {{cli}}") == " {{cli}}"
    used = set()
    for rel in template_files():
        if rel == TREADME:
            continue
        text = ACTIONS_EXPR.sub("", doc(rel))
        used |= set(PLACEHOLDER.findall(text))
        assert text.count("{{") == len(PLACEHOLDER.findall(text)), f"{rel}: a malformed placeholder"
    assert used == explained, (sorted(used - explained), sorted(explained - used))


def test_harness_toml_parses_and_holds_what_the_kit_reads():
    with open(os.path.join(REPO, "templates/harness.toml"), "rb") as f:
        cfg = tomllib.load(f)
    assert set(cfg) == {"harness", "ssot", "release", "layers"}
    assert {"name", "cli", "env_prefix", "db_file", "scripts_dir", "languages", "markets", "home_env_file"} \
        <= set(cfg["harness"])
    assert [f"meaning_{lang}" for lang in cfg["harness"]["languages"]] == header("templates/ssot/message_codes.tsv")[2:]
    for key, path in cfg["ssot"].items():
        if key != "dir":
            assert os.path.exists(os.path.join(REPO, "templates", path)), (key, path)
    assert {"CLAUDE.md", "tests", ".gitlab-ci.yml"} <= set(cfg["release"]["internal"])
    assert {"SKILL.md", "README.md", "harness.toml"} <= set(cfg["release"]["must_ship"])
    assert {"pull", "ingest", "compute", "writer", "writer_importers", "clients", "spawn_allowed"} <= set(cfg["layers"])
    assert cfg["layers"]["writer"] == "_lib/writer" and cfg["layers"]["writer_importers"] == ["execute_actions"]


def codeowned(text: str) -> dict[str, str]:
    return dict(ln.split() for ln in text.splitlines() if ln.strip() and not ln.startswith("#"))


def test_codeowners_protects_the_risky_list_with_real_paths():
    assert codeowned("# c\n/a  @o\n") == {"/a": "@o"}
    paths = codeowned(doc("templates/CODEOWNERS"))
    assert set(paths.values()) == {"{{owner}}"}
    assert {"/scripts/kit/", "/console/", "/scripts/_lib/writer.py", "/scripts/execute_actions.py",
            "/scripts/_lib/schema.py", "/CLAUDE.md", "/harness.toml", "/ssot/glossary.tsv",
            "/ssot/user-stories.tsv", "/ssot/policies.tsv", "/ssot/constants.tsv"} <= set(paths)
    assert not [p for p in paths if p.startswith("/src/")]
    for p in paths:
        if p.startswith("/ssot/"):
            assert os.path.exists(os.path.join(REPO, "templates", p[1:])), p
    agent = doc("templates/AGENT_INSTRUCTIONS.md")
    for p in ("scripts/kit/", "console/", "scripts/_lib/writer.py", "scripts/execute_actions.py", "scripts/_lib/schema.py"):
        assert p in agent, f"CLAUDE.md does not say what {p} is"


def ci_pattern(text: str, after_word: str) -> str:
    return re.search(after_word + r"[^']*'([^']+)'", text).group(1)


def test_the_ci_template_gates_on_result_and_its_title_rule_works():
    text = doc("templates/gitlab-ci.yml")
    for job in ("secret scan:", "story id:", "{{cli}} test:"):
        assert re.search(rf"^{re.escape(job)}$", text, re.M), job
    assert "uv run scripts/{{cli}}.py test" in text and "'^RESULT: [0-9]+ passed$'" in text
    title = re.compile(ci_pattern(text, "grep -Eq"))
    for good in ("S01 add the budget report", "Fix expiry (P12)", "release 0.2.0", "S012 the hundredth story"):
        assert title.search(good), good
    for bad in ("fix a typo", "S1 short", "AS01 prefix", "P7 one digit", "Release notes"):
        assert not title.search(bad), bad
    job = doc("templates/ci/story-id.yml")
    assert ci_pattern(job, "grep -Eq") == ci_pattern(text, "grep -Eq"), "the two story-id rules differ"
    secret = re.compile(ci_pattern(text, "grep -rEn"))
    for leak in ("-" * 5 + "BEGIN RSA PRIVATE " + "KEY-----", "AKIA" + "Q" * 16, "ghp" + "_" + "a" * 36):
        assert secret.search(leak), leak[:8]
    assert not secret.search("a line about a key, AKIA-short and sk-short")


def test_the_skill_template_has_its_frontmatter_and_three_scopes():
    text = doc("templates/SKILL.md")
    front = text.split("---\n")[1]
    assert re.findall(r"^(\w+):", front, re.M) == ["name", "description", "version", "type"]
    for part in ("**Read:**", "**Pending writes:**", "**Out of scope:**"):
        assert part in section(text, "Scope of this skill: v1"), part
    skill = doc(SKILL)
    front = skill.split("---\n")[1]
    assert re.search(r"^name: build-harness$", front, re.M) and re.search(r"^description: \S", front, re.M)
    for heading in ("When to use", "The steps, in order", "Never", "Where questions go", "Where the state is"):
        assert section(skill, heading), heading
    assert "console" in section(skill, "Where questions go")


def test_decision_rights_keeps_the_lanes_and_names_deciders_and_dissent():
    text = doc("templates/decision-rights.md")
    for heading in ("1. Always the owner", "2. AI proposes, owner confirms", "3. AI alone",
                    "The risky list (owner merges)", "Channels that count", "Deciders and advisers", "Dissent"):
        assert section(text, heading), heading
    kinds = re.findall(r"^\| ([A-Z][^|]*?)(?::[^|]*)? \|", section(text, "Deciders and advisers"), re.M)
    assert {"Meaning", "Numbers", "Money", "Contract", "Risky merges", "Release"} <= set(kinds), kinds
    for rel in ("templates/decision-rights.md", "templates/team-review.md"):
        assert "Advice never answers" in doc(rel) or "Advice is evidence, not an answer" in doc(rel), rel


CALL = re.compile(r"ask\.py (?:--\S+ \S+ )*([a-z]+)\b")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def console_cli() -> tuple[set[str], set[str]]:
    """(ask.py's verbs, every flag of ask.py and serve.py), read from their parsers."""
    import argparse
    sys.path.insert(0, os.path.join(REPO, "console"))
    import ask
    import serve

    def flags(parser) -> set[str]:
        out = set()
        for a in parser._actions:
            out.update(o for o in a.option_strings if o.startswith("--"))
            if isinstance(a, argparse._SubParsersAction):
                for sub in a.choices.values():
                    out |= flags(sub)
        return out
    sub = next(a for a in ask._parser()._actions if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices), flags(ask._parser()) | flags(serve._parser())


TOOL = re.compile(r"python3 ((?:build|kit/tools)/\w+\.py)([^`\n]*)")


def help_flags(script: str) -> set[str]:
    """The flags a playbook tool's own --help lists."""
    import subprocess
    p = subprocess.run([sys.executable, os.path.join(REPO, script), "--help"], capture_output=True, text=True,
                       timeout=60, cwd=REPO, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode == 0, (script, p.stderr[-300:])
    return set(FLAG.findall(p.stdout))


def test_the_console_verbs_and_flags_the_build_docs_use_are_real():
    verbs, flags = console_cli()
    assert CALL.findall("run ask.py fly, then ask.py --as x add -") == ["fly", "add"]
    assert "fly" not in verbs and "--fly" not in flags                          # the check would notice
    called = set()
    for rel in (BUILD, SKILL, "templates/team-review.md", "templates/decision-rights.md"):
        found = set(CALL.findall(doc(rel)))
        called |= found
        assert found <= verbs, (rel, sorted(found - verbs))
    assert {"add", "answers", "applied", "digest"} <= called, called
    shown = set(FLAG.findall(TOOL.sub("", doc("templates/team-review.md"))))   # a build tool's flags are its own
    assert shown and shown <= flags, sorted(shown - flags)
    assert set(FLAG.findall(doc("templates/decision-rights.md"))) - {"--json"} <= flags   # --json is the harness's
    assert "--deciders" in flags and "--deciders" in doc("templates/team-review.md")


def test_every_build_tool_command_the_docs_print_uses_real_flags():
    assert TOOL.findall("run `python3 build/x.py --a B --c` now") == [("build/x.py", " --a B --c")]
    runs = [(rel, m) for rel in (BUILD, SKILL, "templates/team-review.md") for m in TOOL.findall(doc(rel))]
    assert len(runs) >= 5, runs
    known = {}
    for rel, (script, rest) in runs:
        if not os.path.exists(os.path.join(REPO, script)):
            assert script in to_be_built(doc(BUILD)), (rel, script)             # a tool not built yet is listed
            continue
        known.setdefault(script, help_flags(script))
        shown = set(FLAG.findall(rest))
        assert shown <= known[script], (rel, script, sorted(shown - known[script]))
    assert "--pick" in known.get("build/intake_to_asks.py", {"--pick"})


def test_the_dissent_ask_in_team_review_is_one_the_console_accepts():
    sys.path.insert(0, os.path.join(REPO, "console"))
    import core
    block = re.search(r"```json\n(.*?)\n```", doc("templates/team-review.md"), re.S).group(1)
    ask = json.loads(block)
    broken = {k: v for k, v in ask.items() if k != "if_no"}
    assert [e["field"] for e in core.validate_ask(broken)[1]] == ["if_no"]      # the console would notice
    assert core.validate_ask(ask)[1] == []
    assert ask["step"] == "confirm" and len(ask["evidence"]) >= 2               # both views, and "no" keeps the first
    first = ask["evidence"][0]["source"].split()[-1].split("@")[0]
    assert first != ask["id"] and ask["id"].startswith(first), (first, ask["id"])  # a new id, never the answered one


if __name__ == "__main__":
    _t.main(globals())
