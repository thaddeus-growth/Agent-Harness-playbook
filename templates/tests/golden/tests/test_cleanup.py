"""What a run leaves and what it refuses: output inside a work tree is refused
before anything runs; the detached worktrees and the temp folder are gone
after a run, after a failed one and after a SIGTERM in the middle of a case,
with every process of that case killed; a live data folder is only read and
never named; a write switch in the plan stops the run before any case."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _t  # noqa: E402
import fake_harness as fh  # noqa: E402


def setup(d: Path) -> tuple[Path, Path]:
    tmp = d / "tmp"
    tmp.mkdir()
    return fh.build(d), tmp


def left(repo: Path, tmp: Path) -> tuple[list, list]:
    """(worktrees besides the repo itself, anything in the engine's TMPDIR)."""
    return fh.worktrees(repo)[1:], sorted(os.listdir(tmp))


def processes_naming(text: str) -> list[str]:
    ps = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "args="], capture_output=True, text=True)
    return [ln for ln in ps.stdout.splitlines() if text in ln and "ps -A" not in ln]


def test_output_inside_a_work_tree_is_refused_before_anything_runs():
    with _t.tmpdir() as d:
        repo, tmp = setup(d)
        for args in (["snapshot", str(repo / "golden-out")],
                     ["compare", "HEAD", "HEAD", "--keep", str(repo / "tests" / "keep")]):
            p = fh.engine(repo, *args, tmp=tmp)
            assert p.returncode != 0 and "refused" in p.stderr and "inside a checkout" in p.stderr
            assert "cases" not in p.stdout and left(repo, tmp) == ([], [])
        (d / "full").mkdir()
        (d / "full" / "x").write_text("x")
        p = fh.engine(repo, "snapshot", str(d / "full"), tmp=tmp)
        assert p.returncode != 0 and "not empty" in p.stderr
        inner = repo / "tmp-inside"
        inner.mkdir()
        p = fh.engine(repo, "snapshot", str(d / "out"), tmp=inner)          # $TMPDIR in a checkout
        assert p.returncode != 0 and "inside a checkout" in p.stderr
        assert os.listdir(inner) == [] and not (d / "out").exists()


def test_worktrees_and_temp_are_gone_after_a_run_and_after_a_failed_one():
    with _t.tmpdir() as d:
        repo, tmp = setup(d)
        p = fh.engine(repo, "compare", "HEAD", "HEAD", tmp=tmp)
        assert p.returncode == 0 and left(repo, tmp) == ([], []), p.stdout + p.stderr
        p = fh.engine(repo, "compare", "HEAD", "no-such-rev", tmp=tmp)     # BASE was checked out
        assert p.returncode != 0 and "neither a directory nor a git rev" in p.stderr
        assert left(repo, tmp) == ([], [])


def test_a_sigterm_mid_case_removes_the_worktrees_and_kills_the_case():
    with _t.tmpdir() as d:
        repo, tmp = setup(d)
        data = fh.make_data(d / "data")
        (data / "slow").write_text("1")                                     # the summary waits 60 s
        env = fh.env(TMPDIR=str(tmp))
        p = subprocess.Popen([sys.executable, str(repo / "tests" / "golden" / "engine.py"),
                              "compare", "HEAD", "HEAD", "--data", str(data)],
                             cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 60
            while not list(tmp.glob("*/work-base/data/started")):
                assert time.monotonic() < deadline and p.poll() is None, "no case started"
                time.sleep(0.1)
            assert len(fh.worktrees(repo)) == 3                             # BASE and HEAD are out
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
        assert p.returncode == 128 + signal.SIGTERM
        assert left(repo, tmp) == ([], [])
        deadline = time.monotonic() + 10
        while processes_naming(str(tmp)) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not processes_naming(str(tmp)), processes_naming(str(tmp))
        assert (data / "slow").exists() and not (data / "started").exists()  # only its copy ran


def test_a_live_data_folder_is_only_read_and_never_named():
    with _t.tmpdir() as d:
        repo, tmp = setup(d)
        data = fh.make_data(d / "client-data")
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in data.iterdir()}
        keep = d / "keep"
        p = fh.engine(repo, "compare", "HEAD", "HEAD", "--strict", "--data", str(data),
                      "--keep", str(keep), tmp=tmp)
        assert p.returncode == 0 and "0 differ" in p.stdout, p.stdout + p.stderr
        assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in data.iterdir()}
        shown = p.stdout + p.stderr + "".join(
            f.read_text(encoding="utf-8") for f in keep.rglob("*") if f.is_file())
        assert str(data) not in shown and "client-data" not in shown
        assert left(repo, tmp) == ([], [])


def test_a_write_switch_in_the_plan_stops_the_run_before_any_case():
    with _t.tmpdir() as d:
        repo, tmp = setup(d)
        cases = repo / "tests" / "golden" / "cases.py"                      # the working copy runs
        text = cases.read_text(encoding="utf-8")
        cases.write_text(text.replace('"--dry-run", "--json")', '"--apply", "--json")'),
                         encoding="utf-8")
        p = fh.engine(repo, "compare", "HEAD", "HEAD", tmp=tmp)
        assert p.returncode != 0 and "refused case execute_dry_run: --apply" in p.stderr
        assert "cases +" not in p.stdout and left(repo, tmp) == ([], [])


if __name__ == "__main__":
    _t.main(globals())
