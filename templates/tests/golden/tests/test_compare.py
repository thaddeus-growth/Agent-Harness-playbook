"""The self-checks, run on a fake harness with a few commits: two runs of one
tree give 0 differences with the hash seed unpinned, and a set-order bug
does not; a commit that changes no document gives 0 even strict; a changed
registry default is caught; a page that raises fails the run; a fixture edit
alone gives 0, because both sides read BASE's fixtures; every clock, the child verbs' too, reads the pin; a case
sees no credential and no write switch. The engine runs as a harness runs
it: its own copy, committed in the repo it compares."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _t  # noqa: E402
import fake_harness as fh  # noqa: E402

PIN = "2031-01-15T12:00:00+00:00"


def compare(repo: Path, tmp: Path, *args: str, **env: str):
    p = fh.engine(repo, "compare", *args, tmp=tmp, **env)
    assert "Traceback" not in p.stderr, p.stderr
    return p.returncode, p.stdout


def doc(snap: Path, name: str) -> dict:
    return json.loads((snap / f"{name}.json").read_text(encoding="utf-8"))["doc"]


def test_head_vs_head_twice_gives_zero_with_the_hash_seed_unpinned():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        for run in (1, 2):                  # PYTHONHASHSEED=0 is in the engine's env
            keep = d / f"keep{run}"
            rc, out = compare(repo, tmp, "HEAD", "HEAD", "--strict", "--keep", str(keep))
            assert rc == 0 and "13 common cases, 0 added, 0 removed, 0 differ" in out, out
        names = {p.name for p in (keep / "head").iterdir()}
        for must in ("report_summary.json", "report_summary__by_day.json",        # variants
                     "report_entity__FAIL_id_no_such_entity.json",                # a failure doc
                     "report_entity__id_entity.json",                             # waited on a slot
                     "execute_dry_run.json", "facts_list.json",
                     "page_home_en.html", "page_home_zh.html", "page_entity_zh.html"):
            assert must in names, must
        failure = json.loads((keep / "head" / "report_entity__FAIL_id_no_such_entity.json").read_text())
        assert failure["rc"] == 2 and failure["doc"]["code"] == "unknown_entity"
        assert doc(keep / "head", "report_summary")["meta"]["data_dir"] == "<DATA_DIR>"
        assert doc(keep / "head", "report_summary")["meta"]["generated_at"] == "<NOW>"


def test_a_set_order_bug_differs_between_two_runs_of_the_same_tree():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        bug = fh.commit(repo, fh.SET_ORDER, "order from a set")
        rc, out = compare(repo, tmp, bug, bug)
        assert rc == 1 and "CHANGED" in out and "report_summary.json" in out, out


def test_a_commit_that_changes_no_document_gives_zero_even_strict():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        base = fh.git(repo, "rev-parse", "HEAD")
        rc, out = compare(repo, tmp, base, fh.commit(repo, fh.COMMENT_ONLY, "comment"), "--strict")
        assert rc == 0 and "0 differ" in out, out


def test_a_throwaway_change_to_one_registry_default_is_caught():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        base = fh.git(repo, "rev-parse", "HEAD")
        rc, out = compare(repo, tmp, base, fh.commit(repo, fh.NEW_DEFAULT, "default"))
        assert rc == 1, out
        assert "report_summary.json: rc 0/0" in out and "CHANGED  .rows[1].judged  [true, false]" in out
        assert "LIST-LEN .would_write" in out and "page_" not in out    # pages show no verdict here


def test_a_page_that_raises_fails_the_run():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        base = fh.git(repo, "rev-parse", "HEAD")
        rc, out = compare(repo, tmp, base, fh.commit(repo, fh.PAGE_RAISES, "zh page raises"))
        assert rc == 1 and "FAILED: ['_pages']" in out and "page_home_zh.html: HTML differs" in out, out


def test_a_fixture_edit_alone_gives_zero_because_both_sides_read_base_fixtures():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        edit = {"fixtures/rows.json": ('"clicks": 9', '"clicks": 90')}
        base = fh.git(repo, "rev-parse", "HEAD")
        rc, out = compare(repo, tmp, base, fh.commit(repo, edit, "fixture"), "--strict")
        assert rc == 0 and "0 differ" in out, out


def test_every_clock_reads_the_pin_in_a_case_and_in_the_verbs_it_starts():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        snap = d / "snap"
        p = fh.engine(repo, "snapshot", str(snap), "--today", "2031-01-15", tmp=tmp)
        assert p.returncode == 0 and f"(pin {PIN})" in p.stdout, p.stdout + p.stderr
        summary, rollup = doc(snap, "report_summary"), doc(snap, "report_rollup")
        assert summary["meta"]["today"] == "2031-01-15" and summary["meta"]["generated_at"] == PIN
        assert rollup["child_today"] == "2031-01-15"                    # a child verb, via the runner
        assert doc(snap, "execute_dry_run")["child_today"] == "2031-01-15"
        assert doc(snap, "facts_list")["facts"]["updated_at"] == PIN    # stamped while seeding
        page = (snap / "page_home_zh.html").read_text(encoding="utf-8")
        assert f"截至 {PIN}" in page and "<p>2031-01-15</p>" in page   # the page's own clock too


def test_a_case_sees_no_credential_and_no_write_switch():
    with _t.tmpdir() as d:
        repo, tmp = fh.build(d), d / "tmp"
        tmp.mkdir()
        snap = d / "snap"
        p = fh.engine(repo, "snapshot", str(snap), tmp=tmp, FAKE_API_KEY="k-not-for-cases",
                      FAKE_ALLOW_WRITES="1")
        assert p.returncode == 0, p.stdout + p.stderr
        dry = doc(snap, "execute_dry_run")
        assert dry["key_loaded"] is False and dry["meta"]["writes_enabled"] is False
        assert dry["dry_run"] is True and dry["would_write"]
        assert not any("k-not-for-cases" in f.read_text(encoding="utf-8") for f in snap.iterdir())


if __name__ == "__main__":
    _t.main(globals())
