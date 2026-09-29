"""intake_to_asks.py: picked intake items become asks the real console takes.
Every ask it makes is posted with console/ask.py in a folder of its own, so a
field the console would refuse fails here. Each test names the rule it guards."""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

ASKABLE = ["w1", "w2", "w3", "s1", "s2", "s3", "n1", "n2", "n3", "n4", "n5",
           "q1", "q2", "q3", "q4", "q6"]          # g* are not asked; q5 has no suggestion
M1 = _t.load(_t.FIX1)


def convert(picks, *extra, intake=(_t.FIX1, _t.FIX2)):
    return _t.tool("intake_to_asks.py", "--intake", *intake, "--pick", ",".join(picks), *extra)


def by_id(asks):
    return {a["id"]: a for a in asks}


def all_asks():
    out = []
    for batch in (ASKABLE[:8], ASKABLE[8:]):
        code, asks = convert(batch)
        assert code == 0 and isinstance(asks, list), asks
        out += asks
    return by_id(out)


def ev_text(ask):
    return json.dumps(ask["evidence"], ensure_ascii=False)


# ------------------------------------------------------------------ rules --

def test_every_askable_item_becomes_an_ask_the_real_console_accepts():
    for batch in (ASKABLE[:8], ASKABLE[8:]):
        code, asks = convert(batch)
        assert code == 0 and [a["id"] for a in asks] == [f"intake-{i}" for i in batch], asks
        with _t.tmpdir() as d:
            code, doc = _t.ask(d, "add", "-", stdin=json.dumps(asks))
            assert code == 0 and doc["posted"] == [a["id"] for a in asks], doc
            code, listed = _t.ask(d, "list")
            assert [r["kind"] for r in listed["asks"]] == [a["kind"] for a in asks]


def test_every_ask_carries_evidence_with_a_source_a_recommendation_with_because_and_if_no():
    for i, a in all_asks().items():
        assert a["evidence"] and all(e.get("source") for e in a["evidence"] if "quote" in e), i
        assert a["evidence"][0].get("source"), i                # the first item says where it was said
        assert str(a["recommend"]["value"]).strip() and a["recommend"]["because"].strip(), i
        assert a["if_no"].strip() and a["why"].strip(), i
        assert len(a["title"]) <= 120 and a["kind"] in ("word", "story", "number", "question"), i


def test_a_word_is_a_confirm_that_names_the_word_and_its_meaning_and_quotes_the_client():
    a = all_asks()["intake-w1"]
    assert a["step"] == "confirm" and a["kind"] == "word" and a["group"] == "Words we use"
    assert a["title"] == 'Call it "sampler", meaning a 50 g pouch of one tea, sold to try it?', a["title"]
    assert a["evidence"][0] == {"quote": "The little 50 gram pouches, we call those samplers.",
                                "source": "meeting 2026-03-02 00:12:31"}
    assert "A 50 g pouch of one tea, sold to try it." in a["why"]          # the meaning, whole
    assert a["recommend"]["value"] == "yes"
    w3 = all_asks()["intake-w3"]                                           # unsure: said, and why
    assert "said once; it may mean the front page only" in ev_text(w3)
    assert "not sure" in w3["recommend"]["because"]


def test_a_story_is_a_confirm_with_want_so_that_done_when_and_the_human_step():
    a = all_asks()["intake-s2"]
    assert a["step"] == "confirm" and a["kind"] == "story"
    assert a["title"] == 'Build this: "I want to approve each push before its ads start"?'
    assert a["why"] == "I want to approve each push before its ads start, so that no push spends money I did not agree to."
    labels = [(e.get("label"), e.get("value")) for e in a["evidence"]]
    assert ("Done when ①", "the push shows its daily cap and expected spend") in labels
    assert ("Done when ③", "I can stop it at any time") in labels
    assert ("Your step", "approve: you approve each action before it happens") in labels
    assert a["evidence"][0]["quote"] == "Nothing should start spending before I say yes."


def test_a_number_is_a_provide_with_its_unit_the_quote_and_the_quoted_value():
    asks = all_asks()
    a = asks["intake-n1"]
    assert a["step"] == "provide" and a["input"] == {"type": "number", "unit": "days"}
    assert a["recommend"]["value"] == "45" and a["evidence"][0]["source"] == "meeting 2026-03-02 00:16:20"
    assert ("What it applies to", "the time from ordering Sencha to having it in the warehouse") in \
        [(e.get("label"), e.get("value")) for e in a["evidence"]]
    r = asks["intake-n2"]                                  # a range: its low end, and the range is shown
    assert r["recommend"]["value"] == "20" and "20–30 USD per day" in ev_text(r)
    assert r["input"] == {"type": "number", "unit": "USD per day"}
    with _t.tmpdir() as d:                                 # a unit too long for the input still reaches the owner
        doc = copy.deepcopy(M1)
        next(x for x in doc["numbers"] if x["iid"] == "n3")["unit"] = "weeks of stock left"
        p = _t.save(os.path.join(d, "m.json"), doc)
        code, asks = convert(["n3"], intake=(p, _t.FIX2))
        assert code == 0 and asks[0]["input"] == {"type": "number"}, asks
        assert "weeks of stock left" in ev_text(asks[0]) and "weeks of stock left" in asks[0]["title"]
        code, out = _t.ask(os.path.join(d, "c"), "add", "-", stdin=json.dumps(asks))
        assert code == 0, out


def test_a_question_is_a_choose_when_it_lists_options_else_a_provide_and_shows_both_sides():
    asks = all_asks()
    q1 = asks["intake-q1"]
    assert q1["step"] == "choose" and [o["value"] for o in q1["options"]] == ["USD", "EUR"]
    assert q1["recommend"] == {"value": "USD", "because": "The ad account bills in USD."}
    q2 = asks["intake-q2"]                                 # a contradiction: both quotes side by side
    quotes = [e.get("quote") for e in q2["evidence"]]
    assert "From the day we order, Sencha takes forty-five days to reach the warehouse." in quotes
    assert "In the rainy season it is more like sixty days." in quotes
    q3 = asks["intake-q3"]
    assert q3["step"] == "provide" and q3["input"] == {"type": "text"}
    assert q3["recommend"]["value"] == "Nobody: pushes wait until I am back"
    q4 = asks["intake-q4"]
    assert q4["evidence"][0]["source"] == "doc:kickoff-brief.pdf#page 2"


def test_more_picks_than_max_are_refused_and_max_can_only_lower_the_budget():
    code, doc = convert(ASKABLE[:11])
    assert code == 2 and doc["code"] == "too_many" and "ok" in doc and doc["ok"] is False, doc
    code, doc = convert(["w1", "w2", "w3"], "--max", "2")
    assert code == 2 and doc["code"] == "too_many"
    assert convert(["w1", "w2"], "--max", "2")[0] == 0
    for n in ("11", "0", "-1"):
        code, doc = convert(["w1"], "--max", n)
        assert code == 2 and doc["code"] == "bad_request", n
    code, doc = _t.tool("intake_to_asks.py", "--pick", "w1")          # a missing flag is one document too
    assert code == 2 and doc["code"] == "bad_request"


def test_unknown_repeated_goal_and_unsuggested_picks_are_refused():
    for picks, want in ((["w1", "x9"], "unknown_iid"), (["w1", "w1"], "duplicate_pick"),
                        (["g1"], "not_askable"), (["q5"], "no_suggestion"), ([""], "bad_request")):
        code, doc = convert(picks)
        assert code == 2 and isinstance(doc, dict) and doc["code"] == want, (picks, doc)


def test_an_intake_the_checker_does_not_pass_is_refused_with_its_problems():
    with _t.tmpdir() as d:
        doc = copy.deepcopy(M1)
        doc["numbers"][0].pop("quote")
        p = _t.save(os.path.join(d, "m.json"), doc)
        code, out = convert(["w1"], intake=(p, _t.FIX2))
        assert code == 2 and out["code"] == "intake_invalid", out
        assert ("n1", "no_quote") in [(x["iid"], x["code"]) for x in out["problems"]]


def test_each_person_answers_in_a_console_of_their_own():
    code, doc = convert(["w1", "q4"], "--audience", "client")
    assert code == 2 and doc["code"] == "wrong_audience" and [x["iid"] for x in doc["problems"]] == ["q4"]
    code, asks = convert(["q4"], "--audience", "builder")
    assert code == 0 and asks[0]["id"] == "intake-q4"


def test_what_is_settled_in_the_console_is_not_asked_again_and_the_open_budget_holds():
    with _t.tmpdir() as d:
        code, asks = convert(["w1", "w2"])
        assert _t.ask(d, "add", "-", stdin=json.dumps(asks))[0] == 0
        _t.owner_answers(d, {"intake-w1": "yes"})
        code, doc = convert(["w1"], "--console-dir", d)
        assert code == 2 and doc["code"] == "already_asked", doc
        code, again = convert(["w2"], "--console-dir", d)               # still open: posting it again revises it
        assert code == 0 and again[0]["id"] == "intake-w2"
        code, more = convert(["s1", "s2", "s3", "n1", "n2", "n3", "n4", "n5"], "--console-dir", d)
        assert code == 0 and _t.ask(d, "add", "-", stdin=json.dumps(more))[0] == 0   # 9 open now
        code, doc = convert(["q1", "q2"], "--console-dir", d)
        assert code == 2 and doc["code"] == "over_budget", doc
        assert convert(["q1"], "--console-dir", d)[0] == 0
        code, doc = convert(["q1"], "--console-dir", os.path.join(d, "nowhere"), "--ask", os.path.join(d, "none.py"))
        assert code == 2 and doc["code"] == "console_unreadable"


def test_it_writes_nothing():
    with _t.tmpdir() as d:
        before = _t.snapshot(_t.FIXTURES)
        code, _ = _t.tool("intake_to_asks.py", "--intake", _t.FIX1, _t.FIX2, "--pick", "w1,n1", cwd=d)
        assert code == 0 and os.listdir(d) == [] and _t.snapshot(_t.FIXTURES) == before


if __name__ == "__main__":
    _t.main(globals())
