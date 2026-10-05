#!/usr/bin/env python3
"""Vendor the playbook's kit/ and console/ into a harness, as plain copies.

    python3 kit/tools/vendor.py --harness PATH [--kit] [--console] [--dry-run]

(neither --kit nor --console = both)

  * kit/     -> <harness>/<scripts_dir>/kit/   (scripts_dir from the
               harness's harness.toml, default "scripts"), so `scripts/` on
               sys.path makes `import kit` work and `git archive` ships it;
  * console/ -> <harness>/console/, plus a VERSION file holding the kit
               version it was vendored with (the console has none of its
               own).

The kit copy holds only the packs the harness takes (kit/tools/packs.py):
`[kit] packs = [...]` in its harness.toml, plus base and testkit; with no
`[kit] packs`, every pack. A module the harness's own code imports is never
left out: the run is refused, naming the importing file and the pack to add.

Each copy leaves out tests/, __pycache__, *.pyc and .DS_Store; a file
the playbook no longer has is removed from the copy; then the copy's
MANIFEST.sha256 is (re)written, so the harness's drift guard fails on any
later local edit. Idempotent: a second run changes nothing and says so.
Prints every added / updated / removed file.

Stdlib only; runs without a bound harness.

Test: kit/tests/test_vendor.py.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

try:
    from kit.tools import manifest, packs
except ImportError:                  # run as a script: kit/tools is on sys.path
    import manifest                  # type: ignore[no-redef]
    import packs                     # type: ignore[no-redef]

PLAYBOOK = Path(__file__).resolve().parents[2]


def scripts_dir(harness: Path) -> str:
    try:
        raw = tomllib.loads((harness / "harness.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "scripts"
    return str(raw.get("harness", {}).get("scripts_dir") or "scripts")


def kit_files(kit_dir: Path, taken: set[str]) -> set[str]:
    """The kit files that belong to the packs `taken` (the package's own files always)."""
    table = packs.table(kit_dir)
    known = set(table)
    return {rel for rel in manifest.files(kit_dir)
            if (m := packs.module_of(rel, known)) is None or table.get(m) in taken}


def left_out(harness: Path, kit_dir: Path, dest: Path, taken: set[str]) -> list[str]:
    """`file: imports kit.X (pack P)` for each import of a module the copy would not hold."""
    table = packs.table(kit_dir)
    return [f"{f}: imports kit.{m} (pack {table[m]})"
            for m, fs in sorted(packs.harness_imports(harness, dest, set(table)).items())
            if table[m] not in taken for f in fs]


def plan(src: Path, dest: Path, extra: dict[str, bytes],
         only: set[str] | None = None) -> dict[str, list]:
    """{added, updated, removed, unchanged} relpaths for syncing `src` (plus
    `extra` = {relpath: bytes} written only into the copy) into `dest`;
    `only`, when given, is the subset of `src` files to copy."""
    want = {rel: (src / rel).read_bytes() for rel in manifest.files(src)
            if only is None or rel in only}
    want.update(extra)
    have = set(manifest.files(dest)) if dest.is_dir() else set()
    out: dict[str, list] = {"added": [], "updated": [], "removed": [],
                            "unchanged": []}
    for rel, data in sorted(want.items()):
        p = dest / rel
        if rel not in have:
            out["added"].append(rel)
        elif p.read_bytes() != data:
            out["updated"].append(rel)
        else:
            out["unchanged"].append(rel)
    out["removed"] = sorted(have - set(want))
    return out


def sync(src: Path, dest: Path, extra: dict[str, bytes], *,
         dry_run: bool = False, only: set[str] | None = None) -> dict[str, list]:
    p = plan(src, dest, extra, only)
    if dry_run:
        return p
    for rel in p["added"] + p["updated"]:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel in extra:
            target.write_bytes(extra[rel])
        else:
            shutil.copyfile(src / rel, target)
            shutil.copymode(src / rel, target)
    for rel in p["removed"]:
        (dest / rel).unlink()
        d = (dest / rel).parent
        while d != dest and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    before = (dest / manifest.NAME).read_bytes() \
        if (dest / manifest.NAME).is_file() else None
    manifest.write(dest)
    if (dest / manifest.NAME).read_bytes() != before:
        p.setdefault("manifest", []).append(manifest.NAME)
    return p


def report(label: str, dest: Path, harness: Path, p: dict[str, list],
           version: str) -> None:
    rel = dest.relative_to(harness)
    for kind in ("added", "updated", "removed"):
        for f in p[kind]:
            print(f"  {kind:<8} {rel}/{f}")
    n = {k: len(p[k]) for k in ("added", "updated", "removed")}
    if any(n.values()):
        print(f"{label}: {n['added']} added, {n['updated']} updated, "
              f"{n['removed']} removed -> {rel}/ ({version})")
    else:
        print(f"{label}: up to date -> {rel}/ ({version})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", required=True, help="the harness checkout")
    ap.add_argument("--kit", action="store_true", help="vendor kit/")
    ap.add_argument("--console", action="store_true", help="vendor console/")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change, write nothing")
    args = ap.parse_args(argv)
    harness = Path(args.harness).expanduser().resolve()
    if not harness.is_dir():
        print(f"error: {harness} is not a directory", file=sys.stderr)
        return 2
    if harness == PLAYBOOK or PLAYBOOK in harness.parents:
        print(f"error: {harness} is the playbook itself", file=sys.stderr)
        return 2
    both = not (args.kit or args.console)
    version = (PLAYBOOK / "kit" / "VERSION").read_text("utf-8").strip()
    jobs = []
    if args.kit or both:
        kit_dir, dest = PLAYBOOK / "kit", harness / scripts_dir(harness) / "kit"
        try:
            taken = packs.chosen(harness, kit_dir)
        except packs.PackError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        missing = left_out(harness, kit_dir, dest, taken)
        if missing:
            print("error: the harness imports kit modules outside the packs it takes; "
                  "add their packs to [kit] packs in harness.toml:", file=sys.stderr)
            for line in missing:
                print(f"  {line}", file=sys.stderr)
            return 2
        print(f"kit packs: {', '.join(sorted(taken))}")
        jobs.append(("kit", kit_dir, dest, {}, kit_files(kit_dir, taken)))
    if args.console or both:
        src = PLAYBOOK / "console"
        extra = ({} if (src / "VERSION").is_file()
                 else {"VERSION": (version + "\n").encode()})
        jobs.append(("console", src, harness / "console", extra, None))
    for label, src, dest, extra, only in jobs:
        if dest.is_symlink():
            print(f"error: {dest} is a symlink; a vendored copy must be "
                  f"plain files", file=sys.stderr)
            return 2
        p = sync(src, dest, extra, dry_run=args.dry_run, only=only)
        report(label + (" (dry run)" if args.dry_run else ""), dest,
               harness, p, version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
