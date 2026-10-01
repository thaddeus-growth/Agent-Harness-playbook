"""check_intake.py: no quote, no item. Each test names the rule it guards and
would fail if that rule were removed. The fixture is a fictional tea shop over
two meetings; each test breaks one thing in a copy of it."""

from __future__ import annotations

import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

sys.path.insert(0, _t.BUILD)
import check_intake as ci  # noqa: E402

M1, M2 = _t.load(_t.FIX1), _t.load(_t.FIX2)


def check(*docs):
    """Write the documents to a folder, run the checker on them: (exit, report)."""
    with _t.tmpdir() as d:
        paths = []
        for n, doc in enumerate(docs):
            p = os.path.join(d, f"m{n}.intake.json")
            if isinstance(doc, str):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(doc)
            else:
                _t.save(p, doc)
            paths.append(p)
        return _t.tool("check_intake.py", *paths)


def codes(report, iid=None):
    return {p["code"] for p in report["problems"] if iid is None or p["iid"] == iid}


def item(doc, bucket, iid):
    return next(x for x in doc[bucket] if x["iid"] == iid)


def broken(fn, doc=M1):
    d = copy.deepcopy(doc)
    fn(d)
    return d


# ------------------------------------------------------------------ rules --

def test_the_fixture_passes_and_the_answer_is_one_json_document():
    code, doc = _t.tool("check_intake.py", _t.FIX1, _t.FIX2)
    assert code == 0 and doc == {"ok": True, "files": 2, "items": 20, "problems": []}, doc


def test_no_file_named_and_a_file_that_is_not_json_are_refused_not_crashed():
    code, doc = _t.tool("check_intake.py")
    assert code == 2 and doc["code"] == "bad_request"
    code, doc = check("{ not json")
    assert code == 2 and codes(doc) == {"bad_json"} and doc["problems"][0]["iid"] is None, doc
    code, doc = check("{ not json", M2)                     # the other file still checked: its refs now dangle
    assert code == 2 and codes(doc) == {"bad_json", "unknown_ref"}, doc
    code, doc = check('{"meeting": NaN}')                  # NaN is not JSON, and never a number
    assert code == 2 and "bad_json" in codes(doc)


def test_the_shape_is_the_schema_and_every_problem_names_iid_code_and_message():
    d = broken(lambda d: item(d, "words", "w1").pop("meaning"))
    code, r = check(d)
    assert code == 2 and codes(r, "w1") == {"missing_field"}, r
    d = broken(lambda d: item(d, "words", "w2").update(wrod="x"))
    assert codes(check(d)[1], "w2") == {"unknown_field"}           # a typo surfaces, never vanishes
    d = broken(lambda d: item(d, "stories", "s1").update(audience="team", human_step="maybe"))
    assert codes(check(d)[1], "s1") == {"bad_value"}
    d = broken(lambda d: item(d, "stories", "s1").update(done_when=[]))
    assert codes(check(d)[1], "s1") == {"too_short"}
    d = broken(lambda d: d.pop("questions"), M2)
    code, r = check(d)
    assert [(p["iid"], p["code"]) for p in r["problems"]] == [(None, "missing_field")], r
    for p in r["problems"] + check(broken(lambda d: item(d, "words", "w1").pop("meaning")))[1]["problems"]:
        assert set(p) >= {"iid", "code", "message"} and p["code"] in ci.CODES, p


def test_every_meeting_has_a_consent_line():
    d = broken(lambda d: d["meeting"].pop("consent"))
    assert codes(check(d)[1]) == {"no_consent"}
    d = broken(lambda d: d["meeting"].update(consent="  "))
    assert codes(check(d)[1]) == {"no_consent"}
    d = broken(lambda d: d["meeting"].update(consent=""))
    assert codes(check(d)[1]) == {"no_consent"}
    # BUILD.md B0, meeting-intake.md and the schema say: a line that says consent was not given or not recorded
    # passes (the owner is then asked); only a blank line is refused
    for said in ("not given", "not recorded", "not given: the client declined at 00:00:12"):
        d = broken(lambda d: d["meeting"].update(consent=said))
        assert check(d)[0] == 0, said


def test_every_item_has_a_source_a_timestamp_on_a_real_date_or_a_document():
    for bucket, iid in (("goals", "g2"), ("words", "w1"), ("stories", "s1"), ("numbers", "n3"), ("questions", "q3")):
        d = broken(lambda d: item(d, bucket, iid).pop("source"))
        assert codes(check(d)[1], iid) == {"no_source"}, (bucket, iid)
    for bad in ("00:12:31", "yesterday", "2026-03-02 0:12:31", "2026-03-02 00:61:00", "2026-02-30 00:00:01",
                "doc:", "2026-03-02 00:12:31;2026-03-02 00:12:40"):
        d = broken(lambda d: item(d, "goals", "g2").update(source=bad))
        assert codes(check(d)[1], "g2") == {"bad_source"}, bad
    for good in ("doc:kickoff-brief.pdf#page 2", "2026-03-02 00:01:00; doc:price list"):
        d = broken(lambda d: item(d, "goals", "g2").update(source=good))
        assert check(d)[0] == 0, good


def test_every_number_has_a_quote_and_a_unit():
    d = broken(lambda d: item(d, "numbers", "n3").pop("quote"))
    assert codes(check(d)[1], "n3") == {"no_quote"}
    d = broken(lambda d: item(d, "numbers", "n3").update(quote=""))
    assert codes(check(d)[1], "n3") == {"no_quote"}
    d = broken(lambda d: item(d, "numbers", "n3").pop("unit"))
    assert codes(check(d)[1], "n3") == {"no_unit"}
    d = broken(lambda d: item(d, "numbers", "n3").update(unit=" "))
    assert codes(check(d)[1], "n3") == {"no_unit"}
    d = broken(lambda d: item(d, "words", "w2").pop("quote"))      # a word is asked too: it needs one
    assert codes(check(d)[1], "w2") == {"no_quote"}


def test_a_number_is_the_digits_or_range_the_client_said_never_rounded_or_estimated():
    for v in ("~45", "about 45", "45 approx.", "大概 45", "≈45"):
        d = broken(lambda d: item(d, "numbers", "n3").update(value=v))
        assert "estimate_marker" in codes(check(d)[1], "n3"), v
    for unit in ("weeks (approx.)", "weeks, roughly", "周左右", "weeks (estimated)", "weeks, rounded"):
        d = broken(lambda d: item(d, "numbers", "n3").update(unit=unit))
        assert codes(check(d)[1], "n3") == {"estimate_marker"}, unit
    d = broken(lambda d: item(d, "numbers", "n3").update(key="reorder_weeks_est"))
    assert codes(check(d)[1], "n3") == {"estimate_marker"}
    for v in ("四五千", "six", "6 weeks", "1,000", True, "3 → 4"):
        d = broken(lambda d: item(d, "numbers", "n3").update(value=v))
        assert codes(check(d)[1], "n3") == {"value_not_number"}, v
    for v in ("6", 6, 6.5, "0.5", "-2", "20-30", "20 – 30"):
        d = broken(lambda d: item(d, "numbers", "n3").update(value=v))
        assert check(d)[0] == 0, v
    d = broken(lambda d: item(d, "numbers", "n3").update(value="30–20"))
    assert codes(check(d)[1], "n3") == {"bad_range"}


def test_a_contradiction_is_listed_as_a_question_that_names_both_sides():
    d = broken(lambda d: d["questions"].remove(item(d, "questions", "q2")))          # n1 45 days vs n4 60 days
    code, r = check(d, M2)
    assert code == 2 and codes(r) == {"contradiction_not_asked"}, r
    assert r["problems"][0]["iid"] == "n4" and "n1" in r["problems"][0]["message"]
    d = broken(lambda d: item(d, "questions", "q2").update(about=["n1"]))            # one side is not enough
    assert codes(check(d, M2)[1]) == {"contradiction_not_asked"}
    d2 = broken(lambda d: d["questions"].clear(), M2)                                # across two meetings
    code, r = check(M1, d2)
    assert codes(r) == {"contradiction_not_asked"} and r["problems"][0]["iid"] == "n5", r
    d2 = broken(lambda d: item(d, "numbers", "n5").update(value="20-30"), M2)        # the same thing twice is no clash
    d2["questions"].clear()
    assert check(M1, d2)[0] == 0
    d = broken(lambda d: d["words"].append({"iid": "w9", "audience": "client", "word": "Sampler",
                                            "meaning": "Any pouch under 100 g.", "quote": "Samplers are anything small.",
                                            "source": "2026-03-02 00:44:00"}))
    assert codes(check(d, M2)[1]) == {"contradiction_not_asked"}                     # one word, two meanings
    d["questions"].append({"iid": "q9", "audience": "client", "text": "Which is a sampler?",
                           "source": "2026-03-02 00:44:00", "about": ["w1", "w9"]})
    assert check(d, M2)[0] == 0


def test_iids_are_unique_across_files_and_carry_their_bucket_letter():
    d2 = broken(lambda d: item(d, "numbers", "n5").update(iid="n1"), M2)
    for q in d2["questions"]:
        q["about"] = ["n2", "n1"]
    code, r = check(M1, d2)
    assert "duplicate_iid" in codes(r, "n1") and code == 2, r
    code, r = check(M1, M1)
    assert {p["code"] for p in r["problems"]} == {"duplicate_iid"} and len(r["problems"]) == 16, r
    for bad in ("n9", "w01", "W1", "w", "word1"):
        d = broken(lambda d: item(d, "words", "w1").update(iid=bad))
        assert codes(check(d, M2)[1], bad) == {"bad_iid"}, bad


def test_about_names_only_iids_that_exist():
    d = broken(lambda d: item(d, "questions", "q1").update(about=["n99"]))
    assert codes(check(d, M2)[1], "q1") == {"unknown_ref"}
    d = broken(lambda d: item(d, "questions", "q1").update(about=["q1"]))
    assert codes(check(d, M2)[1], "q1") == {"unknown_ref"}


def test_a_suggestion_is_one_of_the_options_and_options_differ():
    d = broken(lambda d: item(d, "questions", "q1")["suggest"].update(value="GBP"))
    assert codes(check(d, M2)[1], "q1") == {"bad_suggest"}
    d = broken(lambda d: item(d, "questions", "q1").update(options=["USD", "usd"], suggest={"value": "USD", "because": "x"}))
    assert codes(check(d, M2)[1], "q1") == {"duplicate_option"}


def test_every_text_fits_the_console_so_what_is_accepted_is_what_was_shown():
    for bucket, iid, field, n in (("words", "w1", "meaning", 201), ("words", "w1", "quote", 301),
                                  ("stories", "s1", "want", 181), ("numbers", "n3", "applies_to", 121),
                                  ("questions", "q3", "text", 361)):
        d = broken(lambda d: item(d, bucket, iid).update({field: "x" * n}))
        assert codes(check(d, M2)[1], iid) == {"too_long"}, (field, n)
    d = broken(lambda d: item(d, "stories", "s1")["done_when"].append("y" * 121))
    assert codes(check(d, M2)[1], "s1") == {"too_long"}
    # the schema's limits are the console's own: nothing that passes here is cut there
    sys.path.insert(0, _t.CONSOLE)
    import core
    sys.path.remove(_t.CONSOLE)
    L, S = core.LIMITS, ci.load_schema()["$defs"]
    assert S["quote"]["maxLength"] <= L["quote"]
    assert S["source"]["maxLength"] + len("meeting ") <= L["source"]
    assert S["story"]["properties"]["done_when"]["items"]["maxLength"] <= L["value"]
    assert S["number"]["properties"]["applies_to"]["maxLength"] <= L["value"]
    assert S["note"]["maxLength"] <= L["value"]
    assert S["question"]["properties"]["options"]["items"]["maxLength"] <= L["option_label"]
    assert S["question"]["properties"]["options"]["maxItems"] <= L["options"]
    assert S["question"]["properties"]["suggest"]["properties"]["because"]["maxLength"] <= L["because"]
    assert len("I want , so that .") + 2 * S["story"]["properties"]["want"]["maxLength"] <= L["why"]
    assert S["word"]["properties"]["meaning"]["maxLength"] + 120 <= L["why"]


def test_the_schema_uses_only_keywords_the_checker_understands_and_codes_it_registers():
    schema = ci.load_schema()
    assert ci.schema_keywords(schema) <= ci.KNOWN_KEYWORDS, ci.schema_keywords(schema) - ci.KNOWN_KEYWORDS
    named = set()

    def walk(x):
        if isinstance(x, dict):
            named.update(v for k, v in x.items() if k in ("x-code", "x-missing"))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(schema)
    assert named and named <= set(ci.CODES), named - set(ci.CODES)
    assert "$schema" not in json.dumps(schema)            # no link to a host: the repository publishes no outside URL


# ------------------------------------------- the organizer prompt's own example --

PROMPT = os.path.join(_t.ROOT, "templates", "meeting-intake.md")
BUCKETS = (("goals", "goal"), ("words", "word"), ("stories", "story"), ("numbers", "number"), ("questions", "question"))
# What the prompt and BUILD.md promise, written out here so that loosening the schema is a visible edit of this
# file and not a change of the expectation: every item has an iid, an audience and a source; a word, a story and a
# number carry the client's quote; a number its unit and what it applies to.
ALL_ITEMS = {"iid", "audience", "source"}
REQUIRED = {
    "goal": ALL_ITEMS | {"text"},
    "word": ALL_ITEMS | {"word", "meaning", "quote"},
    "story": ALL_ITEMS | {"want", "so_that", "done_when", "human_step", "quote"},
    "number": ALL_ITEMS | {"key", "value", "unit", "applies_to", "quote"},
    "question": ALL_ITEMS | {"text"},
}
MEETING_REQUIRED = {"date", "participants", "consent"}
TOP_REQUIRED = {"meeting", "goals", "words", "stories", "numbers", "questions"}


def prompt_example() -> dict:
    """The one JSON example of templates/meeting-intake.md (Output shape), parsed."""
    blocks = re.findall(r"```json\n(.*?)\n```", _t.read(PROMPT), re.S)
    assert len(blocks) == 1, len(blocks)
    return json.loads(blocks[0])


def filled(node, key=None):
    """The example with each `…` (what the organizer fills in) replaced by a value the schema accepts."""
    if isinstance(node, dict):
        return {k: filled(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [filled(x, key) for x in node]
    if isinstance(node, str) and "…" in node:
        return "2026-09-27 00:04:31" if key == "source" else node.replace("…", "x")
    return node


def test_the_example_in_the_organizer_prompt_passes_the_checker_once_its_blanks_are_filled():
    example = prompt_example()
    assert "…" in json.dumps(example, ensure_ascii=False)                # there are blanks to fill
    assert "…" not in json.dumps(filled(example), ensure_ascii=False)
    code, report = check(filled(example))
    assert code == 0 and report["ok"] is True and report["items"] == len(BUCKETS), report
    assert check(example)[0] == 2                                          # as written it is not an intake: the blanks are not values


def test_the_schema_requires_what_the_prompt_and_build_md_promise():
    schema = ci.load_schema()
    assert set(schema["required"]) == TOP_REQUIRED
    assert set(schema["properties"]["meeting"]["required"]) == MEETING_REQUIRED
    for bucket, name in BUCKETS:
        assert set(schema["$defs"][name]["required"]) == REQUIRED[name], (name, set(schema["$defs"][name]["required"]) ^ REQUIRED[name])
    assert all(ALL_ITEMS <= REQUIRED[n] for _, n in BUCKETS) and {"quote"} <= REQUIRED["story"] & REQUIRED["number"]
    prompt, build = _t.read(PROMPT), _t.read(os.path.join(_t.ROOT, "BUILD.md"))
    for said in ("Give every item an `iid`", "Give every item an `audience`", "A story and a number each carry a `quote`"):
        assert said in prompt, said                                      # the prose the table above is read from
    assert "every item has an `iid` that is never reused, an `audience` (`client` or `builder`) and a `source`" in build
    assert "consent line that says consent was not given or not recorded passes the check" in build


def test_the_example_holds_every_key_the_schema_requires_and_none_it_forbids():
    example, schema = prompt_example(), ci.load_schema()
    assert set(example) == TOP_REQUIRED and set(example["meeting"]) == MEETING_REQUIRED
    for bucket, name in BUCKETS:
        required, allowed = REQUIRED[name], set(schema["$defs"][name]["properties"])
        assert example[bucket], bucket
        for it in example[bucket]:
            assert required <= set(it) <= allowed, (bucket, sorted(required - set(it)), sorted(set(it) - allowed))


def test_the_checker_refuses_the_example_when_any_required_key_is_left_out():
    good = filled(prompt_example())
    assert check(good)[0] == 0
    cut = 0
    for bucket, name in BUCKETS:
        for key in sorted(REQUIRED[name]):
            d = copy.deepcopy(good)
            del d[bucket][0][key]
            code, report = check(d)
            assert code == 2 and [q["path"] for q in report["problems"]] == [f"{bucket}[0].{key}"], (bucket, key, report)
            assert codes(report) <= {"missing_field", "no_source", "no_quote", "no_unit"}, report
            cut += 1
    assert cut == sum(len(v) for v in REQUIRED.values()) == 30, cut       # iid, audience and, for a story, quote among them
    for key in sorted(MEETING_REQUIRED):
        d = copy.deepcopy(good)
        del d["meeting"][key]
        assert check(d)[0] == 2, key


if __name__ == "__main__":
    _t.main(globals())
