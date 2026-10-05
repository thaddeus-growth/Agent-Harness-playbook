"""tools/fleet.py: where a harness's vendored copies stand against the playbook.

A throwaway git repository stands in for the playbook (CI checks out one
commit, so the real history is not there): kit 0.1.0, then 0.2.0. A harness
vendored at 0.1.0 is behind; a file in no version of the playbook is local;
the copy's own manifest names an edit made after vendoring.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "tools" / "fleet.py"


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def vendor(src: Path, dest: Path) -> None:
    """A plain copy with its MANIFEST.sha256, as kit/tools/vendor.py writes it."""
    lines = []
    for p in sorted(src.rglob("*")):
        if p.is_file() and "tests" not in p.relative_to(src).parts:
            rel = p.relative_to(src).as_posix()
            write(dest, {rel: p.read_text()})
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}")
    (dest / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


def fleet(playbook: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(FLEET), "--playbook", str(playbook), *args],
                          capture_output=True, text=True)


def setup(tmp: Path) -> tuple[Path, Path]:
    pb, h = tmp / "playbook", tmp / "acme"
    write(pb, {"kit/VERSION": "0.1.0\n", "kit/a.py": "A = 1\n", "kit/gone.py": "G = 1\n",
               "kit/tests/test_a.py": "t\n", "console/core.py": "C = 1\n"})
    sh(pb, "git", "init", "-q")
    sh(pb, "git", "add", "-A")
    sh(pb, "git", "commit", "-qm", "0.1.0")
    write(h, {"harness.toml": '[harness]\nname = "acme"\nscripts_dir = "scripts"\n'})
    vendor(pb / "kit", h / "scripts" / "kit")
    (pb / "kit" / "gone.py").unlink()
    write(pb, {"kit/VERSION": "0.2.0\n", "kit/a.py": "A = 2\n", "kit/b.py": "B = 1\n"})
    sh(pb, "git", "add", "-A")
    sh(pb, "git", "commit", "-qm", "0.2.0")
    return pb, h


def test_a_copy_vendored_earlier_is_behind_and_loses_nothing():
    with _t.tmpdir() as tmp:
        pb, h = setup(Path(tmp))
        r = fleet(pb, "status", str(h), "--json")
        assert r.returncode == 0, r.stderr
        [doc] = json.loads(r.stdout)["harnesses"]
        [kit] = doc["copies"]
        assert doc["name"] == "acme" and doc["history"] == "ok", doc
        assert (kit["version"], kit["playbook_version"]) == ("0.1.0", "0.2.0"), kit
        states = {f["file"]: f["state"] for f in kit["files"]}
        assert states == {"VERSION": "older", "a.py": "older", "b.py": "missing", "gone.py": "extra"}, states
        assert "as the playbook had it from kit 0.1.0" in next(f["note"] for f in kit["files"] if f["file"] == "a.py")
        assert kit["verdict"].startswith("behind") and kit["edited"] == [], kit
        assert not (h / "console").exists() and [c["part"] for c in doc["copies"]] == ["kit"]


def test_a_change_no_playbook_version_had_is_local_and_named():
    with _t.tmpdir() as tmp:
        pb, h = setup(Path(tmp))
        (h / "scripts" / "kit" / "a.py").write_text("A = 'forked'\n")
        write(h, {"scripts/kit/mine.py": "M = 1\n"})
        r = fleet(pb, "status", str(h))
        assert r.returncode == 0, r.stderr
        assert "local changes in 2 file(s)" in r.stdout, r.stdout
        assert "local    a.py" in r.stdout and "extra    mine.py" in r.stdout, r.stdout
        assert "edited after vendoring: a.py, mine.py" in r.stdout, r.stdout


def test_a_copy_equal_to_the_playbook_is_in_sync():
    with _t.tmpdir() as tmp:
        pb, h = setup(Path(tmp))
        kit = h / "scripts" / "kit"
        subprocess.run(["rm", "-rf", str(kit)], check=True)
        vendor(pb / "kit", kit)
        r = fleet(pb, "status", str(h), "--json")
        [copy] = json.loads(r.stdout)["harnesses"][0]["copies"]
        assert copy["verdict"] == "in sync" and copy["files"] == [], copy


def test_diff_shows_the_harness_copy_against_the_playbook():
    with _t.tmpdir() as tmp:
        pb, h = setup(Path(tmp))
        r = fleet(pb, "diff", str(h), "kit/a.py")
        assert r.returncode == 0, r.stderr
        assert "--- harness/kit/a.py" in r.stdout and "+++ playbook/kit/a.py" in r.stdout, r.stdout
        assert "-A = 1" in r.stdout and "+A = 2" in r.stdout and "b.py" not in r.stdout, r.stdout
        assert "kit/b.py" in fleet(pb, "diff", str(h)).stdout


def test_without_history_it_says_so():
    with _t.tmpdir() as tmp:
        pb, h = setup(Path(tmp))
        subprocess.run(["rm", "-rf", str(pb / ".git")], check=True)
        doc = json.loads(fleet(pb, "status", str(h), "--json").stdout)["harnesses"][0]
        assert doc["history"].startswith("none"), doc
        assert fleet(pb, "status", str(Path(tmp) / "nowhere")).returncode == 2


if __name__ == "__main__":
    _t.main(globals())
