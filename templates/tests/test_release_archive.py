#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""A release is `git archive` of its tag; `.gitattributes` keeps the internal
files out of it with `export-ignore`.

Asked of git itself, the way `git archive` prunes (a path is left out when it
or a parent folder is export-ignore): `git ls-files`, then `git check-attr
export-ignore` on every file and folder. No archive is built, so a shallow
clone is enough. Edit the lists below to your repository.

  * every INTERNAL path exists, and so does every export-ignore entry in
    .gitattributes: a stale entry hides nothing and fails;
  * no INTERNAL file ships (a folder: nothing under it);
  * the registry index decides for its files: one whose reader is NO_READER
    stays out, one with a reader ships;
  * every `.tsv` that runtime code opens by its literal name ships;
  * MUST_SHIP ships, and nothing but the internal files is left out.

Passes with one note only when git is absent (a host holding a release has no
tests to run anyway); fails when ROOT is not the top of a repository.
"""

import csv
import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase

from _check import check, finish

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# for the people changing the code and its CI; the index's reader-less files join these
INTERNAL = (".gitattributes", "CLAUDE.md", ".claude", "tests", "core/tests", "evals",
            ".github", ".gitlab-ci.yml", "ssot/README.md")
MUST_SHIP = ("README.md", "src/harness.py", "core/gate.py")
RUNTIME = ("src", "core")                # code whose literal `x.tsv` names must ship
INDEX, READER, NO_READER = "ssot/index.tsv", "reader", "—"
OPENED = re.compile(r"""["']([\w.-]+\.tsv)["']""")


def git(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", ROOT, *args], input=stdin,
                          capture_output=True, text=True, check=True).stdout


def parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def ignore_entries(text: str) -> list[str]:
    """The patterns .gitattributes marks export-ignore."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("#") and "export-ignore" in parts[1:]:
            out.append(parts[0])
    return out


def matches(entry: str, paths) -> bool:
    """Whether a .gitattributes pattern names one of `paths` (files and folders)."""
    pat = entry.strip("/")
    anchored = entry.startswith("/") or "/" in pat
    return any(fnmatchcase(p if anchored else p.rpartition("/")[2], pat) for p in paths)


def main() -> int:
    try:
        top = git("rev-parse", "--show-toplevel").strip()
    except FileNotFoundError:
        print("  (note: no git on this machine; nothing to ask)")
        check("skipped: git is absent", True)
        return finish()
    except subprocess.CalledProcessError as e:
        top = e.stderr.strip()
    check("ROOT is the top of a git repository", os.path.realpath(top) == ROOT, top)
    if os.path.realpath(top) != ROOT:
        return finish()

    tracked = [f for f in git("ls-files", "-z").split("\0") if f]
    folders = {d for f in tracked for d in parents(f)}
    out = git("check-attr", "--stdin", "-z", "export-ignore",
              stdin="\0".join(sorted(set(tracked) | folders)) + "\0").split("\0")
    ignored = {out[i] for i in range(0, len(out) - 2, 3) if out[i + 2] == "set"}
    shipped = {f for f in tracked if f not in ignored and not ignored & set(parents(f))}

    with open(os.path.join(ROOT, INDEX), encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    planning = {r["file"] for r in rows if r[READER].strip() == NO_READER}
    read = {r["file"].partition(":")[0] for r in rows if r[READER].strip() != NO_READER}
    internal = {f for f in tracked if under(f, INTERNAL) or f in planning}

    stale = [p for p in INTERNAL if not any(under(f, (p,)) for f in tracked)]
    check("every INTERNAL path exists (a stale entry hides nothing)", not stale, stale)
    with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as fh:
        entries = ignore_entries(fh.read())
    stale = [e for e in entries if not matches(e, set(tracked) | folders)]
    check("every export-ignore entry in .gitattributes names a tracked path",
          entries and not stale, stale or "no export-ignore entry")
    check("no internal file ships", not internal & shipped, sorted(internal & shipped))
    check(f"{INDEX}: the files it names no reader for stay out",
          planning and not planning & shipped, sorted(planning & shipped))
    check(f"{INDEX}: every file it names a reader for ships",
          read and read <= shipped, sorted(read - shipped))

    check("the opened-name pattern finds a registry name in code",
          OPENED.findall('open(ROOT / "ssot" / "constants.tsv")') == ["constants.tsv"])
    opened = set()
    for f in tracked:
        if under(f, RUNTIME) and f.endswith(".py") and f not in internal:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                opened |= set(OPENED.findall(fh.read()))
    unshipped = sorted(n for n in opened
                       if not any(os.path.basename(f) == n for f in shipped))
    check(f"every .tsv runtime code opens by name ships ({', '.join(sorted(opened))})",
          not unshipped, unshipped)

    check(f"{', '.join(MUST_SHIP)} ship", set(MUST_SHIP) <= shipped,
          sorted(set(MUST_SHIP) - shipped))
    dropped = sorted(set(tracked) - internal - shipped)
    check("nothing but the internal files is left out", not dropped, dropped)
    return finish()


if __name__ == "__main__":
    sys.exit(main())
