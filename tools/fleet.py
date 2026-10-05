#!/usr/bin/env python3
"""Where each harness's vendored copies stand against this playbook.

    python3 tools/fleet.py status HARNESS... [--json]
    python3 tools/fleet.py diff HARNESS [FILE...]

For each harness it reads the kit (<scripts_dir>/kit/), the console
(console/) and the Zylos adapter (zylos/), whichever it holds, and compares
each file with the playbook's current copy (kit/, console/, hosts/zylos/):

  * same: equal to the playbook's current file;
  * older: equal to an earlier version of the playbook's file, found in the
    playbook's git history (the kit or adapter VERSION at that commit is
    named: the version in which that content first appeared): re-vendoring brings it up to date and loses nothing;
  * local: in no version the playbook ever had. This is a local change:
    upstream it (a fix here, with its test) or drop it before re-vendoring;
  * missing: the playbook has it and the copy does not (the copy is older);
  * extra: the copy has it and the playbook does not now (removed upstream,
    when history has it; a local file otherwise).

`edited` lists the files that differ from the copy's own MANIFEST.sha256
(an edit made after vendoring). The verdict per copy is `in sync`,
`behind` (re-vendor) or `local changes` (decide each file first). Phase 1 of
RESTRUCTURE.md: bring every harness to one kit version.

`diff` prints a unified diff, harness copy against the playbook's current
file, for each file that is not the same (or only the FILEs named, as
`kit/facts.py`, `console/core.py` or `zylos/lib.js`).

Read-only: it writes nothing anywhere. Stdlib only; needs `git` for the
history lookups (without history, older and local cannot be told apart and
the report says so). Test: tests/test_fleet.py.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

PLAYBOOK = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "__pycache__"}
SKIP_FILES = {"MANIFEST.sha256", ".DS_Store"}
# The adapter files a harness does not copy (hosts/zylos/README.md), and the
# one it writes itself.
ZYLOS_NOT_COPIED = {"ecosystem.config.cjs.template", "manifest.example.json"}
ZYLOS_OWN = {"manifest.json"}


def files(root: Path, skip: set[str] = frozenset()) -> list[str]:
    """Relative paths under `root`, as vendor.py copies them."""
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or SKIP_DIRS & set(rel.parts[:-1]) or p.name in SKIP_FILES \
                or p.suffix == ".pyc" or rel.as_posix() in skip:
            continue
        out.append(rel.as_posix())
    return out


def blob_sha(data: bytes) -> str:
    """The git blob id of `data` (what `git hash-object` prints)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git(playbook: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(playbook), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


class History:
    """Every blob a playbook path ever had, and the version at that commit."""

    def __init__(self, playbook: Path):
        self.playbook = playbook
        self.ok = git(playbook, "rev-parse", "HEAD") is not None
        self.shallow = (git(playbook, "rev-parse", "--is-shallow-repository") or "").strip() == "true"
        self._blobs: dict[str, dict[str, str]] = {}
        self._versions: dict[tuple[str, str], str] = {}

    def blobs(self, path: str) -> dict[str, str]:
        """{blob id: newest commit that had it} for `path`."""
        if path not in self._blobs:
            out: dict[str, str] = {}
            text = git(self.playbook, "log", "--format=C %H", "--raw", "--no-abbrev",
                       "--no-renames", "--", path) if self.ok else None
            commit = None
            for line in (text or "").splitlines():
                if line.startswith("C "):
                    commit = line[2:]
                elif line.startswith(":") and commit:
                    new = line.split()[3]
                    if set(new) != {"0"}:
                        out.setdefault(new, commit)
            self._blobs[path] = out
        return self._blobs[path]

    def version(self, commit: str, version_file: str) -> str:
        key = (commit, version_file)
        if key not in self._versions:
            v = git(self.playbook, "show", f"{commit}:{version_file}")
            self._versions[key] = v.strip() if v else "?"
        return self._versions[key]


def read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def parts(harness: Path, playbook: Path) -> list[dict]:
    """The copies a harness can hold: label, playbook dir, copy dir, VERSION file in the playbook."""
    h = read_toml(harness / "harness.toml").get("harness", {})
    scripts = str(h.get("scripts_dir") or "scripts")
    common = {"root": playbook}
    return [{**common, **p} for p in (
        {"label": "kit", "src": playbook / "kit", "src_rel": "kit",
         "dest": harness / scripts / "kit", "version_file": "kit/VERSION", "skip": set()},
        {"label": "console", "src": playbook / "console", "src_rel": "console",
         "dest": harness / "console", "version_file": "kit/VERSION", "skip": set()},
        {"label": "zylos", "src": playbook / "hosts" / "zylos", "src_rel": "hosts/zylos",
         "dest": harness / "zylos", "version_file": "hosts/zylos/VERSION",
         "skip": ZYLOS_NOT_COPIED | ZYLOS_OWN},
    )]


def manifest_edits(dest: Path) -> list[str] | None:
    """Files that differ from the copy's own MANIFEST.sha256; None when it has none."""
    m = dest / "MANIFEST.sha256"
    if not m.is_file():
        return None
    want = {}
    for line in m.read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, _, rel = line.partition("  ")
            want[rel.strip()] = digest.strip()
    have = {rel: hashlib.sha256((dest / rel).read_bytes()).hexdigest()
            for rel in files(dest)}
    return sorted(r for r in set(want) | set(have) if want.get(r) != have.get(r))


def compare(part: dict, history: History) -> dict | None:
    src, dest = part["src"], part["dest"]
    if not dest.is_dir():
        return None
    want = set(files(src, part["skip"]))
    have = set(files(dest, part["skip"]))
    rows = []
    for rel in sorted(want | have):
        path = f"{part['src_rel']}/{rel}"
        if rel not in have:
            rows.append({"file": rel, "state": "missing"})
            continue
        data = (dest / rel).read_bytes()
        if rel in want and (src / rel).read_bytes() == data:
            continue
        commit = history.blobs(path).get(blob_sha(data))
        row = {"file": rel}
        if commit:
            row.update(state="older" if rel in want else "extra",
                       note=f"as the playbook had it from {part['label']} {history.version(commit, part['version_file'])}"
                            f" ({commit[:10]})" + ("" if rel in want else ", removed since"))
        else:
            row["state"] = "local" if rel in want else "extra"
            if rel not in want:
                row.update(own=True, note="not in the playbook's history: a file of the harness's own")
        rows.append(row)
    v, cur = dest / "VERSION", part["root"] / part["version_file"]
    version = v.read_text("utf-8").strip() if v.is_file() else None
    current = cur.read_text("utf-8").strip() if cur.is_file() else None
    local = [r["file"] for r in rows if r.pop("own", False) or r["state"] == "local"]
    if not rows and version == current:
        verdict = "in sync"
    elif local:
        verdict = f"local changes in {len(local)} file(s): upstream or drop each, then re-vendor"
    else:
        verdict = "behind: re-vendor, nothing local is lost"
    return {"part": part["label"], "dir": str(dest), "version": version, "playbook_version": current,
            "edited": manifest_edits(dest), "files": rows, "verdict": verdict}


def status(harness: Path, playbook: Path, history: History) -> dict:
    h = read_toml(harness / "harness.toml").get("harness", {})
    copies = [c for p in parts(harness, playbook) if (c := compare(p, history))]
    return {"harness": str(harness), "name": h.get("name"), "copies": copies,
            "history": "ok" if history.ok and not history.shallow
            else "shallow: older and local cannot be told apart for old versions" if history.ok
            else "none: older and local cannot be told apart"}


def render(doc: dict) -> str:
    out = [f"{doc['name'] or '?'}  {doc['harness']}"]
    if doc["history"] != "ok":
        out.append(f"  history: {doc['history']}")
    if not doc["copies"]:
        out.append("  no vendored kit, console or zylos/ found")
    for c in doc["copies"]:
        out.append(f"  {c['part']:<8} {c['version'] or 'no VERSION'} (playbook {c['playbook_version']}): {c['verdict']}")
        if c["edited"]:
            out.append(f"           edited after vendoring: {', '.join(c['edited'])}")
        for r in c["files"]:
            out.append(f"           {r['state']:<8} {r['file']}" + (f"  [{r['note']}]" if r.get("note") else ""))
    return "\n".join(out)


def diff(harness: Path, playbook: Path, only: list[str]) -> str:
    out = []
    for p in parts(harness, playbook):
        if not p["dest"].is_dir():
            continue
        for rel in sorted(set(files(p["src"], p["skip"])) | set(files(p["dest"], p["skip"]))):
            name = f"{p['label']}/{rel}"
            if only and name not in only:
                continue
            a = (p["dest"] / rel).read_bytes() if (p["dest"] / rel).is_file() else b""
            b = (p["src"] / rel).read_bytes() if (p["src"] / rel).is_file() else b""
            if a == b:
                continue
            out.extend(difflib.unified_diff(
                a.decode("utf-8", "replace").splitlines(keepends=True),
                b.decode("utf-8", "replace").splitlines(keepends=True),
                fromfile=f"harness/{name}", tofile=f"playbook/{p['src_rel']}/{rel}"))
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--playbook", default=str(PLAYBOOK), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="each copy's version, verdict and differing files")
    s.add_argument("harness", nargs="+")
    s.add_argument("--json", action="store_true")
    d = sub.add_parser("diff", help="unified diffs, harness copy against the playbook")
    d.add_argument("harness")
    d.add_argument("file", nargs="*", help="e.g. kit/facts.py; default every differing file")
    args = ap.parse_args(argv)
    playbook = Path(args.playbook).resolve()
    harnesses = [Path(h).expanduser().resolve() for h in
                 (args.harness if args.cmd == "status" else [args.harness])]
    for h in harnesses:
        if not h.is_dir():
            print(f"error: {h} is not a directory", file=sys.stderr)
            return 2
    if args.cmd == "diff":
        sys.stdout.write(diff(harnesses[0], playbook, args.file))
        return 0
    history = History(playbook)
    docs = [status(h, playbook, history) for h in harnesses]
    if args.json:
        print(json.dumps({"playbook": str(playbook), "harnesses": docs}, indent=2))
    else:
        print("\n\n".join(render(doc) for doc in docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
