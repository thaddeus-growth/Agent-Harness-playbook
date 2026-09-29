"""digest.py: the decision record a team lead forwards. One console holds an
ask in every state; the record must show each, through ask.py only. Consoles
that do not exist here (an older one without `digest`, one from before team
review was removed) are played by fake_ask.py around the real ask.py. Each test names the
rule it guards and would fail if that rule were removed."""

from __future__ import annotations

import ast
import json
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

FAKE = os.path.join(_t.HERE, "fake_ask.py")


@contextmanager
def console():
    """A console with an ask in every state; yields (folder, the posted asks file)."""
    with _t.tmpdir() as d:
        code, asks = _t.tool("intake_to_asks.py", "--intake", _t.FIX1, _t.FIX2, "--pick", "w1,n1,s2,w3,q1")
        assert code == 0, asks
        posted = _t.save(os.path.join(d, "asks.json"), asks)
        con = os.path.join(d, "con")
        assert _t.ask(con, "add", posted)[0] == 0
        _t.owner_answers(con, {"intake-w1": "yes", "intake-n1": ("50", "it got slower"), "intake-q1": "EUR"})
        assert _t.ask(con, "applied", "intake-w1", "--where", 'glossary: "sampler" (ssot/glossary.tsv)')[0] == 0
        assert _t.ask(con, "withdraw", "intake-w3", "--reason", "asked in chat instead")[0] == 0
        yield con, posted


def digest(con, *args, env=None):
    return _t.tool("digest.py", "--console-dir", con, *args, env=env, raw=True)


def section(md, title_start):
    part = md[md.index("### ") :]
    block = next(b for b in part.split("\n### ") if title_start in b.split("\n")[0])
    return block


# ------------------------------------------------------------------ rules --

def test_every_ask_is_in_the_record_whatever_its_status():
    with console() as (con, posted):
        code, md = digest(con, "--asks", posted, "--title", "Northwind Tea Co.: round 1")
        assert code == 0 and md.startswith("# Northwind Tea Co.: round 1\n"), md
        assert "5 asks: 3 answered (1 as suggested, 2 not), 1 waiting, 1 withdrawn; 1 applied." in md, md
        for title in ('Call it "sampler"', 'Call it "push"', "What is the time from ordering Sencha",
                      'Build this: "I want to approve each push', "Which currency is the daily cap in?"):
            assert title in md, title
        assert "- **Withdrawn:** asked in chat instead," in section(md, 'Call it "push"')
        assert "- **Answer:** none yet; asked" in section(md, "Build this")
        assert "- **Applied:** not yet." in section(md, "Which currency")


def test_an_answer_names_its_value_who_when_and_whether_it_took_the_suggestion():
    with console() as (con, posted):
        code, md = digest(con, "--asks", posted)
        w1 = section(md, 'Call it "sampler"')
        assert "by mara, " in w1 and " UTC. Took the suggestion." in w1, w1
        assert '- **Applied:** glossary: "sampler" (ssot/glossary.tsv),' in w1
        n1 = section(md, "What is the time")
        assert "Did not take the suggestion." in n1 and "- **Suggested:** 45. It is the number you said" in n1, n1
        assert "- **Comment:** it got slower" in n1
        q1 = section(md, "Which currency")
        assert "EUR" in q1 and "Did not take the suggestion." in q1


def test_the_evidence_and_the_suggestion_are_shown_from_the_console_or_the_posted_asks():
    with console() as (con, posted):
        for env, extra, origin in (({}, ["--asks", posted], None),                          # whatever this console has
                                   ({"FAKE_ASK_MODE": "old"}, ["--asks", posted], "posted file")):
            args = ["--ask", FAKE] if env else []
            code, md = digest(con, *args, *extra, env=env)
            assert code == 0, md
            w1 = section(md, 'Call it "sampler"')
            assert '"The little 50 gram pouches, we call those samplers." (meeting 2026-03-02 00:12:31)' in w1, w1
            assert "- **Suggested:** yes. It is the word you used in the meeting on 2026-03-02." in w1
            code, doc = _t.tool("digest.py", "--console-dir", con, "--format", "json", *args, *extra, env=env)
            if origin:
                assert {a["details_from"] for a in doc["asks"]} == {origin}, doc
        code, md = digest(con, "--ask", FAKE, env={"FAKE_ASK_MODE": "old"})        # an old console, no --asks
        assert code == 0 and "5 asks show no evidence or suggestion" in md and "**Evidence:**" not in md, md


def test_json_is_the_same_record_as_data():
    with console() as (con, posted):
        code, doc = _t.tool("digest.py", "--console-dir", con, "--format", "json", "--asks", posted)
        assert code == 0 and doc["ok"] is True and isinstance(doc["seq"], int), doc
        assert doc["counts"] == {"asks": 5, "answered": 3, "took_suggestion": 1, "overrode": 2, "open": 1,
                                 "withdrawn": 1, "applied": 1, "without_details": 0}, doc["counts"]
        a = {x["id"]: x for x in doc["asks"]}
        assert a["intake-n1"]["answer"]["value"] == "50" and a["intake-n1"]["answer"]["by"] == "web:mara"
        assert a["intake-n1"]["answer"]["suggested"] is False and a["intake-n1"]["recommend"]["value"] == "45"
        assert a["intake-w1"]["applied"]["where"] == 'glossary: "sampler" (ssot/glossary.tsv)'
        assert a["intake-w3"]["withdrawn"]["reason"] == "asked in chat instead"
        assert a["intake-s2"]["answer"] is None and a["intake-s2"]["status"] == "open"


def test_a_console_that_still_gives_advice_is_read_and_the_advice_is_left_out():
    with console() as (con, posted):
        code, md = digest(con, "--asks", posted, "--ask", FAKE, env={"FAKE_ASK_MODE": "legacy"})
        assert code == 0 and "Advice" not in md and "rainy season" not in md and "disagree" not in md, md
        code, out = _t.tool("digest.py", "--console-dir", con, "--ask", FAKE, "--format", "json", env={"FAKE_ASK_MODE": "legacy"})
        assert code == 0 and all("advice" not in x for x in out["asks"])


def test_text_from_the_log_cannot_break_the_page():
    with _t.tmpdir() as d:
        code, asks = _t.tool("intake_to_asks.py", "--intake", _t.FIX1, _t.FIX2, "--pick", "q3")
        assert _t.ask(d, "add", "-", stdin=json.dumps(asks))[0] == 0
        evil = "<script>x</script> | [click](https://phish.example.com) `code`"
        _t.owner_answers(d, {"intake-q3": (evil, "line one\n# not a heading\n| a | b |")})
        code, md = digest(d)
        assert code == 0 and "<script>" not in md and "[click](" not in md, md
        assert "\\<script\\>" in md and "\\[click\\]" in md
        assert "\n# not a heading" not in md and "\n| a" not in md
        rows = [ln for ln in md.splitlines() if ln.startswith("| 1 |")]
        assert len(rows) == 1 and rows[0].count(" | ") == 4, rows            # one table row, five cells


def test_the_log_is_read_only_through_ask_py():
    src = _t.read(os.path.join(_t.BUILD, "digest.py"))
    tree = ast.parse(src)
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "core" not in imports and "events.jsonl" not in src
    with console() as (con, posted):
        log = os.path.join(os.path.dirname(con), "calls.log")
        code, md = digest(con, "--ask", FAKE, env={"FAKE_ASK_LOG": log})
        calls = [json.loads(ln) for ln in _t.read(log).splitlines()]
        assert [c[2:] for c in calls][:2] == [["list", "--status", "all"], ["answers", "--all"]], calls
        assert all(c[:2] == ["--dir", con] for c in calls) and len(calls) <= 3, calls


def test_no_folder_and_an_unreadable_console_are_refused_as_one_document():
    code, out = _t.tool("digest.py")
    assert code == 2 and out["code"] == "no_dir", out
    with console() as (con, posted):
        code, out = _t.tool("digest.py", "--console-dir", con, "--ask", FAKE, env={"FAKE_ASK_MODE": "garbage"})
        assert code == 2 and out["code"] == "console_unreadable", out
        code, out = _t.tool("digest.py", "--console-dir", con, "--asks", os.path.join(con, "none.json"))
        assert code == 2 and out["code"] == "bad_request", out


if __name__ == "__main__":
    _t.main(globals())
