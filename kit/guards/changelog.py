"""CHANGELOG.md stays whole and names the version SKILL.md carries.

A host (and a person re-vendoring) reads the changelog to learn what a version
changed; a version bumped without its entry, or a heading out of order, leaves
that history wrong. `check_changelog(root)` returns every problem of these
rules:

  1. `CHANGELOG.md` exists and its first section is `## [Unreleased]`;
  2. every other heading is `## [X.Y.Z] - YYYY-MM-DD`, versions strictly
     descending, dates never increasing, each release with at least one
     `### ` section;
  3. the `version:` in SKILL.md's frontmatter (what the host reads and the
     release carries) is the newest release heading. A harness that has not
     released yet (no release heading) is at 0.1.0;
  4. every `vX.Y.Z` tag the checkout holds has a heading (no tags, or no
     git: nothing to compare).

`self_test()` plants each fault in a throwaway tree and returns the rules that
failed to catch theirs.

The rule that a merge request adds its line is the `changelog` job of
templates/gitlab-ci.yml: it needs the diff, which a test run does not have.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

SEMVER = re.compile(r"\d+\.\d+\.\d+")
HEADING = re.compile(r"^## \[([^\]]+)\](?:\s*-\s*(.*?))?\s*$", re.M)
DATE = re.compile(r"\d{4}-\d\d-\d\d")
SKILL_VERSION = re.compile(r"^version:\s*(\S+)\s*$", re.M)
FIRST = "0.1.0"


def key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def tags(root: Path) -> list[str]:
    """The vX.Y.Z tags of the checkout, as X.Y.Z; [] when git or tags are absent."""
    try:
        out = subprocess.run(["git", "tag", "--list", "v[0-9]*"], cwd=root,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [t[1:] for t in out.stdout.split() if SEMVER.fullmatch(t[1:])]


def check_changelog(root: Path | str) -> list[str]:
    root = Path(root)
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return ["CHANGELOG.md does not exist"]
    log = path.read_text(encoding="utf-8")
    heads = HEADING.findall(log)
    names = [n for n, _ in heads]
    problems = []
    if names[:1] != ["Unreleased"]:
        problems.append("the first section of CHANGELOG.md is not `## [Unreleased]`")
    rel = [(n, d) for n, d in heads if n != "Unreleased"]
    bad = [n for n, d in rel if not (SEMVER.fullmatch(n) and DATE.fullmatch(d))]
    if bad:
        problems.append(f"release headings must read `## [X.Y.Z] - YYYY-MM-DD`: {bad}")
        return problems
    versions = [key(n) for n, _ in rel]
    if versions != sorted(set(versions), reverse=True):
        problems.append(f"release versions are not strictly descending: {[n for n, _ in rel]}")
    dates = [d for _, d in rel]
    if dates != sorted(dates, reverse=True):
        problems.append(f"release dates increase going down: {dates}")
    for section in re.split(r"(?m)^## \[", log)[1:]:
        name = section.split("]", 1)[0]
        if name != "Unreleased" and "### " not in section:
            problems.append(f"[{name}] has no `### ` section (say what changed)")
    skill = root / "SKILL.md"
    if skill.is_file():
        m = SKILL_VERSION.search(skill.read_text(encoding="utf-8"))
        if m is not None:       # a SKILL.md with no version is not held to one
            want = rel[0][0] if rel else FIRST
            if m.group(1) != want:
                problems.append(
                    f"SKILL.md is at {m.group(1)} but the newest CHANGELOG entry is "
                    f"{want if rel else 'none (a harness not yet released is at ' + FIRST + ')'}: "
                    f"a version bump needs its entry")
    missing = [t for t in tags(root) if t not in set(names)]
    if missing:
        problems.append(f"tags without a CHANGELOG heading: {missing}")
    return problems


GOOD = "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-02-01\n\n### Added\n- a\n\n## [1.0.0] - 2026-01-01\n\n### Added\n- b\n"
SKILL = "---\nname: x\nversion: {}\n---\n"
PLANTED = {
    "no file": (None, "1.1.0", "does not exist"),
    "Unreleased not first": (GOOD.replace("## [Unreleased]\n\n", ""), "1.1.0", "not `## [Unreleased]`"),
    "versions out of order": (GOOD.replace("1.1.0", "0.9.0"), "0.9.0", "not strictly descending"),
    "dates increase": (GOOD.replace("2026-01-01", "2026-03-01"), "1.1.0", "dates increase"),
    "an empty release": (GOOD.replace("### Added\n- b\n", ""), "1.1.0", "no `### ` section"),
    "a malformed heading": (GOOD.replace("## [1.0.0] - 2026-01-01", "## [1.0] - Jan"), "1.1.0", "must read"),
    "SKILL.md ahead of the log": (GOOD, "1.2.0", "a version bump needs its entry"),
}


def self_test() -> list[str]:
    """Each planted fault must be caught, and the good tree must pass."""
    out = []
    for label, (log, version, needle) in {"clean": (GOOD, "1.1.0", None), **PLANTED}.items():
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            if log is not None:
                (root / "CHANGELOG.md").write_text(log, encoding="utf-8")
            (root / "SKILL.md").write_text(SKILL.format(version), encoding="utf-8")
            got = check_changelog(root)
        if needle is None and got:
            out.append(f"a clean changelog was refused: {got}")
        elif needle is not None and not any(needle in p for p in got):
            out.append(f"{label}: not caught (got {got})")
    return out
