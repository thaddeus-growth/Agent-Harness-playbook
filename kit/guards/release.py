"""A release is `git archive` of its tag; .gitattributes' export-ignore
keeps the internal files out of it.

Asked of git itself, the way `git archive` prunes (a path is left out when
it or a parent directory is export-ignore): `git ls-files`, then `git
check-attr export-ignore` on each file and each of its directories. No
archive is built, so a shallow clone is enough. `check_release(root)`
returns every problem of these rules:

  * every `[release].internal` path exists (a stale entry hides nothing)
    and none of it ships (a directory: nothing under it);
  * an ssot file ships iff the ssot index names code that reads it (its
    reader is not —): the registries ship, the planning files don't;
  * every `.tsv` runtime code opens by its literal name ships (runtime =
    `[release].runtime`, default the scripts dir);
  * every `[release].must_ship` path ships, and so do the vendored kit
    (<scripts_dir>/kit/) and console (console/): no internal entry may
    cover them;
  * nothing else is left out;
  * .gitattributes marks export-ignore exactly the internal paths and the
    planning files: `render_gitattributes()` writes that file from the
    config, so it is generated, never hand-kept.

Without git or a repository `shipped()` is None and check_release()
returns no problem; check_release_archive() says it skipped.

Deviations (SPEC §guards / the reference): `[release].runtime` is new
(default: the scripts dir); the opened-registry pattern no longer takes a
bare suffix such as ".agent.tsv" for a file name (the vendored kit holds
one); .gitattributes is compared by its export-ignore entries, so other
attributes may share the file.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from kit.config import HarnessConfig
from kit.guards import harness, read, report, strs
from kit.guards import ssot as _ssot

# A string literal that is only a file name: a registry the code opens
# (not a suffix such as ".agent.tsv").
OPENED = re.compile(r"""["']([\w-][\w.-]*\.tsv)["']""")
CODE_EXT = (".py", ".js", ".mjs", ".cjs")
ATTRS = ".gitattributes"


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], input=stdin,
                          capture_output=True, text=True, check=True).stdout


def parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/")
               for p in prefixes)


def shipped(root: Path | str) -> tuple[list[str], set[str]] | None:
    """(tracked files, the ones `git archive` would ship); None when git or
    the repository is absent."""
    root = Path(root)
    try:
        tracked = [f for f in _git(root, "ls-files", "-z").split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        return None
    asked = sorted(set(tracked) | {d for f in tracked for d in parents(f)})
    out = _git(root, "check-attr", "--stdin", "-z", "export-ignore",
               stdin="\0".join(asked) + "\0").split("\0")
    ignored = {out[i] for i in range(0, len(out) - 2, 3)
               if out[i + 2] == "set"}
    return tracked, {f for f in tracked
                     if f not in ignored and not ignored & set(parents(f))}


def _params(cfg: HarnessConfig) -> tuple[tuple, tuple, tuple]:
    rel = cfg.release
    return (tuple(p.strip("/") for p in strs(rel.get("internal"))),
            tuple(p.strip("/") for p in strs(rel.get("must_ship"))),
            tuple(p.strip("/") for p in strs(rel.get("runtime"),
                                              (cfg.scripts_dir,))))


def ignored_paths(root: Path | str | None = None) -> list[str]:
    """What must be export-ignore: [release].internal, then the planning
    files of the ssot index, in that order."""
    cfg = harness(root)
    internal, _, _ = _params(cfg)
    planning = sorted(p for p in _ssot.planning_files(_ssot.rules(cfg=cfg))
                      if ":" not in p and p not in internal)
    return [*internal, *planning]


def render_gitattributes(root: Path | str | None = None) -> str:
    """The .gitattributes the config implies (write it with
    Path(".gitattributes").write_text(render_gitattributes()))."""
    cfg = harness(root)
    internal, _, _ = _params(cfg)
    planning = [p for p in ignored_paths(root) if p not in internal]
    lines = ["# A release is `git archive` of its tag: every path below "
             "stays out of it.",
             "# Generated from harness.toml [release].internal and the ssot "
             "index rows with no",
             "# reader (kit.guards.release.render_gitattributes); the release "
             "guard fails when they differ.",
             ""]
    lines += [f"/{p} export-ignore" for p in internal]
    if planning:
        lines += ["", "# ssot files no runtime code opens (no reader in the "
                      "index)"]
        lines += [f"/{p} export-ignore" for p in planning]
    return "\n".join(lines) + "\n"


def attributes(text: str) -> set[str]:
    """The paths a .gitattributes text marks export-ignore."""
    out = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("#") \
                and "export-ignore" in parts[1:]:
            out.add(parts[0].strip("/"))
    return out


def check_gitattributes(root: Path | str | None = None) -> list[str]:
    """.gitattributes marks export-ignore exactly what the config implies."""
    cfg = harness(root)
    path = cfg.root / ATTRS
    have = attributes(read(path)) if path.is_file() else set()
    want = set(ignored_paths(root))
    out = [f"{ATTRS}: lacks /{p} export-ignore" for p in sorted(want - have)]
    out += [f"{ATTRS}: marks /{p} export-ignore, which neither "
            f"[release].internal nor the ssot index (no reader) lists"
            for p in sorted(have - want)]
    if out:
        out.append(f"{ATTRS}: regenerate it from the config "
                   f"(kit.guards.release.render_gitattributes)")
    return out


def check_release(root: Path | str | None = None) -> list[str]:
    """Every problem of the release rules (module docstring); [] when git or
    the repository is absent (see shipped())."""
    cfg = harness(root)
    got = shipped(cfg.root)
    if got is None:
        return []
    tracked, ships = got
    internal, must_ship, runtime = _params(cfg)
    r = _ssot.rules(cfg=cfg)
    planning = {p for p in _ssot.planning_files(r) if ":" not in p}
    read_by_code = _ssot.read_files(r)
    internal_files = {f for f in tracked if under(f, internal)
                      or f in planning}
    out: list[str] = []

    stale = [p for p in internal if not any(under(f, (p,)) for f in tracked)]
    if stale:
        out.append(f"[release].internal names paths git does not track "
                   f"(a stale entry hides nothing): {stale}")
    leak = sorted(internal_files & ships)
    if leak:
        out.append(f"internal files ship: {leak}")
    unread = sorted(read_by_code - ships)
    if unread:
        out.append(f"files the ssot index names a reader for do not ship: "
                   f"{unread}")

    opened: set[str] = set()
    for f in tracked:
        if under(f, runtime) and f.endswith(CODE_EXT) \
                and (cfg.root / f).is_file():
            opened |= set(OPENED.findall(read(cfg.root / f)))
    unshipped = sorted(n for n in opened if not any(
        f in ships for f in tracked if os.path.basename(f) == n))
    if unshipped:
        out.append(f".tsv files runtime code opens by name do not ship: "
                   f"{unshipped}")

    missing = sorted(set(must_ship) - ships)
    if missing:
        out.append(f"[release].must_ship paths do not ship: {missing}")
    for d in (f"{cfg.scripts_dir}/kit", "console"):
        cover = [p for p in internal if under(d, (p,))]
        if cover:
            out.append(f"{d}/ is internal ({cover}): the vendored copy must "
                       f"ship")
    dropped = sorted(set(tracked) - internal_files - ships)
    if dropped:
        out.append(f"left out of the release but not internal: {dropped}")
    return out + check_gitattributes(cfg.root)


def check_release_archive(root: Path | str | None = None) -> bool:
    """check_release() as one kit.testing.check line (a note when git is
    absent)."""
    cfg = harness(root)
    if shipped(cfg.root) is None:
        print("  (skip: no git repository to ask)")
        return report("release: skipped, git or the repository is absent",
                      [])
    return report("release: git archive ships exactly what the config says",
                  check_release(root))
