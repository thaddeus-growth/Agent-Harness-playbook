"""A vendored kit or console is a plain copy nobody edits in place.

kit/tools/vendor.py copies the playbook's kit/ to <harness>/<scripts_dir>/
kit/ and console/ to <harness>/console/, and writes a MANIFEST.sha256 in
each (kit/tools/manifest.py). A fix goes upstream first and is then
re-vendored, so:

  * `check_vendored(dir)`: every file under `dir` matches its
    MANIFEST.sha256 (a changed, added or removed file is named), and its
    VERSION is present, non-empty and covered by the manifest;
  * `check_harness(root)`: the harness has a vendored kit, both copies
    (the console when present) pass check_vendored, and the console's
    VERSION equals the kit's: they are vendored together.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

from pathlib import Path

from kit.guards import harness, report
from kit.tools import manifest


def check_vendored(dir: Path | str) -> list[str]:
    """Every drift of the vendored copy at `dir` from its manifest."""
    d = Path(dir)
    if not d.is_dir():
        return [f"{d}: no vendored copy here"]
    out = [f"{d.name}/: {p}" for p in manifest.check(d)]
    v = d / "VERSION"
    if not (v.is_file() and v.read_text(encoding="utf-8").strip()):
        out.append(f"{d.name}/: no VERSION")
    elif (d / manifest.NAME).is_file() and "VERSION" not in manifest.parse(
            (d / manifest.NAME).read_text(encoding="utf-8")):
        out.append(f"{d.name}/: VERSION is not in {manifest.NAME}")
    return out


def check_harness(root: Path | str | None = None) -> list[str]:
    """The harness's vendored kit (and console, when present) match their
    manifests and carry the same VERSION."""
    cfg = harness(root)
    kit_dir = cfg.root / cfg.scripts_dir / "kit"
    console = cfg.root / "console"
    if not kit_dir.is_dir():
        return [f"no vendored kit at {cfg.scripts_dir}/kit/ (run "
                f"kit/tools/vendor.py --harness {cfg.root})"]
    out = check_vendored(kit_dir)
    if console.is_dir():
        out += check_vendored(console)
        kv, cv = kit_dir / "VERSION", console / "VERSION"
        if kv.is_file() and cv.is_file() and (
                kv.read_text(encoding="utf-8").strip()
                != cv.read_text(encoding="utf-8").strip()):
            out.append(f"console/VERSION {cv.read_text().strip()!r} is not "
                       f"the kit's {kv.read_text().strip()!r}: vendor them "
                       f"together")
    return out


def check_drift(root: Path | str | None = None) -> bool:
    """check_harness() as one kit.testing.check line."""
    return report("drift: the vendored kit and console match their "
                  "manifests", check_harness(root))
