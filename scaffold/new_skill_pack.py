#!/usr/bin/env python3
"""Generate a new skill pack skeleton: green before its first feature.

    python3 scaffold/new_skill_pack.py --name acme-skill-pack --cli acme \\
        --prefix ACME --dir ../acme-skill-pack [--markets AA,BB] \\
        [--packs compliance,data] [--owner @handle] [--repo-home WHERE] \\
        [--langs en,zh-Hans,zh-Hant] [--dry-run]
    python3 scaffold/new_skill_pack.py --dir ../acme-skill-pack --update-kit

What it writes (BUILD.md, B1):

  * every template with a rendered target (RENDERED below; templates/README.md
    lists the others as used, not copied, or written by a later build step),
    placeholders filled ({{name}}, {{cli}}, {{env_prefix}}, {{owner}},
    {{repo_home}}, {{today}}): skill-pack.toml, CLAUDE.md, SKILL.md, README.md,
    references/workflows.md, .gitignore, .gitlab-ci.yml, .gitlab/CODEOWNERS,
    docs/ (decision rights, the install and triage checklists, the data bug
    classes, the doctor's checks, the owner review loop, vendor API
    discovery, the session handoff, the roadmap), ssot/README.md.
    `<<fill: …>>` stays only in the files the build fills later (FILLED);
  * ssot/: every registry and owner file as its header row, plus the
    constants row the kit's write path reads (approval_ttl_hours), the
    build steps (stages.agent.tsv) and the index of what it wrote;
  * the skeleton (scaffold/skeleton/): scripts/<cli>.py (the kit's
    Dispatcher), scripts/verbs.py, the kit's facts, decisions, pending,
    compute stories, queue and execute verbs, scripts/_lib/ (the schema:
    human tables only; the one writer: an empty allowlist), .env.example,
    evals/, .claude/, and exactly the thirteen tests templates/README.md
    names, each a thin call into kit.testing.suites, plus tests/run.py;
  * the kit and the console vendored (kit/tools/vendor.py) into
    scripts/kit/ and console/, each with its VERSION and MANIFEST.sha256;
  * .gitattributes rendered from skill-pack.toml and the ssot index
    (kit.guards.release.render_gitattributes).

It refuses a --dir that exists and is not empty, or that sits inside this
playbook (except under scaffold/out/, an ignored preview folder); it takes
no data path; it leaves no `{{` behind. `--dry-run`
prints the file list and writes nothing. `--update-kit` vendors the kit
and the console again into an existing skill pack and touches nothing else.

Next: `git init`, commit, `python3 scripts/<cli>.py test` (the suite is
green before the first feature; CI runs the same); then, before the first
push and in this order: create the client-term list outside the
repository, seeded with the client's name from the intake; name it in
skill-pack.toml (`[guards.leak] terms`); install the leak guard as the
pre-push hook (HOOK); push. The hook refuses a push while no list is
named, so it is installed only once one is.

Test: scaffold/tests/test_new_skill_pack.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
PLAYBOOK = Path(__file__).resolve().parents[1]
TEMPLATES = PLAYBOOK / "templates"
SKELETON = Path(__file__).resolve().parent / "skeleton"
sys.path.insert(0, str(PLAYBOOK))

from kit import zhconv  # noqa: E402
from kit.tools import packs as kit_packs, vendor  # noqa: E402

# the pre-push hook the next steps install (kit/tools/leak_guard.py)
HOOK = "scripts/kit/tools/pre-push.sample"

# template (under templates/) -> target (under the skill pack)
RENDERED = {
    "skill-pack.toml": "skill-pack.toml",
    "AGENT_INSTRUCTIONS.md": "CLAUDE.md",
    "SKILL.md": "SKILL.md",
    "README-operator.md": "README.md",
    "workflows.md": "references/workflows.md",
    "gitignore": ".gitignore",
    "gitlab-ci.yml": ".gitlab-ci.yml",
    "CODEOWNERS": ".gitlab/CODEOWNERS",
    "CHANGELOG.md": "CHANGELOG.md",
    "decision-rights.md": "docs/decision-rights.md",
    "install-checklist.md": "docs/install-checklist.md",
    "triage-checklist.md": "docs/triage-checklist.md",
    "bug-classes.md": "docs/bug-classes.md",
    "doctor-checks.md": "docs/doctor-checks.md",
    "owner-review-loop.md": "docs/owner-review-loop.md",
    "vendor-api-discovery.md": "docs/vendor-api-discovery.md",
    "session-handoff.md": "docs/session-handoff.md",
    "ROADMAP.md": "docs/ROADMAP.md",
    "ssot/README.md": "ssot/README.md",
}
# the files the build writes later (B2, B8): `<<fill: …>>` may stay there
FILLED = {"SKILL.md", "README.md", "references/workflows.md",
          "docs/decision-rights.md"}
# skeleton path -> target (`cli` = the CLI word); everything else keeps its
# path under scaffold/skeleton/
SKELETON_TARGETS = {"scripts/cli.py": "scripts/{cli}.py",
                    "claude/README.md": ".claude/README.md",
                    "env.example": ".env.example"}
# ssot files a new skill pack starts with, as their header row; alert rules
# come with the skill pack's own alert compute (B6)
SSOT_HEADERS = ["glossary.tsv", "glossary.agent.tsv", "user-stories.tsv",
                "user-stories.agent.tsv", "policies.tsv", "policies.agent.tsv",
                "fact_keys.tsv", "decision_keys.tsv", "message_codes.tsv",
                "story_checks.tsv"]
KEPT_CONSTANTS = ("approval_ttl_hours",)
GENERATED_TESTS = ("test_run_tests.py", "test_ssot.py", "test_layering.py",
                   "test_clock.py", "test_boundary.py", "test_json_contract.py",
                   "test_human_tables.py", "test_gate.py", "test_release.py",
                   "test_changelog.py", "test_roadmap.py", "test_kit_drift.py",
                   "test_never_table.py")

# The day-one checklist in docs/stages.md, item by item (the item's first
# words), and the generated file(s) that answer it. A new checklist item
# fails scaffold/tests/test_new_skill_pack.py until it is mapped here.
DAY_ONE = {
    "Meeting intake": ["ssot/stages.agent.tsv"],
    "A review page before the first paid batch": ["docs/owner-review-loop.md",
                                                  "docs/vendor-api-discovery.md"],
    "`docs/ROADMAP.md`": ["docs/ROADMAP.md", "tests/test_roadmap.py",
                          "docs/session-handoff.md"],
    "The test kit before the first feature": [
        "tests/run.py", "tests/test_run_tests.py", "tests/test_clock.py",
        "tests/test_layering.py", "tests/test_release.py", "CLAUDE.md"],
    "An empty registry index": ["ssot/index.tsv", "tests/test_ssot.py"],
    "CI from the first commit": [".gitlab-ci.yml"],
    "A story-check registry": ["ssot/story_checks.tsv",
                               "scripts/compute_stories.py"],
    "\"Declare it or refuse\"": ["scripts/{cli}.py", "scripts/facts.py",
                                 "tests/test_gate.py",
                                 "docs/doctor-checks.md"],
    "Raw that only grows": ["scripts/kit/raw.py"],
    "One fixture test per data bug class": ["docs/bug-classes.md"],
    "Human tables apart from the cache": ["scripts/_lib/schema.py",
                                          "tests/test_human_tables.py"],
    "The `--json` contract": ["tests/test_json_contract.py",
                              "ssot/message_codes.tsv"],
    "Vendor [`kit/`]": ["scripts/kit/human.py", "scripts/kit/messages.py",
                       "scripts/kit/runner.py", "tests/test_gate.py",
                       "tests/test_json_contract.py",
                       "tests/test_never_table.py"],
    "Typed keys": ["ssot/fact_keys.tsv", "ssot/decision_keys.tsv"],
    "Boundary and layering tests": ["tests/test_boundary.py",
                                    "tests/test_layering.py",
                                    "console/ui_rules.tsv"],
    "A golden-diff tool": ["CLAUDE.md"],
    "The owner's inbox": ["console/ask.py", "console/serve.py"],
    "Writes and paid calls off": ["scripts/_lib/writer.py",
                                  "scripts/execute_actions.py"],
    "Protected paths for the risky list": [".gitlab/CODEOWNERS"],
    "Git rules": ["CLAUDE.md", ".gitlab-ci.yml"],
    "Releases are a `git archive`": ["tests/test_release.py",
                                     ".gitattributes",
                                     "docs/install-checklist.md"],
    "Before the first hosted client": ["docs/install-checklist.md"],
}

NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
CLI = re.compile(r"^[a-z][a-z0-9]{1,15}$")
PREFIX = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
MARKET = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?$")
TAKEN = {"kit", "verbs", "facts", "decisions", "pending", "compute_stories",
         "queue_actions", "execute_actions", "console", "test", "tests"}
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class Refused(Exception):
    """A request the scaffolder will not carry out (exit 2, one line)."""


# ---- rendering --------------------------------------------------------------

def fill(text: str, values: dict[str, str], where: str) -> str:
    def one(m: re.Match) -> str:
        if m.group(1) not in values:
            raise Refused(f"{where}: unknown placeholder {m.group(0)}")
        return values[m.group(1)]
    out = PLACEHOLDER.sub(one, text)
    if "{{" in out:
        raise Refused(f"{where}: a `{{{{` is left after filling")
    return out


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{x}"' for x in items) + "]"


def render_toml(text: str, markets: list[str], langs: list[str],
                packs: list[str] = ()) -> str:
    text = re.sub(r"^markets = \[\][^\n]*", lambda m: m.group(0).replace(
        "[]", toml_list(markets), 1), text, count=1, flags=re.M)
    text = re.sub(r"^packs = \[\]", "packs = " + toml_list(list(packs)),
                  text, count=1, flags=re.M)
    text = re.sub(r'^languages = \["en", "zh"\]', "languages = "
                  + toml_list(langs), text, count=1, flags=re.M)
    return text


def codeowners(text: str, files: set[str]) -> str:
    """The template's blocks whose paths the new skill pack has; a block for a
    path it does not have yet (the client console's) is left out."""
    blocks, out = text.split("\n\n"), []
    for block in blocks:
        paths = [ln.split()[0] for ln in block.splitlines()
                 if ln.strip() and not ln.startswith("#")]
        if paths and not all(_covered(p, files) for p in paths):
            continue
        out.append(block)
    return "\n\n".join(out)


def _covered(path: str, files: set[str]) -> bool:
    p = path.strip("/")
    return p in files or any(f.startswith(p + "/") for f in files)


def labels(head: list[str], langs: list[str]) -> list[str]:
    """A header with its per-language columns (label_*, meaning_*) for the
    skill pack's languages, in place of the template's en and zh."""
    out = []
    for c in head:
        m = re.match(r"^(label|meaning)_(en|zh)$", c)
        if m:
            if m.group(2) == "en":
                out += [f"{m.group(1)}_{lang}" for lang in langs]
            continue
        out.append(c)
    return out


def chinese(row: dict, col: str) -> str:
    """The template's label for `col`: its own Chinese (the template writes
    Simplified) for zh, zh-Hans and zh-Hant (converted), English otherwise."""
    if col in row:
        return row[col]
    if col in ("label_zh-Hans", "label_zh-Hant"):
        return zhconv.convert(row["label_zh"], col.removeprefix("label_"))
    return row["label_en"]


def constants(langs: list[str]) -> str:
    rows = (TEMPLATES / "ssot" / "constants.tsv").read_text(
        encoding="utf-8").rstrip("\n").split("\n")
    head = rows[0].split("\t")
    out = ["\t".join(labels(head, langs))]
    for line in rows[1:]:
        r = dict(zip(head, line.split("\t")))
        if r["name"] not in KEPT_CONSTANTS:
            continue
        r["why"] = ("an approval that has not run within this many hours "
                    "must be approved again: the playbook's gate")
        cells = []
        for c in labels(head, langs):
            m = re.match(r"^label_([\w-]+)$", c)
            cells.append(chinese(r, c) if m else r[c])
        out.append("\t".join(cells))
    return "\n".join(out) + "\n"


def ssot_files(langs: list[str]) -> dict[str, str]:
    out = {}
    for name in SSOT_HEADERS:
        head = (TEMPLATES / "ssot" / name).read_text(
            encoding="utf-8").split("\n", 1)[0].split("\t")
        out[f"ssot/{name}"] = "\t".join(labels(head, langs)) + "\n"
    out["ssot/constants.tsv"] = constants(langs)
    out["ssot/stages.agent.tsv"] = (TEMPLATES / "ssot" / "stages.agent.tsv"
                                    ).read_text(encoding="utf-8")
    index = (TEMPLATES / "ssot" / "index.tsv").read_text(
        encoding="utf-8").rstrip("\n").split("\n")
    keep = [index[0]] + [ln for ln in index[1:]
                         if ln.split("\t")[0] in {"ssot/index.tsv", *out}]
    out["ssot/index.tsv"] = "\n".join(keep) + "\n"
    return out


def plan(values: dict[str, str], markets: list[str], langs: list[str],
         packs: list[str] = ()) -> dict[str, str]:
    """{target path: text} of every file the scaffolder writes itself (the
    vendored kit and console, and .gitattributes, come after)."""
    files: dict[str, str] = {}
    for rel in sorted(p.relative_to(SKELETON).as_posix()
                      for p in SKELETON.rglob("*") if p.is_file()
                      and "__pycache__" not in p.parts
                      and p.suffix != ".pyc" and p.name != ".DS_Store"):
        target = SKELETON_TARGETS.get(rel, rel).format(cli=values["cli"])
        files[target] = fill((SKELETON / rel).read_text(encoding="utf-8"),
                             values, f"skeleton/{rel}")
    files.update(ssot_files(langs))
    for src, target in RENDERED.items():
        text = fill((TEMPLATES / src).read_text(encoding="utf-8"), values,
                    f"templates/{src}")
        if src == "skill-pack.toml":
            text = render_toml(text, markets, langs, packs)
        files[target] = text
    vendored = {"scripts/kit/raw.py", "console/ask.py", "console/serve.py",
                "console/ui_rules.tsv", ".gitattributes"}
    files[".gitlab/CODEOWNERS"] = codeowners(files[".gitlab/CODEOWNERS"],
                                             set(files) | vendored)
    filled = FILLED | ({"CLAUDE.md"} if values["repo_home"].startswith(
        "<<fill:") else set())
    for target, text in files.items():
        if "<<fill:" in text and target not in filled:
            raise Refused(f"{target}: holds `<<fill:` but the build does not "
                          f"fill it")
    return files


# ---- writing ------------------------------------------------------------------

OUT = Path(__file__).resolve().parent / "out"   # a preview folder, ignored


def check_dir(d: Path, *, update: bool) -> None:
    if (d == PLAYBOOK or PLAYBOOK in d.parents) and OUT not in d.parents:
        raise Refused(f"{d} is inside the playbook checkout: a skill pack is a "
                      f"repository of its own")
    if update:
        if not (d / "skill-pack.toml").is_file():
            raise Refused(f"{d} holds no skill-pack.toml: --update-kit vendors "
                          f"into an existing skill pack")
        return
    if d.exists() and (not d.is_dir() or any(d.iterdir())):
        raise Refused(f"{d} exists and is not empty: the scaffolder writes "
                      f"a new skill pack only")


def write(d: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if rel.startswith("scripts/") and rel.endswith(".py") \
                and text.startswith("#!"):
            p.chmod(0o755)


def gitattributes(d: Path) -> str:
    from kit.guards import release
    return release.render_gitattributes(d)


def values_of(a: argparse.Namespace) -> tuple[dict[str, str], list[str],
                                              list[str]]:
    for flag, rx, v in (("--name", NAME, a.name), ("--cli", CLI, a.cli),
                        ("--prefix", PREFIX, a.prefix)):
        if not v or not rx.match(v):
            raise Refused(f"{flag} {v!r}: expected {rx.pattern}")
    if a.cli in TAKEN:
        raise Refused(f"--cli {a.cli!r} is a script or folder name the "
                      f"skeleton already uses")
    markets = [m.strip() for m in (a.markets or "").split(",") if m.strip()]
    if any(not MARKET.match(m) for m in markets) \
            or len(set(markets)) != len(markets):
        raise Refused(f"--markets {a.markets!r}: distinct plain codes, e.g. "
                      f"AA,BB")
    langs = [x.strip() for x in (a.langs or "en,zh").split(",") if x.strip()]
    if not langs or langs[0] != "en" or any(not LANG.match(x) for x in langs) \
            or len(set(langs)) != len(langs):
        raise Refused(f"--langs {a.langs!r}: distinct language codes, en "
                      f"first (the kit's own codes are en and zh)")
    owner = a.owner or "@owner"
    if not re.match(r"^@[\w./-]+$", owner):
        raise Refused(f"--owner {owner!r}: a handle such as @owner")
    home = a.repo_home or "<<fill: where the repository lives>>"
    names = set(kit_packs.table(PLAYBOOK / "kit").values()) - set(kit_packs.ALWAYS)
    packs = [x.strip() for x in (a.packs or "").split(",") if x.strip()]
    if any(x not in names for x in packs) or len(set(packs)) != len(packs):
        raise Refused(f"--packs {a.packs!r}: distinct names of "
                      f"{', '.join(sorted(names))} (base and testkit always come)")
    return ({"name": a.name, "cli": a.cli, "env_prefix": a.prefix,
             "owner": owner, "repo_home": home,
             "today": date.today().isoformat()}, markets, langs, packs)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scaffold/new_skill_pack.py",
        description="Generate a new skill pack skeleton from templates/ and "
                    "vendor the kit and the console into it.")
    p.add_argument("--dir", required=True, help="the new skill pack's folder "
                   "(absent or empty; outside this playbook)")
    p.add_argument("--name", help="the skill pack's name, e.g. acme-skill-pack")
    p.add_argument("--cli", help="the CLI word, e.g. acme")
    p.add_argument("--prefix", help="the env prefix, e.g. ACME")
    p.add_argument("--markets", default="", help="the closed set of scopes, "
                   "comma-separated (default: none, no partition)")
    p.add_argument("--packs", default="", help="kit packs besides base and "
                   "testkit, comma-separated: compliance, generation, data "
                   "(default: none)")
    p.add_argument("--langs", default="en,zh", help="languages of the message "
                   "registry, en first (default en,zh)")
    p.add_argument("--owner", help="the owner's handle for CODEOWNERS "
                   "(default @owner)")
    p.add_argument("--repo-home", help="where the repository lives (named in "
                   "CLAUDE.md; without it CLAUDE.md keeps a <<fill: …>>)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the file list, write nothing")
    p.add_argument("--update-kit", action="store_true",
                   help="vendor the kit and the console again into an "
                        "existing skill pack; touch nothing else")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    d = Path(a.dir).expanduser().resolve()
    try:
        check_dir(d, update=a.update_kit)
        if a.update_kit:
            return vendor.main(["--skill-pack", str(d)]
                               + (["--dry-run"] if a.dry_run else []))
        values, markets, langs, packs = values_of(a)
        files = plan(values, markets, langs, packs)
    except Refused as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        for rel in sorted(files):
            print(f"  write    {rel}")
        print("  write    .gitattributes (from skill-pack.toml and ssot/index.tsv)")
        print(f"  vendor   {'scripts/kit/'} and console/ (kit/tools/vendor.py)")
        print(f"dry run: {len(files) + 1} files and two vendored copies "
              f"for {d}; nothing written")
        return 0
    d.mkdir(parents=True, exist_ok=True)
    write(d, files)
    rc = vendor.main(["--skill-pack", str(d)])
    if rc != 0:
        return rc
    write(d, {".gitattributes": gitattributes(d)})
    fills = sorted(r for r, t in files.items() if "<<fill:" in t)
    print(f"{values['name']}: {len(files) + 1} files written to {d}, the kit "
          f"and the console vendored")
    print(f"  still to fill by the build (B2, B8): {', '.join(fills)}")
    for line in next_steps(d, values):
        print(line)
    return 0


def next_steps(d: Path, values: dict) -> list[str]:
    """What to run after the scaffold, in order: the client-term list
    exists and is named before the leak guard's hook is installed, so the
    hook never stands between a new skill pack and its first push."""
    lists = f"~/<workspaces>/{values['name']}/*/data/client_terms.txt"
    return [
        f"next: cd {d} && git init && git add -A && git commit -m "
        f"'scaffold' && python3 scripts/{values['cli']}.py test",
        "then, before the first push (BUILD.md, B1):",
        f"  1. create the client-term list outside this repository, seeded "
        f"with the client's name from the intake (one term per line), e.g. "
        f"{lists.replace('*', '<client>')}",
        f"  2. name it in skill-pack.toml: [guards.leak] terms = [\"{lists}\"]",
        f"  3. install the leak guard as the pre-push hook: install -m 755 "
        f"{HOOK} .git/hooks/pre-push",
        "  4. push",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
