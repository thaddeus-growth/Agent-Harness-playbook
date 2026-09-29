"""apply_answers.py: the owner's answers, written where they belong, once.
Each test runs the whole loop: intake_to_asks.py -> ask.py add -> the owner
answers (in-process, as the console page would) -> ask.py answers ->
apply_answers.py, on a repository whose ssot/ is a copy of templates/ssot/.
Each test names the rule it guards and would fail if that rule were removed."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

INTAKE = (_t.FIX1, _t.FIX2)


def post(w, picks):
    code, asks = _t.tool("intake_to_asks.py", "--intake", *INTAKE, "--pick", ",".join(picks))
    assert code == 0, asks
    code, doc = _t.ask(w["con"], "add", "-", stdin=json.dumps(asks))
    assert code == 0, doc
    return {a["id"]: a for a in asks}


def answers_file(w, *extra, name="answers.json", edit=None):
    code, doc = _t.ask(w["con"], "answers", *extra)
    assert code == 0, doc
    if edit:
        edit(doc)
    return _t.save(os.path.join(w["root"], name), doc)


def apply(w, answers, *extra, data=True):
    args = ["--answers", answers, "--intake", *INTAKE, "--ssot", w["ssot"]]
    if data:
        args += ["--data-dir", w["data"]]
    return _t.tool("apply_answers.py", *args, *extra)


def loop(w, answers: dict):
    """Post the asks for these iids, answer them, apply: (exit, report, asks)."""
    asks = post(w, list(answers))
    _t.owner_answers(w["con"], {f"intake-{i}": v for i, v in answers.items()})
    code, out = apply(w, answers_file(w))
    return code, out, asks


def seq_of(w, i):
    code, doc = _t.ask(w["con"], "answers", "--all")
    return next(r["apply"] for r in doc["answers"] if r["id"] == f"intake-{i}")


def row(path, col, value):
    return next(r for r in _t.tsv(path) if r.get(col) == value)


# ------------------------------------------------------------------ rules --

def test_an_accepted_word_is_a_glossary_row_and_a_sibling_that_names_its_answer():
    with _t.workspace() as w:
        g = os.path.join(w["ssot"], "glossary.tsv")
        with open(g, "a", encoding="utf-8") as f:           # a row the owner signed earlier stays as it is
            f.write("blend\tTwo or more teas in one tin.\n")
        before = _t.read(g)
        code, out, _ = loop(w, {"w1": "yes"})
        assert code == 0 and out["ok"] and [a["action"] for a in out["applied"]] == ["accepted"], out
        assert _t.read(g) == before + "sampler\tA 50 g pouch of one tea, sold to try it.\n"   # appended, the rest untouched
        sib = row(os.path.join(w["ssot"], "glossary.agent.tsv"), "term", "sampler")
        assert sib["status"] == "accepted" and sib["ask"] == "intake-w1"
        assert sib["decided"] == "console:" + seq_of(w, "w1") and sib["source"] == "meeting:2026-03-02 00:12:31", sib


def test_an_accepted_story_gets_the_next_id_never_a_reused_one_and_an_accepted_sibling():
    with _t.workspace() as w:
        ua = os.path.join(w["ssot"], "user-stories.agent.tsv")
        with open(ua, "a", encoding="utf-8") as f:            # S07 was retired once: its id is never given again
            head = _t.read(ua).splitlines()[0].split("\t")
            f.write("\t".join({"id": "S07", "status": "retired"}.get(c, "") if c in ("id", "status") else ""
                              for c in head) + "\n")
        code, out, _ = loop(w, {"s2": "yes"})
        assert code == 0 and out["applied"][0]["story"] == "S08", out
        s = row(os.path.join(w["ssot"], "user-stories.tsv"), "id", "S08")
        assert s["human_step"] == "approve" and s["i_want"] == "to approve each push before its ads start"
        assert s["done_when"] == ("① the push shows its daily cap and expected spend; "
                                  "② nothing starts until I approve it; ③ I can stop it at any time")
        sib = row(ua, "id", "S08")
        assert sib["status"] == "accepted" and sib["decided"] == "console:" + seq_of(w, "s2"), sib


def test_a_number_waits_as_pending_in_the_data_folder_and_never_enters_the_repo():
    with _t.workspace() as w:
        before = _t.snapshot(w["repo"])
        asks = post(w, ["n1"])
        _t.owner_answers(w["con"], {"intake-n1": ("50", "it got slower")})
        ans = answers_file(w)
        code, out = apply(w, ans, data=False)                              # no data folder: refused whole
        assert code == 2 and out["code"] == "data_dir_required" and _t.snapshot(w["repo"]) == before, out
        code, out = _t.tool("apply_answers.py", "--answers", ans, "--intake", *INTAKE, "--ssot", w["ssot"],
                            "--data-dir", os.path.join(w["repo"], "data"))
        assert code == 2 and out["code"] in ("data_dir_in_repo", "data_dir_missing"), out
        os.makedirs(os.path.join(w["repo"], "data"))
        code, out = _t.tool("apply_answers.py", "--answers", ans, "--intake", *INTAKE, "--ssot", w["ssot"],
                            "--data-dir", os.path.join(w["repo"], "data"))
        assert code == 2 and out["code"] == "data_dir_in_repo", out
        code, out = _t.tool("apply_answers.py", "--answers", ans, "--intake", *INTAKE, "--ssot", w["ssot"],
                            "--data-dir", os.path.join(w["root"], "missing"))
        assert code == 2 and out["code"] == "data_dir_missing", out
        os.rmdir(os.path.join(w["repo"], "data"))
        code, out = apply(w, ans)
        assert code == 0 and out["applied"][0]["action"] == "pending", out
        n = _t.tsv(os.path.join(w["data"], "intake", "numbers.tsv"))
        assert n == [{"key": "lead_time_days", "value": "50", "unit": "days",
                      "applies_to": "the time from ordering Sencha to having it in the warehouse",
                      "quote": asks["intake-n1"]["evidence"][0]["quote"], "source": "meeting:2026-03-02 00:16:20",
                      "status": "pending", "suggested": "no", "comment": "it got slower",
                      "ask": "intake-n1", "decided": "console:" + seq_of(w, "n1")}], n
        assert _t.snapshot(w["repo"]) == before                            # the repo did not change at all
        assert out["follow_up"] and out["follow_up"][0]["comment"] == "it got slower"


def test_a_no_is_recorded_as_dropped_with_its_answer_and_the_owner_files_are_untouched():
    with _t.workspace() as w:
        owner = {f: _t.read(os.path.join(w["ssot"], f)) for f in ("glossary.tsv", "user-stories.tsv")}
        code, out, _ = loop(w, {"w3": "no", "s1": "no"})
        assert code == 0 and [a["action"] for a in out["applied"]] == ["dropped", "dropped"], out
        assert {f: _t.read(os.path.join(w["ssot"], f)) for f in owner} == owner
        g = row(os.path.join(w["ssot"], "glossary.agent.tsv"), "term", "push")
        s = row(os.path.join(w["ssot"], "user-stories.agent.tsv"), "ask", "intake-s1")
        assert g["status"] == s["status"] == "dropped"
        assert g["decided"] == "console:" + seq_of(w, "w3") and s["decided"] == "console:" + seq_of(w, "s1")
        assert s["id"] == "intake-s1"                                      # a dropped story takes no S id


def test_applying_the_same_answers_twice_changes_nothing():
    with _t.workspace() as w:
        code, first, _ = loop(w, {"w1": "yes", "w3": "no", "s2": "yes", "n2": "25", "q1": "USD"})
        assert code == 0 and len(first["applied"]) == 5, first
        after = _t.snapshot(w["ssot"], w["data"])
        code, again = apply(w, os.path.join(w["root"], "answers.json"))
        assert code == 0 and again["applied"] == [] and len(again["already"]) == 5, again
        assert _t.snapshot(w["ssot"], w["data"]) == after
        assert again["commands"] == first["commands"]                      # still to run, once


def test_it_prints_the_applied_commands_and_running_them_closes_the_asks():
    with _t.workspace() as w:
        code, out, _ = loop(w, {"w1": "yes", "n1": "45", "q3": "Nobody: pushes wait until I am back"})
        assert code == 0 and len(out["commands"]) == 3, out
        for cmd in out["commands"]:
            argv = shlex.split(cmd)
            assert argv[0] == "python3" and argv[1].endswith("ask.py") and argv[2] == "applied", argv
            assert re.fullmatch(r"intake-[a-z0-9]+@[0-9]+", argv[3]) and argv[4] == "--where", argv
            p = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True,
                               env={**_t.ENV, "CONSOLE_DIR": w["con"]}, cwd=_t.ROOT)
            assert p.returncode == 0, p.stdout
        code, doc = _t.ask(w["con"], "answers")
        assert doc["answers"] == []                                        # nothing waits to be applied
        code, again = apply(w, answers_file(w, "--all", name="all.json"))
        assert code == 0 and again["commands"] == [] and len(again["skipped"]) == 3, again
        code, listed = _t.ask(w["con"], "list", "--status", "all")
        wheres = {r["id"]: r["applied"]["where"] for r in listed["asks"]}
        assert wheres["intake-w1"] == 'glossary: "sampler" (ssot/glossary.tsv)', wheres


def test_an_answer_whose_signature_does_not_check_or_whose_ask_changed_is_never_written():
    with _t.workspace() as w:
        post(w, ["w1", "s2"])
        _t.owner_answers(w["con"], {"intake-w1": "yes", "intake-s2": "yes"})
        before = _t.snapshot(w["ssot"])

        def spoil(doc):
            doc["answers"][0]["verified"] = False
            doc["answers"][1]["revised"] = True
        code, out = apply(w, answers_file(w, edit=spoil))
        assert code == 2 and {p["code"] for p in out["problems"]} == {"not_verified", "revised"}, out
        assert out["applied"] == [] and out["commands"] == [] and _t.snapshot(w["ssot"]) == before

        def unchecked(doc):
            for a in doc["answers"]:
                a["verified"] = None                   # no secret on this side: written, and said so
        code, out = apply(w, answers_file(w, edit=unchecked, name="u.json"))
        assert code == 0 and len(out["unverified"]) == 2 and "ask.py verify" in out["note"], out


def test_dry_run_writes_nothing_and_reports_the_same_plan():
    with _t.workspace() as w:
        post(w, ["w1", "s2", "n1"])
        _t.owner_answers(w["con"], {"intake-w1": "yes", "intake-s2": "yes", "intake-n1": "45"})
        ans = answers_file(w)
        before = _t.snapshot(w["ssot"], w["data"])
        code, plan = apply(w, ans, "--dry-run")
        assert code == 0 and plan["dry_run"] is True and _t.snapshot(w["ssot"], w["data"]) == before, plan
        code, real = apply(w, ans)
        assert [a["where"] for a in plan["applied"]] == [a["where"] for a in real["applied"]]
        assert plan["commands"] == real["commands"]


def test_a_word_already_in_the_glossary_is_a_problem_not_a_second_row():
    with _t.workspace() as w:
        g = os.path.join(w["ssot"], "glossary.tsv")
        with open(g, "a", encoding="utf-8") as f:
            f.write("Sampler\tAny small pouch.\n")
        before = _t.snapshot(w["ssot"])
        code, out, _ = loop(w, {"w1": "yes"})
        assert code == 2 and out["problems"][0]["code"] == "term_exists" and _t.snapshot(w["ssot"]) == before, out


def test_the_same_ask_answered_again_is_a_problem_not_a_second_row():
    with _t.workspace() as w:
        code, out, _ = loop(w, {"s2": "yes"})                  # written, but not yet recorded as applied
        assert code == 0
        _t.owner_reopens(w["con"], "intake-s2")
        _t.owner_answers(w["con"], {"intake-s2": "no"})
        before = _t.snapshot(w["ssot"])
        code, out = apply(w, answers_file(w, name="again.json"))
        assert code == 2 and out["problems"][0]["code"] == "answered_again" and _t.snapshot(w["ssot"]) == before, out


def test_a_question_answer_is_kept_in_the_data_folder():
    with _t.workspace() as w:
        code, out, _ = loop(w, {"q1": "EUR", "q3": "My sister"})
        q = _t.tsv(os.path.join(w["data"], "intake", "questions.tsv"))
        assert [(r["question"], r["answer"], r["suggested"]) for r in q] == [
            ("Which currency is the daily cap in?", "EUR", "no"),
            ("Who may approve a push when you are away?", "My sister", "no")], q


def test_answers_that_are_not_intake_asks_are_skipped():
    with _t.workspace() as w:
        assert _t.ask(w["con"], "add", os.path.join(_t.CONSOLE, "examples", "confirm-word.json"))[0] == 0
        _t.owner_answers(w["con"], {"word-sampler": "yes"})
        before = _t.snapshot(w["ssot"])
        code, out = apply(w, answers_file(w), data=False)
        assert code == 0 and out["skipped"] == [{"id": "word-sampler", "why": "not an intake ask"}], out
        assert _t.snapshot(w["ssot"]) == before


def test_what_is_written_is_what_the_owner_was_shown():
    with _t.workspace() as w:
        code, out, asks = loop(w, {"w1": "yes", "s2": "yes", "n2": "20"})
        assert code == 0, out
        shown = {i: json.dumps(a, ensure_ascii=False) for i, a in asks.items()}
        g = row(os.path.join(w["ssot"], "glossary.tsv"), "term", "sampler")
        assert g["term"] in shown["intake-w1"] and g["definition"] in shown["intake-w1"]
        sid = next(a["story"] for a in out["applied"] if a["kind"] == "story")
        assert sid == "S01"                                 # the first story of a new harness
        s = row(os.path.join(w["ssot"], "user-stories.tsv"), "id", sid)
        for text in [s["i_want"], s["so_that"], s["human_step"]] + [d[2:] for d in s["done_when"].split("; ")]:
            assert text in shown["intake-s2"], text
        n = _t.tsv(os.path.join(w["data"], "intake", "numbers.tsv"))[0]
        for text in (n["unit"], n["applies_to"], n["quote"]):
            assert text in shown["intake-n2"], text


def test_only_apply_answers_writes_and_no_build_tool_reads_the_log_or_imports_the_console():
    for name in sorted(os.listdir(_t.BUILD)):
        if not name.endswith(".py"):
            continue
        src = _t.read(os.path.join(_t.BUILD, name))
        tree = ast.parse(src)
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imports & {"core", "serve", "pages", "relay"}, (name, imports)
        assert "events.jsonl" not in src, name
        writes = [f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and isinstance(n.func.value, ast.Name) and n.func.value.id in ("os", "tempfile", "shutil")
                  and n.func.attr in ("replace", "rename", "mkstemp", "fdopen", "makedirs", "mkdir",
                                      "unlink", "remove", "rmtree", "copy", "copyfile", "move")]
        opened_for_writing = re.search(r"open\([^)]*['\"][wax]", src)
        if name == "apply_answers.py":
            assert "os.replace" in writes and "tempfile.mkstemp" in writes     # the check sees a writer
        else:
            assert not writes and not opened_for_writing, (name, writes)


if __name__ == "__main__":
    _t.main(globals())
