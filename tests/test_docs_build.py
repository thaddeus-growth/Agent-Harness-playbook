"""The build workflow names only what is there, or says it is not there yet.

BUILD.md, the build-harness skill and the templates README are read as text:
every playbook path and link they name exists, or is in BUILD.md's one list
"Still being built"; every template BUILD.md names exists; every template is
listed; every harness test any of them names is one the scaffolder generates.
The templates themselves are held to the shapes the docs promise: the step
table, the skill and ssot/stages.agent.tsv list the same steps; the ssot files
carry the columns the kit reads and the trail every owner row needs; the
placeholders are the ones explained; harness.toml parses; CODEOWNERS names real
paths; the CI templates' title rule and secret scan match what they should and
every CI job states its rules; the digest guidance in decision-rights.md names real
console verbs. Each check is tried on a broken input first, so it cannot pass by
looking at nothing.
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


ROW = re.compile(r"^\| \[([^\]]+)\]\([^)]*\) \| [^|]* \| `([^`]+)`[^|]*\|", re.M)   # | [template](link) | step | `target` ... | ...
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def rendered_problems(rendered: dict[str, str], readme: str) -> list[str]:
    """The templates the scaffolder renders that `readme` does not list with the target it renders them to."""
    rows = dict(ROW.findall(readme))
    return [f"{t}: README says {rows.get(t)!r}, the scaffolder writes {target!r}"
            for t, target in rendered.items() if rows.get(t) != target]


def test_every_template_the_scaffolder_renders_is_listed_with_the_target_it_writes():
    sys.path.insert(0, os.path.join(REPO, "scaffold"))
    import new_harness
    assert dict(ROW.findall("| [a.md](a.md) | B1 | `docs/a.md` | x |")) == {"a.md": "docs/a.md"}
    assert rendered_problems({"a.md": "docs/a.md"}, "| [a.md](a.md) | B1 | `docs/b.md` | x |") != []       # the check would notice
    assert rendered_problems({"a.md": "docs/a.md"}, "") != []
    assert len(new_harness.RENDERED) >= 15, new_harness.RENDERED
    assert rendered_problems(new_harness.RENDERED, doc(TREADME)) == []


def stated_test_counts(text: str) -> list[str]:
    """The number of generated tests a doc states, as the word it uses."""
    return (re.findall(r"generates the (\w+) day-one tests", text) + re.findall(r"the (\w+) tests a new harness starts with", text)
            + re.findall(r"exactly the (\w+) tests", text))


def test_the_docs_say_how_many_tests_a_new_harness_starts_with_and_it_is_the_real_number():
    assert stated_test_counts("generates the nine day-one tests; the ten tests a new harness starts with") == ["nine", "ten"]
    want = NUMBER_WORDS[len(GENERATED)]
    for rel, n in (("docs/kit-and-tools.md", 2), (BUILD, 1)):
        said = stated_test_counts(doc(rel))
        assert len(said) == n and set(said) == {want}, (rel, said, want)
    assert set(stated_test_counts(doc("docs/kit-and-tools.md").replace("the ten tests", "the nine tests"))) != {want}   # a stale count shows


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
    assert set(cfg) == {"harness", "kit", "ssot", "release", "layers"}
    assert cfg["kit"] == {"packs": []}, "a new harness takes base and testkit; --packs adds more"
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


NOT_JOBS = {"stages", "workflow", "default", "variables", "include"}      # a CI file's own top-level keys
CI_TEMPLATES = ("templates/gitlab-ci.yml", "templates/ci/gitlab-ci.yml")
# One sample of each credential shape the CI templates name, built in pieces so this file holds no leak itself.
LEAKS = {"private key": "-" * 5 + "BEGIN RSA PRIVATE " + "KEY-----",
         "AWS key id": "AKIA" + "Q" * 16,
         "AWS session key id": "ASIA" + "Q" * 16,
         "GitHub token": "ghp" + "_" + "a" * 36,
         "GitLab token": "glpat" + "-" + "a" * 20,
         "GitHub fine-grained token": "github" + "_pat_" + "a" * 22,
         "Slack token": "xox" + "b-" + "1" * 12,
         "sk- key": "sk" + "-" + "a" * 32}


def ci_jobs(text: str) -> dict[str, str]:
    """{job name: its block} of a GitLab CI file: every top-level key that opens a block, except the file's own
    (stages, workflow, default, variables, include)."""
    parts = re.split(r"^([^\s#][^:\n]*):[ \t]*$", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2) if parts[i] not in NOT_JOBS}


def jobs_without_rules(text: str) -> list[str]:
    """The jobs with no `rules:` of their own. A job with none runs only in branch pipelines, never in a merge
    request's, so a merge request pipeline can turn green with no test run."""
    return [name for name, body in ci_jobs(text).items() if not re.search(r"^  rules:[ \t]*\n    - ", body, re.M)]


def without_rules(text: str, job: str) -> str:
    """`text` with the `rules:` block of one job cut out."""
    head, sep, body = text.partition(f"\n{job}:\n")
    return head + sep + re.sub(r"^  rules:[ \t]*\n(?:    .*\n)+", "", body, count=1, flags=re.M)


def fuller_patterns(text: str) -> list[re.Pattern]:
    """The extended regexes in the secret scan's pattern file (the heredoc of templates/ci/gitlab-ci.yml)."""
    body = text.split("<<'PATTERNS'\n", 1)[1].split("\n      PATTERNS", 1)[0]
    lines = [ln.strip() for ln in body.splitlines()]
    return [re.compile(ln) for ln in lines if ln and not ln.startswith("#")]


def leaks_missed(patterns: list[re.Pattern]) -> list[str]:
    """The names of the LEAKS shapes that none of `patterns` catches."""
    return [name for name, leak in LEAKS.items() if not any(p.search(leak) for p in patterns)]


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
    job = doc("templates/ci/gitlab-ci.yml")                                      # the fuller CI holds the same rule
    assert ci_pattern(job, "grep -Eq") == ci_pattern(text, "grep -Eq"), "the two CI templates' story-id rules differ"
    assert ci_pattern(job.replace("[SP]", "[S]"), "grep -Eq") != ci_pattern(text, "grep -Eq")   # the comparison would notice
    secret = re.compile(ci_pattern(text, "grep -rEn"))
    for name, leak in LEAKS.items():
        assert secret.search(leak), name
    assert not secret.search("a line about a key, AKIA-short and sk-short")


def test_every_job_of_both_ci_templates_states_its_rules():
    sample = "stages: [x]\nworkflow:\n  rules:\n    - if: $A\na:\n  stage: x\n  rules:\n    - when: on_success\n" \
             "b:\n  stage: x\n  script:\n    - true\n"
    assert list(ci_jobs(sample)) == ["a", "b"]                                  # the file's own keys are not jobs
    assert jobs_without_rules(sample) == ["b"]                                  # the check would notice a job with none
    assert jobs_without_rules(without_rules(sample, "a")) == ["a", "b"]
    short, fuller = (doc(rel) for rel in CI_TEMPLATES)
    assert {"secret scan", "story id", "changelog", "{{cli}} test"} <= set(ci_jobs(short)) and len(ci_jobs(short)) == 4
    assert {"secret scan", "story id", "changelog", "test", "zylos check", "adapter smoke"} <= set(ci_jobs(fuller))
    for rel, text in zip(CI_TEMPLATES, (short, fuller)):
        assert jobs_without_rules(text) == [], (rel, jobs_without_rules(text))
        for job in ci_jobs(text):                                                # and each job's rules are what is checked
            assert jobs_without_rules(without_rules(text, job)) == [job], (rel, job)


def changelog_check(text: str) -> str:
    """The shell of the `changelog` job's check, as the CI template writes it (the block under its `- |`)."""
    block = text.split("\nchangelog:\n", 1)[1].split("    - |\n", 1)[1]
    return "\n".join(ln[6:] for ln in block.splitlines() if ln.startswith("      ") or not ln.strip()).split("\n\n")[0]


def test_the_changelog_job_asks_a_changelog_line_of_a_visible_change():
    import subprocess
    for rel in CI_TEMPLATES:
        sh = changelog_check(doc(rel))
        def run(changed: str, title: str = "S01 a change") -> tuple[int, str]:
            r = subprocess.run(["sh", "-c", sh], capture_output=True, text=True,
                               env={"changed": changed, "CI_MERGE_REQUEST_TITLE": title, "PATH": os.environ["PATH"]})
            return r.returncode, r.stdout
        assert run("tests/a.py\n.gitlab-ci.yml\nCLAUDE.md")[0] == 0, rel              # nothing a host sees
        assert run("ssot/user-stories.agent.tsv\nconsole/ui_rules.tsv")[0] == 0, rel   # notes and the console's owner file
        for visible in ("scripts/x.py", "SKILL.md", "ssot/constants.tsv", "console/serve.py", "references/w.md"):
            code, out = run(visible)
            assert code == 1 and visible in out, (rel, visible)                          # the line is missing
            assert run(f"{visible}\nCHANGELOG.md")[0] == 0, (rel, visible)             # it is there
            assert run(visible, "S01 a typo [no changelog]")[0] == 0, (rel, visible)    # the title opts out


ZYLOS_CHECK = "node zylos/lib.js check"


def zylos_job_problems(job: str, readme: str, pinned: int) -> list[str]:
    """What is wrong with the `zylos check` job of the fuller CI template: its one script line is the command the adapter's
    README gives, and its node image is no older than `pinned`, the major the playbook's own CI runs the adapter's tests on."""
    out = []
    script = re.search(r"^  script:[ \t]*\n((?:    .*(?:\n|\Z))+)", job, re.M)
    if not script or [ln.strip() for ln in script[1].splitlines()] != [f"- {ZYLOS_CHECK}"]:
        out.append(f"its script is not exactly `{ZYLOS_CHECK}`")
    if f"`{ZYLOS_CHECK}`" not in readme:
        out.append("the adapter's README does not give that command")
    image = re.search(r"^  image: node:(\d+)[\w.-]*[ \t]*$", job, re.M)
    if not image or int(image[1]) < pinned:
        out.append(f"its image is not node {pinned} or newer")
    return out


def test_the_zylos_check_job_runs_the_command_the_adapter_readme_gives_on_the_node_ci_uses():
    readme = doc("hosts/zylos/README.md")
    ci = doc(".github/workflows/ci.yml")
    pinned = int(re.search(r'^\s+node-version: "(\d+)"$', ci, re.M)[1])
    job = ci_jobs(doc("templates/ci/gitlab-ci.yml"))["zylos check"]
    good = "  stage: check\n  image: node:24-alpine\n  script:\n    - node zylos/lib.js check\n"
    assert zylos_job_problems(good, readme, 24) == []
    for label, broken in (("a typo in the verb", good.replace("lib.js check", "lib.js chek")),
                          ("the adapter's path in this repository, not in a harness", good.replace("zylos/lib", "hosts/zylos/lib")),
                          ("a failure swallowed", good.replace("lib.js check", "lib.js check || true")),
                          ("a second script line", good + "    - true\n"),
                          ("no script", good.replace("  script:\n    - node zylos/lib.js check\n", "")),
                          ("an old node", good.replace("node:24", "node:10")),
                          ("no node image", good.replace("node:24-alpine", "python:3.12-slim"))):
        assert zylos_job_problems(broken, readme, 24), label
    assert zylos_job_problems(good, readme.replace(f"`{ZYLOS_CHECK}`", "`node zylos/lib.js`"), 24)    # the README is what it is held to
    assert zylos_job_problems(good.replace("node:24", "node:26"), readme, 24) == []                   # newer is fine
    assert zylos_job_problems(job, readme, pinned) == [], zylos_job_problems(job, readme, pinned)


def zylos_copy_problems(text: str) -> list[str]:
    """What is wrong with the one line of a build doc that tells a harness to copy the Zylos adapter: it sends the builder
    to the adapter's README and says that what is copied is what that README lists, not the whole folder (its `tests/`
    and two templates stay behind: the adapter's tests copy console/ and need the playbook around them)."""
    lines = [ln for ln in text.splitlines() if "hosts/zylos/README.md" in ln and re.search(r"cop(?:y|ies)", ln.lower())]
    if len(lines) != 1:
        return [f"{len(lines)} lines tell a harness to copy the adapter, not 1"]
    line = lines[0]
    out = []
    if re.search(r"cop(?:y|ies) \[hosts/zylos/\]\(", line):
        out.append("it has the harness copy the whole hosts/zylos/ folder")
    if "tests" not in line:
        out.append("it does not say the adapter's tests stay behind")
    return out


def test_the_build_docs_have_a_zylos_harness_copy_what_the_adapter_readme_lists_not_its_whole_folder():
    readme = doc("hosts/zylos/README.md")
    listed = section(readme, "What a harness copies, and what it writes")
    assert "lib.js" in listed and "tests/" not in listed.split("A harness **writes**")[0]     # the README's list leaves tests/ out
    assert "without `tests/` and the two templates" in readme                                # and says so
    old_build = "- A harness installed on Zylos copies [hosts/zylos/](hosts/zylos/README.md) into itself as `zylos/`, writes x.\n"
    old_skill = "12. **B9 Release**: On Zylos, copy [hosts/zylos/](../../hosts/zylos/README.md) first (BUILD.md, B9).\n"
    assert zylos_copy_problems(old_build) and zylos_copy_problems(old_skill)                  # the wording this replaced is refused
    assert zylos_copy_problems("Copy hosts/zylos/README.md's files.\nAnd hosts/zylos/README.md again: copy.\n")   # two lines, or none
    assert zylos_copy_problems("nothing about the host") == ["0 lines tell a harness to copy the adapter, not 1"]
    for rel in (BUILD, SKILL):
        assert zylos_copy_problems(doc(rel)) == [], (rel, zylos_copy_problems(doc(rel)))


def zylos_release_problems(b9: str, internal: list[str]) -> list[str]:
    """What is wrong with the B9 bullet that has a harness on Zylos bring the adapter into its release: it comes before the
    tag bullet (the release is `git archive` of the tag and the host installs that archive, so a copy made after the tag is
    not in it), says so, names [release].must_ship as the guard, and none of what it copies is [release].internal."""
    bullets = [ln for ln in b9.splitlines() if ln.startswith("- ")]
    adapter = [i for i, ln in enumerate(bullets) if "hosts/zylos/README.md" in ln]
    tag = [i for i, ln in enumerate(bullets) if ln.startswith("- Tag `v")]
    if len(adapter) != 1 or len(tag) != 1:
        return [f"{len(adapter)} adapter bullets and {len(tag)} tag bullets, not 1 and 1"]
    out = []
    if adapter[0] > tag[0]:
        out.append("the adapter's bullet comes after the tag bullet")
    for needle in ("before the tag is cut", "[release].must_ship", "zylos/manifest.json", "ecosystem.config.cjs"):
        if needle not in bullets[adapter[0]]:
            out.append(f"the adapter's bullet does not say {needle!r}")
    out += [f"[release].internal lists {p}, which never ships" for p in internal
            if p.strip("/").split("/")[0] in ("zylos", "ecosystem.config.cjs")]
    return out


def test_the_adapter_is_copied_and_tracked_before_the_tag_so_the_release_archive_has_it():
    with open(os.path.join(REPO, "templates/harness.toml"), "rb") as f:
        internal = tomllib.load(f)["release"]["internal"]
    b9 = section(doc(BUILD), "B9 · Release")
    assert b9, "BUILD.md has no B9 section"
    bullets = "- Tag `v1`: x.\n- A harness on Zylos: hosts/zylos/README.md, before the tag is cut, [release].must_ship, zylos/manifest.json, ecosystem.config.cjs.\n"
    assert zylos_release_problems(bullets, []) == ["the adapter's bullet comes after the tag bullet"]          # the order it had
    assert zylos_release_problems(bullets.replace("before the tag is cut, ", ""), [])                         # the sentence that says why
    assert zylos_release_problems("- Tag `v1`: x.\n", [])                                                     # no adapter bullet at all
    assert zylos_release_problems("- A harness on Zylos: hosts/zylos/README.md.\n- Tag `v1`: x.\n", [])       # no word of the guard
    assert zylos_release_problems(b9, ["zylos"]) and zylos_release_problems(b9, ["ecosystem.config.cjs"])   # a stale internal entry hides it
    assert zylos_release_problems(b9, internal) == [], zylos_release_problems(b9, internal)


ADAPTER_LIMIT = "Checked against a fake host only"


def zylos_claim_problems(readme: str, adapter: str) -> list[str]:
    """While the adapter's own README says it has only run against a fake host, the top-level README says so too and lists
    the adapter under 'Still unproven': its reuse table says 'Reuse' and its mermaid map puts it under 'Copy', and nothing
    else would tell a builder that no real zylos-core has run it."""
    if ADAPTER_LIMIT not in adapter:
        return []
    out = []
    unproven = re.search(r"Still unproven:([^\n]*)", readme)
    if not unproven or "Zylos adapter" not in unproven[1]:
        out.append("'Still unproven' does not list the Zylos adapter")
    if not re.search(r"adapter itself has only run against a fake host", readme):
        out.append("the README does not say the adapter has only run against a fake host")
    return out


def test_the_readme_does_not_promise_more_of_the_zylos_adapter_than_the_adapters_own_readme():
    adapter, readme = doc("hosts/zylos/README.md"), doc("docs/channels.md")
    assert ADAPTER_LIMIT in adapter, "the adapter's README no longer says it is fake-host only: settle the top-level claim"
    assert zylos_claim_problems(readme, adapter) == [], zylos_claim_problems(readme, adapter)
    assert zylos_claim_problems(readme.replace("and the Zylos adapter on a real zylos-core", ""), adapter)   # the list that was silent
    assert zylos_claim_problems(readme.replace("has only run against a fake host", "has run"), adapter)      # the proven-here paragraph
    assert zylos_claim_problems("Reuse.\n", adapter) and zylos_claim_problems("Reuse.\n", "a real host ran it") == []   # lifted with the limit


OLDER_CI = (("`--update-kit` does not rewrite", "says --update-kit leaves .gitlab-ci.yml alone"),
            ("](templates/gitlab-ci.yml)", "links the template it is a copy of"),
            ("never runs in a merge request pipeline", "says what a job with no rules does"),
            ("`- when: on_success`", "gives the rule to add by hand"))


def older_ci_problems(build: str) -> list[str]:
    """What is wrong with the B10 bullet for a harness scaffolded before every CI job stated its rules: --update-kit does not
    rewrite `.gitlab-ci.yml` (scaffold test [7]), so the owner adds the rules by hand. A job with no rules never runs in a
    merge request pipeline (GitLab's job rules page), so without them that pipeline can turn green with no test run."""
    lines = [ln for ln in section(build, "B10 · Operate").splitlines() if "`.gitlab-ci.yml`" in ln]
    if len(lines) != 1:
        return [f"{len(lines)} lines of B10 name `.gitlab-ci.yml`, not 1"]
    return [f"the line {why}" for need, why in OLDER_CI if need not in lines[0]]


def test_build_md_tells_a_harness_with_an_older_ci_file_to_add_the_rules_by_hand():
    build = doc(BUILD)
    assert older_ci_problems(build) == [], older_ci_problems(build)
    for need, why in OLDER_CI:                                                   # each part of the line is held
        assert older_ci_problems(build.replace(need, "x")) == [f"the line {why}"], need
    assert older_ci_problems(section(build, "B10 · Operate")) == ["0 lines of B10 name `.gitlab-ci.yml`, not 1"]   # no section, no line
    cut = "\n".join(ln for ln in build.splitlines() if "`--update-kit` does not rewrite" not in ln)
    assert older_ci_problems(cut) == ["0 lines of B10 name `.gitlab-ci.yml`, not 1"]
    assert "rules:" in doc(TREADME) and "BUILD.md, B10" in doc(TREADME)        # the templates README points there too


def test_the_meeting_source_tag_has_one_spelling_in_the_docs_that_give_it():
    tags = lambda text: set(re.findall(r"[a-z_]*meeting_<date>", text))        # noqa: E731
    assert tags("--source meeting_<date> and client_meeting_<date>") == {"meeting_<date>", "client_meeting_<date>"}   # the check sees two
    found = set()
    for rel in (BUILD, "templates/meeting-intake.md", "templates/workflows.md"):
        found |= tags(doc(rel))
    assert found == {"client_meeting_<date>"}, found


def test_the_fuller_ci_secret_scan_catches_every_shape_the_short_form_does():
    short = re.compile(ci_pattern(doc("templates/gitlab-ci.yml"), "grep -rEn"))
    text = doc("templates/ci/gitlab-ci.yml")
    fuller = fuller_patterns(text)
    assert [name for name, leak in LEAKS.items() if not short.search(leak)] == []
    assert leaks_missed(fuller) == []
    for name, line in (("Slack token", "      xox[abpr]-[0-9A-Za-z-]{10,}\n"),
                       ("sk- key", "      (^|[^0-9A-Za-z])sk-[0-9A-Za-z_-]{32,}\n"),
                       ("GitLab token", "      glpat-[0-9A-Za-z_-]{20}\n"),
                       ("GitHub fine-grained token", "      github_pat_[0-9A-Za-z_]{22,}\n")):
        assert line in text, line
        assert leaks_missed(fuller_patterns(text.replace(line, ""))) == [name], name     # the check would notice a pattern dropped
    for fine in ("a line about a key, AKIA-short and sk-short", "task-" + "a" * 40, "risk-" + "b" * 40):
        assert not any(p.search(fine) for p in fuller), fine                     # sk- has a left boundary here


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


def test_decision_rights_keeps_the_lanes_and_the_digest():
    text = doc("templates/decision-rights.md")
    for heading in ("1. Always the owner", "2. AI proposes, owner confirms", "3. AI alone",
                    "The risky list (owner merges)", "Channels that count", "The digest"):
        assert section(text, heading), heading
    assert "Deciders and advisers" not in text and "Dissent" not in text


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
    for rel in (BUILD, SKILL, "templates/decision-rights.md"):
        found = set(CALL.findall(doc(rel)))
        called |= found
        assert found <= verbs, (rel, sorted(found - verbs))
    assert {"add", "answers", "applied", "digest"} <= called, called
    assert set(FLAG.findall(doc("templates/decision-rights.md"))) - {"--json"} <= flags   # --json is the harness's
    assert "--deciders" not in flags and "--user-header" in flags


def test_every_build_tool_command_the_docs_print_uses_real_flags():
    assert TOOL.findall("run `python3 build/x.py --a B --c` now") == [("build/x.py", " --a B --c")]
    runs = [(rel, m) for rel in (BUILD, SKILL, "templates/decision-rights.md") for m in TOOL.findall(doc(rel))]
    assert len(runs) >= 4, runs
    known = {}
    for rel, (script, rest) in runs:
        if not os.path.exists(os.path.join(REPO, script)):
            assert script in to_be_built(doc(BUILD)), (rel, script)             # a tool not built yet is listed
            continue
        known.setdefault(script, help_flags(script))
        shown = set(FLAG.findall(rest))
        assert shown <= known[script], (rel, script, sorted(shown - known[script]))
    assert "--pick" in known.get("build/intake_to_asks.py", {"--pick"})



GUIDE = ["README.md"] + sorted(f"docs/{n}" for n in os.listdir(os.path.join(REPO, "docs")) if n.endswith(".md"))
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def anchors(rel: str) -> set[str]:
    """GitHub's heading anchors of a Markdown file."""
    heads = re.findall(r"^#{1,6} (.+)$", doc(rel), re.M)
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in heads}


def link_problems(rel: str, text: str) -> list[str]:
    """Relative links in `text` (as written in `rel`) whose file or #anchor is not there."""
    out = []
    for target in MD_LINK.findall(text):
        if re.match(r"[a-z]+:", target):
            continue
        path, _, anchor = target.partition("#")
        dest = os.path.normpath(os.path.join(os.path.dirname(rel), path)) if path else rel
        if not os.path.exists(os.path.join(REPO, dest)):
            out.append(f"{rel}: {target} (no {dest})")
        elif anchor and dest.endswith(".md") and anchor not in anchors(dest):
            out.append(f"{rel}: {target} (no heading #{anchor} in {dest})")
    return out


def test_every_link_in_the_readme_and_docs_resolves():
    assert len(GUIDE) >= 6, GUIDE
    for rel in GUIDE:
        assert link_problems(rel, doc(rel)) == [], link_problems(rel, doc(rel))
    assert link_problems("docs/x.md", "[a](../README.md#who-decides-what) [b](../nope.md) [c](rules.md#nope)") == [
        "docs/x.md: ../nope.md (no nope.md)", "docs/x.md: rules.md#nope (no heading #nope in docs/rules.md)"]
    assert len(doc("README.md").split()) <= 2000, "the README is the overview; the detail goes in docs/"


SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}   # agentskills.io/specification


def test_the_build_skill_follows_the_agent_skills_spec():
    head = doc(SKILL).split("---\n")[1]
    keys = re.findall(r"^([\w-]+):", head, re.M)
    assert set(keys) <= SKILL_KEYS and {"name", "description"} <= set(keys), keys
    name = re.search(r"^name: (.+)$", head, re.M)[1].strip()
    desc = re.search(r"^description: (.+)$", head, re.M)[1].strip()
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64, name
    assert name == os.path.basename(os.path.dirname(SKILL)), "the name is the skill's folder"
    assert 0 < len(desc) <= 1024, len(desc)


if __name__ == "__main__":
    _t.main(globals())
