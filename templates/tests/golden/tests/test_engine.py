"""The engine's parts, one at a time: the diff walker and its verdicts, the
Masker, the refusals (output inside a work tree, a write switch, an unsafe
name), the frozen copy of a live folder, the case env, the verb table read
with ast (a dict, or the list of Verb calls a scaffolded harness holds), the
pin, and the midnight rule. Each check that guards something
also runs on an input that must trip it."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import _t  # noqa: E402
import engine  # noqa: E402
import fake_harness as fh  # noqa: E402

UTC = dt.timezone.utc
START = dt.datetime(2031, 1, 15, 9, 30, tzinfo=UTC)


def found(a, b) -> list[tuple]:
    out: list = []
    engine.walk(a, b, "", out)
    return [(k, p) for k, p, _ in out]


def verdict(files_a: dict, files_b: dict, strict: bool = False) -> tuple[int, str]:
    """engine.diff over two snapshot folders holding these files."""
    with _t.tmpdir() as d:
        for side, files in (("a", files_a), ("b", files_b)):
            (d / side).mkdir()
            for name, body in files.items():
                text = body if isinstance(body, str) else json.dumps(body)
                (d / side / name).write_text(text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = engine.diff(d / "a", d / "b", strict)
        return rc, out.getvalue()


def case(doc, rc=0) -> dict:
    return {"rc": rc, "doc": doc}


# -------------------------------------------------------------------- diff
def test_the_walker_names_every_kind_of_difference():
    assert found({"a": 1, "l": [1, 2], "s": "x"}, {"l": [1], "s": "y", "n": 2}) == [
        ("REMOVED", ".a"), ("LIST-LEN", ".l"), ("added", ".n"), ("CHANGED", ".s")]
    assert found({"v": 1}, {"v": 1.0}) == [("CHANGED", ".v")]          # a type is behaviour
    assert found({"v": 1}, {"v": True}) == [("CHANGED", ".v")]
    assert found({"v": None}, {"v": 0}) == [("CHANGED", ".v")]
    assert found([{"x": [1, {"y": 2}]}], [{"x": [1, {"y": 3}]}]) == [("CHANGED", "[0].x[1].y")]
    assert found({"same": [1, {"k": "v"}]}, {"same": [1, {"k": "v"}]}) == []


def test_a_removed_or_changed_thing_fails_and_an_added_key_fails_only_when_strict():
    base = {"c.json": case({"k": 1})}
    assert verdict(base, base) == (0, "1 common cases, 0 added, 0 removed, 0 differ\n")
    assert verdict(base, {"c.json": case({"k": 1, "new": 2})})[0] == 0
    assert verdict(base, {"c.json": case({"k": 1, "new": 2})}, strict=True)[0] == 1
    assert verdict(base, {**base, "d.json": case({})})[0] == 0          # an added case
    assert verdict(base, {**base, "d.json": case({})}, strict=True)[0] == 1
    rc, out = verdict({**base, "d.json": case({})}, base)
    assert rc == 1 and "REMOVED case d.json" in out
    rc, out = verdict(base, {"c.json": case({"k": 2})})
    assert rc == 1 and "CHANGED  .k  [1, 2]" in out
    rc, out = verdict(base, {"c.json": case({"k": 1}, rc=2)})
    assert rc == 1 and "RC" in out
    rc, out = verdict(base, {"c.json": case({"__not_json__": "oops", "__stderr__": ""})})
    assert rc == 1 and "NOT-JSON" in out
    rc, out = verdict({"c.json": case({"__render_failed__": "x"})},
                      {"c.json": case({"__render_failed__": "x"})})
    assert rc == 1, "a page that raised fails even when both sides raised alike"


def test_any_html_difference_fails_and_is_shown_split_on_tags():
    page = "<html><body><p>as of <NOW></p><ul><li>a</li></ul></body></html>"
    assert verdict({"p.html": page}, {"p.html": page}, strict=True)[0] == 0
    rc, out = verdict({"p.html": page}, {"p.html": page.replace("<li>a<", "<li>b<")})
    assert rc == 1 and "p.html: HTML differs" in out
    assert "-li>a</li" in out and "+li>b</li" in out                     # one line per tag


# ------------------------------------------------------------------ Masker
def test_only_temp_paths_and_stamps_from_inside_the_run_are_masked():
    with _t.tmpdir() as work:
        data = work / "data"
        m = engine.Masker([(data, "<DATA_DIR>"), (work, "<WORK>"), (None, "<DATA_SRC>")],
                          START, START + dt.timedelta(minutes=2))
        assert m(f"{data}/x.db") == "<DATA_DIR>/x.db"                     # the longer path first
        assert m(f"{work}/y") == "<WORK>/y"
        assert m({f"{data}": [f"at {work}"]}) == {"<DATA_DIR>": ["at <WORK>"]}   # keys too
        inside = "2031-01-15T09:33:00+00:00"
        assert m(inside) == "<NOW>" and m(f"saved {inside} by init") == "saved <NOW> by init"
        assert m("2031-01-15 09:31:10") == "<NOW>"                        # naive reads as UTC
        assert m(m.text(f"<p>{inside}</p>")) == "<p><NOW></p>"
        for kept in ("2031-01-15T09:10:00+00:00", "2031-01-14T09:31:00Z", "2031-01-15",
                     "2031-01-15T12:00:00+00:00"):                        # outside the span: data
            assert m(kept) == kept, kept
        assert m(7) == 7 and m(None) is None and m(True) is True
        (work / "link").symlink_to(data, target_is_directory=True)
        m = engine.Masker([(work / "link", "<L>")], START, START)
        assert m.text(f"{work}/link/a and {data}/b") == "<L>/a and <L>/b"   # the real path too


# ---------------------------------------------------------------- refusals
def test_output_inside_a_git_work_tree_or_a_tree_or_not_empty_is_refused():
    with _t.tmpdir() as d:
        (d / "repo" / ".git").mkdir(parents=True)
        (d / "wt").mkdir()
        (d / "wt" / ".git").write_text("gitdir: elsewhere\n")             # a worktree's .git is a file
        (d / "export").mkdir()
        for bad in (d / "repo" / "out", d / "repo" / "a" / "b", d / "wt" / "out"):
            with _t.refused("refused", "inside a checkout"):
                engine.outside(bad, set())
        with _t.refused("inside a checkout"):
            engine.outside(d / "export" / "out", {d / "export"})
        (d / "full").mkdir()
        (d / "full" / "f").write_text("x")
        with _t.refused("not empty"):
            engine.outside(d / "full", set())
        assert engine.outside(d / "new", set()) == d / "new"
        (d / "empty").mkdir()
        assert engine.outside(d / "empty", set()) == d / "empty"


def test_a_write_switch_or_an_unsafe_name_refuses_the_whole_plan():
    ok = {"report_summary": ("report", "summary", "--", "--json")}
    assert engine.checked(ok) == ok
    for argv in (("execute", "--", "--apply"), ("execute", "--", "--json", "--apply=yes")):
        with _t.refused("refused case", "--apply"):
            engine.checked({**ok, "x": argv})
    for name in ("../up", "a/b", "", ".hidden", "sp ace"):
        with _t.refused("case name"):
            engine.checked({name: ("report", "summary")})


def test_verbs_are_read_from_the_table_with_ast_and_never_imported():
    with _t.tmpdir() as tree:
        (tree / "tool.py").write_text(
            'raise SystemExit("imported")\n'
            "def run(): pass\n"
            'VERBS = {("report", "summary"): run, "init": run, ("a", "b", "c"): run}\n')
        assert engine.verbs_of(tree) == {("report", "summary"), ("init",), ("a", "b", "c")}
        (tree / "tool.py").write_text("OTHER = {}\n")
        with _t.refused("no VERBS"):
            engine.verbs_of(tree)
    verbs = {("report", "summary"), ("init",)}
    assert engine.has_verb(["report", "summary", "--", "--json"], verbs)
    assert engine.has_verb(["init"], verbs)
    assert not engine.has_verb(["report", "rollup", "--", "--json"], verbs)
    assert not engine.has_verb(["--", "init"], verbs)


@contextlib.contextmanager
def naming(**names):
    """engine.C (the cases hook) with these names set for the block."""
    saved = {k: getattr(engine.C, k) for k in names}
    for k, v in names.items():
        setattr(engine.C, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(engine.C, k, v)


KIT_VERBS = ('from kit.verbs import Verb\n\nVERBS = [\n'
             '    Verb(("facts", "init"), "facts.py", "human", False, answers="Declare the scope"),\n'
             '    Verb(("facts", "list"), "facts.py", "read"),\n'
             '    Verb(("pending",), "pending.py", "read"),\n'
             '    Verb(words=("execute", "apply"), script="execute_actions.py", kind="external"),\n'
             '    Verb(("test",), "../tests/run.py", "dev", False, False),\n]\n')
KIT_FILES = {"VERB_FILE": "scripts/verbs.py", "ENTRY": "scripts/tool.py"}


def test_verbs_are_also_read_from_the_list_of_verb_calls_a_scaffolded_harness_holds():
    with _t.tmpdir() as tree:
        (tree / "scripts").mkdir()
        (tree / "scripts" / "tool.py").write_text("raise SystemExit('imported')\n")   # the dispatcher holds no table
        (tree / "scripts" / "verbs.py").write_text(KIT_VERBS)
        with naming(**KIT_FILES):
            assert engine.verbs_of(tree) == {("facts", "init"), ("facts", "list"), ("pending",), ("execute", "apply"), ("test",)}
            (tree / "scripts" / "verbs.py").write_text(KIT_VERBS.replace("VERBS =", "VERBS: list =").replace("[\n", "(\n", 1)
                                                       .replace("]\n", ")\n"))         # an annotated tuple is a table too
            assert ("facts", "list") in engine.verbs_of(tree)
            for bad in ('VERBS = [*OTHER, Verb(("a",), "a.py", "read")]\n',           # an entry that is not a call
                        'VERBS = [Verb(words, "a.py", "read")]\n',                    # words that are not literal
                        'VERBS = [Verb(("a", 1), "a.py", "read")]\n',                 # a word that is not a string
                        'VERBS = [Verb()]\n'):
                (tree / "scripts" / "verbs.py").write_text(bad)
                with _t.refused("VERBS", "Verb(words", "line 1"):
                    engine.verbs_of(tree)                                                  # refused, never skipped
            (tree / "scripts" / "verbs.py").write_text("OTHER = []\n")
            with _t.refused("no VERBS", "scripts/verbs.py"):
                engine.verbs_of(tree)
        assert engine.C.VERB_FILE == engine.C.ENTRY                                        # the example hook names no other file


def test_the_verb_list_the_scaffolder_writes_is_read_whole():
    skeleton = Path(__file__).resolve().parents[4] / "scaffold" / "skeleton" / "scripts" / "verbs.py"
    text = skeleton.read_text(encoding="utf-8")
    declared = re.findall(r"^    Verb\(\(([^)]*)\)", text, re.M)
    assert len(declared) >= 20, declared                                                   # the real table, not a stub
    with _t.tmpdir() as tree:
        (tree / "scripts").mkdir()
        (tree / "scripts" / "verbs.py").write_text(text.replace("{{name}}", "x-harness").replace("{{cli}}", "x"))
        with naming(**KIT_FILES):
            verbs = engine.verbs_of(tree)
    assert len(verbs) == len(declared) and ("facts", "list") in verbs and ("execute", "apply") in verbs and ("test",) in verbs


def test_a_case_sees_the_allowlist_and_never_a_hash_seed():
    saved = dict(os.environ), engine.C.ENV_KEEP
    try:
        os.environ.update(FAKE_API_KEY="k", FAKE_ALLOW_WRITES="1", CONFIRM_SECRET="s",
                          PYTHONHASHSEED="0", LC_ALL="C", PATH=saved[0].get("PATH", ""))
        engine.C.ENV_KEEP = (*saved[1], "PYTHONHASHSEED")                  # even when a hook lists it
        os.environ["HOME"] = "/operator/home"
        engine.C.ENV_KEEP = (*engine.C.ENV_KEEP, "HOME")                   # even when a hook lists it
        env = engine.case_env(Path("data"), Path("side/home"))
        assert env["HOME"] == "side/home"
        allowed = set(engine.C.ENV_KEEP) | set(engine.C.ENV_SET) | {engine.C.DATA_ENV}
        assert set(env) <= allowed | {k for k in env if k.startswith(tuple(engine.C.ENV_PREFIXES))}
        assert not {"FAKE_API_KEY", "FAKE_ALLOW_WRITES", "CONFIRM_SECRET", "PYTHONHASHSEED"} & set(env)
        assert env[engine.C.DATA_ENV] == "data" and env["LC_ALL"] == "C" and env["TZ"] == "UTC"
    finally:
        os.environ.clear()
        os.environ.update(saved[0])
        engine.C.ENV_KEEP = saved[1]


# ------------------------------------------------------------------ freeze
@contextlib.contextmanager
def copying(after_copy):
    """shutil.copytree that calls after_copy(n) after its n-th copy."""
    real, n = shutil.copytree, [0]

    def wrapped(src, dst, *a, **k):
        out = real(src, dst, *a, **k)
        n[0] += 1
        after_copy(n[0])
        return out
    shutil.copytree = wrapped
    try:
        yield n
    finally:
        shutil.copytree = real


def test_a_frozen_copy_holds_link_targets_and_the_source_is_only_read():
    with _t.tmpdir() as d:
        outer = d / "outer.db"
        outer.write_text("real")
        (d / "outer_dir").mkdir()
        (d / "outer_dir" / "f").write_text("in a linked folder")
        src = fh.make_data(d / "src")
        (src / "linked.db").symlink_to(outer)
        (src / "linked_dir").symlink_to(d / "outer_dir")
        before = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}
        engine.freeze(src, d / "copy")
        copy = d / "copy"
        assert not [p for p in copy.rglob("*") if p.is_symlink()]
        assert (copy / "linked.db").read_text() == "real"
        assert (copy / "linked_dir" / "f").read_text() == "in a linked folder"
        (copy / "linked.db").write_text("written by a case")
        assert outer.read_text() == "real"                                  # never through a link
        assert before == {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}


def test_a_dangling_link_or_a_folder_that_keeps_changing_is_refused_without_naming_it():
    with _t.tmpdir() as d:
        src = fh.make_data(d / "src")
        (src / "gone.db").symlink_to(d / "nowhere")
        with _t.refused("--data: copy failed") as r:
            engine.freeze(src, d / "copy")
        assert str(src) not in r.message and not (d / "copy").exists()
        (src / "gone.db").unlink()
        grow = src / "store.json"
        with copying(lambda n: grow.write_text(grow.read_text() + " ")) as n:
            with _t.refused("changed while it was copied") as r:
                engine.freeze(src, d / "copy")
        assert n[0] == engine.FREEZE_TRIES and not (d / "copy").exists()
        assert str(src) not in r.message


def test_a_folder_that_changed_once_is_copied_again_and_a_shm_file_is_ignored():
    with _t.tmpdir() as d:
        src = fh.make_data(d / "src")
        (src / "db-shm").write_text("0")
        grow = src / "store.json"
        with copying(lambda n: grow.write_text(grow.read_text() + " ") if n == 1 else None) as n:
            engine.freeze(src, d / "copy")
        assert n[0] == 2 and (d / "copy" / "store.json").read_text() == grow.read_text()
        with copying(lambda n: (src / "db-shm").write_text(str(n) * 9)) as n:
            engine.freeze(src, d / "copy2")
        assert n[0] == 1


# --------------------------------------------------------- pin, midnight
def test_the_pin_is_the_start_unless_another_day_is_asked_for_without_data():
    later = dt.date(2031, 3, 1)
    assert engine.the_pin(None, None, START) == START
    assert engine.the_pin(START.date(), None, START) == START
    assert engine.the_pin(later, None, START) == dt.datetime(2031, 3, 1, 12, tzinfo=UTC)
    assert engine.the_pin(later, Path("data"), START) == START


def test_a_run_that_crosses_a_utc_midnight_fails():
    with _t.tmpdir() as d:
        repo = fh.build(d)
        for crossed in (False, True):
            clock = iter([dt.datetime(2031, 1, 15, 23, 59, 58, tzinfo=UTC)])
            end = dt.datetime(2031, 1, 16 if crossed else 15, 0 if crossed else 23,
                              0 if crossed else 59, 59, tzinfo=UTC)
            real = engine.utcnow
            engine.utcnow = lambda: next(clock, end)
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = engine.main(["snapshot", str(d / f"out{crossed}"), "--tree", str(repo)])
            finally:
                engine.utcnow = real
            assert rc == (1 if crossed else 0), out.getvalue()
            assert ("crossed a UTC midnight" in out.getvalue()) == crossed


if __name__ == "__main__":
    _t.main(globals())
