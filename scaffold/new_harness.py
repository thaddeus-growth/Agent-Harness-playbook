#!/usr/bin/env python3
"""Generate a new harness skeleton: green before its first feature.

    python3 scaffold/new_harness.py --name acme-harness --cli acme \\
        --prefix ACME --dir ../acme-harness [--markets HK,TW] \\
        [--owner @handle] [--repo-home WHERE] [--langs en,zh] [--dry-run]
    python3 scaffold/new_harness.py --dir ../acme-harness --update-kit

What it writes (BUILD.md, B1):

  * every template with a target (templates/README.md), placeholders
    filled ({{name}}, {{cli}}, {{env_prefix}}, {{owner}}, {{repo_home}}):
    harness.toml, CLAUDE.md, SKILL.md, README.md, references/workflows.md,
    .gitignore, .gitlab-ci.yml, .gitlab/CODEOWNERS, docs/ (decision rights,
    team review, the install and triage checklists, the data bug classes,
    the doctor's checks), ssot/README.md.
    `<<fill: …>>` stays only in the files the build fills later (FILLED);
  * ssot/: every registry and owner file as its header row, plus the
    constants row the kit's write path reads (approval_ttl_hours), the
    build steps (stages.agent.tsv) and the index of what it wrote;
  * the skeleton (scaffold/skeleton/): scripts/<cli>.py (the kit's
    Dispatcher), scripts/verbs.py, the kit's facts, decisions, pending,
    compute stories, queue and execute verbs, scripts/_lib/ (the schema:
    human tables only; the one writer: an empty allowlist), .env.example,
    evals/, .claude/, and exactly the ten tests templates/README.md
    names, each a thin call into kit.testing.suites, plus tests/run.py;
  * the kit and the console vendored (kit/tools/vendor.py) into
    scripts/kit/ and console/, each with its VERSION and MANIFEST.sha256;
  * .gitattributes rendered from harness.toml and the ssot index
    (kit.guards.release.render_gitattributes).

It refuses a --dir that exists and is not empty, or that sits inside this
playbook (except under scaffold/out/, an ignored preview folder); it takes
no data path; it leaves no `{{` behind. `--dry-run`
prints the file list and writes nothing. `--update-kit` vendors the kit
and the console again into an existing harness and touches nothing else.

Next: `git init`, commit, push, and `python3 scripts/<cli>.py test` (the
suite is green before the first feature; CI runs the same).

Test: scaffold/tests/test_new_harness.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PLAYBOOK = Path(__file__).resolve().parents[1]
TEMPLATES = PLAYBOOK / "templates"
SKELETON = Path(__file__).resolve().parent / "skeleton"
sys.path.insert(0, str(PLAYBOOK))

from kit.tools import vendor  # noqa: E402

# template (under templates/) -> target (under the harness)
RENDERED = {
    "harness.toml": "harness.toml",
    "AGENT_INSTRUCTIONS.md": "CLAUDE.md",
    "SKILL.md": "SKILL.md",
    "README-operator.md": "README.md",
    "workflows.md": "references/workflows.md",
    "gitignore": ".gitignore",
    "gitlab-ci.yml": ".gitlab-ci.yml",
    "CODEOWNERS": ".gitlab/CODEOWNERS",
    "decision-rights.md": "docs/decision-rights.md",
    "team-review.md": "docs/team-review.md",
    "install-checklist.md": "docs/install-checklist.md",
    "triage-checklist.md": "docs/triage-checklist.md",
    "bug-classes.md": "docs/bug-classes.md",
    "doctor-checks.md": "docs/doctor-checks.md",
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
# ssot files a new harness starts with, as their header row; alert rules
# come with the harness's own alert compute (B6)
SSOT_HEADERS = ["glossary.tsv", "glossary.agent.tsv", "user-stories.tsv",
                "user-stories.agent.tsv", "policies.tsv", "policies.agent.tsv",
                "fact_keys.tsv", "decision_keys.tsv", "message_codes.tsv",
                "story_checks.tsv"]
KEPT_CONSTANTS = ("approval_ttl_hours",)
GENERATED_TESTS = ("test_run_tests.py", "test_ssot.py", "test_layering.py",
                   "test_clock.py", "test_boundary.py", "test_json_contract.py",
                   "test_human_tables.py", "test_gate.py", "test_release.py",
                   "test_kit_drift.py")

# The playbook README's day-one checklist, item by item (the item's first
# words), and the generated file(s) that answer it. A new checklist item
# fails scaffold/tests/test_new_harness.py until it is mapped here.
DAY_ONE = {
    "Meeting intake": ["ssot/stages.agent.tsv"],
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
                       "tests/test_json_contract.py"],
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
LANG = re.compile(r"^[a-z]{2,3}$")
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


def render_toml(text: str, markets: list[str], langs: list[str]) -> str:
    text = re.sub(r"^markets = \[\][^\n]*", lambda m: m.group(0).replace(
        "[]", toml_list(markets), 1), text, count=1, flags=re.M)
    text = re.sub(r'^languages = \["en", "zh"\]', "languages = "
                  + toml_list(langs), text, count=1, flags=re.M)
    return text


def codeowners(text: str, files: set[str]) -> str:
    """The template's blocks whose paths the new harness has; a block for a
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
    harness's languages, in place of the template's en and zh."""
    out = []
    for c in head:
        m = re.match(r"^(label|meaning)_(en|zh)$", c)
        if m:
            if m.group(2) == "en":
                out += [f"{m.group(1)}_{lang}" for lang in langs]
            continue
        out.append(c)
    return out


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
            m = re.match(r"^label_(\w+)$", c)
            cells.append(r.get(c, r["label_en"]) if m else r[c])
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


def plan(values: dict[str, str], markets: list[str], langs: list[str]
         ) -> dict[str, str]:
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
        if src == "harness.toml":
            text = render_toml(text, markets, langs)
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
        raise Refused(f"{d} is inside the playbook checkout: a harness is a "
                      f"repository of its own")
    if update:
        if not (d / "harness.toml").is_file():
            raise Refused(f"{d} holds no harness.toml: --update-kit vendors "
                          f"into an existing harness")
        return
    if d.exists() and (not d.is_dir() or any(d.iterdir())):
        raise Refused(f"{d} exists and is not empty: the scaffolder writes "
                      f"a new harness only")


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
                      f"HK,TW")
    langs = [x.strip() for x in (a.langs or "en,zh").split(",") if x.strip()]
    if not langs or langs[0] != "en" or any(not LANG.match(x) for x in langs) \
            or len(set(langs)) != len(langs):
        raise Refused(f"--langs {a.langs!r}: distinct language codes, en "
                      f"first (the kit's own codes are en and zh)")
    owner = a.owner or "@owner"
    if not re.match(r"^@[\w./-]+$", owner):
        raise Refused(f"--owner {owner!r}: a handle such as @owner")
    home = a.repo_home or "<<fill: where the repository lives>>"
    return ({"name": a.name, "cli": a.cli, "env_prefix": a.prefix,
             "owner": owner, "repo_home": home}, markets, langs)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scaffold/new_harness.py",
        description="Generate a new harness skeleton from templates/ and "
                    "vendor the kit and the console into it.")
    p.add_argument("--dir", required=True, help="the new harness's folder "
                   "(absent or empty; outside this playbook)")
    p.add_argument("--name", help="the harness's name, e.g. acme-harness")
    p.add_argument("--cli", help="the CLI word, e.g. acme")
    p.add_argument("--prefix", help="the env prefix, e.g. ACME")
    p.add_argument("--markets", default="", help="the closed set of scopes, "
                   "comma-separated (default: none, no partition)")
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
                        "existing harness; touch nothing else")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    d = Path(a.dir).expanduser().resolve()
    try:
        check_dir(d, update=a.update_kit)
        if a.update_kit:
            return vendor.main(["--harness", str(d)]
                               + (["--dry-run"] if a.dry_run else []))
        values, markets, langs = values_of(a)
        files = plan(values, markets, langs)
    except Refused as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        for rel in sorted(files):
            print(f"  write    {rel}")
        print("  write    .gitattributes (from harness.toml and ssot/index.tsv)")
        print(f"  vendor   {'scripts/kit/'} and console/ (kit/tools/vendor.py)")
        print(f"dry run: {len(files) + 1} files and two vendored copies "
              f"for {d}; nothing written")
        return 0
    d.mkdir(parents=True, exist_ok=True)
    write(d, files)
    rc = vendor.main(["--harness", str(d)])
    if rc != 0:
        return rc
    write(d, {".gitattributes": gitattributes(d)})
    fills = sorted(r for r, t in files.items() if "<<fill:" in t)
    print(f"{values['name']}: {len(files) + 1} files written to {d}, the kit "
          f"and the console vendored")
    print(f"  still to fill by the build (B2, B8): {', '.join(fills)}")
    print(f"next: cd {d} && git init && git add -A && git commit -m "
          f"'scaffold' && python3 scripts/{values['cli']}.py test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
