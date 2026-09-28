"""ask.py, the agent's CLI, driven the way an agent drives it: every verb
through a subprocess, the JSON read back, the exit code checked. The human's
side (answers, reopens, notes) is written in-process with core.Store, because
ask.py must not have it. Each test names the rule it guards and would fail if
that rule were removed.
"""

from __future__ import annotations

import ast
import codecs
import json
import os
import re
import subprocess
import sys
import threading
import time
from argparse import Namespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import _t  # noqa: E402
import ask as cli  # noqa: E402
import core  # noqa: E402

ASK = os.path.join(ROOT, "ask.py")
EXAMPLES = os.path.join(ROOT, "examples")
for _k in ("CONSOLE_DIR", "CONSOLE_SECRET"):    # the tests' own store must not see the developer's
    os.environ.pop(_k, None)


# ---------------------------------------------------------------- helpers --

def strict(text):
    """The document as any strict JSON reader takes it: NaN, Infinity and 1e999 are no numbers."""
    def constant(name):
        raise AssertionError(f"{name} is not JSON: {text[:300]}")

    def finite(digits):
        x = float(digits)
        assert x - x == 0, f"{digits} is not a finite number: {text[:300]}"
        return x
    return json.loads(text, parse_constant=constant, parse_float=finite)


def go(*args, d=None, name=None, env=None, stdin=None, cwd=None, timeout=60):
    """One ask.py process; stdout must be exactly one line of strict JSON, stderr empty."""
    argv = [sys.executable, ASK]
    if d is not None:
        argv += ["--dir", d]
    if name is not None:
        argv += ["--as", name]
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run(argv + list(args), capture_output=True, encoding="utf-8",
                       env={**os.environ, **(env or {})}, cwd=cwd, timeout=timeout, **kw)
    assert p.stderr == "", p.stderr
    assert p.stdout.endswith("\n") and p.stdout.count("\n") == 1, p.stdout
    strict(p.stdout)
    return p


def run(*args, **kw):
    p = go(*args, **kw)
    return p.returncode, json.loads(p.stdout)


def ok(*args, **kw):
    code, doc = run(*args, **kw)
    assert code == 0 and doc["ok"] is True, (code, doc)
    return doc


def refused(code_name, *args, **kw):
    code, doc = run(*args, **kw)
    assert code == 2 and doc["ok"] is False and doc["code"] == code_name, (code, doc)
    assert doc["code"] in core.CODES and doc["message"] and isinstance(doc["params"], dict), doc
    return doc


def mk_ask(id, step="confirm", **over):
    a = {"id": id, "step": step, "title": f"Is {id} right?", "why": "Because it matters today.",
         "evidence": [{"label": "Fact", "value": "1", "source": "test"}],
         "if_no": "Nothing changes."}
    if step in ("confirm", "approve"):
        a["recommend"] = {"value": "yes", "because": "It looks right."}
    if step == "approve":
        a["effect"] = "Something small happens."
    if step == "choose":
        a["options"] = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
        a["recommend"] = {"value": "a", "because": "A is safer."}
    if step == "provide":
        a["input"] = {"type": "number", "min": 0, "max": 100}
    a.update(over)
    return a


def put(folder, name, obj):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))
    return path


def answer(d, id, value, comment="", gate=None):
    s = core.Store(d)
    return s.answer("tester", id, value, shown=s.state()["asks"][id]["hash"],
                    comment=comment, gate=gate)


def rewrite(d, fn):
    """Edit events.jsonl by hand, the way a tamperer would."""
    path = os.path.join(d, "events.jsonl")
    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    for e in events:
        fn(e)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


REPORT = {"ok", "seq", "open", "answers", "reopened", "notes", "notes_truncated", "advice", "dir"}   # what `answers` says


def ids(rows):
    return [r["id"] for r in rows]


def logged(d):
    """The bytes of the log ('' when there is none): "writes nothing" means no event."""
    try:
        with open(os.path.join(d, "events.jsonl"), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


# ------------------------------------------------------ folder, arguments --

def test_a_folder_is_required_and_never_guessed_from_the_cwd():
    with _t.tmpdir() as cwd:
        f = put(cwd, "x.json", mk_ask("a"))
        for argv in (["add", f], ["list"], ["answers"], ["wait", "--timeout", "0"],
                     ["applied", "a", "--where", "w"], ["withdraw", "a", "--reason", "r"],
                     ["say", "hi"], ["verify"]):
            refused("no_dir", *argv, cwd=cwd)
        refused("no_dir", "list", d="", cwd=cwd)
        refused("no_dir", "list", env={"CONSOLE_DIR": ""}, cwd=cwd)
        assert os.listdir(cwd) == ["x.json"], os.listdir(cwd)      # nothing was made in the cwd


def test_schema_and_help_need_no_folder_and_never_touch_one():
    with _t.tmpdir() as cwd, _t.tmpdir() as broken:
        put(broken, "events.jsonl", "not json\n")               # a log no verb could read
        f = put(cwd, "not-a-folder", "")
        for kw in ({}, {"env": {"CONSOLE_DIR": ""}}, {"d": os.path.join(cwd, "nope")},
                   {"d": f}, {"d": broken}, {"env": {"CONSOLE_DIR": broken}}):
            doc = ok("schema", cwd=cwd, **kw)
            assert doc["steps"] == list(core.STEPS) and doc["fields"] and doc["examples"], kw
            for argv in (["--help"], ["add", "--help"], ["applied", "-h"]):
                assert ok(*argv, cwd=cwd, **kw)["help"].startswith("usage:"), (argv, kw)
        assert sorted(os.listdir(cwd)) == ["not-a-folder"], os.listdir(cwd)
        assert os.listdir(broken) == ["events.jsonl"]
        # the verbs that do read the log still ask for the folder, and the broken one still refuses
        refused("no_dir", "list", cwd=cwd)
        refused("corrupt_log", "list", d=broken)


def test_the_folder_comes_from_the_environment_and_the_flag_wins():
    with _t.tmpdir() as a, _t.tmpdir() as b:
        core.Store(a).post("agent", [mk_ask("only-in-a")])
        assert ids(ok("list", env={"CONSOLE_DIR": a})["asks"]) == ["only-in-a"]
        assert ok("list", d=b, env={"CONSOLE_DIR": a})["asks"] == []
        f = put(b, "in.json", mk_ask("via-env"))
        assert ok("add", f, env={"CONSOLE_DIR": a})["posted"] == ["via-env"]
        assert ids(core.Store(a).state()["asks"].values()) == ["only-in-a", "via-env"]


def test_every_ok_document_says_which_folder_it_used():
    with _t.tmpdir() as d, _t.tmpdir() as files:
        core.Store(d).post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("c")])
        answer(d, "a", "yes")                                    # also makes the secret `verify` needs
        f = put(files, "n.json", mk_ask("n"))
        for argv in (["add", f], ["list"], ["answers"], ["wait", "--timeout", "0"], ["say", "hi"],
                     ["applied", "a", "--where", "w"], ["withdraw", "b", "--reason", "r"], ["verify"]):
            assert ok(*argv, d=d)["dir"] == d, argv
        # a relative path is made absolute against the cwd, so a mistyped one shows up where it is read
        base = os.path.realpath(files)
        want = os.path.join(base, "console")
        assert ok("list", d="console", cwd=base)["dir"] == want
        assert ok("list", d="x/../console", cwd=base)["dir"] == want
        assert ok("list", env={"CONSOLE_DIR": "console"}, cwd=base)["dir"] == want
        assert ok("list", d=d, env={"CONSOLE_DIR": "elsewhere"})["dir"] == d      # the flag wins
        assert not os.path.exists(want)                                            # and naming it made nothing
        # `schema` needs no folder: null when none is declared, the declared one otherwise
        assert ok("schema")["dir"] is None
        assert ok("schema", d="nope", cwd=base)["dir"] == os.path.join(base, "nope")


BAD_ARGS = [[], ["nosuchverb"], ["--nosuch", "list"], ["--dir"], ["list", "--status", "everything"],
            ["list", "--nosuch"], ["list", "extra"], ["answers", "--since", "-1"],
            ["answers", "--since", "soon"], ["answers", "--since", "1.5"],
            ["wait", "--timeout", "nan"], ["wait", "--timeout", "inf"], ["wait", "--timeout", "-3"],
            ["wait", "--since", "x"], ["applied", "a"], ["applied", "--where", "x"],
            ["withdraw", "a"], ["withdraw", "--reason", "x"], ["say"], ["say", "two", "words"],
            ["applied", "a@", "--where", "x"], ["applied", "@5", "--where", "x"],
            ["applied", "a@x", "--where", "x"], ["applied", "a@-1", "--where", "x"],
            ["applied", "a@1.5", "--where", "x"], ["applied", "a@ 1", "--where", "x"],
            ["applied", "a@\u0663", "--where", "x"], ["applied", "a@" + "9" * 16, "--where", "x"],
            ["applied", "a@" + "9" * 5000, "--where", "x"],
            ["add", "--max-open", "0"], ["add", "--max-open", "many"], ["add", "a.json", "b.json"]]


def test_a_bad_argument_is_a_document_never_usage_text():
    with _t.tmpdir() as d:
        for argv in BAD_ARGS:
            p = go(*argv, d=d)
            doc = json.loads(p.stdout)
            assert p.returncode == 2 and doc["ok"] is False, (argv, p.stdout)
            assert doc["code"] == "bad_request" and doc["message"], (argv, doc)
            assert "usage:" not in p.stdout.lower(), (argv, p.stdout)
        # arguments are read before the folder is looked for
        assert refused("bad_request", "nosuchverb")["message"]


def test_help_is_a_document_too():
    for argv in (["--help"], ["-h"], ["add", "--help"], ["wait", "-h"]):
        doc = ok(*argv)
        assert doc["help"].startswith("usage:") and "ask.py" in doc["help"], argv


def test_every_outcome_is_one_line_of_json_even_when_the_folder_is_not_one():
    with _t.tmpdir() as d:
        f = put(d, "not-a-folder", "")
        for argv in (["list"], ["add", put(d, "a.json", mk_ask("a"))], ["wait", "--timeout", "0"],
                     ["say", "hi"]):
            refused("bad_request", *argv, d=f)


# ------------------------------------------------------------------- add --

def test_add_posts_one_ask_or_an_array_from_a_file_or_stdin():
    with _t.tmpdir() as d, _t.tmpdir() as files:
        doc = ok("add", put(files, "one.json", mk_ask("one")), d=d)
        assert doc == {"ok": True, "posted": ["one"], "updated": [], "unchanged": [], "open": 1,
                       "budget": core.MAX_OPEN, "seq": 1, "dry_run": False, "dir": d}, doc
        doc = ok("add", "-", d=d, stdin=json.dumps([mk_ask("two", "choose"), mk_ask("three", "approve")]))
        assert doc["posted"] == ["two", "three"] and doc["open"] == 3 and doc["seq"] == 3, doc
        doc = ok("add", d=d, stdin=json.dumps(mk_ask("four", "provide")))   # no FILE: stdin
        assert doc["posted"] == ["four"] and doc["open"] == 4, doc
        assert {e["type"] for e in core.Store(d).events()} == {"ask"}


def test_add_says_unchanged_when_nothing_differs_and_updated_when_an_open_ask_is_revised():
    with _t.tmpdir() as d, _t.tmpdir() as files:
        f = put(files, "a.json", mk_ask("a"))
        first = ok("add", f, d=d)
        again = ok("add", f, d=d)
        assert again["unchanged"] == ["a"] and again["posted"] == [] and again["seq"] == first["seq"]
        f2 = put(files, "a2.json", mk_ask("a", title="Is a still right?"))
        rev = ok("add", f2, d=d)
        assert rev["updated"] == ["a"] and rev["posted"] == [] and rev["open"] == 1, rev
        row = ok("list", d=d)["asks"][0]
        assert row["rev"] == 2 and row["title"] == "Is a still right?", row


def test_add_dry_run_checks_and_counts_but_writes_nothing():
    with _t.tmpdir() as base, _t.tmpdir() as files:
        d = os.path.join(base, "console")
        f = put(files, "a.json", [mk_ask("a"), mk_ask("b")])
        doc = ok("add", f, "--dry-run", d=d)
        assert doc["dry_run"] is True and doc["posted"] == ["a", "b"] and doc["open"] == 2, doc
        assert logged(d) == b""
        refused("invalid_ask", "add", "--dry-run", d=d, stdin=json.dumps({"id": "x"}))
        ok("add", f, d=d)
        before = logged(d)
        assert before
        ok("add", put(files, "c.json", mk_ask("c")), "--dry-run", d=d)
        assert logged(d) == before


def test_add_is_all_or_nothing_and_names_every_problem_at_once():
    with _t.tmpdir() as d:
        bad = mk_ask("bad")
        del bad["why"]
        bad["ttle"] = "typo"
        doc = refused("invalid_ask", "add", "-", d=d, stdin=json.dumps([mk_ask("good"), bad]))
        errs = doc["params"]["errors"]
        assert all(set(e) >= {"ask", "field", "code", "message"} for e in errs), errs
        assert {(e["ask"], e["field"], e["code"]) for e in errs} == {
            ("bad", "why", "required"), ("bad", "ttle", "unknown_field")}, errs
        assert logged(d) == b""                                          # not even the good one
        assert refused("invalid_ask", "add", d=d, stdin=json.dumps("just words"))["params"]["errors"]


def test_add_stops_at_the_budget_and_writes_nothing_of_the_batch():
    with _t.tmpdir() as d:
        doc = refused("over_budget", "add", d=d, stdin=json.dumps([mk_ask(f"a{i}") for i in range(core.MAX_OPEN + 1)]))
        assert doc["params"]["max"] == core.MAX_OPEN and len(doc["params"]["adding"]) == core.MAX_OPEN + 1
        assert logged(d) == b""
        ok("add", "--max-open", "2", d=d, stdin=json.dumps([mk_ask("a"), mk_ask("b")]))
        doc = refused("over_budget", "add", "--max-open", "2", d=d, stdin=json.dumps(mk_ask("c")))
        assert doc["params"]["open"] == ["a", "b"] and doc["params"]["max"] == 2, doc
        # revising an open ask does not count against the budget
        assert ok("add", "--max-open", "2", d=d, stdin=json.dumps(mk_ask("a", title="Really a?")))["updated"] == ["a"]
        assert ids(ok("list", d=d)["asks"]) == ["a", "b"]


def test_max_open_only_lowers_the_limit_and_a_bigger_number_is_no_error():
    with _t.tmpdir() as d:
        batch = [mk_ask(f"a{i}") for i in range(core.MAX_OPEN + 1)]
        for big in ("11", "99", "100000"):
            doc = refused("over_budget", "add", "--max-open", big, d=d, stdin=json.dumps(batch))
            assert doc["params"]["max"] == core.MAX_OPEN, doc
            assert logged(d) == b""
        doc = ok("add", "--max-open", "99", d=d, stdin=json.dumps(batch[:core.MAX_OPEN]))
        assert doc["open"] == core.MAX_OPEN and doc["budget"] == core.MAX_OPEN, doc
        doc = refused("over_budget", "add", "--max-open", "99", d=d, stdin=json.dumps(mk_ask("eleventh")))
        assert doc["params"]["max"] == core.MAX_OPEN and len(doc["params"]["open"]) == core.MAX_OPEN, doc
        assert ok("add", "--max-open", "99", "--dry-run", d=d,
                  stdin=json.dumps(mk_ask("a0", title="Revised?")))["updated"] == ["a0"]
        assert len(ok("list", d=d)["asks"]) == core.MAX_OPEN


def test_the_help_of_max_open_says_it_can_only_lower():
    text = " ".join(ok("add", "--help")["help"].split())
    assert "--max-open N lower the limit" in text and "only lower" in text \
        and f"never raise it above {core.MAX_OPEN}" in text, text


def test_add_refuses_an_id_that_is_answered_or_withdrawn():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        answer(d, "a", "yes")
        s.withdraw("agent", ["b"], "no longer needed")
        for i in ("a", "b"):
            doc = refused("id_used", "add", d=d, stdin=json.dumps(mk_ask(i)))
            assert doc["params"]["id"] == i


def test_add_reads_bad_input_as_a_bad_request():
    with _t.tmpdir() as d, _t.tmpdir() as files:
        for text in ("{", "", "[", "not json"):
            assert "JSON" in refused("bad_request", "add", d=d, stdin=text)["message"], text
        assert "no asks" in refused("bad_request", "add", d=d, stdin="[]")["message"]
        refused("bad_request", "add", os.path.join(files, "missing.json"), d=d)
        refused("bad_request", "add", files, d=d)                          # a folder, not a file
        bad_bytes = os.path.join(files, "latin.json")
        with open(bad_bytes, "wb") as f:
            f.write(b'{"id": "\x80\x81"}')
        refused("bad_request", "add", bad_bytes, d=d)
        refused("bad_request", "add", "-", d=d, stdin="[" * 100000)          # nesting too deep
        refused("invalid_ask", "add", "-", d=d, stdin="[1]")
        assert logged(d) == b""


def test_add_refuses_nan_and_infinity_so_no_document_ever_holds_one():
    hostile = ('{"id": NaN}', "[NaN]", "[Infinity]", "[-Infinity]", '[{"id": [1e999]}]',
               '[{"id": "a", "step": "provide", "input": {"type": "number", "max": 1e999}}]',
               "[-1e999]", "[1E400]")
    with _t.tmpdir() as d, _t.tmpdir() as files:
        for text in hostile:
            for doc in (refused("bad_request", "add", d=d, stdin=text),          # go() reads stdout as strict JSON
                        refused("bad_request", "add", put(files, "h.json", text), d=d)):
                assert doc["message"].startswith("not valid JSON"), (text, doc)
        assert logged(d) == b""
        # numbers that are finite are still numbers
        ask = mk_ask("p", "provide", input={"type": "number", "min": 0.5, "max": 1e300, "unit": "USD"})
        assert ok("add", d=d, stdin=json.dumps(ask))["posted"] == ["p"]


def test_absurd_nesting_is_a_bad_request_wherever_it_bites():
    deep = "[" * 100000 + "]" * 100000
    with _t.tmpdir() as d:
        for text in (json.dumps(mk_ask("a", evidence=[{"label": "x", "value": "@@"}])),
                     json.dumps(mk_ask("a", recommend={"value": "@@", "because": "b"}))):
            doc = refused("bad_request", "add", d=d, stdin=text.replace('"@@"', deep))
            assert "nested" in doc["message"], doc
        assert logged(d) == b""


def test_add_takes_a_byte_order_mark_and_non_ascii_text():
    with _t.tmpdir() as d, _t.tmpdir() as files:
        path = os.path.join(files, "bom.json")
        with open(path, "wb") as f:
            f.write(codecs.BOM_UTF8 + json.dumps(mk_ask("cha", title="要不要先推广红茶？")).encode("utf-8"))
        assert ok("add", path, d=d)["posted"] == ["cha"]
        # written as it is, and readable in any locale (no \u escapes, no encode error)
        p = go("list", d=d, env={"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"})
        assert p.returncode == 0 and "要不要先推广红茶？" in p.stdout and "\\u" not in p.stdout, p.stdout


def test_add_without_a_file_on_a_terminal_asks_for_one_instead_of_waiting():
    try:
        master, slave = os.openpty()
    except OSError:
        return                                   # no pty here: nothing to test with
    try:
        with _t.tmpdir() as d:
            p = subprocess.run([sys.executable, ASK, "--dir", d, "add"], stdin=slave,
                               capture_output=True, encoding="utf-8", timeout=30)
            doc = json.loads(p.stdout)
            assert p.returncode == 2 and doc["code"] == "bad_request" and "FILE" in doc["message"], p.stdout
    finally:
        os.close(master)
        os.close(slave)


def test_concurrent_adds_keep_the_log_whole():
    with _t.tmpdir() as d:
        out = []
        threads = [threading.Thread(target=lambda i=i: out.append(
            ok("add", d=d, stdin=json.dumps(mk_ask(f"c{i}"))))) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(x["seq"] for x in out) == [1, 2, 3, 4, 5], out
        assert sorted(ids(ok("list", d=d)["asks"])) == [f"c{i}" for i in range(5)]


# ------------------------------------------------------------------ list --

def test_list_shows_open_by_default_and_what_became_of_the_others():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("c", "choose"), mk_ask("w")])
        answer(d, "a", "yes")
        answer(d, "c", "b", comment="prefer b")
        s.applied("agent", ["a"], "the docs")
        s.withdraw("agent", ["w"], "not needed")
        last = s.state()["seq"]
        doc = ok("list", d=d)
        assert ids(doc["asks"]) == ["b"] and doc["open"] == 1 and doc["seq"] == last, doc
        row = doc["asks"][0]
        assert row["step"] == "confirm" and row["kind"] == "general" and row["group"] == "" \
            and row["title"] == "Is b right?" and row["status"] == "open" and row["rev"] == 1, row
        assert row["answer"] is None and row["applied"] is None and row["withdrawn"] is None
        rows = ok("list", "--status", "answered", d=d)["asks"]
        assert ids(rows) == ["a", "c"]
        a, c = rows
        assert a["answer"]["value"] == "yes" and a["answer"]["suggested"] is True \
            and a["applied"]["where"] == "the docs", a
        assert c["answer"]["value"] == "b" and c["answer"]["suggested"] is False and c["applied"] is None, c
        w = ok("list", "--status", "withdrawn", d=d)["asks"]
        assert ids(w) == ["w"] and w[0]["withdrawn"]["reason"] == "not needed"
        assert ids(ok("list", "--status", "all", d=d)["asks"]) == ["a", "b", "c", "w"]
        assert ok("list", "--status", "open", d=d)["open"] == 1


def test_list_of_an_empty_folder_is_empty_not_an_error():
    with _t.tmpdir() as d:
        assert ok("list", "--status", "all", d=d) == {"ok": True, "seq": 0, "open": 0, "asks": [], "dir": d}


# --------------------------------------------------------------- answers --

def test_answers_lists_what_waits_to_be_applied_and_then_what_was_applied():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b", "choose"), mk_ask("c", "provide"), mk_ask("waiting")])
        answer(d, "a", "yes")
        eb = answer(d, "b", "b", comment="B please")
        answer(d, "c", "42")
        doc = ok("answers", d=d)
        assert doc["open"] == 1 and doc["seq"] == s.state()["seq"] and "timed_out" not in doc, doc
        assert doc["reopened"] == [] and doc["notes"] == []
        ra, rb, rc = doc["answers"]
        assert set(ra) >= {"seq", "at", "by", "id", "step", "kind", "title", "value", "comment",
                           "suggested", "revised", "gate", "verified", "apply"}, ra
        assert (ra["id"], ra["step"], ra["by"], ra["title"], ra["value"], ra["comment"]) == \
            ("a", "confirm", "web:tester", "Is a right?", "yes", ""), ra
        assert ra["suggested"] is True and ra["revised"] is False and ra["gate"] is None \
            and ra["verified"] is True, ra
        assert rb["seq"] == eb["seq"] and rb["value"] == "b" and rb["comment"] == "B please" \
            and rb["suggested"] is False and rb["step"] == "choose", rb
        assert rc["value"] == "42" and rc["step"] == "provide"
        ok("applied", "a", "c", "--where", "the shop config", d=d)
        assert ids(ok("answers", d=d)["answers"]) == ["b"]
        everything = ok("answers", "--all", d=d)["answers"]
        assert ids(everything) == ["a", "b", "c"]
        assert everything[0]["applied"]["where"] == "the shop config" and everything[1]["applied"] is None


def test_every_answer_row_carries_apply_ready_for_the_applied_verb():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b", "choose"), mk_ask("c")])
        answer(d, "a", "yes")
        answer(d, "b", "b")
        doc = ok("answers", d=d)
        first, second = doc["answers"]
        assert first["apply"] == f"a@{first['seq']}" and second["apply"] == f"b@{second['seq']}"
        # the string goes straight in: it names the answer that was read
        assert ok("applied", first["apply"], "--where", "here", d=d)["applied"] == ["a"]
        # `wait` hands over the same rows, and `--all` keeps the key on the ones already applied
        answer(d, "c", "no")
        doc = ok("wait", "--since", "0", "--timeout", "0", d=d)
        assert [r["apply"] for r in doc["answers"]] == \
            [f"b@{second['seq']}", f"c@{s.state()['asks']['c']['answer']['seq']}"], doc
        assert [r["apply"] for r in ok("answers", "--all", d=d)["answers"]] == \
            [first["apply"], second["apply"], doc["answers"][1]["apply"]]


def test_answers_carry_the_title_that_was_shown_and_say_when_the_ask_moved_on():
    with _t.tmpdir() as d:
        s = core.Store(d)
        harness = {"verb": ["facts", "confirm", "x"], "expect": {"items": {"x": "yes"}}}
        s.post("agent", [mk_ask("x", title="Old title?", gate=harness)])
        shown = s.state()["asks"]["x"]["hash"]
        s.post("agent", [mk_ask("x", title="New title?", gate=harness)])
        gate = {"ok": True, "verb": ["facts", "confirm", "x"], "message": "written"}
        s.answer("tester", "x", "yes", shown=shown, gate=gate)     # the harness was written: recorded
        row = ok("answers", d=d)["answers"][0]
        assert row["title"] == "Old title?" and row["revised"] is True and row["gate"] == gate, row
        assert ok("list", "--status", "answered", d=d)["asks"][0]["title"] == "New title?"


def test_answers_lists_reopened_asks_and_notes_after_since():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        ea = answer(d, "a", "yes")
        answer(d, "b", "no")
        er = s.reopen("tester", "a", ea["seq"])
        en = s.note("tester", "Please skip the gift box")
        doc = ok("answers", d=d)
        assert ids(doc["answers"]) == ["b"], doc
        assert [(r["seq"], r["id"], r["title"], r["by"]) for r in doc["reopened"]] == \
            [(er["seq"], "a", "Is a right?", "web:tester")], doc
        assert [(n["seq"], n["text"], n["by"], n["verified"]) for n in doc["notes"]] == \
            [(en["seq"], "Please skip the gift box", "web:tester", True)], doc
        assert doc["reopened"][0]["at"] and doc["notes"][0]["at"]
        after_reopen = ok("answers", "--since", str(er["seq"]), d=d)
        assert after_reopen["reopened"] == [] and len(after_reopen["notes"]) == 1
        assert ok("answers", "--since", str(en["seq"]), d=d)["notes"] == []
        assert ids(ok("answers", "--since", str(er["seq"] - 1), d=d)["reopened"]) == ["a"]
        # unapplied answers are work to do, not news: `since` never hides them
        assert ids(ok("answers", "--since", "999", d=d)["answers"]) == ["b"]
        # answered again, it is no longer "reopened"
        answer(d, "a", "no")
        doc = ok("answers", d=d)
        assert doc["reopened"] == [] and ids(doc["answers"]) == ["b", "a"], doc
        # reopened and then withdrawn is not "open again"
        s.reopen("tester", "a", s.state()["asks"]["a"]["answer"]["seq"])
        s.withdraw("agent", ["a"], "changed my mind")
        assert ok("answers", d=d)["reopened"] == []


def test_a_report_shows_the_newest_20_notes_and_says_when_there_were_more():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        for i in range(20):
            s.note("tester", f"n{i}")
        doc = ok("answers", d=d)
        assert [n["text"] for n in doc["notes"]] == [f"n{i}" for i in range(20)], doc
        assert doc["notes_truncated"] is False                     # exactly 20 is all of them
        for i in range(20, 25):
            s.note("tester", f"n{i}")
        for doc in (ok("answers", d=d), ok("wait", "--since", "0", "--timeout", "0", d=d)):
            assert [n["text"] for n in doc["notes"]] == [f"n{i}" for i in range(5, 25)], doc
            assert doc["notes_truncated"] is True and doc["seq"] == s.state()["seq"]
            assert doc["notes"][0]["seq"] < doc["notes"][-1]["seq"]      # still oldest first
        # `since` counts before the cap: from the 10th note on there are 15, all shown
        tenth = next(n["seq"] for n in ok("answers", d=d)["notes"] if n["text"] == "n9")
        doc = ok("answers", "--since", str(tenth), d=d)
        assert [n["text"] for n in doc["notes"]] == [f"n{i}" for i in range(10, 25)]
        assert doc["notes_truncated"] is False
        assert ok("answers", "--since", str(s.state()["seq"]), d=d)["notes"] == []


def test_verified_is_true_false_or_null_by_the_secret():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        answer(d, "a", "yes")
        s.note("tester", "hello")

        def flags(**kw):
            doc = ok("answers", d=d, **kw)
            return doc["answers"][0]["verified"], doc["notes"][0]["verified"]
        assert flags() == (True, True)
        assert flags(env={"CONSOLE_SECRET": "x" * 32}) == (False, False)      # another secret
        real = s.secret()
        rewrite(d, lambda e: e.update(value="no") if e["type"] == "answer" else None)
        assert flags() == (False, True)                                       # the tampered one
        os.remove(os.path.join(d, "secret"))
        assert flags() == (None, None)                                        # nothing to check with
        assert flags(env={"CONSOLE_SECRET": real.decode()}) == (False, True)  # env secret is used


def test_a_too_short_console_secret_is_refused_at_once_by_every_verb_that_reads_it():
    with _t.tmpdir() as d:
        core.Store(d).post("agent", [mk_ask("a")])
        for argv in (["answers"], ["verify"], ["wait", "--timeout", "30"]):
            t0 = time.monotonic()
            doc = refused("bad_request", *argv, d=d, env={"CONSOLE_SECRET": "short"}, timeout=20)
            assert "CONSOLE_SECRET" in doc["message"], doc
            assert time.monotonic() - t0 < 15, argv               # `wait` says so before it waits


# ---------------------------------------------------------------- verify --

def test_verify_checks_every_human_event_and_says_when_it_cannot():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        ea = answer(d, "a", "yes")
        answer(d, "b", "no")
        s.reopen("tester", "a", ea["seq"])
        s.note("tester", "hello")
        doc = ok("verify", d=d)
        assert doc == {"ok": True, "checked": 4, "bad": [], "unsigned": [], "secret": True, "dir": d}, doc

        rewrite(d, lambda e: e.update(value="yes") if e["type"] == "answer" and e["id"] == "b" else None)
        bad_seq = [e["seq"] for e in s.events() if e["type"] == "answer" and e["id"] == "b"]
        code, doc = run("verify", d=d)
        assert code == 2 and doc["ok"] is False and doc["bad"] == bad_seq and doc["unsigned"] == [] \
            and doc["checked"] == 4 and doc["secret"] is True and doc["message"], (code, doc)
        assert doc["code"] == "bad_signature" and doc["params"] == {"bad": bad_seq, "unsigned": []}   # a refusal like any other

        forged = {"seq": s.state()["seq"] + 1, "at": core.now(), "by": "web:tester", "type": "note",
                  "text": "the agent wrote this"}
        with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(forged) + "\n")
        code, doc = run("verify", d=d)
        assert code == 2 and doc["unsigned"] == [forged["seq"]] and doc["bad"] == bad_seq \
            and doc["checked"] == 5, (code, doc)
        assert ok("answers", d=d)["notes"][-1]["verified"] is False      # unsigned is not verified

        os.remove(os.path.join(d, "secret"))
        refused("no_secret", "verify", d=d)                  # nothing to check with is no pass


def test_verify_with_no_secret_is_a_refusal_never_a_pass():
    with _t.tmpdir() as base:
        d = os.path.join(base, "console")
        doc = refused("no_secret", "verify", d=d)                       # a folder that is not there yet
        assert "CONSOLE_SECRET" in doc["message"]
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        refused("no_secret", "verify", d=d)                             # events, but nobody signed anything
        answer(d, "a", "yes")                                           # the human side makes the secret
        assert ok("verify", d=d)["checked"] == 1
        with open(os.path.join(d, "secret"), "w") as f:
            f.write("too short\n")
        refused("no_secret", "verify", d=d)                             # a secret that short is none
        os.remove(os.path.join(d, "secret"))
        assert sorted(os.listdir(d)) == ["events.jsonl"]                # and the check made none


def test_verify_of_an_empty_store_with_a_secret_is_ok():
    with _t.tmpdir() as d:
        assert ok("verify", d=d, env={"CONSOLE_SECRET": "s" * 32}) == {
            "ok": True, "checked": 0, "bad": [], "unsigned": [], "secret": True, "dir": d}


# ------------------------------------------------------------------ wait --

def test_wait_returns_as_soon_as_a_human_answers():
    with _t.tmpdir() as d:
        s = core.Store(d)
        seq = s.post("agent", [mk_ask("a"), mk_ask("b")])["seq"]
        timer = threading.Timer(0.8, answer, (d, "a", "yes"))
        timer.start()
        t0 = time.monotonic()
        code, doc = run("wait", "--since", str(seq), "--timeout", "20", d=d)
        took = time.monotonic() - t0
        timer.join()
        assert code == 0 and doc["timed_out"] is False and ids(doc["answers"]) == ["a"], doc
        assert 0.6 < took < 10, took
        assert set(doc) == REPORT | {"timed_out"}, doc
        assert doc["open"] == 1 and doc["seq"] == s.state()["seq"]


def test_wait_wakes_for_a_note_and_for_a_reopen_and_hands_over_the_same_shape():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        ea = answer(d, "a", "yes")
        en = s.note("tester", "one more thing")
        t0 = time.monotonic()
        doc = ok("wait", "--since", str(ea["seq"]), "--timeout", "20", d=d)
        assert doc["timed_out"] is False and [n["text"] for n in doc["notes"]] == ["one more thing"], doc
        assert ids(doc["answers"]) == ["a"]
        er = s.reopen("tester", "a", ea["seq"])
        doc = ok("wait", "--since", str(en["seq"]), "--timeout", "20", d=d)
        assert doc["timed_out"] is False and ids(doc["reopened"]) == ["a"] and doc["answers"] == [], doc
        assert doc["reopened"][0]["seq"] == er["seq"] and doc["notes"] == []
        assert time.monotonic() - t0 < 15


# ----------------------------------------------------------- team review --

def advise(d, id, stance, reason="", user="carol", on=None):
    """A view, written the way the console writes it (ask.py cannot)."""
    s = core.Store(d)
    cur = s.state()["asks"][id]
    return s.advise(user, id, on if on is not None else core.target(cur)["seq"], stance, reason)


def test_answers_and_wait_hand_over_every_view_after_since_with_what_it_was_about():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a", "choose"), mk_ask("b")])
        on_ask = advise(d, "a", "disagree", "B is what we agreed in the meeting.")
        ea = answer(d, "a", "a", comment="A it is")
        on_answer = advise(d, "a", "disagree", "Still B.", user="dan")
        agree = advise(d, "a", "agree", user="erin")
        doc = ok("answers", d=d)
        assert set(doc) == REPORT
        rows = doc["advice"]
        assert [r["seq"] for r in rows] == [on_ask["seq"], on_answer["seq"], agree["seq"]], rows
        first, second, third = rows
        assert set(first) == {"seq", "at", "by", "id", "title", "on", "on_seq", "value", "stance", "reason",
                              "current", "verified"}, first
        assert (first["on"], first["on_seq"], first["value"], first["stance"], first["current"]) == \
            ("ask", 1, "a", "disagree", False), first                  # about the suggestion; the ask has moved on since
        assert (second["on"], second["on_seq"], second["value"], second["by"], second["reason"], second["current"]) == \
            ("answer", ea["seq"], "a", "web:dan", "Still B.", True), second
        assert third["stance"] == "agree" and third["reason"] == "" and third["verified"] is True
        assert ids(doc["answers"]) == ["a"] and doc["answers"][0]["apply"] == f"a@{ea['seq']}"     # the answer still stands
        assert [r["seq"] for r in ok("answers", "--since", str(on_answer["seq"]), d=d)["advice"]] == [agree["seq"]]
        assert ok("answers", "--since", str(agree["seq"]), d=d)["advice"] == []
        many = [advise(d, "b", "agree", user=f"u{i}") for i in range(25)]    # never cut short, unlike notes
        assert len(ok("answers", "--since", str(agree["seq"]), d=d)["advice"]) == 25 and many
        ok("applied", f"a@{ea['seq']}", "--where", "the plan", d=d)
        late = advise(d, "a", "disagree", "Applied, and still wrong.")     # a view of an applied answer is heard too
        row = ok("answers", "--since", str(many[-1]["seq"]), d=d)["advice"]
        assert [(r["seq"], r["on"], r["current"]) for r in row] == [(late["seq"], "answer", True)]


def test_wait_wakes_for_a_view_and_hands_it_over():
    with _t.tmpdir() as d:
        s = core.Store(d)
        seq = s.post("agent", [mk_ask("a")])["seq"]
        timer = threading.Timer(0.8, advise, (d, "a", "disagree", "Not yet."))
        timer.start()
        t0 = time.monotonic()
        doc = ok("wait", "--since", str(seq), "--timeout", "20", d=d)
        timer.join()
        assert doc["timed_out"] is False and 0.6 < time.monotonic() - t0 < 10, doc
        assert set(doc) == REPORT | {"timed_out"} and doc["answers"] == []
        assert [(r["id"], r["stance"], r["reason"], r["on"]) for r in doc["advice"]] == [("a", "disagree", "Not yet.", "ask")]


def test_list_counts_each_asks_views_as_it_stands():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("w")])
        advise(d, "a", "disagree", "no")
        advise(d, "a", "agree", user="dan")
        advise(d, "a", "agree")                                          # carol changed her mind: her last word counts
        advise(d, "w", "disagree", "no")
        s.withdraw("agent", ["w"], "moot")
        rows = {r["id"]: r["advice"] for r in ok("list", "--status", "all", d=d)["asks"]}
        assert rows == {"a": {"agree": 2, "disagree": 0}, "b": {"agree": 0, "disagree": 0},
                        "w": {"agree": 0, "disagree": 0}}, rows
        answer(d, "a", "yes")                                            # a new thing to judge: the answer
        assert ok("list", "--status", "answered", d=d)["asks"][0]["advice"] == {"agree": 0, "disagree": 0}
        advise(d, "a", "disagree", "Wrong call.")
        assert ok("list", "--status", "answered", d=d)["asks"][0]["advice"] == {"agree": 0, "disagree": 1}


def test_digest_renders_the_decision_record_as_markdown_and_as_json():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("pick", "choose", title="Which tea | first?",
                                evidence=[{"label": "Orders", "value": "38", "source": "shop"},
                                          {"quote": "Chai *sells*", "source": "meeting"},
                                          {"table": {"caption": "Stock", "columns": ["Tea", "Weeks"],
                                                     "rows": [["Chai", "14"], ["Green|tea", "9"]]}}]),
                         mk_ask("spend", "approve"), mk_ask("gone"), mk_ask("open")])
        advise(d, "pick", "disagree", "The meeting said B.")
        first = answer(d, "pick", "b", comment="B then")
        s.reopen("alice", "pick", first["seq"])
        final = answer(d, "pick", "a")
        advise(d, "pick", "disagree", "<b>Still</b> B.", user="dan")
        ok("applied", f"pick@{final['seq']}", "--where", "the spring plan", d=d)
        answer(d, "spend", "no")
        s.withdraw("agent", ["gone"], "not needed")
        doc = ok("digest", "--format", "json", d=d)
        assert set(doc) == {"ok", "format", "seq", "asks", "dir"} and doc["format"] == "json" and doc["seq"] == s.state()["seq"]
        rec = {r["id"]: r for r in doc["asks"]}
        assert list(rec) == ["pick", "spend", "gone", "open"]
        pick = rec["pick"]
        assert (pick["status"], pick["state"], pick["title"], pick["step"]) == ("answered", "applied", "Which tea | first?", "choose")
        assert pick["evidence"][0] == {"label": "Orders", "value": "38", "source": "shop"}
        assert pick["suggestion"] == {"value": "a", "label": "A", "because": "A is safer."}
        a = pick["answer"]
        assert (a["value"], a["label"], a["by"], a["suggested"], a["verified"], a["seq"]) == ("a", "A", "web:tester", True, True, final["seq"])
        assert [(e["value"], e["comment"], e["reopened"]["by"]) for e in pick["earlier"]] == [("b", "B then", "web:alice")]
        assert [(v["by"], v["on"], v["stance"], v["current"]) for v in pick["advice"]] == \
            [("web:carol", "ask", "disagree", False), ("web:dan", "answer", "disagree", True)]
        assert pick["applied"]["where"] == "the spring plan" and pick["withdrawn"] is None
        assert (rec["spend"]["state"], rec["spend"]["answer"]["label"], rec["spend"]["answer"]["suggested"]) == ("waiting", "Reject", False)
        assert rec["spend"]["effect"] == "Something small happens."
        assert (rec["gone"]["state"], rec["gone"]["withdrawn"]["reason"], rec["gone"]["answer"]) == ("withdrawn", "not needed", None)
        assert (rec["open"]["state"], rec["open"]["answer"], rec["open"]["advice"]) == ("open", None, [])
        md = ok("digest", d=d)
        assert set(md) == {"ok", "format", "seq", "text", "dir"} and md["format"] == "md"
        assert ok("digest", "--format", "md", d=d)["text"].split("As of")[0] == md["text"].split("As of")[0]   # md is the default
        text = md["text"]
        assert text.startswith("# Decision record\n") and text.endswith("\n") and "\n\n\n" not in text
        assert "4 asks: 1 applied, 1 answered and waiting to be applied, 1 open, 1 withdrawn." in text
        assert "Signatures checked with this console's secret." in text
        heads = re.findall(r"^## (\d)\. (.*)$", text, re.M)
        assert heads == [("1", "Which tea \\| first?"), ("2", "Is spend right?"), ("3", "Is gone right?"), ("4", "Is open right?")]
        part = text.split("## 1. ")[1].split("## 2. ")[0]
        for said in ("`pick`", "**Applied**", "**Why:** Because it matters today.", "- Orders: 38 (shop)",
                     "- \u201cChai \\*sells\\*\u201d (meeting)", "| Tea | Weeks |", "| Green\\|tea | 9 |",
                     "**Options:** A · B", "**Suggested:** A \u2014 A is safer.", "**If no:** Nothing changes.",
                     "**Answer taken back:** B, by tester", "reopened", "by alice",
                     "**Answer:** A, by tester", "the suggestion",
                     "- carol disagrees about the question", ": The meeting said B.",
                     "- dan disagrees about the answer", "\\<b\\>Still\\</b\\> B.",
                     "**Applied:** the spring plan"):
            assert said in part, (said, part)
        assert "<b>" not in text                                        # what people wrote shows as words, never as markup
        assert "**Answer:** Reject, by tester" in text and "not the suggestion" in text
        assert "**If approved:** Something small happens." in text and "**Withdrawn:** not needed" in text
        assert "**Open: waiting for an answer**" in text and d not in text        # no machine path in a file that is forwarded
        os.remove(os.path.join(d, "secret"))
        assert "Signatures not checked: there is no secret here." in ok("digest", d=d)["text"]
        assert ok("digest", "--format", "json", d=d)["asks"][0]["answer"]["verified"] is None
        refused("bad_request", "digest", "--format", "html", d=d)
    with _t.tmpdir() as d:
        empty = ok("digest", d=d)
        assert "0 asks" in empty["text"] and ok("digest", "--format", "json", d=d)["asks"] == []


def test_wait_ignores_what_the_agent_itself_writes():
    with _t.tmpdir() as d:
        s = core.Store(d)
        seq = s.post("agent", [mk_ask("a")])["seq"]

        def agent_writes():
            s.post("agent", [mk_ask("b")])
            s.say("agent", "still here")
            s.withdraw("agent", ["b"], "changed my mind")
        timer = threading.Timer(0.4, agent_writes)
        timer.start()
        t0 = time.monotonic()
        code, doc = run("wait", "--since", str(seq), "--timeout", "1.5", d=d)
        timer.join()
        assert code == 0 and doc["timed_out"] is True and doc["answers"] == [], doc
        assert time.monotonic() - t0 >= 1.4
        assert doc["seq"] == s.state()["seq"] > seq


def test_wait_times_out_with_exit_0_and_the_same_shape():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        answer(d, "a", "yes")                      # said before we started waiting
        t0 = time.monotonic()
        code, doc = run("wait", "--timeout", "1", d=d)
        assert code == 0 and doc["timed_out"] is True, doc
        assert time.monotonic() - t0 >= 0.9
        assert set(doc) == REPORT | {"timed_out"}, doc
        assert ids(doc["answers"]) == ["a"]        # still waiting to be applied, so still listed
        assert ok("wait", "--timeout", "0", d=d)["timed_out"] is True        # 0 = look once
        assert ok("wait", "--since", "0", "--timeout", "0", d=d)["timed_out"] is False


def test_the_documented_loop_misses_no_answer():
    with _t.tmpdir() as d:
        added = ok("add", d=d, stdin=json.dumps([mk_ask("a"), mk_ask("b")]))
        read = ok("answers", d=d)
        assert read["answers"] == [] and read["seq"] == added["seq"]
        ea = answer(d, "a", "yes")                        # lands while the agent is busy, before its next add
        added2 = ok("add", d=d, stdin=json.dumps(mk_ask("c")))
        assert added2["seq"] > ea["seq"]
        doc = ok("wait", "--since", str(read["seq"]), "--timeout", "0", d=d)      # the seq `answers` returned
        assert doc["timed_out"] is False and ids(doc["answers"]) == ["a"], doc
        # add's own seq is already past that answer, which is why the loop does not use it
        assert ok("wait", "--since", str(added2["seq"]), "--timeout", "0", d=d)["timed_out"] is True
        # and round two: apply, then wait with the seq of the last reply
        assert ok("applied", doc["answers"][0]["apply"], "--where", "here", d=d)["applied"] == ["a"]
        answer(d, "b", "no")
        again = ok("wait", "--since", str(doc["seq"]), "--timeout", "0", d=d)
        assert again["timed_out"] is False and ids(again["answers"]) == ["b"], again


def test_the_flow_in_the_docstring_is_the_one_schema_prints():
    assert cli.FLOW in " ".join(cli.__doc__.split())
    assert "wait --since <seq from add>" not in cli.__doc__


def test_wait_survives_a_folder_that_does_not_exist_yet_and_writes_nothing():
    with _t.tmpdir() as base:
        d = os.path.join(base, "later")
        code, doc = run("wait", "--timeout", "1", d=d)
        assert code == 0 and doc["timed_out"] is True and doc["seq"] == 0 and doc["answers"] == []
        assert not os.path.exists(d)               # a wait never makes the folder

        def later():
            core.Store(d).post("agent", [mk_ask("a")])
            answer(d, "a", "yes")
        timer = threading.Timer(0.8, later)
        timer.start()
        code, doc = run("wait", "--since", "0", "--timeout", "20", d=d)
        timer.join()
        assert code == 0 and doc["timed_out"] is False and ids(doc["answers"]) == ["a"], doc


def test_wait_by_default_means_after_the_seq_at_the_start_and_polls_without_spinning():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        answer(d, "a", "yes")                                    # before the wait: must not wake it
        started, sleeps, result = threading.Event(), [], {}
        real_sleep = time.sleep

        def sleep(seconds):
            sleeps.append(seconds)
            started.set()
            real_sleep(seconds)
        time.sleep = sleep
        try:
            t = threading.Thread(target=lambda: result.update(
                doc=cli.cmd_wait(Namespace(dir=d, since=None, timeout=20))))
            t.start()
            assert started.wait(10), "wait returned for an answer that was there before it started"
            answer(d, "b", "no")
            t.join(10)
            assert not t.is_alive()
            doc = result["doc"]
            assert doc["timed_out"] is False and ids(doc["answers"]) == ["a", "b"], doc
            # and with nothing new it sleeps between looks: it is neither silent nor a busy loop
            sleeps.clear()
            t0 = time.monotonic()
            doc = cli.cmd_wait(Namespace(dir=d, since=None, timeout=1.2))
            took = time.monotonic() - t0
        finally:
            time.sleep = real_sleep
        assert doc["timed_out"] is True and took >= 1.1
        assert 2 <= len(sleeps) <= 4 and max(sleeps) <= 0.5 + 1e-9, sleeps


def test_readers_never_write():
    readers = (["list"], ["answers"], ["wait", "--timeout", "0"], ["verify"], ["schema"], ["digest"],
               ["digest", "--format", "json"])
    with _t.tmpdir() as base:
        missing = os.path.join(base, "nope")
        for argv in readers:
            assert run(*argv, d=missing)[0] == (2 if argv == ["verify"] else 0), argv     # no secret: refused
        assert not os.path.exists(missing)

        d = os.path.join(base, "real")
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        answer(d, "a", "yes")
        os.remove(os.path.join(d, "secret"))

        def snapshot():
            return {n: (os.stat(os.path.join(d, n)).st_mtime_ns, open(os.path.join(d, n), "rb").read())
                    for n in os.listdir(d)}
        before = snapshot()
        for argv in readers:
            assert run(*argv, d=d)[0] == (2 if argv == ["verify"] else 0), argv
        assert snapshot() == before                # no new file either: no secret was made


# ----------------------------------------------- applied, withdraw, say --

def test_applied_records_where_and_is_all_or_nothing():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("open")])
        answer(d, "a", "yes")
        answer(d, "b", "no")
        refused("unknown_id", "applied", "a", "nosuch", "--where", "x", d=d)
        refused("not_answered", "applied", "a", "open", "--where", "x", d=d)
        assert ids(ok("answers", d=d)["answers"]) == ["a", "b"]          # a was not applied by those
        doc = ok("applied", "a", "b", "--where", "the shop config", d=d, name="bob")
        assert doc == {"ok": True, "applied": ["a", "b"], "seq": s.state()["seq"], "dir": d}, doc
        applied = [e for e in s.events() if e["type"] == "applied"]
        assert [(e["id"], e["by"], e["where"]) for e in applied] == \
            [("a", "agent:bob", "the shop config"), ("b", "agent:bob", "the shop config")]
        refused("bad_request", "applied", "open", "--where", "w" * (core.LIMITS["where"] + 1), d=d)
        refused("bad_request", "applied", "a", "--where", " ", d=d)


def test_applied_with_a_seq_names_the_answer_that_was_read_and_refuses_a_newer_one():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("c")])
        first = answer(d, "a", "yes")
        answer(d, "b", "no")
        answer(d, "c", "yes")
        read = ok("answers", d=d)["answers"][0]["apply"]
        assert read == f"a@{first['seq']}"
        # the owner takes it back and answers again after the agent read it
        s.reopen("tester", "a", first["seq"])
        second = answer(d, "a", "no")
        before = logged(d)
        doc = refused("changed", "applied", read, "--where", "x", d=d)
        assert doc["params"] == {"id": "a"}, doc
        assert logged(d) == before
        # all or nothing: a good ID@SEQ beside a stale one writes neither
        good_b = f"b@{s.state()['asks']['b']['answer']['seq']}"
        refused("changed", "applied", good_b, read, "--where", "x", d=d)
        assert logged(d) == before
        refused("changed", "applied", "c@1", "--where", "x", d=d)             # a seq no answer has
        refused("changed", "applied", "c@0", "--where", "x", d=d)
        refused("unknown_id", "applied", f"nosuch@{second['seq']}", "--where", "x", d=d)
        assert logged(d) == before
        # the fresh row works; with and without a seq in one call; the output is the ids
        fresh = next(r["apply"] for r in ok("answers", d=d)["answers"] if r["id"] == "a")
        assert fresh == f"a@{second['seq']}"
        doc = ok("applied", fresh, "b", f"c@{s.state()['asks']['c']['answer']['seq']}",
                 "--where", "the shop config", d=d)
        assert doc == {"ok": True, "applied": ["a", "b", "c"], "seq": s.state()["seq"], "dir": d}, doc
        assert [(e["id"], e["where"]) for e in s.events() if e["type"] == "applied"] == \
            [("a", "the shop config"), ("b", "the shop config"), ("c", "the shop config")]
        assert s.state()["problems"] == []
        refused("already_applied", "applied", fresh, "--where", "again", d=d)


def test_applied_reads_the_last_at_sign_as_the_seq_and_nothing_else_after_it():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a")])
        e = answer(d, "a", "yes")
        # a token that is not ID or ID@SEQ is a bad request before anything is looked up
        for token in ("a@", "@5", "@", "a@x", "a@-1", "a@+1", "a@1.5", "a@1e3", "a@ 1", "a@0x1",
                      "a@\u0663", "a@" + "9" * 16, "a@" + "9" * 5000):
            refused("bad_request", "applied", token, "--where", "x", d=d)
        # the last '@' splits: an id with an '@' in it names no ask
        assert refused("unknown_id", "applied", f"a@1@{e['seq']}", "--where", "x", d=d)["params"] == {"id": "a@1"}
        assert ids(ok("answers", d=d)["answers"]) == ["a"]                     # nothing was applied
        # leading zeros are still decimal
        assert ok("applied", f"a@{e['seq']:03d}", "--where", "x", d=d)["applied"] == ["a"]


def test_the_same_id_twice_needs_the_same_seq_and_counts_once():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        ea = answer(d, "a", "yes")
        answer(d, "b", "yes")
        for tokens in ([f"a@{ea['seq']}", "a"], ["a", f"a@{ea['seq']}"], [f"a@{ea['seq']}", f"a@{ea['seq'] + 1}"]):
            doc = refused("bad_request", "applied", *tokens, "--where", "x", d=d)
            assert "twice" in doc["message"], doc
        assert ids(ok("answers", d=d)["answers"]) == ["a", "b"]
        doc = ok("applied", f"a@{ea['seq']}", f"a@{ea['seq']}", "b", "b", "--where", "x", d=d)
        assert doc["applied"] == ["a", "b"] and [e["type"] for e in s.events()].count("applied") == 2


def test_the_default_name_is_main_and_the_flag_changes_it():
    with _t.tmpdir() as d:
        s = core.Store(d)
        ok("add", d=d, stdin=json.dumps([mk_ask("a"), mk_ask("b"), mk_ask("c")]))
        answer(d, "a", "yes")
        ok("applied", "a", "--where", "here", d=d)
        ok("withdraw", "b", "--reason", "gone", d=d)
        ok("say", "hello", d=d)
        assert {e["by"] for e in s.events() if e["type"] in core.ROLES["agent"]} == {"agent:main"}
        ok("say", "and I am someone else", d=d, name="helper")
        assert s.state()["messages"][-1]["by"] == "agent:helper"


def test_withdraw_takes_open_asks_back_all_or_nothing():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b"), mk_ask("c")])
        answer(d, "c", "yes")
        refused("not_open", "withdraw", "a", "c", "--reason", "gone", d=d)
        refused("unknown_id", "withdraw", "a", "nosuch", "--reason", "gone", d=d)
        assert ids(ok("list", d=d)["asks"]) == ["a", "b"]                 # nothing was withdrawn
        doc = ok("withdraw", "a", "b", "--reason", "gone", d=d)
        assert doc == {"ok": True, "withdrawn": ["a", "b"], "seq": s.state()["seq"], "dir": d}, doc
        assert s.state()["asks"]["a"]["withdrawn"]["reason"] == "gone"
        refused("bad_request", "withdraw", "c", "--reason", "r" * (core.LIMITS["reason"] + 1), d=d)


def test_the_same_id_twice_is_one_event():
    with _t.tmpdir() as d:
        s = core.Store(d)
        s.post("agent", [mk_ask("a"), mk_ask("b")])
        answer(d, "a", "yes")
        ok("applied", "a", "a", "--where", "here", d=d)
        ok("withdraw", "b", "b", "--reason", "gone", d=d)
        events = s.events()
        assert [e["type"] for e in events].count("applied") == 1
        assert [e["type"] for e in events].count("withdraw") == 1
        assert s.state()["problems"] == []


def test_say_is_a_message_on_top_of_the_inbox_and_the_name_is_logged():
    with _t.tmpdir() as d:
        s = core.Store(d)
        doc = ok("say", "Back at 3 pm.", d=d, name="bob")
        assert doc == {"ok": True, "seq": 1, "dir": d}
        msg = s.state()["messages"][-1]
        assert (msg["type"], msg["by"], msg["text"]) == ("say", "agent:bob", "Back at 3 pm."), msg
        ok("say", "and the default name", d=d)
        assert s.state()["messages"][-1]["by"] == "agent:main"
        refused("bad_request", "say", "x" * (core.LIMITS["say"] + 1), d=d)
        refused("bad_request", "say", " ", d=d)
        refused("bad_request", "say", "hi", d=d, name="not a token")
        refused("bad_request", "say", "hi", d=d, name="-x")
        assert len(s.events()) == 2


def test_a_damaged_log_is_refused_and_a_torn_last_line_is_not_an_event():
    with _t.tmpdir() as d:
        path = os.path.join(d, "events.jsonl")
        good = json.dumps({"seq": 1, "at": core.now(), "by": "agent:a", "type": "say", "text": "hi"})
        put(d, "events.jsonl", "not json\n" + good + "\n")
        for argv in (["list"], ["answers"], ["wait", "--timeout", "0"], ["verify"]):
            assert refused("corrupt_log", *argv, d=d, env={"CONSOLE_SECRET": "s" * 32})["params"]["line"] == 1
        put(d, "events.jsonl", good + "\n" + '{"seq": 2, "ty')             # a write that never finished
        assert ok("list", d=d)["seq"] == 1
        assert open(path).read().endswith('"ty')                           # and reading did not touch it


def test_a_nan_in_the_log_is_refused_and_never_printed():
    with _t.tmpdir() as d:
        core.Store(d).post("agent", [mk_ask("a")])
        answer(d, "a", "yes")
        path = os.path.join(d, "events.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        lines[-1] = json.dumps({**json.loads(lines[-1]), "gate": "@@"}).replace('"@@"', "NaN")
        put(d, "events.jsonl", "\n".join(lines) + "\n")
        for argv in (["answers"], ["wait", "--timeout", "0"]):
            refused("corrupt_log", *argv, d=d)                    # go() reads stdout as strict JSON


# --------------------------------------------------------- schema, examples --

def test_schema_is_cores_tables_plus_the_examples():
    with _t.tmpdir() as d:
        doc = ok("schema", d=d)
        assert doc["steps"] == list(core.STEPS) and doc["limits"] == core.LIMITS \
            and doc["max_open"] == core.MAX_OPEN
        assert set(doc["fields"]) == set(core.ASK_FIELDS)
        for f, (required, allowed) in core.ASK_FIELDS.items():
            def steps(v):
                return list(core.STEPS) if v == "all" else list(v or ())
            row = doc["fields"][f]
            assert row["required_for"] == steps(required), f
            assert row["allowed_for"] == steps(allowed), f
            assert row["about"].strip(), f
            assert row.get("limit") == core.LIMITS.get(f), f
        assert doc["flow"] == "add -> answers -> apply -> applied -> wait --since <the seq answers returned>"
        assert doc["flow"] == cli.FLOW
        assert doc["answer_values"]
        assert list(doc["examples"]) == list(core.STEPS)
        for step, example in doc["examples"].items():
            assert example["step"] == step
            with open(os.path.join(EXAMPLES, {"confirm": "confirm-word", "approve": "approve-spend",
                                              "choose": "choose-priority",
                                              "provide": "provide-number"}[step] + ".json"),
                      encoding="utf-8") as f:
                assert example == json.load(f), step


EXAMPLE_FILES = ("confirm-word.json", "choose-priority.json", "provide-number.json", "approve-spend.json")


def load_example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return json.load(f)


def test_the_examples_are_exactly_the_four_and_all_valid_asks():
    assert sorted(n for n in os.listdir(EXAMPLES) if n.endswith(".json")) == sorted(EXAMPLE_FILES)
    for name in EXAMPLE_FILES:
        raw = load_example(name)
        ask, errors = core.validate_ask(raw)
        assert errors == [] and ask, (name, errors)
        assert set(raw) <= set(ask) and ask["title"] == raw["title"], name
    with _t.tmpdir() as d:                            # and they fit the budget together
        docs = [load_example(n) for n in EXAMPLE_FILES]
        doc = ok("add", d=d, stdin=json.dumps(docs))
        assert len(doc["posted"]) == 4 and doc["open"] == 4, doc


def test_the_examples_are_good_asks_to_copy():
    everything = ""
    for name in EXAMPLE_FILES:
        a = load_example(name)
        everything += json.dumps(a)
        assert a["title"].endswith("?") and "\n" not in a["title"], name       # a question, one line
        assert a["why"].strip() and a["if_no"].strip() and a["recommend"]["because"].strip(), name
        assert a["kind"] and a["group"], name
        facts = [i for i in a["evidence"] if "table" not in i]
        assert facts and all(i.get("source") for i in facts), name           # evidence says where from
        for i in a["evidence"]:                                              # a table has no source field: its caption says
            assert "table" not in i or "Source: " in i["table"]["caption"], name
        assert not re.search(r"https?://|@|\bB0[0-9A-Z]{8}\b", json.dumps(a)), name
    assert "Northwind Tea Co." in everything

    confirm = load_example("confirm-word.json")
    quote = [i for i in confirm["evidence"] if "quote" in i]
    assert len(quote) == 1 and quote[0]["source"] == "meeting 2026-03-02, 12:31"
    assert confirm["recommend"]["value"] in ("yes", "no") and "gate" not in confirm

    choose = load_example("choose-priority.json")
    assert len(choose["options"]) == 3
    assert choose["recommend"]["value"] in {o["value"] for o in choose["options"]}
    assert all(o.get("note") for o in choose["options"])

    provide = load_example("provide-number.json")
    assert provide["gate"] == {"verb": ["facts", "confirm", "unit_cost"], "value_arg": True,
                               "expect": {"items": {"unit_cost": "$value"}}}
    assert provide["input"]["type"] == "number" and {"min", "max", "unit"} <= set(provide["input"])
    ask, _ = core.validate_ask(provide)
    value, err = core.check_value(ask, provide["recommend"]["value"])
    assert err is None and core.gate_runs(ask, value)
    assert core.expect_for(ask, value) == {"items": {"unit_cost": value}}

    approve = load_example("approve-spend.json")
    assert approve["effect"].strip()
    tables = [i["table"] for i in approve["evidence"] if "table" in i]
    assert len(tables) == 1 and len(tables[0]["rows"]) == 3 and tables[0]["caption"]


# ---------------------------------------------------------- the boundary --

def test_the_agent_side_has_no_way_to_answer_reopen_note_or_sign():
    with open(ASK, encoding="utf-8") as f:
        source = f.read()
    for forbidden in (".answer(", ".reopen(", ".note(", ".advise(", "ensure_secret", "sign("):
        assert forbidden not in source, forbidden
    with _t.tmpdir() as d, _t.tmpdir() as files:       # and no verb, run on its own, leaves one behind
        f = put(files, "a.json", [mk_ask("a"), mk_ask("b")])
        ok("add", f, d=d)
        ok("say", "hello", d=d)
        ok("withdraw", "b", "--reason", "gone", d=d)
        for argv in (["list"], ["answers"], ["wait", "--timeout", "0"], ["schema"], ["digest"]):
            ok(*argv, d=d)
        refused("no_secret", "verify", d=d)
        assert os.listdir(d) == ["events.jsonl"], os.listdir(d)              # no secret was made
        assert {e["type"] for e in core.Store(d).events()} <= set(core.ROLES["agent"])


def test_it_is_a_stdlib_only_pep_723_script():
    with open(ASK, encoding="utf-8") as f:
        source = f.read()
    lines = source.splitlines()
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    block = source.split("# /// script")[1].split("# ///")[0]
    assert 'requires-python = ">=3.11"' in block and "dependencies = []" in block
    assert os.access(ASK, os.X_OK)
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported - {"core"} <= set(sys.stdlib_module_names), imported


if __name__ == "__main__":
    _t.main(globals())
