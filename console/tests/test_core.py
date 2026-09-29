"""core.py: what the console's one contract promises. Sections: validation, the
answer, the fold, the store (post / budget / torn, blank and corrupt logs /
durability / answer binding / reopen and applied), the role split and
signatures, concurrency (writers of one kind, then the two sides racing), and
`test_adv_*`: hostile input. A `test_adv_*` test names the bad input in its
docstring and fails when core.py accepts it or crashes on it.
"""

import copy
import errno
import json
import os
import random
import re
import subprocess
import sys
import threading
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import _t  # noqa: E402
import core  # noqa: E402
from _t import assert_raises, tmpdir, tmpstore  # noqa: E402

os.environ.pop("CONSOLE_SECRET", None)      # tests choose their own secret
C_DIR = os.path.abspath(os.path.join(HERE, ".."))
DROP = object()                             # ask(field=DROP) removes the field


# ---------------------------------------------------------------- helpers --

def ask(step="confirm", **over):
    """A valid raw ask (fictional shop) for `step`, with fields replaced."""
    a = {"id": "tea-word", "step": step,
         "title": "Call the loose-leaf line 'Blends'?",
         "why": "The owner's emails say blends; the catalogue says mixes.",
         "evidence": [{"label": "Catalogue", "value": "Mixes"}],
         "if_no": "The catalogue keeps saying mixes."}
    if step in ("confirm", "approve"):
        a["recommend"] = {"value": "yes", "because": "It matches the emails."}
    if step == "approve":
        a["effect"] = "Raise the daily tea-ad budget from 20 to 30."
    if step == "choose":
        a["options"] = [{"value": "a", "label": "Blends", "note": "owner's word"},
                        {"value": "b", "label": "Mixes"},
                        {"value": "c", "label": "Both"}]
        a["recommend"] = {"value": "a", "because": "It matches the emails."}
    if step == "provide":
        a["input"] = {"type": "number", "min": 0, "max": 100, "unit": "USD"}
    for k, v in over.items():
        if v is DROP:
            a.pop(k, None)
        else:
            a[k] = v
    return a


def gated(**over):
    """A provide ask whose answer goes through a harness gate."""
    return ask("provide", id="unit-cost",
               gate={"verb": ["facts", "confirm", "unit_cost"], "value_arg": True,
                     "expect": {"items": {"unit_cost": "$value"}}}, **over)


def ok(raw):
    a, errs = core.validate_ask(raw)
    assert a is not None and not errs, errs
    return a


def errs(raw):
    a, e = core.validate_ask(raw)
    assert a is None and e, "expected the ask to be refused"
    return e


def has(errors, field, code):
    return any(e["field"] == field and e["code"] == code for e in errors)


def raw_log(store):
    try:
        with open(store.path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b""


def put(store, data: bytes):
    os.makedirs(store.dir, exist_ok=True)
    with open(store.path, "wb") as f:
        f.write(data)


def shown(store, id="tea-word"):
    return store.state()["asks"][id]["hash"]


def answer(store, value="yes", id="tea-word", user="bob", **kw):
    return store.answer(user, id, value, shown=kw.pop("shown", None) or shown(store, id), **kw)


def event(seq, type, **f):
    return {"seq": seq, "at": "2026-01-01T00:00:00Z", "by": "agent:bot",
            "type": type, **f}


def check_log(store, n, secret=None):
    """The file is `n` valid events, seq exactly 1..n, and every human event
    verifies. Returns the events."""
    raw = raw_log(store)
    assert raw.endswith(b"\n") or not raw, "log ends in a torn line"
    evs = [json.loads(ln) for ln in raw.split(b"\n")[:-1]]
    assert [e["seq"] for e in evs] == list(range(1, n + 1)), \
        [e["seq"] for e in evs][:60]
    for e in evs:
        assert isinstance(e.get("at"), str) and e["type"] in (
            core.ROLES["agent"] + core.ROLES["human"])
        if e["type"] in core.ROLES["human"]:
            assert core.verify(secret or store.secret(), e) is True, e
    return evs


@contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update(kw)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def walk_strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from walk_strings(k)
            yield from walk_strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_strings(v)


@contextmanager
def _raises(exc):
    try:
        yield
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def refused_or_ok(fn):
    """Run fn: a Refused is fine, any other exception is a crash."""
    try:
        return fn()
    except core.Refused:
        return None


def paths(x, kind, path=()):
    """Paths to every `kind` (dict or list) inside x; x itself is ()."""
    if isinstance(x, kind):
        yield path
    for k, v in (x.items() if isinstance(x, dict) else enumerate(x) if isinstance(x, list) else ()):
        yield from paths(v, kind, path + (k,))


def at(x, path):
    for p in path:
        x = x[p]
    return x


def rand_json(rng, depth=0):
    """A random JSON-like value: an object at depth 0, then objects, lists and
    scalars of every kind matches() tells apart (text, numbers, numeric text,
    bool, null), containers nested up to six levels."""
    r = rng.random()
    if depth == 0 or (depth < 6 and r < .3):
        return {f"k{i}": rand_json(rng, depth + 1) for i in range(rng.randint(1 if depth == 0 else 0, 4))}
    if depth < 6 and r < .55:
        return [rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return rng.choice(["x", "abc", "", "10", "0.5", "1e2", 7, -3, 2.5, 100, True, False, None])


# ------------------------------------------------------------- validation --

def test_valid_ask_each_step():
    for step in core.STEPS:
        raw = ask(step)
        before = copy.deepcopy(raw)
        a = ok(raw)
        assert raw == before, "validate_ask must not mutate its input"
        assert a["step"] == step and a["id"] == "tea-word"
        assert a["kind"] == "general" and a["group"] == ""     # the defaults
        assert set(a) <= set(core.ASK_FIELDS)
        assert ("effect" in a) == (step == "approve")
        assert ("options" in a) == (step == "choose")
        assert ("input" in a) == (step == "provide")
        assert ("recommend" in a) == (step != "provide")       # provide's is optional
        assert core.validate_ask(a) == (a, []), "a stored ask re-validates to itself"


def test_required_fields():
    for step in core.STEPS:
        need = ["id", "title", "why", "evidence", "if_no"]
        need += {"confirm": ["recommend"], "approve": ["recommend", "effect"],
                 "choose": ["recommend", "options"], "provide": []}[step]
        for f in need:
            blanks = [DROP, None] + (["", "   "] if f in ("id", "title", "why", "if_no", "effect") else [])
            blanks += [[]] if f in ("evidence", "options") else []
            blanks += [{}] if f == "recommend" else []
            for blank in blanks:
                e = errs(ask(step, **{f: blank}))
                assert has(e, f, "required"), (step, f, blank, e)
    for f in ("kind", "group", "input", "gate"):                # optional ones
        ok(ask("provide", **{f: DROP}))
    ok(ask("provide", recommend=DROP))
    assert has(errs(ask(step=DROP)), "step", "bad_value")
    assert has(errs(ask(step="maybe")), "step", "bad_value")
    assert has(errs(ask(step=["confirm"])), "step", "bad_value")


def test_limits():
    L = core.LIMITS
    assert set(L) == {"id", "kind", "group", "title", "why", "if_no", "because", "effect", "label", "value",
                      "source", "quote", "caption", "cell", "option_label", "option_note", "unit", "answer",
                      "comment", "note", "say", "where", "reason", "evidence", "options", "columns", "rows",
                      "verb", "expect"}, "a limit was added or removed: give it a test below"
    table = [   # (limit, builder, field)
        (L["id"], lambda s: ask(id=s), "id"),
        (L["kind"], lambda s: ask(kind=s), "kind"),
        (L["group"], lambda s: ask(group=s), "group"),
        (L["title"], lambda s: ask(title=s), "title"),
        (L["why"], lambda s: ask(why=s), "why"),
        (L["if_no"], lambda s: ask(if_no=s), "if_no"),
        (L["effect"], lambda s: ask("approve", effect=s), "effect"),
        (L["because"], lambda s: ask(recommend={"value": "yes", "because": s}), "recommend.because"),
        (L["answer"], lambda s: ask("provide", input={"type": "text"},
                                    recommend={"value": s, "because": "b"}), "recommend.value"),
        (L["label"], lambda s: ask(evidence=[{"label": s, "value": "v"}]), "evidence[0].label"),
        (L["value"], lambda s: ask(evidence=[{"label": "l", "value": s}]), "evidence[0].value"),
        (L["source"], lambda s: ask(evidence=[{"label": "l", "value": "v", "source": s}]), "evidence[0].source"),
        (L["quote"], lambda s: ask(evidence=[{"quote": s}]), "evidence[0].quote"),
        (L["source"], lambda s: ask(evidence=[{"quote": "q", "source": s}]), "evidence[0].source"),
        (L["caption"], lambda s: ask(evidence=[{"table": {"caption": s, "columns": ["a"], "rows": [["1"]]}}]),
         "evidence[0].table.caption"),
        (L["cell"], lambda s: ask(evidence=[{"table": {"columns": ["a"], "rows": [[s]]}}]),
         "evidence[0].table.rows"),
        (L["option_label"], lambda s: ask("choose", options=[{"value": "a", "label": s}, {"value": "b", "label": "B"}]),
         "options[0].label"),
        (L["option_note"], lambda s: ask("choose", options=[{"value": "a", "label": "A", "note": s}, {"value": "b", "label": "B"}]),
         "options[0].note"),
        (L["value"], lambda s: ask("choose", options=[{"value": s, "label": "A"}, {"value": "b", "label": "B"}],
                                   recommend={"value": s, "because": "b"}), "options[0].value"),
        (L["unit"], lambda s: ask("provide", input={"type": "number", "unit": s}), "input.unit"),
    ]
    for limit, build, field in table:
        ok(build("x" * limit))
        assert has(errs(build("x" * (limit + 1))), field, "too_long"), (field, limit)
    assert ok(ask(title="茶" * L["title"]))                     # limits count characters
    # counts
    ev = lambda n: [{"label": f"l{i}", "value": "v"} for i in range(n)]   # noqa: E731
    ok(ask(evidence=ev(L["evidence"])))
    assert has(errs(ask(evidence=ev(L["evidence"] + 1))), "evidence", "too_many")
    opts = lambda n: [{"value": f"o{i}", "label": f"O{i}"} for i in range(n)]   # noqa: E731
    ok(ask("choose", options=opts(L["options"]), recommend={"value": "o0", "because": "b"}))
    assert has(errs(ask("choose", options=opts(L["options"] + 1),
                        recommend={"value": "o0", "because": "b"})), "options", "bad_value")
    assert has(errs(ask("choose", options=opts(1), recommend={"value": "o0", "because": "b"})),
               "options", "bad_value")
    tbl = lambda c, r: [{"table": {"columns": [f"c{i}" for i in range(c)],       # noqa: E731
                                   "rows": [["1"] * c for _ in range(r)]}}]
    ok(ask(evidence=tbl(L["columns"], L["rows"])))
    assert has(errs(ask(evidence=tbl(L["columns"] + 1, 1))), "evidence[0].table.columns", "bad_value")
    assert has(errs(ask(evidence=tbl(1, L["rows"] + 1))), "evidence[0].table.rows", "bad_value")
    # gate: verb words and the size of expect
    gate = lambda n: {"verb": ["w"] * n, "expect": {"k": "v"}}   # noqa: E731
    ok(ask(gate=gate(L["verb"])))
    assert has(errs(ask(gate=gate(L["verb"] + 1))), "gate.verb", "bad_value")
    pad = L["expect"] - len(core.canon({"k": ""}))
    ok(ask(gate={"verb": ["w"], "expect": {"k": "v" * pad}}))
    assert errs(ask(gate={"verb": ["w"], "expect": {"k": "v" * (pad + 1)}}))


def test_unknown_fields_are_errors():
    assert has(errs(ask(titel="x")), "titel", "unknown_field")
    cases = [
        ("evidence[0].labl", dict(evidence=[{"label": "l", "value": "v", "labl": 1}])),
        ("evidence[0].extra", dict(evidence=[{"quote": "q", "extra": 1}])),
        ("evidence[0].extra", dict(evidence=[{"table": {"columns": ["a"], "rows": [["1"]]}, "extra": 1}])),
        ("evidence[0].table.colour", dict(evidence=[{"table": {"columns": ["a"], "rows": [["1"]], "colour": 1}}])),
        ("recommend.reason", dict(recommend={"value": "yes", "because": "b", "reason": "x"})),
        ("gate.retry", dict(gate={"verb": ["w"], "expect": {"k": 1}, "retry": 3})),
    ]
    for field, over in cases:
        assert has(errs(ask(**over)), field, "unknown_field"), (field, over)
    assert has(errs(ask("choose", options=[{"value": "a", "label": "A", "price": 1},
                                           {"value": "b", "label": "B"}])),
               "options[0].price", "unknown_field")
    assert has(errs(ask("provide", input={"type": "text", "step": 1})), "input.step", "unknown_field")


def test_step_specific_fields():
    assert has(errs(ask("confirm", options=[{"value": "a", "label": "A"}])), "options", "not_allowed")
    assert has(errs(ask("choose", effect="x")), "effect", "not_allowed")
    assert has(errs(ask("confirm", effect="x")), "effect", "not_allowed")
    assert has(errs(ask("confirm", input={"type": "text"})), "input", "not_allowed")
    assert has(errs(ask("approve", input={"type": "text"})), "input", "not_allowed")
    assert has(errs(ask("provide", effect="x")), "effect", "not_allowed")
    assert has(errs(ask("provide", options=[{"value": "a", "label": "A"}])), "options", "not_allowed")
    ok(ask("provide", recommend={"value": "5", "because": "b"}))   # recommend fits every step
    for bad in (None, [], "x", 5):
        assert core.validate_ask(bad) == (None, [{"field": "", "code": "bad_type",
                                                  "message": "an ask is a JSON object"}])


def test_all_errors_at_once():
    e = errs({"id": "Bad Id!", "step": "choose", "title": "", "why": "x" * 401,
              "evidence": [], "if_no": None, "options": [{"value": "a"}],
              "recommend": {"value": "zzz"}, "gate": {"verb": "x"}, "typo": 1})
    fields = {x["field"] for x in e}
    for f in ("id", "title", "why", "evidence", "if_no", "options", "recommend.because",
              "gate.verb", "gate.expect", "typo"):
        assert f in fields, (f, sorted(fields))
    assert len(e) >= 10


def test_each_problem_is_listed_once():
    """One field with one problem is one error, however many checks see it."""
    assert [(x["field"], x["code"]) for x in errs(ask("approve", effect=DROP))] == [("effect", "required")]
    assert [(x["field"], x["code"]) for x in errs(ask("approve", effect=""))] == [("effect", "required")]
    cell = "c" * (core.LIMITS["cell"] + 1)                 # three cells that are too long: one problem, one line
    e = errs(ask(evidence=[{"table": {"columns": ["a", "b"], "rows": [[cell, cell], [cell, "1"]]}}]))
    assert [(x["field"], x["code"]) for x in e] == [("evidence[0].table.rows", "too_long")]
    e = errs(ask(evidence=[{"table": {"columns": [cell, cell], "rows": [["1", "1"]]}}]))
    assert [(x["field"], x["code"]) for x in e] == [("evidence[0].table.columns", "too_long")]
    hopeless = [ask("approve", effect=DROP, recommend=DROP, evidence=[]), ask("choose", options=DROP), ask("approve", effect=5),
                ask(id="\x00", title="\x00", why="\x00", if_no="\x00"), ask(title=5, why=5, if_no=5),
                ask(evidence=[{"table": {"columns": [cell] * 3, "rows": [[cell] * 3] * 4}}, {"quote": cell * 4}]),
                ask("provide", input={"type": "number", "min": "a", "max": "b", "unit": "u" * 99},
                    recommend={"value": "z" * 501, "because": "b" * 201}),
                ask(gate={"verb": ["-x"] * 9, "expect": {"a": {}}, "value_arg": 1, "extra": 1})]
    for raw in hopeless:
        pairs = [(x["field"], x["code"]) for x in errs(raw)]
        assert len(pairs) == len(set(pairs)), pairs
    with tmpstore() as s:                                  # once per ask: two asks with the same fault are two lines
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask("approve", id="a", effect=DROP), ask("approve", id="b", effect=DROP)])
        got = [(x["ask"], x["field"], x["code"]) for x in r.err.params["errors"]]
        assert got == [("a", "effect", "required"), ("b", "effect", "required")], got


def test_error_documents_are_well_formed():
    bad = [ask(id="X"), ask(step="nope"), ask(evidence="x"), ask(evidence=[5]),
           ask(evidence=[{"quote": "q", "url": "javascript:x"}]), ask("choose", options="x"),
           ask("provide", input={"type": "blob"}), ask(gate=5), ask(recommend="yes"),
           ask("provide", input={"type": "number", "min": "a"}), ask(title=5)]
    for raw in bad:
        for e in errs(raw):
            assert set(e) == {"field", "code", "message"}, e
            assert e["code"] in core.FIELD_CODES, e
            assert isinstance(e["field"], str) and e["message"], e


def test_recommend_value_is_validated():
    assert has(errs(ask(recommend={"value": "maybe", "because": "b"})), "recommend.value", "bad_value")
    assert has(errs(ask("approve", recommend={"value": "y", "because": "b"})), "recommend.value", "bad_value")
    assert ok(ask(recommend={"value": " yes ", "because": "b"}))["recommend"]["value"] == "yes"
    assert has(errs(ask("choose", recommend={"value": "zzz", "because": "b"})), "recommend.value", "bad_value")
    assert ok(ask("provide", recommend={"value": 50, "because": "b"}))["recommend"]["value"] == "50"
    assert ok(ask("provide", recommend={"value": 50.5, "because": "b"}))["recommend"]["value"] == "50.5"
    assert has(errs(ask("provide", recommend={"value": 500, "because": "b"})), "recommend.value", "bad_value")
    assert has(errs(ask("provide", recommend={"value": "abc", "because": "b"})), "recommend.value", "bad_value")
    assert has(errs(ask("provide", input={"type": "date"}, recommend={"value": "2026-13-01", "because": "b"})),
               "recommend.value", "bad_value")
    ok(ask("provide", input={"type": "date"}, recommend={"value": "2026-01-31", "because": "b"}))
    assert has(errs(ask("provide", input={"type": "url"}, recommend={"value": "ftp://x", "because": "b"})),
               "recommend.value", "bad_value")
    assert has(errs(ask(recommend={"value": "yes"})), "recommend.because", "required")
    assert has(errs(ask(recommend={"because": "b"})), "recommend.value", "required")
    assert has(errs(ask(recommend="yes")), "recommend", "bad_type")


def test_gate_shape():
    give = {"verb": ["facts", "confirm"], "expect": {"a": "$value"}, "value_arg": True}
    for step in ("provide", "choose"):
        assert ok(ask(step, gate=give))["gate"] == give
    g = ok(ask("confirm", gate={"verb": ["facts", "confirm"], "expect": {"a": 1}}))["gate"]
    assert g == {"verb": ["facts", "confirm"], "expect": {"a": 1}, "value_arg": False}
    assert "gate" not in ok(ask("provide", gate=None))
    ok(ask("approve", gate={"verb": ["queue", "approve"], "expect": {"n": 1}}))
    stored = ok(gated())
    assert core.validate_ask(stored) == (stored, []), "a stored gated ask re-validates to itself"
    bads = [
        ("gate", "bad_type", 5), ("gate", "bad_type", ["x"]),
        ("gate.verb", "bad_value", {"expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": "facts", "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": [], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": ["--force"], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": ["-x"], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": ["a b"], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": ["a;b"], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": ["a=b"], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": [""], "expect": {"a": 1}}),
        ("gate.verb", "bad_value", {"verb": [5], "expect": {"a": 1}}),
        ("gate.expect", "required", {"verb": ["x"]}),
        ("gate.expect", "required", {"verb": ["x"], "expect": {}}),
        ("gate.expect", "required", {"verb": ["x"], "expect": "$value"}),
        ("gate.expect", "required", {"verb": ["x"], "expect": ["a"]}),
        ("gate.expect", "required", {"verb": ["x"], "expect": {"a": {1, 2}}}),
        ("gate.expect", "required", {"verb": ["x"], "expect": {"a": object()}}),
        ("gate.value_arg", "bad_type", {"verb": ["x"], "expect": {"a": 1}, "value_arg": "yes"}),
    ]
    for field, code, gate in bads:
        assert has(errs(ask("provide", gate=gate)), field, code), (field, gate)
    deep = {"a": 1}
    for _ in range(50):
        deep = {"a": deep}
    assert has(errs(ask("provide", gate={"verb": ["x"], "expect": deep})), "gate.expect", "required")
    for step in ("confirm", "approve"):
        assert has(errs(ask(step, gate={"verb": ["x"], "expect": {"a": 1}, "value_arg": True})),
                   "gate.value_arg", "not_allowed")


def test_a_gate_on_choose_and_provide_must_carry_the_answer():
    """The answer has to reach the harness AND be bound to what was shown:
    value_arg true and "$value" as a whole value in expect. Each missing piece
    is refused; a yes/no gate needs neither."""
    give = {"verb": ["x"], "expect": {"n": "$value"}, "value_arg": True}
    nested = {"verb": ["x"], "value_arg": True, "expect": {"a": {"b": ["c", {"d": "$value"}]}}}
    missing = [
        {"verb": ["x"], "expect": {"n": "$value"}},                                # never sent
        {"verb": ["x"], "expect": {"n": "$value"}, "value_arg": False},
        {"verb": ["x"], "expect": {"n": 1}, "value_arg": True},                    # sent, never bound
        {"verb": ["x"], "expect": {"n": 1}},                                       # neither
        {"verb": ["x"], "expect": {"n": "the $value"}, "value_arg": True},         # "$value" is the whole string
        {"verb": ["x"], "expect": {"n": "$Value"}, "value_arg": True},
    ]
    for step in ("provide", "choose"):
        for g in (give, nested):
            assert ok(ask(step, gate=g))["gate"] == g, (step, g)
        for g in missing:
            e = errs(ask(step, gate=g))
            assert [x["field"] for x in e] == ["gate.value_arg"] and has(e, "gate.value_arg", "required"), (step, g, e)
    for step in ("confirm", "approve"):
        assert ok(ask(step, gate={"verb": ["x"], "expect": {"n": 1}}))["gate"]["value_arg"] is False
        assert has(errs(ask(step, gate=give)), "gate.value_arg", "not_allowed")
    with tmpstore() as s:                                          # and post carries the refusal
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask("provide", gate={"verb": ["x"], "expect": {"n": 1}})])
        assert has(r.err.params["errors"], "gate.value_arg", "required") and raw_log(s) == b""


def test_a_gate_expect_may_not_name_nothing():
    """An empty {} or [] anywhere in expect checks nothing: below the top level
    `matches` compares key for key, so `{"a": {}}` would pass only a subject that
    says nothing there, and the owner is shown a payload with a hole. It is
    refused wherever it sits; empty text, 0, false and null still name a value."""
    empties = [{"a": {}}, {"a": []}, {"a": [{}]}, {"a": [[]]}, {"a": {"b": {}}}, {"a": {"b": {"c": []}}},
               {"a": [1, []]}, {"a": [{"k": 1}, {}]}, {"a": 1, "b": {"c": [{"d": {}}]}}]
    for exp in empties:
        for step in ("confirm", "approve"):
            assert has(errs(ask(step, gate={"verb": ["x"], "expect": exp})), "gate.expect", "required"), (step, exp)
            assert has(errs(ask(step, gate={"verb": ["x"], "expect": dict(exp, n="$value")})),
                       "gate.expect", "required"), (step, exp)
        for step in ("provide", "choose"):
            assert has(errs(ask(step, gate={"verb": ["x"], "value_arg": True, "expect": dict(exp, n="$value")})),
                       "gate.expect", "required"), (step, exp)
    named = {"a": None, "b": "", "c": 0, "d": False, "e": [None, ""], "f": {"g": [0]}, "n": "$value"}
    for step in ("provide", "choose"):
        g = ok(ask(step, gate={"verb": ["x"], "value_arg": True, "expect": named}))["gate"]
        assert g["expect"] == named
    stored = ok(gated())
    assert core.validate_ask(stored) == (stored, [])
    deep_dict = deep_list = {}
    for _ in range(20000):                                   # too deep is refused before anything walks it
        deep_dict, deep_list = {"a": deep_dict}, [deep_list]
    for exp in (deep_dict, {"a": deep_dict, "n": "$value"}, {"a": deep_list, "n": "$value"}):
        assert has(errs(ask("provide", gate={"verb": ["x"], "value_arg": True, "expect": exp})), "gate.expect", "required")
    with tmpstore() as s:                                    # and post carries the refusal, writing nothing
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask("provide", gate={"verb": ["x"], "value_arg": True, "expect": {"n": "$value", "a": {}}})])
        assert has(r.err.params["errors"], "gate.expect", "required") and raw_log(s) == b""


def test_evidence_shapes():
    a = ok(ask(evidence=[
        {"label": "Stock", "value": 12, "source": "sheet", "url": "https://example.com/s"},
        {"quote": "Call it Blends.", "source": "call, 3 May", "url": " http://example.com/c "},
        {"table": {"caption": "Sales", "columns": [" Week ", "Units"],
                   "rows": [["1", 20], ["2", None]]}}]))
    assert a["evidence"][0] == {"label": "Stock", "value": "12", "source": "sheet",
                                "url": "https://example.com/s"}
    assert a["evidence"][1]["url"] == "http://example.com/c"
    assert a["evidence"][2] == {"table": {"caption": "Sales", "columns": ["Week", "Units"],
                                          "rows": [["1", "20"], ["2", ""]]}}
    assert ok(ask(evidence=[{"table": {"columns": ["a"], "rows": [["1"]]}}]))["evidence"][0]["table"]["caption"] == ""
    assert has(errs(ask(evidence="x")), "evidence", "required")
    assert has(errs(ask(evidence=["x"])), "evidence[0]", "bad_type")
    assert has(errs(ask(evidence=[{}])), "evidence[0].label", "required")
    assert has(errs(ask(evidence=[{"label": "l"}])), "evidence[0].value", "required")
    assert has(errs(ask(evidence=[{"quote": ""}])), "evidence[0].quote", "required")
    assert has(errs(ask(evidence=[{"table": "x"}])), "evidence[0].table", "bad_type")
    assert has(errs(ask(evidence=[{"table": {"columns": [], "rows": [["1"]]}}])), "evidence[0].table.columns", "bad_value")
    assert has(errs(ask(evidence=[{"table": {"columns": [" "], "rows": [["1"]]}}])), "evidence[0].table.columns", "bad_value")
    assert has(errs(ask(evidence=[{"table": {"columns": ["a", "b"], "rows": [["1"]]}}])), "evidence[0].table.rows", "bad_value")
    assert has(errs(ask(evidence=[{"table": {"columns": ["a"], "rows": []}}])), "evidence[0].table.rows", "bad_value")
    for url in ("javascript:alert(1)", "ftp://x.example", "//x.example", "https://a b", "https://x/<y>",
                'https://x/"y', "data:text/html,x", "https://" + "a" * 301, 5):
        assert has(errs(ask(evidence=[{"label": "l", "value": "v", "url": url}])), "evidence[0].url", "bad_value"), url


def test_option_rules():
    assert has(errs(ask("choose", options=[{"value": "a", "label": "A"}, {"value": "a", "label": "B"}])),
               "options[1].value", "duplicate")
    assert has(errs(ask("choose", options=[{"value": "a"}, {"value": "b", "label": "B"}])),
               "options[0].label", "required")
    assert has(errs(ask("choose", options=["a", "b"])), "options[0]", "bad_type")
    a = ok(ask("choose"))
    assert a["options"][0]["note"] == "owner's word" and "note" not in a["options"][1]


def test_input_rules():
    assert ok(ask("provide", input=DROP))["input"] == {"type": "text"}
    assert ok(ask("provide", input=None))["input"] == {"type": "text"}
    assert ok(ask("provide", input={"type": "date"}))["input"] == {"type": "date"}
    assert ok(ask("provide", input={"unit": "kg"}))["input"] == {"type": "text", "unit": "kg"}
    assert ok(ask("provide", input={"type": "number", "min": 0, "max": 2.5, "unit": "kg"}))["input"] == \
        {"type": "number", "min": 0, "max": 2.5, "unit": "kg"}
    assert has(errs(ask("provide", input="number")), "input", "bad_type")
    assert has(errs(ask("provide", input={"type": "blob"})), "input.type", "bad_value")
    assert has(errs(ask("provide", input={"type": "text", "min": 1})), "input.min", "not_allowed")
    assert has(errs(ask("provide", input={"type": "date", "max": 1})), "input.max", "not_allowed")
    for bad in ("1", True, float("nan"), float("inf"), None):
        assert has(errs(ask("provide", input={"type": "number", "min": bad})), "input.min", "bad_type"), bad


def test_text_hygiene():
    a = ok(ask(title="  Tea\x00 blends\x07\x7f  ", why="line one\r\nline two\n\nline\x00 three"))
    assert a["title"] == "Tea blends"
    assert a["why"] == "line one\nline two\n\nline three"
    assert ok(ask(title="茶 Blends？"))["title"] == "茶 Blends？"
    assert "\n" not in ok(ask(title="one\ntwo"))["title"]                # a title is one line
    assert ok(ask(evidence=[{"quote": "a\nb"}]))["evidence"][0]["quote"] == "a\nb"
    assert has(errs(ask(title=5)), "title", "bad_type")
    assert has(errs(ask(title=["x"])), "title", "bad_type")
    assert has(errs(ask(id="Tea Word")), "id", "bad_value")
    assert ok(ask(id="  tea-word  "))["id"] == "tea-word"


def test_invalid_asks_never_touch_the_disk():
    with tmpdir() as d:
        s = core.Store(os.path.join(d, "not-yet"))
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask(), ask(id="second", title="")])
        assert not os.path.exists(s.dir)
        assert [e["ask"] for e in r.err.params["errors"]] == ["second"]
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask(), 5])
        assert r.err.params["errors"][0]["ask"] == "#2"
        with assert_raises("invalid_ask") as r:
            s.post("bot", [ask(), ask()])
        assert has(r.err.params["errors"], "id", "duplicate")
        assert not os.path.exists(s.dir)
        doc = r.err.doc()
        assert doc["ok"] is False and doc["code"] == "invalid_ask" and doc["params"]["errors"]


# ------------------------------------------------------------- the answer --

def test_check_value_confirm_and_approve():
    for step in ("confirm", "approve"):
        a = ok(ask(step))
        assert core.check_value(a, "yes") == ("yes", None)
        assert core.check_value(a, " no ") == ("no", None)
        for bad in ("Yes", "y", "true", "", "   ", None, True, "yes no"):
            v, e = core.check_value(a, bad)
            assert v is None and e["code"] == "bad_value" and e["reason"] == "yes_no" and e["message"], bad


def test_check_value_choose():
    a = ok(ask("choose"))
    assert core.check_value(a, "a") == ("a", None)
    assert core.check_value(a, " b ") == ("b", None)
    for bad in ("d", "A", "", None, "a b"):
        v, e = core.check_value(a, bad)
        assert v is None and e["reason"] == "choice", bad


def test_check_value_provide_text():
    a = ok(ask("provide", input={"type": "text"}))
    assert core.check_value(a, "  hello ") == ("hello", None)
    assert core.check_value(a, "x" * 500) == ("x" * 500, None)
    assert core.check_value(a, "x" * 501)[1]["reason"] == "too_long"
    for bad in ("", "   ", None):
        assert core.check_value(a, bad)[1]["reason"] == "empty"
    assert core.check_value(ok(ask("provide", input=DROP)), "anything") == ("anything", None)


def test_check_value_provide_number():
    a = ok(ask("provide", input={"type": "number", "min": 0, "max": 100}))
    for good in ("0", "100", "42", "4.5", "1e2", "-0", " 42 ", "0.0"):
        assert core.check_value(a, good)[1] is None, good
    assert core.check_value(a, " 42 ")[0] == "42"
    assert core.check_value(a, 5)[0] == "5"
    for bad, reason in (("-0.01", "range"), ("100.01", "range"), ("1e3", "range"),
                        ("abc", "number"), ("1,5", "number"), ("nan", "number"), ("inf", "number"),
                        ("-inf", "number"), ("1e999", "number"), ("", "empty"), ("1" * 501, "too_long")):
        v, e = core.check_value(a, bad)
        assert v is None and e["code"] == "bad_value" and e["reason"] == reason, (bad, e)
    free = ok(ask("provide", input={"type": "number"}))
    assert core.check_value(free, "-1e9")[1] is None
    only_min = ok(ask("provide", input={"type": "number", "min": 5}))
    assert core.check_value(only_min, "4.99")[1]["reason"] == "range"
    assert core.check_value(only_min, "1e9")[1] is None


def test_check_value_number_forms():
    """What a browser's number field lets a person type is a number here (a bare
    .5, a trailing dot, a plus), and stays exactly as typed; nothing else that
    only float() or a Unicode table would read."""
    a = ok(ask("provide", input={"type": "number", "min": -10, "max": 10}))
    for good in (".5", "5.", "+3", "+.5", "-.5", "5.e-1", "+5.", "-.5E-1", "0.", ".0", "+0", "007", "1E+1"):
        assert core.check_value(a, good) == (good, None), good
    assert core.check_value(a, " .5 ") == (".5", None) and core.check_value(a, 0.5) == ("0.5", None)
    for bad in ("1_000", "1_0", "١٢", "１", "1٢", "١٢.٥", "1e", "1e+", "e5", ".", "+", "-", "+.",
                ".e1", "+-1", "-+1", "--1", "++1", "1.2.3", "..5", "5..", "0x10", "1,5", "1 2", "+ 3", "1e1.5", "+e1"):
        v, e = core.check_value(a, bad)
        assert v is None and e["code"] == "bad_value" and e["reason"] == "number", (bad, v, e)
    for over, reason in ((".5e2", "range"), ("+11", "range"), ("10.5", "range"), ("-10.5", "range"), ("-.5e3", "range")):
        assert core.check_value(a, over)[1]["reason"] == reason, over
    low = ok(ask("provide", input={"type": "number", "min": 1}))
    assert core.check_value(low, ".5")[1]["reason"] == "range" and core.check_value(low, "+1.")[1] is None
    with tmpstore() as s:                                      # end to end: recorded as typed, then bound like any answer
        s.post("bot", [ask("provide", id="n", input={"type": "number", "min": 0}, recommend={"value": ".5", "because": "b"})])
        assert s.state()["asks"]["n"]["ask"]["recommend"]["value"] == ".5"
        ev = s.answer("bob", "n", "+3.", shown=shown(s, "n"))
        assert ev["value"] == "+3." and ev["suggested"] is False
    with tmpstore() as s:
        s.post("bot", [ask("provide", id="n", input={"type": "number"}, recommend={"value": ".5", "because": "b"})])
        assert s.answer("bob", "n", " .5", shown=shown(s, "n"))["suggested"] is True
    e = errs(ask("provide", input={"type": "number"}, recommend={"value": "1_000", "because": "b"}))
    assert has(e, "recommend.value", "bad_value")


def test_check_value_provide_date_and_url():
    d = ok(ask("provide", input={"type": "date"}))
    assert core.check_value(d, "2026-02-28") == ("2026-02-28", None)
    for bad in ("2026-02-30", "2026-13-01", "2026/02/28", "28-02-2026", "tomorrow", "2026-02"):
        assert core.check_value(d, bad)[1]["reason"] == "date", bad
    u = ok(ask("provide", input={"type": "url"}))
    for good in ("https://example.com/a?b=1", "http://x"):
        assert core.check_value(u, good) == (good, None)
    for bad in ("ftp://x", "javascript:alert(1)", "https://a b", "https://a.com/<x>", "//example.com",
                "https://" + "a" * 301, "example.com"):
        assert core.check_value(u, bad)[1]["reason"] == "url", bad
    reasons = set()
    for a, v in ((ok(ask()), "?"), (ok(ask("choose")), "?"), (d, "?"), (u, "?"), (d, "")):
        reasons.add(core.check_value(a, v)[1]["reason"])
    assert reasons == {"yes_no", "choice", "date", "url", "empty"}


def test_check_comment_is_the_one_check_of_a_comment():
    L = core.LIMITS["comment"]
    assert core.check_comment("  ok, go  ") == "ok, go"
    assert core.check_comment("a\tb\r\nc") == "a b\nc"           # a tab is a space; a comment keeps its lines
    assert core.check_comment("a\x00b\x07c\u202ed\ud83d") == "abcd"
    for blank in (None, "", "   ", " \n\t ", "\x00\x07\u202e\ud800"):
        assert core.check_comment(blank) == "", repr(blank)      # optional; only control characters is no comment
    assert core.check_comment("c" * L) == "c" * L
    assert core.check_comment("c" * L + "\x00" * 50) == "c" * L   # the limit counts what is stored
    for bad in ("c" * (L + 1), "c" * (L + 1) + "\x00", 5, ["x"], {"a": 1}):
        with assert_raises("bad_request") as r:
            core.check_comment(bad)
        assert r.err.params == {"field": "comment"}, bad
    with tmpstore() as s:                                        # what a server checked is what is stored
        s.post("bot", [ask(id=f"c{i}") for i in range(3)])
        for i, text in enumerate(["  ok\x00 go\n\nnow  ", "\x00\x07", "x" * L]):
            ev = answer(s, id=f"c{i}", comment=text)
            want = core.check_comment(text)
            assert ev.get("comment", "") == want and core.check_comment(want) == want, text


def test_gate_runs():
    assert core.gate_runs(ok(ask()), "yes") is False               # no gate: never
    g = {"verb": ["x"], "expect": {"a": 1}}
    give = {"verb": ["x"], "expect": {"a": "$value"}, "value_arg": True}
    for step in ("confirm", "approve"):
        a = ok(ask(step, gate=g))
        assert core.gate_runs(a, "yes") is True
        assert core.gate_runs(a, "no") is False                    # refusing confirms nothing
    p = ok(ask("provide", gate=give))
    assert core.gate_runs(p, "no") is True                         # a typed "no" is a value
    c = ok(ask("choose", gate=give, options=[{"value": "no", "label": "N"}, {"value": "yes", "label": "Y"}],
               recommend={"value": "yes", "because": "b"}))
    assert core.gate_runs(c, "no") is True and core.gate_runs(c, "yes") is True


def test_expect_for():
    a = ok(ask("provide", gate={"verb": ["facts", "confirm", "unit_cost"], "value_arg": True,
                                "expect": {"items": {"unit_cost": "$value"},
                                           "list": ["$value", "x", {"k": "$value"}],
                                           "const": "$valuex", "n": 5, "$value": "key"}}))
    before = copy.deepcopy(a)
    assert core.expect_for(a, "4.5") == {"items": {"unit_cost": "4.5"},
                                         "list": ["4.5", "x", {"k": "4.5"}],
                                         "const": "$valuex", "n": 5, "$value": "key"}
    assert a == before, "expect_for must not mutate the ask"
    assert core.expect_for(a, "$other")["items"] == {"unit_cost": "$other"}
    got = {"items": {"unit_cost": 4.5}, "list": [4.5, "x", {"k": "4.5"}],
           "const": "$valuex", "n": 5.0, "$value": "key",
           "verb": "facts confirm", "market": "main", "version": 3}       # the envelope may say more
    assert core.matches(core.expect_for(a, "4.5"), got)
    assert not core.matches(core.expect_for(a, "4.6"), got)
    for deeper in (dict(got, items={"unit_cost": 4.5, "extra": 1}),      # what expect names is exact below the top
                   dict(got, list=[4.5, "x", {"k": "4.5", "z": 1}]),
                   dict(got, list=[4.5, "x", {"k": "4.5"}, "more"]),
                   {k: v for k, v in got.items() if k != "n"}):
        assert not core.matches(core.expect_for(a, "4.5"), deeper)


def test_matches():
    cases = [
        ({"a": 1}, {"a": 1, "b": 2}, True), ({"a": 1, "c": 3}, {"a": 1, "b": 2}, False),
        ({"a": {"b": "x"}}, {"a": {"b": "x"}}, True), ({"a": {"b": "x"}}, {"a": {"b": "x", "z": 1}}, False),
        ({"a": {"b": "x"}}, {"a": "x"}, False),
        ({}, {"a": 1}, True), ({}, 5, False), ({"a": 1}, [1], False),
        ([1, 2], [1, 2], True), ([1, 2], [2, 1], False), ([1, 2], [1, 2, 3], False), ([], [], True),
        ([1], 1, False), ("5", ["5"], False), ("5", {"a": "5"}, False),
        ("10", 10, True), (10, "10.0", True), ("1e1", 10, True), ("0.30", 0.3, True), (10, 10.0, True),
        ("10", "11", False), (10, 11, False),
        ("abc", "abc", True), ("abc", "abd", False), ("abc", "ABC", False), ("", "", True),
        (None, None, True), (None, "", False), ("", None, False),
        (True, True, True), (False, False, True), (True, 1, False), (1, True, False),
        (True, "true", False), (False, 0, False), ("yes", True, False),
        ({"a": [1, {"b": "2"}]}, {"a": [1.0, {"b": 2}]}, True),
        ({"a": [1, {"b": "2"}]}, {"a": [1.0, {"b": 2, "c": 3}]}, False),
    ]
    for want, got, expected in cases:
        assert core.matches(want, got) is expected, (want, got, expected)


def test_matches_the_top_level_may_say_more_and_nothing_below_it_may():
    want = {"items": {"unit_cost": "4.5"}}
    envelope = {"verb": "facts confirm", "market": "main", "version": 3, "meta": {"a": [1, {"b": 2}]}, "none": None}
    assert core.matches(want, {"items": {"unit_cost": "4.5"}, **envelope}) is True     # top-level extras pass
    assert core.matches(want, {**envelope, "items": {"unit_cost": 4.5}}) is True       # wherever they sit in the subject
    assert core.matches(want, {"items": {"unit_cost": "4.5", "wallet_pct": 90}}) is False     # a nested extra item fails
    assert core.matches(want, {"items": {"unit_cost": "4.5", "wallet_pct": 90}, **envelope}) is False
    assert core.matches(want, {"items": {}, **envelope}) is False
    assert core.matches(want, {"items": {"unit_cost": "4.6"}, **envelope}) is False
    # expect naming a key the subject lacks fails, at every level and for every kind of value
    for key_value in ("4.5", 0, "", None, False, [], {}, [1], {"a": 1}):
        assert core.matches({"k": key_value}, {"other": key_value}) is False, key_value
        assert core.matches({"k": key_value}, {}) is False, key_value
        assert core.matches({"a": {"k": key_value}}, {"a": {"other": key_value}}) is False, key_value
    assert core.matches({"a": {"b": 1, "c": 2}}, {"a": {"b": 1}}) is False           # a nested key missing
    assert core.matches({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "d": 2}}) is False   # same size, other name
    assert core.matches({"a": {}}, {"a": {"x": 1}}) is False and core.matches({"a": []}, {"a": [1]}) is False
    # the leniency is for a top-level object only: a list of objects is exact, and so is what is inside it
    assert core.matches([{"a": 1}], [{"a": 1}]) is True
    assert core.matches([{"a": 1}], [{"a": 1, "b": 2}]) is False
    assert core.matches([{"a": 1}, {"a": 2}], [{"a": 1}]) is False
    assert core.matches([{"a": 1}, {"a": 2}], [{"a": 2}, {"a": 1}]) is False


def test_matches_exactness_at_depth_with_lists_of_dicts():
    """Below the top a subject may not carry one key, item or object more or less
    than expect names, at any depth, through dicts in lists in dicts in lists."""
    shapes = [
        {"items": {"unit_cost": "4.5", "sku": ["a", "b"]}, "flag": True, "none": None},
        {"rows": [{"id": 1, "tags": [{"k": "x"}, {"k": "y", "deep": [{"z": [{"w": 1}]}]}]}, {"id": 2, "tags": []}]},
        {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}},
        {"l": [[{"a": 1}, [{"b": [2, {"c": 3}]}]], [[]]], "m": {"n": [{"o": {"p": [{"q": 1}]}}]}},
        {"a": {}, "b": [], "c": [{}], "d": [[], {}]},
    ]
    shapes += [rand_json(random.Random(seed)) for seed in range(80)]
    seen = {"nested": 0, "list": 0}
    for want in shapes:
        got = copy.deepcopy(want)
        assert core.matches(want, got) is True and got == want
        for path in paths(want, dict):
            g = copy.deepcopy(want)
            at(g, path)["zz"] = 1                                   # one key more
            assert core.matches(want, g) is (path == ()), (path, want)     # only the top level may have one
            for k in list(at(want, path)):
                g = copy.deepcopy(want)
                del at(g, path)[k]                                  # one key less
                assert core.matches(want, g) is False, (path, k, want)
                g = copy.deepcopy(want)
                at(g, path)[k] = "\u2603 changed"                    # one value other
                assert core.matches(want, g) is False, (path, k, want)
        for path in paths(want, list):
            g = copy.deepcopy(want)
            at(g, path).append("zz")                                # one item more
            assert core.matches(want, g) is False, (path, want)
            if at(want, path):
                g = copy.deepcopy(want)
                at(g, path).pop()                                   # one item less
                assert core.matches(want, g) is False, (path, want)
        seen["nested"] += len([p for p in paths(want, dict) if p])
        seen["list"] += len(list(paths(want, list)))
        for path in paths(want, dict):                              # an item swapped for an object of extras
            for k, v in at(want, path).items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    g = copy.deepcopy(want)
                    at(g, path)[k][0]["zz"] = 1
                    assert core.matches(want, g) is False, (path, k, want)
    assert seen["nested"] > 150 and seen["list"] > 100, seen       # the random shapes really do go deep


def test_matches_compares_numbers_by_value_at_every_level():
    want = {"a": 10, "n": {"b": "10"}, "l": ["1e1", 0.5, {"c": "0.50"}], "s": "abc"}
    got = {"a": "10.0", "n": {"b": 10.0}, "l": [10, "0.5", {"c": 0.5}], "s": "abc", "extra": 1}
    assert core.matches(want, got) is True
    for path, other in ((("a",), 11), (("n", "b"), "11"), (("l", 0), 11), (("l", 1), "0.51"), (("l", 2, "c"), 0.51),
                        (("s",), "abd")):
        g = copy.deepcopy(got)
        at(g, path[:-1])[path[-1]] = other
        assert core.matches(want, g) is False, path
    for kind in (True, False):                                      # a bool is never a number, nested or not
        assert core.matches({"a": {"b": kind}}, {"a": {"b": int(kind)}}) is False
        assert core.matches({"a": {"b": int(kind)}}, {"a": {"b": kind}}) is False
        assert core.matches({"a": [kind]}, {"a": [str(kind).lower()]}) is False
    assert core.matches({"a": {"b": "007"}}, {"a": {"b": 7}}) is True       # a number is its value, not its spelling
    assert core.matches({"id": "12345678901234567890"}, {"id": 12345678901234567891}) is False


def test_matches_survives_a_hostile_subject():
    """`matches` reads only as deep as `want` goes (an expect is at most a few
    levels deep): a subject nested 20000 levels, with numbers no string can hold,
    or with NaN, gets False, never an exception."""
    deep_list = deep_dict = None
    for _ in range(20000):
        deep_list, deep_dict = [deep_list], {"a": deep_dict}
    for want in ({"a": 1}, {"a": [1]}, {"a": {"a": 1}}, {"a": {"a": {"a": "x"}}}, {"a": "x"}, {"a": None}):
        for hostile in (deep_list, deep_dict, {"a": deep_list}, {"a": deep_dict}, {"a": [deep_dict]},
                        {"a": {"a": deep_dict}}, {"a": {"a": {"a": deep_list}}}):
            assert core.matches(want, hostile) is False, want
    assert core.matches("x", deep_list) is False and core.matches("x", deep_dict) is False
    big = 10 ** 5000                                               # too long for str(): int's own limit
    assert core.matches("1", big) is False and core.matches(big, "1") is False
    assert core.matches(big, big) is True and core.matches(big, big + 1) is False
    assert core.matches({"a": [big]}, {"a": [big + 1], "b": 1}) is False
    assert core.matches("1" * 6000, "1" * 6000) is True and core.matches("1" * 6000, "1" * 5999 + "2") is False
    assert core.matches("1e99999999999999999999999", "1e99999999999999999999998") is False
    nan, inf = float("nan"), float("inf")
    for other in (nan, inf, -inf, "nan", "inf", "-inf", "NaN", "sNaN", "Infinity"):
        assert core.matches(0, other) is False and core.matches("0", other) is False
        assert core.matches(other, 0) is False and core.matches(other, "0") is False
    assert core.matches({"a": nan}, {"a": nan}) is False           # NaN is not a value the human could have been shown
    for hostile in (None, True, 5, 4.5, "x", "", [], [1], {}, {"b": 1}, [{"a": 1}], [[{"a": 1}]], {"a": {"a": 1}, "b": 2}):
        for want in ({"a": 1}, {"a": {"a": 1}}, {"a": [{"a": 1}]}):
            core.matches(want, hostile)                            # no exception, whatever the answer
    many = {f"k{i}": i for i in range(100000)}                     # a wide subject is compared as fast as a narrow one
    assert core.matches({"k5": 5}, many) is True and core.matches({"a": {"k5": 5}}, {"a": many}) is False
    assert core.matches({"a": [1]}, {"a": list(range(100000))}) is False


def test_matches_random_shapes_never_raise_and_are_reflexive():
    rng = random.Random(7)
    for _ in range(500):
        a, b = rand_json(rng), rand_json(rng, rng.randint(0, 1) * 3)
        core.matches(a, b)                                          # never raises
        assert core.matches(a, copy.deepcopy(a)) is True, a
        if isinstance(a, dict) and isinstance(b, dict) and not (set(a) - set(b)):
            assert core.matches(a, {**b, **a}) is True              # top-level extras never hurt


# --------------------------------------------------------------- the fold --

def test_fold_full_lifecycle():
    a = ok(ask())
    h = core.subject_hash(a)
    E = [event(1, "ask", ask=a), event(2, "answer", id="tea-word", value="yes", subject=h, by="web:bob"),
         event(3, "applied", id="tea-word", where="the catalogue")]
    st = core.fold(E)
    s = st["asks"]["tea-word"]
    assert (s["status"], s["rev"], s["hash"], s["opened_seq"], s["by"]) == ("answered", 1, h, 1, "agent:bot")
    assert s["ask"] == a and s["answer"] is E[1] and s["applied"] is E[2] and s["withdrawn"] is None
    assert s["history"] == [E[1]] and s["revs"] == {h: a} and s["opened_at"] == E[0]["at"]
    assert st["order"] == ["tea-word"] and st["seq"] == 3 and st["problems"] == [] and st["messages"] == []
    assert core.open_asks(st) == [] and core.groups(st) == []
    w = core.fold([E[0], event(2, "withdraw", id="tea-word", reason="moot")])["asks"]["tea-word"]
    assert w["status"] == "withdrawn" and w["withdrawn"]["reason"] == "moot"
    r = core.fold(E[:2] + [event(3, "reopen", id="tea-word", answer_seq=2, by="web:bob")])["asks"]["tea-word"]
    assert r["status"] == "open" and r["answer"] is None and [e["type"] for e in r["history"]] == ["answer", "reopen"]


def test_fold_illegal_events_become_problems():
    a = ok(ask())
    A, ANS = event(1, "ask", ask=a), event(2, "answer", id="tea-word", value="yes", subject="s", by="web:bob")
    WD = event(2, "withdraw", id="tea-word", reason="r")
    AP = event(3, "applied", id="tea-word", where="w")
    RE = event(3, "reopen", id="tea-word", answer_seq=2, by="web:bob")
    cases = [   # (events, [(problem seq, type, code)], status afterwards)
        ([event(1, "answer", id="ghost", value="yes")], [(1, "answer", "unknown_id")], None),
        ([event(1, "withdraw", id="ghost")], [(1, "withdraw", "unknown_id")], None),
        ([event(1, "reopen", id="ghost")], [(1, "reopen", "unknown_id")], None),
        ([event(1, "applied", id="ghost")], [(1, "applied", "unknown_id")], None),
        ([A, WD, event(3, "answer", id="tea-word", value="yes")], [(3, "answer", "not_open")], "withdrawn"),
        ([A, ANS, event(3, "withdraw", id="tea-word")], [(3, "withdraw", "not_open")], "answered"),
        ([A, event(2, "reopen", id="tea-word")], [(2, "reopen", "not_open")], "open"),
        ([A, event(2, "applied", id="tea-word")], [(2, "applied", "not_open")], "open"),
        ([A, ANS, AP, event(4, "applied", id="tea-word")], [(4, "applied", "not_open")], "answered"),
        ([A, ANS, AP, event(4, "reopen", id="tea-word")], [(4, "reopen", "not_open")], "answered"),
        ([A, ANS, event(3, "answer", id="tea-word", value="no")], [(3, "answer", "not_open")], "answered"),
        ([A, ANS, event(3, "ask", ask=a)], [(3, "ask", "id_used")], "answered"),
        ([A, WD, event(3, "ask", ask=a)], [(3, "ask", "id_used")], "withdrawn"),
        ([A, WD, event(3, "withdraw", id="tea-word")], [(3, "withdraw", "not_open")], "withdrawn"),
        ([A, WD, RE], [(3, "reopen", "not_open")], "withdrawn"),
        ([A, event(2, "bogus", id="tea-word")], [(2, "bogus", "unknown_type")], "open"),
    ]
    for events, expected, status in cases:
        st = core.fold(events)
        got = [(p["seq"], p["type"], p["code"]) for p in st["problems"]]
        assert got == expected, (events, got)
        assert all(set(p) == {"seq", "type", "id", "code"} for p in st["problems"])
        if status:
            assert st["asks"]["tea-word"]["status"] == status, (events, status)
        legal = [e for e in events if e["seq"] not in {p[0] for p in expected}]
        clean = core.fold(legal)
        assert {k: v for k, v in st.items() if k not in ("problems", "seq")} == \
               {k: v for k, v in clean.items() if k not in ("problems", "seq")}, "an illegal event must change nothing"


def test_fold_revisions_and_history():
    a1, a2 = ok(ask()), ok(ask(why="New evidence arrived."))
    h1, h2 = core.subject_hash(a1), core.subject_hash(a2)
    assert h1 != h2
    E = [event(1, "ask", ask=a1), event(2, "ask", ask=a2),
         event(3, "answer", id="tea-word", value="yes", subject=h1, by="web:bob"),
         event(4, "reopen", id="tea-word", answer_seq=3, by="web:bob"),
         event(5, "answer", id="tea-word", value="no", subject=h2, by="web:bob")]
    s = core.fold(E)["asks"]["tea-word"]
    assert s["rev"] == 2 and s["hash"] == h2 and s["ask"] == a2 and s["opened_seq"] == 1
    assert s["revs"] == {h1: a1, h2: a2}                      # every version shown stays readable
    assert [e["seq"] for e in s["history"]] == [3, 4, 5] and s["answer"]["seq"] == 5
    a3 = ok(ask(why="third"))
    st = core.fold([event(1, "ask", ask=a3), event(2, "answer", id="tea-word", value="yes",
                                                   subject="unknown-hash", by="web:bob")])
    got = st["asks"]["tea-word"]
    assert got["answer"]["subject"] == "unknown-hash"
    assert got["answer"]["subject"] in got["revs"], "History reads revs[answer.subject]: it must always exist"


def test_fold_order_groups_messages_seq():
    mk = lambda i, g: ok(ask(id=i, group=g) if g else ask(id=i))   # noqa: E731
    E = [event(1, "ask", ask=mk("a", "Words")), event(2, "ask", ask=mk("b", "Money")),
         event(3, "ask", ask=mk("c", "Words")), event(4, "ask", ask=mk("d", None)),
         event(5, "say", text="hello"), event(6, "note", text="hi", by="web:bob"),
         event(9, "say", text="again")]
    st = core.fold(E)
    assert st["order"] == ["a", "b", "c", "d"] and st["seq"] == 9
    assert [m["text"] for m in st["messages"]] == ["hello", "hi", "again"]
    assert [(g, [x["id"] for x in asks]) for g, asks in core.groups(st)] == \
        [("Words", ["a", "c"]), ("Money", ["b"]), ("", ["d"])]
    st = core.fold(E + [event(10, "answer", id="a", value="yes", subject="x", by="web:bob"),
                        event(11, "withdraw", id="b", reason="r")])
    assert [x["id"] for x in core.open_asks(st)] == ["c", "d"]
    assert [(g, [x["id"] for x in asks]) for g, asks in core.groups(st)] == [("Words", ["c"]), ("", ["d"])]
    empty = core.fold([])
    assert empty == {"asks": {}, "order": [], "messages": [], "problems": [], "seq": 0}


def test_fold_is_pure():
    a = ok(ask())
    E = [event(1, "ask", ask=a), event(2, "say", text="x"),
         event(3, "answer", id="tea-word", value="yes", subject=core.subject_hash(a), by="web:bob"),
         event(4, "answer", id="ghost", value="yes")]
    before = copy.deepcopy(E)
    one, two = core.fold(E), core.fold(E)
    assert E == before and one == two


def test_state_of_a_missing_log_is_empty_and_creates_nothing():
    with tmpdir() as d:
        s = core.Store(os.path.join(d, "nope"))
        assert s.events() == [] and s.state() == core.fold([])
        assert not os.path.exists(s.dir)


# -------------------------------------------------------------- the store --

def test_store_needs_a_declared_folder():
    for bad in (None, ""):
        with assert_raises("no_dir"):
            core.Store(bad)
    with tmpdir() as d:
        s = core.Store(d)
        assert s.path == os.path.join(os.path.abspath(d), "events.jsonl")
        assert s.dir == os.path.abspath(d)


def test_post_new_same_updated():
    with tmpstore() as s:
        r = s.post("bot", [ask()])
        assert r == {"posted": ["tea-word"], "updated": [], "unchanged": [], "open": 1,
                     "budget": 10, "seq": 1, "dry_run": False}
        again = s.post("bot", [ask()])
        assert again["unchanged"] == ["tea-word"] and again["posted"] == [] and again["seq"] == 1
        reordered = dict(reversed(list(ask().items())))
        assert s.post("bot", [reordered])["unchanged"] == ["tea-word"]
        assert len(s.events()) == 1, "an identical ask writes nothing"
        h1 = shown(s)
        r = s.post("bot", [ask(why="A newer fact.")])
        assert r["updated"] == ["tea-word"] and r["posted"] == [] and r["seq"] == 2
        st = s.state()["asks"]["tea-word"]
        assert st["rev"] == 2 and st["opened_seq"] == 1 and st["hash"] != h1 and set(st["revs"]) == {h1, st["hash"]}
        e = s.events()[0]
        assert e["type"] == "ask" and e["by"] == "agent:bot" and "sig" not in e and e["ask"]["id"] == "tea-word"
        assert s.post("bot", [ask(id="second")])["open"] == 2
        assert s.state()["order"] == ["tea-word", "second"]


def test_post_refuses_a_used_id_and_writes_nothing():
    with tmpstore() as s:
        s.post("bot", [ask(), ask(id="other")])
        answer(s)
        s.withdraw("bot", ["other"], "not needed")
        before = raw_log(s)
        with assert_raises("id_used") as r:
            s.post("bot", [ask(id="fresh"), ask(why="reworded")])
        assert r.err.params == {"id": "tea-word", "status": "answered"}
        with assert_raises("id_used") as r:
            s.post("bot", [ask(id="other")])
        assert r.err.params["status"] == "withdrawn"
        assert raw_log(s) == before, "all or nothing: `fresh` must not have been written"


def test_budget():
    with tmpstore() as s:
        assert core.MAX_OPEN == 10
        s.post("bot", [ask(id=f"a{i}") for i in range(10)])
        with assert_raises("over_budget") as r:
            s.post("bot", [ask(id="one-too-many")])
        assert r.err.params["max"] == 10 and r.err.params["adding"] == ["one-too-many"]
        assert len(r.err.params["open"]) == 10
        before = raw_log(s)
        r = s.post("bot", [ask(id="a3", why="reworded, not new")])          # update: not counted
        assert r["updated"] == ["a3"] and r["open"] == 10
        assert s.post("bot", [ask(id="a4")])["unchanged"] == ["a4"]
        assert raw_log(s) != before
        answer(s, id="a0")                                                  # an answer frees a slot
        assert s.post("bot", [ask(id="fresh")])["posted"] == ["fresh"]
        with assert_raises("over_budget"):
            s.post("bot", [ask(id="again")])
        s.withdraw("bot", ["a1"], "moot")                                   # so does a withdrawal
        assert s.post("bot", [ask(id="again")])["open"] == 10
        with assert_raises("over_budget"):                                  # only NEW asks count in a batch
            s.post("bot", [ask(id="n1"), ask(id="n2"), ask(id="a5", why="also reworded")])
        assert "n1" not in s.state()["asks"], "a refused batch writes nothing"
    with tmpstore() as s:
        s.post("bot", [ask(id="x"), ask(id="y")], max_open=2)
        with assert_raises("over_budget") as r:
            s.post("bot", [ask(id="z")], max_open=2)
        assert r.err.params["max"] == 2
        with assert_raises("over_budget"):
            s.post("bot", [ask(id=f"n{i}") for i in range(11)], dry_run=True)      # a dry run refuses too


def test_max_open_can_only_be_lowered():
    with tmpstore() as s:
        r = s.post("bot", [ask(id=f"a{i}") for i in range(10)], max_open=99)
        assert r["budget"] == 10 and r["open"] == 10
        with assert_raises("over_budget") as e:
            s.post("bot", [ask(id="eleventh")], max_open=99)
        assert e.err.params["max"] == 10 and "eleventh" not in s.state()["asks"]
        with assert_raises("over_budget"):
            s.post("bot", [ask(id="eleventh")], max_open=10 ** 9, dry_run=True)
        assert s.post("bot", [ask(id="a1", why="reworded")], max_open=99)["updated"] == ["a1"]
    with tmpstore() as s:
        assert s.post("bot", [ask(id="x")], max_open=1)["budget"] == 1
        with assert_raises("over_budget") as e:
            s.post("bot", [ask(id="y")], max_open=1)
        assert e.err.params["max"] == 1
        with assert_raises("over_budget"):
            s.post("bot", [ask(id="y")], max_open=0)
        assert s.post("bot", [ask(id="x", why="reworded")], max_open=0)["updated"] == ["x"]   # 0 freezes: updates only


def test_dry_run_writes_no_event():
    with tmpstore() as s:
        s.post("bot", [ask()])
        before = raw_log(s)
        r = s.post("bot", [ask(id="new"), ask(why="changed"), ], dry_run=True)
        assert r["dry_run"] is True and r["posted"] == ["new"] and r["updated"] == ["tea-word"]
        assert r["open"] == 2 and r["seq"] == 1
        assert raw_log(s) == before and "new" not in s.state()["asks"]
        assert s.post("bot", [ask(id="new")])["seq"] == 2            # and the next real post is seq 2


def test_torn_last_line_is_cut_off():
    with tmpstore() as s:
        s.say("bot", "one")
        s.say("bot", "two")
        good = raw_log(s)
        full = json.dumps(event(3, "say", text="café is open"), ensure_ascii=False).encode()
        torn = full[:full.index("é".encode()) + 1]                  # cut inside a 2-byte character
        put(s, good + torn)
        assert [e["seq"] for e in s.events()] == [1, 2] and s.state()["seq"] == 2
        assert raw_log(s) == good + torn, "reading never rewrites the file"
        s.say("bot", "three")
        assert torn not in raw_log(s)
        assert [e["seq"] for e in check_log(s, 3)] == [1, 2, 3]
        put(s, raw_log(s) + torn)
        s.note("bob", "human write cuts it too")
        check_log(s, 4)
        put(s, b'{"seq":1,"at":"x","by":"agent:bot","type":"say"')   # a file that is ONLY a torn line
        assert s.events() == []
        s.say("bot", "fresh")
        assert [e["seq"] for e in check_log(s, 1)] == [1]


def test_a_blank_line_in_the_log_is_skipped_not_corrupt():
    with tmpstore() as s:
        s.post("bot", [ask()])
        s.say("bot", "one")
        e1, e2 = raw_log(s).split(b"\n")[:2]
        for blank in (b"\n", b"   \n", b"\r\n", b"\t \r\n"):
            put(s, blank + e1 + b"\n" + blank + blank + e2 + b"\n" + blank)
            st = s.state()
            assert [e["seq"] for e in s.events()] == [1, 2] and st["problems"] == [] and st["seq"] == 2
            assert s.say("bot", "two")["seq"] == 3                     # a write goes on from the last real seq
            assert [e["seq"] for e in s.events()] == [1, 2, 3]
            assert raw_log(s).startswith(blank + e1), "blank lines are left where they were"
        put(s, e1 + b"\n" + b"  ")                                     # spaces with no newline: a torn tail, cut
        assert [e["seq"] for e in s.events()] == [1]
        s.say("bot", "again")
        assert raw_log(s).startswith(e1 + b"\n") and not raw_log(s).endswith(b"  ")
        put(s, b"\n\n")                                               # a file of only blanks is an empty log
        assert s.events() == [] and s.say("bot", "first")["seq"] == 1
        put(s, b"\n" + e1 + b"\n\n" + b"garbage\n")                    # and line numbers still count the blanks
        with assert_raises("corrupt_log") as r:
            s.events()
        assert r.err.params == {"line": 4}


def test_every_event_is_fsynced_once_its_line_is_written():
    """The size the file has at each fsync is the end of one more whole line:
    fsync comes after the write, once per event, never batched or skipped."""
    with tmpstore() as s:
        real, sizes = os.fsync, []

        def spy(fd):
            sizes.append(os.fstat(fd).st_size)
            return real(fd)
        os.fsync = spy
        try:
            s.post("bot", [ask(id="a"), ask(id="b"), ask(id="c")])     # three events in one call
            answer(s, id="a")                                           # a signed human event
            s.say("bot", "x")
            s.post("bot", [ask(id="b")])                                # unchanged: no event, no fsync
        finally:
            os.fsync = real
        ends, n = [], 0
        for line in raw_log(s).split(b"\n")[:-1]:
            n += len(line) + 1
            ends.append(n)
        assert len(ends) == 5 and sizes == ends, (sizes, ends)


def test_a_failed_write_is_not_acknowledged_and_does_not_poison_the_log():
    """A write or an fsync that fails raises: the caller is never told an event
    was saved that the disk did not take, and the next write starts clean."""
    real_write, real_fsync = os.write, os.fsync
    with tmpstore() as s:
        s.say("bot", "one")
        good = raw_log(s)

        def no_space(fd, data):
            raise OSError(errno.ENOSPC, "no space left")
        os.write = no_space
        try:
            with _raises(OSError):
                s.say("bot", "lost")
            with _raises(OSError):
                s.post("bot", [ask()])
        finally:
            os.write = real_write
        assert raw_log(s) == good

        calls = []

        def half_then_full(fd, data):                     # half the line, then the disk fills
            calls.append(1)
            if len(calls) == 1:
                return real_write(fd, bytes(data)[:len(data) // 2])
            raise OSError(errno.ENOSPC, "no space left")
        os.write = half_then_full
        try:
            with _raises(OSError):
                s.note("bob", "also lost")
        finally:
            os.write = real_write
        assert len(raw_log(s)) > len(good) and [e["seq"] for e in s.events()] == [1]   # a torn tail, not an event
        assert s.say("bot", "two")["seq"] == 2                                          # cut off, and no gap in seq
        check_log(s, 2)

        def failing_fsync(fd):
            raise OSError(errno.EIO, "input/output error")
        os.fsync = failing_fsync
        try:
            with _raises(OSError):
                s.say("bot", "not known to be on disk")
        finally:
            os.fsync = real_fsync


def test_corrupt_middle_line_is_refused_and_nothing_is_written():
    l1 = json.dumps(event(1, "say", text="one")).encode() + b"\n"
    l3 = json.dumps(event(3, "say", text="three")).encode() + b"\n"
    bads = [b"not json\n", b"[1]\n", b'"str"\n', b"null\n", b"\xff\xfe\x00\n",
            b'{"type":"say"}\n', b'{"seq":2}\n']
    for bad in bads:
        for tail in (b"", b'{"seq":4,"torn'):
            with tmpstore() as s:
                data = l1 + bad + l3 + tail
                put(s, data)
                calls = [lambda: s.events(), lambda: s.state(), lambda: s.say("bot", "x"),
                         lambda: s.post("bot", [ask()]), lambda: s.note("bob", "x"),
                         lambda: s.withdraw("bot", ["tea-word"], "r"),
                         lambda: s.applied("bot", ["tea-word"], "w"),
                         lambda: s.answer("bob", "tea-word", "yes", shown="h"),
                         lambda: s.reopen("bob", "tea-word", 1)]
                for call in calls:
                    with assert_raises("corrupt_log") as r:
                        call()
                    assert r.err.params == {"line": 2}, (bad, r.err.params)
                    assert "2" in str(r.err)
                assert raw_log(s) == data, "a refused write must leave the file exactly as it was"


def test_a_line_without_the_fields_the_readers_need_is_corrupt():
    """Every event has `at` and `by` as text; an answer has its `value` as text,
    a say or a note its `text`, a reopen its `answer_seq` as an integer. A line
    that lacks one, or holds another type, is corrupt at its line number: the
    pages and the agent's verbs read those fields, and the file is append-only,
    so one such line would break them for good."""
    def line(ev):
        return (json.dumps(ev, ensure_ascii=False) + "\n").encode()
    a = ok(ask())
    good = {
        "ask": event(2, "ask", ask=ok(ask(id="second"))),
        "answer": event(2, "answer", id="tea-word", value="yes", subject=core.subject_hash(a), by="web:bob"),
        "reopen": event(2, "reopen", id="tea-word", answer_seq=2, by="web:bob"),
        "note": event(2, "note", text="hi", by="web:bob"),
        "say": event(2, "say", text="hi"),
        "withdraw": event(2, "withdraw", id="tea-word", reason="r"),
        "applied": event(2, "applied", id="tea-word", where="w"),
    }
    first = line(event(1, "ask", ask=a))
    without = lambda ev, k: {x: v for x, v in ev.items() if x != k}   # noqa: E731
    odd = (5, None, ["x"], {"a": 1}, True, 1.5)
    bad = []                                                         # (what is wrong, the event)
    for t, ev in good.items():
        for k in ("at", "by"):
            bad += [(f"{t} without {k}", without(ev, k))] + [(f"{t} {k}={o!r}", dict(ev, **{k: o})) for o in odd]
    bad += [("answer without value", without(good["answer"], "value"))]
    bad += [(f"answer value={o!r}", dict(good["answer"], value=o)) for o in odd]
    for t in ("say", "note"):
        bad += [(f"{t} without text", without(good[t], "text"))] + [(f"{t} text={o!r}", dict(good[t], text=o)) for o in odd]
    bad += [("reopen without answer_seq", without(good["reopen"], "answer_seq"))]
    bad += [(f"reopen answer_seq={o!r}", dict(good["reopen"], answer_seq=o)) for o in ("2", 2.0, True, False, None, [2], {"a": 2})]
    assert len(bad) > 100
    with tmpstore() as s:
        for name, ev in good.items():                                # the complete event is fine
            put(s, first + line(ev))
            assert [e["seq"] for e in s.events()] == [1, 2], name
            s.state()
        for what, ev in bad:
            data = first + line(ev)
            put(s, data)
            for call in (s.events, s.state, lambda: s.say("bot", "x"), lambda: s.note("bob", "x"),
                         lambda: s.answer("bob", "tea-word", "yes", shown="h")):
                with assert_raises("corrupt_log") as r:
                    call()
                assert r.err.params == {"line": 2}, (what, r.err.params)
            assert raw_log(s) == data, f"{what}: a refused write must leave the file as it was"


def test_answer_binds_to_the_shown_hash():
    with tmpstore() as s:
        s.post("bot", [ask()])
        h1 = shown(s)
        s.post("bot", [ask(why="The catalogue was updated.")])       # the page still shows h1
        before = raw_log(s)
        with assert_raises("changed") as r:
            s.answer("bob", "tea-word", "yes", shown=h1)
        assert r.err.params == {"id": "tea-word"} and raw_log(s) == before
        for bad in ("", "0" * 16, "nonsense"):
            with assert_raises("changed"):
                s.answer("bob", "tea-word", "yes", shown=bad)
        s.post("bot", [ask(why="The catalogue was updated.")])       # same again: hash is unchanged
        ev = s.answer("bob", "tea-word", "yes", shown=shown(s))
        assert ev["subject"] == shown(s) and ev["type"] == "answer" and "revised" not in ev
    with tmpstore() as s:                                            # A -> B -> A: the page shows A again
        s.post("bot", [ask()])
        h1 = shown(s)
        s.post("bot", [ask(why="B")])
        s.post("bot", [ask()])
        assert shown(s) == h1
        assert s.answer("bob", "tea-word", "yes", shown=h1)["subject"] == h1
    with tmpstore() as s:                                            # answers to what is not open
        with assert_raises("unknown_id"):
            s.answer("bob", "ghost", "yes", shown="h")
        s.post("bot", [ask(), ask(id="w")])
        s.withdraw("bot", ["w"], "r")
        with assert_raises("not_open") as r:
            s.answer("bob", "w", "yes", shown="h")
        assert r.err.params["status"] == "withdrawn"
        answer(s)
        with assert_raises("not_open") as r:
            answer(s, "no")
        assert r.err.params == {"id": "tea-word", "status": "answered"}


def test_answer_event_shape():
    with tmpstore() as s:
        s.post("bot", [ask(), ask("choose", id="pick"), ask("provide", id="num")])
        ev = answer(s, " yes ", comment="  ok, go  ")
        assert ev["value"] == "yes" and ev["comment"] == "ok, go" and ev["suggested"] is True
        assert ev["by"] == "web:bob" and ev["id"] == "tea-word" and ev["seq"] == 4 and "gate" not in ev
        assert ev["subject"] == s.state()["asks"]["tea-word"]["hash"] and core.verify(s.secret(), ev) is True
        ev = answer(s, "b", id="pick")
        assert ev["suggested"] is False and "comment" not in ev
        ev = answer(s, "42", id="num")
        assert ev["suggested"] is False                                  # a provide ask with no recommend
        assert s.events()[-1] == ev
        s.post("bot", [ask(id="late")])
        before = raw_log(s)
        for value in ("maybe", "", None):
            with assert_raises("bad_value") as r:
                answer(s, value, id="late")
            assert r.err.params["reason"] == "yes_no"
        assert raw_log(s) == before
        with assert_raises("bad_request") as r:
            answer(s, "yes", id="late", comment="c" * (core.LIMITS["comment"] + 1))
        assert r.err.params == {"field": "comment"} and raw_log(s) == before
        assert answer(s, "yes", id="late", comment="c" * core.LIMITS["comment"])["comment"] == "c" * 500


def test_gate_ran_answer_is_recorded_even_if_the_ask_was_revised():
    gate = {"ok": True, "verb": ["facts", "confirm", "unit_cost"], "message": "confirmed"}
    with tmpstore() as s:
        s.post("bot", [gated()])
        h1 = shown(s, "unit-cost")
        s.post("bot", [gated(why="A newer fact.")])                   # revised while the page was open
        ev = s.answer("bob", "unit-cost", "5", shown=h1, gate=gate)
        assert ev["gate"] == gate and ev["revised"] is True and ev["subject"] == h1 and ev["value"] == "5"
        st = s.state()["asks"]["unit-cost"]
        assert st["status"] == "answered" and st["revs"][h1]["why"] != st["ask"]["why"]
        assert st["revs"][h1]["why"] == "The owner's emails say blends; the catalogue says mixes."
        assert core.verify(s.secret(), ev) is True
    with tmpstore() as s:                                                # current hash: not revised
        s.post("bot", [gated()])
        ev = s.answer("bob", "unit-cost", "5", shown=shown(s, "unit-cost"), gate=gate)
        assert ev["gate"] == gate and "revised" not in ev


def test_a_stale_page_answers_only_what_the_harness_really_wrote():
    G = {"ok": True, "verb": ["x"], "message": "m"}
    with tmpstore() as s:                                        # gated, updated twice while the page shows the first
        s.post("bot", [gated()])
        g1 = shown(s, "unit-cost")
        s.post("bot", [gated(why="B")])
        s.post("bot", [gated(why="C")])
        with assert_raises("gate_unavailable"):
            s.answer("bob", "unit-cost", "5", shown=g1)
        ev = s.answer("bob", "unit-cost", "5", shown=g1, gate=G)
        assert ev["subject"] == g1 and ev["revised"] is True and ev["gate"] == G
        assert s.state()["asks"]["unit-cost"]["revs"][g1]["why"] == gated()["why"]
    with tmpstore() as s:                                        # not gated: a stale page never answers, with or without a proof
        s.post("bot", [ask()])
        h1 = shown(s)
        s.post("bot", [ask(why="B")])
        s.post("bot", [ask(why="C")])
        for gate in (None, G):
            with assert_raises("changed"):
                s.answer("bob", "tea-word", "yes", shown=h1, gate=gate)
    with tmpstore() as s:                                        # a gate that came in after the page was drawn
        s.post("bot", [ask("provide", id="unit-cost")])
        u1 = shown(s, "unit-cost")
        s.post("bot", [gated()])
        with assert_raises() as r:                               # not through the stale page without the harness…
            s.answer("bob", "unit-cost", "5", shown=u1)
        assert r.err.code in ("gate_unavailable", "changed")
        with assert_raises("changed"):                           # …and no claim of a gate the page never showed
            s.answer("bob", "unit-cost", "5", shown=u1, gate=G)
        assert s.state()["asks"]["unit-cost"]["status"] == "open"
    with tmpstore() as s:                                        # a gate that went away: the harness was written
        s.post("bot", [gated()])                                  # for what the owner was shown
        g1 = shown(s, "unit-cost")
        s.post("bot", [ask("provide", id="unit-cost")])
        with assert_raises("changed"):
            s.answer("bob", "unit-cost", "5", shown=g1)
        assert s.answer("bob", "unit-cost", "5", shown=g1, gate=G)["revised"] is True


def test_reopen_and_applied_rules():
    with tmpstore() as s:
        s.post("bot", [ask(), ask(id="b")])
        with assert_raises("not_answered") as r:
            s.reopen("bob", "tea-word", 3)
        assert r.err.params["status"] == "open"
        with assert_raises("not_answered"):
            s.applied("bot", ["tea-word"], "the catalogue")
        with assert_raises("unknown_id"):
            s.reopen("bob", "ghost", 1)
        with assert_raises("unknown_id"):
            s.applied("bot", ["ghost"], "w")
        ev = answer(s)
        with assert_raises("changed"):
            s.reopen("bob", "tea-word", ev["seq"] + 1)
        assert s.state()["asks"]["tea-word"]["status"] == "answered"
        ro = s.reopen("bob", "tea-word", ev["seq"])
        assert ro["type"] == "reopen" and ro["answer_seq"] == ev["seq"] and ro["by"] == "web:bob"
        assert core.verify(s.secret(), ro) is True
        st = s.state()["asks"]["tea-word"]
        assert st["status"] == "open" and st["answer"] is None and len(st["history"]) == 2
        ev2 = answer(s, "no")
        with assert_raises("changed"):                          # the old page's reopen must not undo the new answer
            s.reopen("bob", "tea-word", ev["seq"])
        before = raw_log(s)
        with assert_raises("not_answered"):                     # all or nothing: `b` is still open
            s.applied("bot", ["tea-word", "b"], "the catalogue")
        assert raw_log(s) == before
        ap = s.applied("bot", ["tea-word"], "  the catalogue  ")
        assert ap == {"applied": ["tea-word"], "seq": ev2["seq"] + 1}
        last = s.events()[-1]
        assert last["type"] == "applied" and last["where"] == "the catalogue" and last["by"] == "agent:bot"
        assert "sig" not in last
        with assert_raises("already_applied"):
            s.reopen("bob", "tea-word", ev2["seq"])
        assert s.state()["asks"]["tea-word"]["applied"] == last
        with assert_raises("bad_request") as r:
            s.applied("bot", ["b"], "  ")
        assert r.err.params == {"field": "where"}
        s.post("bot", [ask(id="c")])
        answer(s, id="c")
        with assert_raises("bad_request"):
            s.applied("bot", ["c"], "w" * (core.LIMITS["where"] + 1))
        assert s.applied("bot", ["c"], "w" * core.LIMITS["where"])["applied"] == ["c"]


def test_reopen_is_refused_once_the_harness_wrote():
    """An answer that passed the harness's gate is already written there: putting
    the ask back in Waiting would tell the owner it is undone. It is refused with
    its own words; a refusal, or an answer that wrote nothing, can still be reopened."""
    G = {"ok": True, "verb": ["facts", "confirm", "unit_cost"], "message": "confirmed"}
    yes_no = {"verb": ["queue", "approve"], "expect": {"n": 1}}
    with tmpstore() as s:
        s.post("bot", [gated(), ask("confirm", id="approval", gate=yes_no), ask(id="plain")])
        wrote = s.answer("bob", "unit-cost", "5", shown=shown(s, "unit-cost"), gate=G)
        before = raw_log(s)
        with assert_raises("already_applied") as r:
            s.reopen("bob", "unit-cost", wrote["seq"])
        assert r.err.params == {"id": "unit-cost"} and raw_log(s) == before
        assert str(r.err) != core.CODES["already_applied"] and "system" in str(r.err).lower()
        assert s.state()["asks"]["unit-cost"]["status"] == "answered" and len(s.state()["asks"]["unit-cost"]["history"]) == 1
        with assert_raises() as r:                                   # a stale page's reopen is refused too
            s.reopen("bob", "unit-cost", wrote["seq"] + 5)
        assert r.err.code in ("already_applied", "changed") and raw_log(s) == before
        # a "no" to a gated yes/no runs no gate and writes nothing there: it can be taken back
        no = answer(s, "no", id="approval")
        assert no.get("gate") is None
        assert s.reopen("bob", "approval", no["seq"])["type"] == "reopen" and s.state()["asks"]["approval"]["status"] == "open"
        yes = s.answer("bob", "approval", "yes", shown=shown(s, "approval"), gate=G)     # a "yes" does write
        with assert_raises("already_applied") as r:
            s.reopen("bob", "approval", yes["seq"])
        assert str(r.err) != core.CODES["already_applied"]
        # an answer with no gate at all is the owner's alone: reopen works; once applied it is refused in the usual words
        plain = answer(s, id="plain")
        assert s.reopen("bob", "plain", plain["seq"])["answer_seq"] == plain["seq"]
        again = answer(s, "no", id="plain")
        s.applied("bot", ["plain"], "nowhere")
        with assert_raises("already_applied") as r:
            s.reopen("bob", "plain", again["seq"])
        assert str(r.err) == core.CODES["already_applied"]
        # the agent can still apply what the harness wrote
        assert s.applied("bot", ["unit-cost"], "the harness")["applied"] == ["unit-cost"]
        assert s.state()["problems"] == []
    with tmpstore() as s:                                            # a revised ask changes nothing: the harness was written
        s.post("bot", [gated()])
        h1 = shown(s, "unit-cost")
        s.post("bot", [gated(why="A newer fact.")])
        ev = s.answer("bob", "unit-cost", "5", shown=h1, gate=G)
        assert ev["revised"] is True
        with assert_raises("already_applied"):
            s.reopen("bob", "unit-cost", ev["seq"])


def test_applied_seqs_refuse_an_answer_the_agent_never_read():
    with tmpstore() as s:
        s.post("bot", [ask(id=i) for i in "abcde"])
        A, B = answer(s, id="a"), answer(s, id="b")
        s.reopen("bob", "a", A["seq"])                           # the agent read A; the human changed their mind
        A2 = answer(s, "no", id="a")
        assert A2["seq"] > A["seq"]
        before = raw_log(s)
        with assert_raises("changed") as r:                      # a stale seq, and all or nothing: b is not applied
            s.applied("bot", ["b", "a"], "the catalogue", seqs={"a": A["seq"], "b": B["seq"]})
        assert r.err.params == {"id": "a"} and raw_log(s) == before
        C = answer(s, id="c")                                    # the same words again are still a new answer
        s.reopen("bob", "c", C["seq"])
        C2 = answer(s, id="c")
        assert C2["value"] == C["value"] and C2["seq"] != C["seq"]
        with assert_raises("changed"):
            s.applied("bot", ["c"], "the catalogue", seqs={"c": C["seq"]})
        E = answer(s, id="e")                                    # reopened and not yet answered again: not answered
        s.reopen("bob", "e", E["seq"])
        with assert_raises("not_answered"):
            s.applied("bot", ["e"], "the catalogue", seqs={"e": E["seq"]})
        assert not [e for e in s.events() if e["type"] == "applied"] and s.state()["problems"] == []
        # the matching seq is applied; an id without an entry is unchecked; an entry for an id not listed is ignored
        r = s.applied("bot", ["a", "b"], "the catalogue", seqs={"a": A2["seq"], "zzz": 1})
        assert r["applied"] == ["a", "b"]
        s.applied("bot", ["c"], "the catalogue", seqs={})       # nothing to check
        s.post("bot", [ask(id="d")])
        answer(s, id="d")
        s.applied("bot", ["d"], "the catalogue", seqs=None)
        applied = [e for e in s.events() if e["type"] == "applied"]
        assert [e["id"] for e in applied] == ["a", "b", "c", "d"] and s.state()["problems"] == []


def test_withdraw_rules():
    with tmpstore() as s:
        s.post("bot", [ask(), ask(id="b"), ask(id="c")])
        answer(s, id="b")
        before = raw_log(s)
        with assert_raises("not_open") as r:                      # answered asks are not the agent's to withdraw
            s.withdraw("bot", ["c", "b"], "moot")
        assert r.err.params["id"] == "b" and raw_log(s) == before
        with assert_raises("unknown_id"):
            s.withdraw("bot", ["c", "ghost"], "moot")
        with assert_raises("bad_request") as r:
            s.withdraw("bot", ["c"], "")
        assert r.err.params == {"field": "reason"}
        with assert_raises("bad_request"):
            s.withdraw("bot", ["c"], "r" * 201)
        assert raw_log(s) == before
        r = s.withdraw("bot", ["tea-word", "c"], " no longer needed ")
        assert r["withdrawn"] == ["tea-word", "c"]
        w = s.state()["asks"]["c"]["withdrawn"]
        assert w["reason"] == "no longer needed" and w["by"] == "agent:bot" and w["type"] == "withdraw"
        with assert_raises("not_open"):
            s.withdraw("bot", ["c"], "again")


def test_say_and_note_texts():
    with tmpstore() as s:
        assert s.say("bot", " hi\x00 there\nsecond line ") == {"seq": 1}
        assert s.events()[0]["text"] == "hi there\nsecond line"
        s.say("bot", "x" * 400)
        s.note("bob", "y" * 1000)
        before = raw_log(s)
        for call, field in ((lambda: s.say("bot", "x" * 401), "text"), (lambda: s.note("bob", "y" * 1001), "text"),
                            (lambda: s.say("bot", "   "), "text"), (lambda: s.note("bob", ""), "text"),
                            (lambda: s.say("bot", 5), "text")):
            with assert_raises("bad_request") as r:
                call()
            assert r.err.params == {"field": field}
        assert raw_log(s) == before
        assert [m["text"] for m in s.state()["messages"]] == ["hi there\nsecond line", "x" * 400, "y" * 1000]


def test_names_are_plain_tokens():
    with tmpstore() as s:
        for bad in ("", None, "-x", "--force", " bob", "a b", "a;b", "a$(b)", "é", "x" * 81, "a|b"):
            with assert_raises("bad_request"):
                s.say(bad, "hi")
            with assert_raises("bad_request"):
                s.note(bad, "hi")
            with assert_raises("bad_request"):
                s.post(bad, [ask()])
        assert raw_log(s) == b""
        for good in ("bob", "web:bob", "a@b.co", "x/y", "A1_-.:@/", "x" * 80):
            s.say(good, "hi")
        assert s.events()[0]["by"] == "agent:bob" and s.events()[-1]["by"] == f"agent:{'x' * 80}"
        s.note("bob", "hi")
        assert s.events()[-1]["by"] == "web:bob"


def test_id_and_token_and_url_patterns():
    for good in ("a", "a-b_c.d", "0abc", "a" * 64):
        assert core.ID_RE.match(good), good
    for bad in ("", "A", "-a", ".a", "_a", "a b", "é", "a" * 65, "a/b"):
        assert not core.ID_RE.match(bad), bad
    for bad in ("-x", "--flag", "", " x", "x y", "é", "x;y", "x$(y)", "x|y", "x*", "x" * 81):
        assert not core.TOKEN_RE.match(bad), bad
    for good in ("https://example.com", "http://x/y?z=1#f"):
        assert core.URL_RE.match(good)
    for bad in ("ftp://x", "javascript:x", "https://", "https://a b", "https://<x>", "example.com"):
        assert not core.URL_RE.match(bad), bad


def test_canon_and_hash():
    assert core.canon({"b": 1, "a": [1, {"z": 1, "y": "é"}]}) == '{"a":[1,{"y":"é","z":1}],"b":1}'
    a = ok(ask())
    h = core.subject_hash(a)
    assert re.fullmatch(r"[0-9a-f]+", h)
    assert core.subject_hash(dict(reversed(list(a.items())))) == h
    assert core.subject_hash(copy.deepcopy(a)) == h
    for k in a:
        b = copy.deepcopy(a)
        b[k] = b[k] + "!" if isinstance(b[k], str) else {"changed": 1}
        assert core.subject_hash(b) != h, k
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", core.now())


def test_every_refusal_code_is_registered():
    src = open(os.path.join(C_DIR, "core.py"), encoding="utf-8").read()
    used = set(re.findall(r'Refused\(\s*"([a-z_]+)"', src))
    assert used and used <= set(core.CODES), used - set(core.CODES)
    assert set(re.findall(r'"code": "([a-z_]+)"', src)) <= set(core.FIELD_CODES) | set(core.CODES)
    with _raises(AssertionError):
        core.Refused("no_such_code")
    r = core.Refused("changed", id="x")
    assert r.doc() == {"ok": False, "code": "changed", "message": core.CODES["changed"], "params": {"id": "x"}}
    r = core.Refused("bad_request", "custom words", field="f")
    assert str(r) == "custom words" and r.doc()["message"] == "custom words" and r.doc()["params"] == {"field": "f"}
    for code, msg in core.CODES.items():
        assert re.fullmatch(r"[a-z_]+", code) and isinstance(msg, str) and msg
    assert json.loads(json.dumps(core.Refused("over_budget", open=["a"], max=10).doc()))["ok"] is False


def test_gate_refusal_messages_do_not_claim_nothing_was_written():
    """After the second harness call something may have been written, so the
    words for a refusal, a timeout or an unclear answer there must not say it
    was not; an unclear one says it may or may not have been, and to check."""
    for code in ("gate_refused", "gate_timeout", "gate_unsure"):
        msg = core.CODES[code]
        assert not re.search(r"nothing|not (been )?(written|saved)|unchanged|no change", msg, re.I), (code, msg)
        assert core.Refused(code).doc()["message"] == msg
    for code in ("gate_timeout", "gate_unsure", "not_recorded"):
        assert "check" in core.CODES[code].lower(), code             # it tells the agent to look
    assert "may or may not" in core.CODES["gate_unsure"]
    assert "was written" not in core.CODES["gate_unsure"]           # it does not claim either


def test_the_codes_for_an_unclear_harness_and_a_missing_secret():
    for code in ("gate_unsure", "no_secret"):
        assert code in core.CODES
        r = core.Refused(code, id="x")
        assert r.doc() == {"ok": False, "code": code, "message": core.CODES[code], "params": {"id": "x"}}
        assert json.loads(json.dumps(r.doc(), allow_nan=False))["code"] == code
        with assert_raises(code):
            raise core.Refused(code)
    assert "CONSOLE_SECRET" in core.CODES["no_secret"]              # it says how to have one
    assert core.CODES["no_secret"] != core.CODES["bad_request"] and core.CODES["gate_unsure"] != core.CODES["gate_refused"]


def test_a_retired_advice_event_in_an_old_log_is_set_aside_not_a_crash():
    """Logs written while team review existed hold `advice` events. They are no
    type now: the fold sets each aside as `unknown_type` and the ask is untouched."""
    a = ok(ask())
    old = event(2, "advice", id="tea-word", on_seq=1, stance="disagree", reason="r", by="web:carol")
    st = core.fold([event(1, "ask", ask=a), old])
    assert [(p["seq"], p["code"]) for p in st["problems"]] == [(2, "unknown_type")]
    assert st["asks"]["tea-word"]["status"] == "open" and st["seq"] == 2
    with tmpstore() as s:
        put(s, (json.dumps(event(1, "ask", ask=a)) + "\n" + json.dumps(old) + "\n").encode())
        assert [e["seq"] for e in s.events()] == [1, 2]
        s.state()


# ------------------------------------------------ the role split, signing --

def test_the_agent_cannot_write_a_human_event_nor_the_reverse():
    assert set(core.ROLES) == {"agent", "human"}
    assert set(core.ROLES["agent"]) == {"ask", "withdraw", "applied", "say"}
    assert set(core.ROLES["human"]) == {"answer", "reopen", "note"}
    with tmpstore() as s:
        with s._txn() as (box, append):
            for t in core.ROLES["human"]:
                with assert_raises("forbidden_event") as r:
                    append("agent", t, "agent:bot", id="x", value="yes", text="t", answer_seq=1)
                assert "answer" in str(r.err) or t in str(r.err)
            for t in core.ROLES["agent"]:
                with assert_raises("forbidden_event"):
                    append("human", t, "web:bob", id="x", text="t", ask=ask(), where="w", reason="r")
            with assert_raises("forbidden_event"):
                append("agent", "bogus", "agent:bot")
            assert box["seq"] == 0
        assert raw_log(s) == b"", "a refused event leaves no trace"


def test_events_carry_the_right_by_and_signature():
    with tmpstore() as s:
        env_secret = "k" * 32
        with env(CONSOLE_SECRET=env_secret):
            s.post("bot", [ask()])
            s.say("bot", "hello")
            answer(s)
            s.note("bob", "thanks")
            s.applied("bot", ["tea-word"], "here")
            s.post("bot", [ask(id="two")])
            s.withdraw("bot", ["two"], "r")
            for e in s.events():
                if e["type"] in core.ROLES["agent"]:
                    assert e["by"].startswith("agent:") and "sig" not in e, e
                else:
                    assert e["by"].startswith("web:") and re.fullmatch(r"[0-9a-f]{64}", e["sig"]), e
                    assert core.verify(env_secret.encode(), e) is True
            check_log(s, 7, env_secret.encode())
        assert not os.path.exists(os.path.join(s.dir, "secret")), "an env secret never creates a file"


def test_agent_verbs_never_make_a_secret():
    with tmpstore() as s:
        s.post("bot", [ask()])
        s.say("bot", "hi")
        s.post("bot", [ask(id="b")], dry_run=True)
        s.withdraw("bot", ["tea-word"], "r")
        assert not os.path.exists(os.path.join(s.dir, "secret"))
        s.note("bob", "the first human write makes the secret")
        p = os.path.join(s.dir, "secret")
        assert os.path.exists(p) and os.stat(p).st_mode & 0o777 == 0o600
        assert os.stat(s.path).st_mode & 0o777 == 0o600


def test_signatures():
    with tmpstore() as s:
        s.post("bot", [ask(), ask("choose", id="pick")])
        ev = answer(s, comment="fine")
        s.answer("bob", "pick", "b", shown=shown(s, "pick"), gate={"ok": True, "message": "m"})
        secret = s.secret()
        assert secret and len(secret) >= 16
        ev = s.events()[2]
        gated_ev = s.events()[3]
        assert core.verify(secret, ev) is True and core.verify(secret, gated_ev) is True
        sig = ev["sig"]
        flip = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        tampered = [dict(ev, value="no"), dict(ev, by="web:eve"), dict(ev, seq=99), dict(ev, comment="x"),
                    dict(ev, id="other"), dict(ev, at="2030-01-01T00:00:00Z"), dict(ev, extra=1),
                    dict(ev, sig=flip), dict(ev, sig=""), {k: v for k, v in ev.items() if k != "sig"},
                    {k: v for k, v in ev.items() if k != "subject"},
                    dict(gated_ev, gate={"ok": True, "message": "other"}),
                    {k: v for k, v in gated_ev.items() if k != "gate"}]
        for t in tampered:
            assert core.verify(secret, t) is False, t
        assert core.verify(None, ev) is None and core.verify(None, tampered[0]) is None
        assert core.verify(b"y" * 16, ev) is False
        assert core.sign(secret, ev) == sig == core.sign(secret, {k: v for k, v in ev.items() if k != "sig"})
        assert core.sign(secret, dict(reversed(list(ev.items())))) == sig
        assert core.sign(secret, dict(ev, sig="whatever")) == sig
        text = raw_log(s).decode()                                    # a hand edit of the file is caught
        edited = text.replace('"value": "yes"', '"value": "no"')
        assert edited != text
        put(s, edited.encode())
        evs = s.events()
        assert evs[2]["value"] == "no" and core.verify(secret, evs[2]) is False
        assert core.verify(secret, evs[3]) is True


def test_secret_sources():
    with tmpstore() as s:
        assert s.secret() is None
        path = os.path.join(s.dir, "secret")
        made = s.ensure_secret()
        assert re.fullmatch(rb"[0-9a-f]{64}", made) and s.secret() == made and s.ensure_secret() == made
        assert open(path, "rb").read() == made + b"\n" and os.stat(path).st_mode & 0o777 == 0o600
        with env(CONSOLE_SECRET="e" * 16):
            assert s.secret() == b"e" * 16 and s.ensure_secret() == b"e" * 16       # env beats the file
        with env(CONSOLE_SECRET="short"):
            with assert_raises("bad_request"):
                s.secret()                                                          # too short: refused, not ignored
            assert open(path, "rb").read() == made + b"\n"
        with env(CONSOLE_SECRET=""):
            assert s.secret() == made                                               # empty is not set: the file
        open(path, "wb").write(b"tiny\n")
        assert s.secret() is None                                                   # a file secret < 16 is none
    with tmpstore() as s:
        with env(CONSOLE_SECRET="e" * 16):
            assert s.ensure_secret() == b"e" * 16
        assert not os.path.exists(os.path.join(s.dir, "secret"))
        nested = core.Store(os.path.join(s.dir, "a", "b"))
        nested.ensure_secret()
        assert os.path.exists(os.path.join(nested.dir, "secret"))                    # creates the folder


def test_a_short_console_secret_is_refused_not_ignored():
    """A CONSOLE_SECRET under 16 characters is a mistake the operator must hear
    about: quietly using the folder's file instead would leave the agent able to
    read the key the operator meant to keep from it."""
    with tmpstore() as s:
        s.post("bot", [ask(id="one"), ask(id="two")])
        before, path = raw_log(s), os.path.join(s.dir, "secret")
        for short in ("short", "x", "e" * 15, " " * 15):
            with env(CONSOLE_SECRET=short):
                for call in (s.secret, s.ensure_secret, lambda: s.note("bob", "hi"),
                             lambda: s.answer("bob", "one", "yes", shown=shown(s, "one")),
                             lambda: s.answer("bob", "two", "yes", shown=shown(s, "two"), comment="c")):
                    with assert_raises("bad_request") as r:
                        call()
                    assert "16" in str(r.err) and r.err.params == {}, (short, str(r.err))
                assert raw_log(s) == before and not os.path.exists(path), "nothing is written, no secret file is made"
                assert s.state()["asks"]["one"]["status"] == "open"
        with env(CONSOLE_SECRET="short"):                           # the agent's side never needs the secret
            s.say("bot", "still here")
            assert s.post("bot", [ask(id="one", why="reworded")])["updated"] == ["one"]
            assert s.withdraw("bot", ["two"], "r")["withdrawn"] == ["two"]
            assert s.state()["asks"]["two"]["status"] == "withdrawn" and len(s.events()) == 5
        assert not os.path.exists(path)
        with env(CONSOLE_SECRET="e" * 16):                          # 16 is enough; 15 was not
            assert s.secret() == b"e" * 16
            ev = s.note("bob", "now it works")
            assert core.verify(b"e" * 16, ev) is True
        assert not os.path.exists(path)
    with tmpstore() as s:                                           # the folder's own secret is untouched by the rule
        s.ensure_secret()
        with env(CONSOLE_SECRET="short"):
            with assert_raises("bad_request"):
                s.secret()
        assert s.secret() is not None and s.note("bob", "hi")["type"] == "note"


# ------------------------------------------------------------ concurrency --

WORKER = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import core
d, go, kind, n, name = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6]
s = core.Store(d)
while not os.path.exists(go):
    time.sleep(0.002)
if kind == "post":
    try:
        r = s.post(name, [json.loads(sys.argv[7])])
        print(json.dumps({"ok": True, "posted": r["posted"]}))
    except core.Refused as e:
        print(json.dumps({"ok": False, "code": e.code}))
else:
    for i in range(n):
        if kind == "say":
            s.say(name, f"{name} {i}")
        else:
            s.note(name, f"{name} {i}")
'''


def spawn(d, go, kind, n, name, extra=()):
    return subprocess.Popen([sys.executable, "-c", WORKER, C_DIR, d, go, kind, str(n), name, *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def reap(procs):
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        outs.append(out)
    return outs


def test_the_store_locks_the_file():
    assert core.fcntl is not None, "the store is single-machine POSIX: flock is what serializes writers"


def test_concurrent_threads_keep_seq_unique_and_the_file_valid():
    threads_n, per = 6, 15
    with tmpstore() as s:
        bar, errors, done = threading.Barrier(threads_n + 1), [], threading.Event()

        def writer(k):
            w = core.Store(s.dir)
            bar.wait()
            try:
                for i in range(per):
                    (w.note if k % 2 else w.say)(f"u{k}" if k % 2 else f"bot{k}", f"m{k}-{i}")
            except BaseException as e:      # noqa: BLE001
                errors.append(e)

        def reader():
            r = core.Store(s.dir)
            bar.wait()
            while not done.is_set():
                try:
                    st = r.state()
                    assert st["problems"] == []
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)
                    return

        ts = [threading.Thread(target=writer, args=(k,)) for k in range(threads_n)]
        rd = threading.Thread(target=reader)
        for t in ts + [rd]:
            t.start()
        for t in ts:
            t.join(60)
        done.set()
        rd.join(10)
        assert not errors, errors
        evs = check_log(s, threads_n * per)
        for k in range(threads_n):                                     # each writer's own order survives
            texts = [e["text"] for e in evs if e["text"].startswith(f"m{k}-")]
            assert texts == [f"m{k}-{i}" for i in range(per)]


def test_concurrent_processes_keep_seq_unique_and_the_file_valid():
    procs_n, per = 4, 20
    with tmpstore() as s:
        go = os.path.join(s.dir, "go")
        os.makedirs(s.dir, exist_ok=True)
        procs = [spawn(s.dir, go, "say" if k % 2 == 0 else "note", per, f"w{k}") for k in range(procs_n)]
        open(go, "w").close()
        reap(procs)
        evs = check_log(s, procs_n * per)       # human events verify: one secret, made once, under the lock
        assert len({e["by"] for e in evs}) == procs_n
        for k in range(procs_n):
            assert [e["text"] for e in evs if e["by"].endswith(f"w{k}")] == [f"w{k} {i}" for i in range(per)]


def test_concurrent_posts_cannot_pass_the_budget():
    with tmpstore() as s:
        go = os.path.join(s.dir, "go")
        os.makedirs(s.dir, exist_ok=True)
        procs = [spawn(s.dir, go, "post", 1, f"p{k}", [json.dumps(ask(id=f"ask-{k}"))]) for k in range(14)]
        open(go, "w").close()
        res = [json.loads(o) for o in reap(procs)]
        assert sum(r["ok"] for r in res) == 10, res
        assert sorted({r["code"] for r in res if not r["ok"]}) == ["over_budget"]
        assert len(core.open_asks(s.state())) == 10
        assert len([e for e in check_log(s, 10) if e["type"] == "ask"]) == 10


def test_concurrent_same_ask_and_same_answer():
    with tmpstore() as s:
        results, bar = [], threading.Barrier(8)

        def post():
            bar.wait()
            results.append(core.Store(s.dir).post("bot", [ask()]))
        ts = [threading.Thread(target=post) for _ in range(8)]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        assert sorted(len(r["posted"]) for r in results) == [0] * 7 + [1]
        assert sum(len(r["unchanged"]) for r in results) == 7 and len(s.events()) == 1
        h, out, bar = shown(s), [], threading.Barrier(8)

        def ans(k):
            bar.wait()
            try:
                out.append(core.Store(s.dir).answer(f"u{k}", "tea-word", "yes", shown=h)["seq"])
            except core.Refused as e:
                out.append(e.code)
        ts = [threading.Thread(target=ans, args=(k,)) for k in range(8)]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        assert sorted(map(str, out)) == sorted(["2"] + ["not_open"] * 7), out
        check_log(s, 2)


def race_outcomes(setup, a, b, rounds=20):
    """Run `a` and `b` on separate Stores at the same moment, `rounds` times on
    fresh folders; every round the log must stay whole (seq 1..n, no event the
    fold rejects). Returns the set of (outcome of a, outcome of b), each "ok"
    or the Refused code."""
    seen = set()
    for _ in range(rounds):
        with tmpstore() as s:
            ctx, bar, out = setup(s), threading.Barrier(2), {}

            def run(k, fn):
                w = core.Store(s.dir)
                bar.wait()
                try:
                    fn(w, ctx)
                    out[k] = "ok"
                except core.Refused as e:
                    out[k] = e.code
                except BaseException as e:      # noqa: BLE001
                    out[k] = repr(e)
            ts = [threading.Thread(target=run, args=(k, fn)) for k, fn in (("a", a), ("b", b))]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
            assert s.state()["problems"] == [], (out, s.state()["problems"])   # check and append were one step
            check_log(s, len(s.events()))
            seen.add((out["a"], out["b"]))
    return seen


def test_racing_verbs_of_the_two_sides_have_one_winner():
    def asked(s):
        s.post("bot", [ask()])
        return shown(s)

    def answered(s):
        s.post("bot", [ask()])
        return answer(s)["seq"]
    update = lambda w, h: w.post("bot", [ask(why="A newer fact.")])                   # noqa: E731
    reply = lambda w, h: w.answer("bob", "tea-word", "yes", shown=h)                   # noqa: E731
    assert race_outcomes(asked, update, reply) <= {("ok", "changed"), ("id_used", "ok")}
    withdraw = lambda w, h: w.withdraw("bot", ["tea-word"], "moot")                    # noqa: E731
    assert race_outcomes(asked, withdraw, reply) <= {("ok", "not_open"), ("not_open", "ok")}
    reopen = lambda w, n: w.reopen("bob", "tea-word", n)                               # noqa: E731
    applied = lambda w, n: w.applied("bot", ["tea-word"], "here", seqs={"tea-word": n})  # noqa: E731
    assert race_outcomes(answered, reopen, applied) <= {("ok", "not_answered"), ("already_applied", "ok")}


def test_random_verbs_never_write_an_event_the_fold_rejects():
    """Seeded random walks over every verb, both sides, stale and current
    pages, with and without a gate proof. After each step: the fold has no
    problem (a verb that succeeds wrote a legal event), seq is 1..n, every human
    event verifies, and each answered ask holds an answer that is valid for the
    revision it was shown, carries the gate proof when that revision needs it,
    and is `suggested` / `revised` by what was shown."""
    G = {"ok": True, "verb": ["x"], "message": "m"}
    bind = {"verb": ["x"], "value_arg": True, "expect": {"v": "$value"}}
    steps = [lambda r, i: ask(id=i, why=r), lambda r, i: ask("choose", id=i, why=r),
             lambda r, i: ask("provide", id=i, why=r), lambda r, i: ask("provide", id=i, why=r, gate=bind),
             lambda r, i: ask("choose", id=i, why=r, gate=bind)]
    for seed in range(40):
        rng = random.Random(seed)
        with tmpstore() as s:
            page = {}
            for n in range(50):
                i = rng.choice("abc")
                st = s.state()
                cur = st["asks"].get(i)
                answer_seq = cur["answer"]["seq"] if cur and cur["answer"] and rng.random() < .8 else rng.randint(1, 60)
                op = rng.choice(["post", "post", "answer", "answer", "proved", "reopen", "applied", "withdraw", "say", "note"])
                try:
                    if op == "post":
                        s.post("bot", [rng.choice(steps)(rng.choice(["w1", "w2", "w3"]), i)],
                               max_open=rng.choice([10, 2, 99]), dry_run=rng.random() < .2)
                        if i in s.state()["asks"]:                                     # (a dry run may have made none)
                            page[i] = rng.choice(list(s.state()["asks"][i]["revs"]))   # what some page shows
                    elif op in ("answer", "proved"):
                        h = rng.choice(list(cur["revs"]) + [page.get(i, "stale")]) if cur else "stale"
                        s.answer("bob", i, rng.choice(["yes", "no", "a", "b", "5", "x"]), shown=h,
                                 comment=rng.choice(["", "c"]), gate=G if op == "proved" else None)
                    elif op == "reopen":
                        s.reopen("bob", i, answer_seq)
                    elif op == "applied":
                        s.applied("bot", [i], "w", seqs=rng.choice([None, {i: answer_seq}]))
                    elif op == "withdraw":
                        s.withdraw("bot", [i], "r")
                    else:
                        (s.say if op == "say" else s.note)(*(("bot", "hi") if op == "say" else ("bob", "hi")))
                except core.Refused:
                    pass
                st = s.state()
                assert st["problems"] == [], (seed, n, op, st["problems"])
                check_log(s, len(s.events()))
                for a in st["asks"].values():
                    if a["status"] != "answered":
                        continue
                    ans = a["answer"]
                    seen = a["revs"][ans["subject"]]
                    assert core.check_value(seen, ans["value"]) == (ans["value"], None), (seed, n, ans)
                    assert not core.gate_runs(seen, ans["value"]) or ans["gate"]["ok"] is True, (seed, n, ans)
                    assert ans["suggested"] == ((seen.get("recommend") or {}).get("value") == ans["value"])
                    assert bool(ans.get("revised")) == (ans["subject"] != a["hash"]), (seed, n, ans)


# ---------------------------------------- hostile input: each names its input --

def test_adv_a_field_that_strips_to_nothing_is_still_required():
    """A title, why, if_no or id made only of control characters (title='\\x00') is as
    good as empty: it is `required`, never stored blank. The empty check runs on
    the cleaned text."""
    for f in ("title", "why", "if_no", "id"):
        raw = ask(**{f: "\x00"})
        a, e = core.validate_ask(raw)
        assert a is None and (has(e, f, "required") or has(e, f, "bad_value")), (f, a)
    a, e = core.validate_ask(ask(evidence=[{"label": "\x00", "value": "\x07"}]))
    assert a is None and e


def test_adv_control_characters_separate_words_instead_of_gluing_them():
    """title='Tea\\nBlends' and why='col1\\tcol2' keep their words apart: a tab or a
    newline becomes a space (a newline stays in the multiline fields), the other
    control characters are dropped. Pasted text never fuses into a word the agent
    did not write."""
    a = ok(ask(title="Tea\nBlends", why="col1\tcol2", if_no="a\r\nb"))
    assert a["title"] != "TeaBlends" and "Tea" in a["title"] and "Blends" in a["title"]
    assert a["why"] != "col1col2", a["why"]
    assert "ab" != a["if_no"], a["if_no"]


def test_adv_bidi_overrides_cannot_disguise_what_is_approved():
    """effect='Raise budget \\u202e05 ot 02' (or a title, label or value holding U+202A..202E
    or U+2066..2069) never reaches the owner's screen: those characters make text
    read as something other than what is stored, so they are refused or stripped."""
    bidi = "".join(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))
    for over in (dict(title=f"Pay {bidi}x"), dict(why=f"a {bidi} b"), dict(if_no=f"a {bidi}"),
                 dict(evidence=[{"label": f"l{bidi}", "value": f"v{bidi}"}])):
        a, e = core.validate_ask(ask(**over))
        assert a is None or not any(c in bidi for s in walk_strings(a) for c in s), over
    a, e = core.validate_ask(ask("approve", effect=f"Raise {bidi}x"))
    assert a is None or not any(c in bidi for s in walk_strings(a) for c in s)


def test_adv_lone_surrogates_never_crash_a_verb():
    """title='Tea \\ud83d' (a JSON escape an agent gets from UTF-16 slicing), and a lone
    surrogate in an ask, a say, an answer, a comment or a note, is refused or
    cleaned: no verb dies with UnicodeEncodeError and the log stays whole."""
    with tmpstore() as s:
        assert refused_or_ok(lambda: s.post("bot", [ask(title="Tea \ud83d")])) is None or s.state()["asks"]
        refused_or_ok(lambda: s.post("bot", [ask(why="a \udcff b", evidence=[{"label": "l\ud800", "value": "v"}])]))
        refused_or_ok(lambda: s.say("bot", "hi \ud83d"))
        s.post("bot", [ask(id="fine")])
        refused_or_ok(lambda: answer(s, "yes", id="fine", comment="c \ud83d"))
        refused_or_ok(lambda: s.note("bob", "n \ud83d"))
        check_log(s, len(s.events()))
    p = ok(ask("provide", input={"type": "text"}))
    v, e = core.check_value(p, "a\ud800b")
    assert e is not None or v.encode("utf-8"), "a surrogate answer must not pass through"


def test_adv_table_column_names_are_bounded_and_clean():
    """A table column name is bounded like a cell (LIMITS['cell']) and as clean as any
    text: columns=['x'*5000] is refused, and '\\x00' or a newline in a name never
    reaches the page."""
    huge = [{"table": {"columns": ["x" * 5000], "rows": [["1"]]}}]
    assert errs(ask(evidence=huge))
    a, e = core.validate_ask(ask(evidence=[{"table": {"columns": ["a\x00b\nc"], "rows": [["1"]]}}]))
    assert a is None or not any(c < " " for c in a["evidence"][0]["table"]["columns"][0])


def test_adv_a_number_range_that_no_value_satisfies_is_refused():
    """input={'type':'number','min':10,'max':1} is refused: no answer could fit, and the
    ask would hold a budget slot for good."""
    e = errs(ask("provide", input={"type": "number", "min": 10, "max": 1}))
    assert any(x["field"].startswith("input") for x in e)
    ok(ask("provide", input={"type": "number", "min": 5, "max": 5}))


def test_adv_gate_expect_must_be_strict_json():
    """gate.expect={'x': NaN} (Python's json accepts NaN and Infinity) is refused: the
    log is strict JSON, which jq or a browser can read, and the bare token NaN is
    not JSON."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        a, e = core.validate_ask(ask("provide", gate={"verb": ["x"], "expect": {"x": bad}}))
        assert a is None and has(e, "gate.expect", "required"), bad
    a = ok(ask("provide", gate={"verb": ["x"], "value_arg": True,
                                "expect": {"x": 1.5, "y": [None, True, "s"], "v": "$value"}}))
    json.dumps(a, allow_nan=False)


def test_adv_names_with_a_trailing_newline_are_not_tokens():
    """A name or a gate verb word with a trailing newline ('bob\\n', 'facts\\n') is not a
    token: it would reach `by` and --relay-user as one argv element. TOKEN_RE
    ends at the true end of the string."""
    assert not core.TOKEN_RE.match("bob\n")
    with tmpstore() as s:
        with assert_raises("bad_request"):
            s.say("bob\n", "hi")
        with assert_raises("bad_request"):
            s.note("bob\n", "hi")
    assert has(errs(ask("provide", gate={"verb": ["facts\n"], "expect": {"a": 1}})), "gate.verb", "bad_value")


def test_adv_a_number_answer_is_plain_ascii_decimal():
    """A typed number is a plain ASCII decimal: '1_000', Arabic-Indic digits, '1 2',
    '+-1', '0x10', '1e' and '.e1' are refused as `number`. float() alone would take
    some of them, and JavaScript, spreadsheets and most harnesses cannot read them."""
    a = ok(ask("provide", input={"type": "number"}))
    for bad in ("1_000", "1_0", "\u0661\u0662", "1 2", "+-1", "0x10", "1e", ".e1"):
        v, e = core.check_value(a, bad)
        assert v is None and e["reason"] == "number", (bad, v)
    for good in ("1", "-1", "1.5", "1e3", "-0.5E-2", "0"):
        assert core.check_value(a, good)[1] is None, good


def test_adv_a_date_answer_is_iso_and_zero_padded():
    """A typed date is ISO and zero-padded: '2026-9-1' and Unicode digits are refused as
    `date` (strptime alone would take them), so the agent never gets a date that
    is not YYYY-MM-DD."""
    d = ok(ask("provide", input={"type": "date"}))
    for bad in ("2026-9-1", "2026-09-1", "2026-9-01", "\u0662\u0660\u0662\u0666-\u0660\u0669-\u0662\u0668"):
        v, e = core.check_value(d, bad)
        assert v is None and e["reason"] == "date", (bad, v)
    assert core.check_value(d, "2026-09-28") == ("2026-09-28", None)


def test_adv_a_typed_answer_has_no_control_characters():
    """A typed answer holds no control character ('a\\x00b', BEL, ESC, DEL), for text
    and url alike: a NUL would reach the harness as --value=a\\x00b, where the
    subprocess call raises, and the others would land in the log and on the page."""
    for step_input in ({"type": "text"}, {"type": "url"}):
        a = ok(ask("provide", input=step_input))
        for bad in ("a\x00b", "a\x07", "a\x1bb", "a\x7f"):
            v = "https://example.com/" + bad if step_input["type"] == "url" else bad
            got, e = core.check_value(a, v)
            assert got is None and e["code"] == "bad_value", (step_input, bad, got)


def test_adv_matches_compares_numbers_exactly():
    """Numbers compare exactly, never as floats: '12345678901234567890' and '...891'
    differ (a long id typed as text), 10**400 against 1 is False and not an
    OverflowError, and '1e999' is not '1e998'."""
    assert core.matches("12345678901234567890", "12345678901234567890") is True
    assert core.matches("12345678901234567890", "12345678901234567891") is False
    assert core.matches("9007199254740993", 9007199254740992) is False
    assert core.matches(10 ** 400, 1) is False
    assert core.matches(1, 10 ** 400) is False
    assert core.matches(10 ** 400, 10 ** 400) is True
    assert core.matches("1e999", "1e998") is False
    assert core.matches("10", 10.0) is True and core.matches("0.10", 0.1) is True


def test_adv_matches_treats_non_finite_words_as_words():
    """'nan' matches 'nan' and 'inf' matches 'inf': equal text always matches, and only
    finite numbers compare by value ('inf' is not 'infinity'), so a gated answer of
    such a word is not refused as changed."""
    assert core.matches("nan", "nan") is True
    assert core.matches("inf", "inf") is True
    assert core.matches("inf", "infinity") is False
    assert core.matches("nan", "inf") is False
    assert core.matches({"name": "nan"}, {"name": "nan", "x": 1}) is True


def test_adv_verify_never_raises_on_a_foreign_signature():
    """verify never raises on a foreign signature ('é', a NUL, a number, null, a list,
    10000 characters, an astral character): one hand-written line must not take
    down History or the agent's `answers`. It is False."""
    sec = b"s" * 16
    base = {"seq": 1, "at": "x", "by": "web:bob", "type": "answer"}
    for sig in ("é", "\u0000", 123, None, ["x"], {"a": 1}, "x" * 10000, "𝒳"):
        assert core.verify(sec, dict(base, sig=sig)) is False, sig
    assert core.verify(sec, base) is False


def test_adv_a_malformed_event_is_corrupt_not_a_crash():
    """A line that is valid JSON with a seq and a type but the wrong shape
    ({"seq":2,"type":"ask"}, {"seq":"2","type":"say"}, an ask with no title) is
    corrupt at its line, or reported as a problem: state() and every write verb
    never die with KeyError or TypeError. `_well_formed` checks the shape the
    pages read, not validate_ask itself, whose rules may tighten while old logs
    must stay readable."""
    lines = [b'{"seq":2,"type":"ask"}', b'{"seq":2,"type":"ask","ask":{"id":"z"}}',
             b'{"seq":2,"type":"ask","ask":"x","at":"t"}', b'{"seq":"2","type":"say"}',
             b'{"seq":2,"type":"ask","at":"t","ask":{"id":"z","step":"confirm"}}']
    crashed = []
    for bad in lines:
        with tmpstore() as s:
            s.post("bot", [ask()])
            good = raw_log(s)
            put(s, good + bad + b"\n")
            for call in (s.state, lambda: s.say("bot", "x"), lambda: s.note("bob", "x")):
                try:
                    st = call()
                    if isinstance(st, dict) and "problems" in st:
                        assert st["problems"]                     # or: reported, not silently accepted
                except core.Refused as e:
                    assert e.code == "corrupt_log"
                except BaseException as e:      # noqa: BLE001
                    crashed.append((bad, type(e).__name__))
    assert not crashed, crashed


def test_adv_the_log_check_is_not_an_assert():
    """The log check is not an `assert`: under `python -O` a line `[1,2]` is still
    refused, never returned as an event."""
    with tmpstore() as s:
        put(s, b"[1,2]\n")
        code = ("import sys; sys.path.insert(0, %r); import core\n"
                "try:\n    core.Store(%r).events(); print('accepted')\n"
                "except core.Refused: print('refused')\n") % (C_DIR, s.dir)
        out = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=30)
        assert out.stdout.strip() == "refused", out.stdout + out.stderr


def test_adv_a_replayed_signed_event_is_not_folded_twice():
    """A verbatim copy of a signed human answer, appended after the ask was reopened,
    is not folded again: seq only grows (problem `bad_seq`), so an old answer
    cannot come back with no human involved."""
    with tmpstore() as s:
        s.post("bot", [ask()])
        ev = answer(s)
        s.reopen("bob", "tea-word", ev["seq"])
        put(s, raw_log(s) + (json.dumps(ev, ensure_ascii=False) + "\n").encode())
        st = s.state()
        assert st["asks"]["tea-word"]["status"] == "open", "the replayed answer was accepted"
        assert any(p["seq"] == ev["seq"] for p in st["problems"])


def test_a_replayed_line_does_not_stop_later_writes():
    """seq comes from the largest in the file, not the last line's: after a
    replayed old line at the end, the next event is still one the fold takes."""
    with tmpstore() as s:
        s.post("bot", [ask()])
        ev = answer(s)
        s.say("bot", "two")
        put(s, raw_log(s) + (json.dumps(ev, ensure_ascii=False) + "\n").encode())     # a copy of seq 2, last
        assert s.say("bot", "three")["seq"] == 4
        st = s.state()
        assert [m["text"] for m in st["messages"]] == ["two", "three"] and st["seq"] == 4
        assert [(p["seq"], p["code"]) for p in st["problems"]] == [(2, "bad_seq")]
        assert st["asks"]["tea-word"]["answer"] == ev


def test_adv_duplicate_ids_in_one_call_write_one_event():
    """withdraw(['x', 'x']) and applied(['x', 'x']) (from `ask.py withdraw x x`) write
    one event each: ids are de-duplicated in order, so the fold has no `not_open`
    problem to show forever."""
    with tmpstore() as s:
        s.post("bot", [ask(), ask(id="b")])
        s.withdraw("bot", ["b", "b"], "moot")
        answer(s)
        s.applied("bot", ["tea-word", "tea-word"], "the catalogue")
        assert s.state()["problems"] == []
        assert [e["type"] for e in s.events()].count("withdraw") == 1
        assert [e["type"] for e in s.events()].count("applied") == 1


def test_adv_applying_an_applied_answer_is_refused():
    """Applying an applied answer a second time is refused `already_applied` (its code
    says so) and writes nothing: the second event would be a `not_open` problem in
    the fold."""
    with tmpstore() as s:
        s.post("bot", [ask()])
        answer(s)
        s.applied("bot", ["tea-word"], "the catalogue")
        before = raw_log(s)
        with assert_raises("already_applied"):
            s.applied("bot", ["tea-word"], "again")
        assert raw_log(s) == before and s.state()["problems"] == []


def test_adv_suggested_means_what_the_owner_was_shown():
    """`suggested` means what the owner was shown: a page showed rev 1 (recommend 5),
    the agent revised the ask to recommend 6, and the owner answered 5 through a
    gate. It is compared with the revision that was shown, not the current one, so
    History does not say 'You changed it'."""
    with tmpstore() as s:
        s.post("bot", [gated(recommend={"value": "5", "because": "Cheapest."})])
        h1 = shown(s, "unit-cost")
        s.post("bot", [gated(why="Newer fact.", recommend={"value": "6", "because": "Now 6."})])
        ev = s.answer("bob", "unit-cost", "5", shown=h1, gate={"ok": True, "message": "m"})
        assert ev["revised"] is True and ev["suggested"] is True


def test_adv_gate_bypass_needs_a_revision_the_owner_could_have_seen():
    """With a gate, `shown` must still be a revision of this ask that existed:
    answer(shown='0000…', gate={'ok': True}) is refused `changed`. Otherwise
    History would show text the owner never saw as 'what was shown'."""
    with tmpstore() as s:
        s.post("bot", [ask(), gated()])
        other = shown(s)                                          # a real hash, but of another ask
        for i, h in (("tea-word", "0" * 16), ("unit-cost", "0" * 16), ("unit-cost", other),
                     ("unit-cost", "0" * 32)):                    # the gated one is where a proof could pass it
            with assert_raises("changed"):
                s.answer("bob", i, "5" if i == "unit-cost" else "yes", shown=h, gate={"ok": True, "message": "m"})
        assert [a["status"] for a in s.state()["asks"].values()] == ["open", "open"]


def test_adv_gate_bypass_only_for_a_gated_ask():
    """A stale page answering an ask WITHOUT a gate is `changed` even when given
    gate={'ok': True}: the proof stands in for 'the harness was really written',
    which only a gated ask can mean."""
    with tmpstore() as s:
        s.post("bot", [ask()])
        h1 = shown(s)
        s.post("bot", [ask(why="changed")])
        with assert_raises("changed"):
            s.answer("bob", "tea-word", "yes", shown=h1, gate={"ok": True, "message": "m"})


def test_adv_gate_bypass_needs_an_ok_gate():
    """Only gate={'ok': True} stands in for a written harness: a stale answer with
    gate={'ok': False, …} is refused (`changed` or a gate_ code), never recorded
    and stamped revised."""
    with tmpstore() as s:
        s.post("bot", [gated()])
        h1 = shown(s, "unit-cost")
        s.post("bot", [gated(why="changed")])
        with assert_raises() as r:
            s.answer("bob", "unit-cost", "5", shown=h1, gate={"ok": False, "message": "refused"})
        assert r.err.code == "changed" or r.err.code.startswith("gate_")
        assert s.state()["asks"]["unit-cost"]["status"] == "open"


def test_adv_a_gated_answer_needs_the_gate_proof():
    """A gated answer is never recorded without the gate's proof: Store.answer with no
    gate, for an ask whose gate runs, is refused with a gate_ code, so a caller's
    bug cannot record a confirmed answer that no harness saw."""
    with tmpstore() as s:
        s.post("bot", [gated()])
        with assert_raises() as r:
            s.answer("bob", "unit-cost", "5", shown=shown(s, "unit-cost"))
        assert r.err.code.startswith("gate_")
        s.answer("bob", "unit-cost", "5", shown=shown(s, "unit-cost"), gate={"ok": True, "message": "m"})


def test_adv_changed_beats_bad_value_for_a_stale_page():
    """A stale page that answers with an option the ask has since dropped gets
    `changed` (the real cause), not bad_value 'pick one of the listed options'
    while its own page still lists that option."""
    with tmpstore() as s:
        s.post("bot", [ask("choose")])
        h1 = shown(s)
        two = [{"value": "a", "label": "Blends"}, {"value": "b", "label": "Mixes"}]
        s.post("bot", [ask("choose", options=two)])
        with assert_raises("changed"):
            s.answer("bob", "tea-word", "c", shown=h1)


def test_adv_updating_an_open_ask_is_allowed_while_over_budget():
    """Updating an open ask adds nothing, so it is allowed while over budget
    (max_open=3 with 5 open, or after a reopen pushed the count past 10), which is
    when the agent most needs to reword or merge. A new ask is still refused."""
    with tmpstore() as s:
        s.post("bot", [ask(id=f"a{i}") for i in range(5)])
        r = s.post("bot", [ask(id="a1", why="reworded")], max_open=3)
        assert r["updated"] == ["a1"]
        with assert_raises("over_budget"):
            s.post("bot", [ask(id="new")], max_open=3)


def test_adv_a_dry_run_creates_and_repairs_nothing():
    """A dry run reads and creates nothing: on a folder that does not exist it makes no
    folder (a typo in --dir leaves no stray directory), and on a log with a torn
    last line it does not cut that line off."""
    with tmpdir() as d:
        s = core.Store(os.path.join(d, "typo"))
        assert s.post("bot", [ask()], dry_run=True)["dry_run"] is True
        assert not os.path.exists(s.dir), "the dry run created the folder"
    with tmpstore() as s:
        s.say("bot", "one")
        data = raw_log(s) + b'{"seq":2,"at":"x","by":"agent:bot","type":"say","text":"tor'
        put(s, data)
        s.post("bot", [ask()], dry_run=True)
        assert raw_log(s) == data, "the dry run rewrote the file"


def test_adv_ensure_secret_is_single_valued_under_a_race():
    """ensure_secret is single-valued under a race: eight threads that start at once
    (or the console and a first answer) all get the same secret, and it is the
    one in the file, so no event is signed with a key that fails verify()."""
    bad = 0
    for _ in range(20):
        with tmpstore() as s:
            got, bar = [], threading.Barrier(8)

            def w():
                bar.wait()
                got.append(core.Store(s.dir).ensure_secret())
            ts = [threading.Thread(target=w) for _ in range(8)]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
            if len(set(got)) != 1 or s.secret() != got[0]:
                bad += 1
    assert bad == 0, f"{bad} of 20 rounds disagreed on the secret"


def test_adv_a_failed_write_is_not_acknowledged():
    """A write the disk took only part of is not acknowledged: say() either raises
    OSError or returns a seq that is really in the log."""
    with tmpstore() as s:
        real = os.write

        def half(fd, data):
            data = bytes(data)
            return real(fd, data[:len(data) // 2]) if data.startswith(b'{"seq"') else real(fd, data)
        os.write = half
        try:
            try:
                r = s.say("bot", "an answer worth keeping")
            except OSError:
                return                                        # honest failure: fine
        finally:
            os.write = real
        assert [e["seq"] for e in s.events()] == [r["seq"]], "acknowledged but not in the log"


def test_adv_the_hash_a_page_is_bound_to_is_not_a_64_bit_truncation():
    """The hash a page is bound to has at least 32 hex characters: a 64-bit truncation
    could be collided by an agent that shows the owner one ask and swaps in
    another without triggering `changed`."""
    assert len(core.subject_hash(ok(ask()))) >= 32


def test_adv_gate_expect_binds_the_answer_through_a_value_not_a_key():
    """gate.expect binds the answer through a value, not a key: {'$value': 'x'} binds
    nothing (expect_for substitutes values only), so a choose or provide gate
    written that way is refused, while "$value" as a value, at any depth, is
    accepted and changes what expect_for gives."""
    for step in ("provide", "choose"):
        for exp in ({"$value": "x"}, {"a": {"$value": 1}}, {"$value": "$other"}, {"a": 1, "$value": "$value2"}):
            a, e = core.validate_ask(ask(step, gate={"verb": ["x"], "expect": exp, "value_arg": True}))
            assert a is None and has(e, "gate.value_arg", "required"), (step, exp)
    for exp in ({"n": "$value"}, {"a": [{"b": "$value"}]}, {"$value": 1, "n": "$value"}):    # what is accepted binds
        for step in ("provide", "choose"):
            a = ok(ask(step, gate={"verb": ["x"], "expect": exp, "value_arg": True}))
            assert core.expect_for(a, "1") != core.expect_for(a, "2"), (step, exp)


def test_adv_gate_expect_and_evidence_urls_are_as_clean_as_every_other_text():
    """gate.expect strings (keys and values) and evidence urls are as clean as every
    other text: a lone surrogate, NUL, DEL or a bidi override in one is refused as
    invalid_ask, never a UnicodeEncodeError in subject_hash and never shown to the
    owner ("What exactly gets written" prints expect)."""
    bad = ["\ud83d", "\x00", "\x07", "\x7f", "‮", "⁦"]
    for c in bad:
        for exp in ({"a": f"5{c}0", "b": "$value"}, {f"k{c}": 1, "b": "$value"}, {"a": [f"x{c}"], "b": "$value"}):
            a, e = core.validate_ask(ask("provide", gate={"verb": ["x"], "expect": exp, "value_arg": True}))
            assert a is None and any(x["field"] == "gate.expect" for x in e), (c, exp)
        for item in ({"label": "l", "value": "v", "url": f"https://x/{c}"}, {"quote": "q", "url": f"https://x/{c}"}):
            a, e = core.validate_ask(ask(evidence=[item]))
            assert a is None and has(e, "evidence[0].url", "bad_value"), (c, item)
    with tmpstore() as s:
        for raw in (ask("provide", gate={"verb": ["x"], "value_arg": True, "expect": {"a": "\ud83d", "b": "$value"}}),
                    ask(evidence=[{"label": "l", "value": "v", "url": "https://x/\ud83d"}])):
            with assert_raises("invalid_ask"):
                s.post("bot", [raw])
        assert raw_log(s) == b""


def test_adv_a_foreign_line_with_unhashable_or_unencodable_fields_is_corrupt_not_a_crash():
    """A line that is valid JSON with an int seq and a text type but an `id` or
    `subject` that is not text ({"seq":2,"type":"withdraw","id":["x"]}), or an ask
    holding a lone surrogate, is corrupt and not a crash: fold never meets an
    unhashable id. verify() of an event with a lone surrogate anywhere is False.
    (Only a line written by hand or by another program can be like this; the
    store's own verbs clean their text.)"""
    a = ok(ask())
    first = (json.dumps({"seq": 1, "at": "t", "by": "agent:bot", "type": "ask", "ask": a}) + "\n").encode()
    lines = [b'{"seq":2,"type":"withdraw","id":["x"]}', b'{"seq":2,"type":"answer","id":{"a":1},"value":"yes"}',
             b'{"seq":2,"type":"answer","id":"tea-word","value":"yes","subject":[1]}',
             b'{"seq":2,"type":"reopen","id":"tea-word","subject":{"a":1}}',
             json.dumps({"seq": 2, "at": "t", "by": "agent:bot", "type": "ask",
                         "ask": dict(a, id="z", title="t\ud83d")}).encode("ascii")]
    crashed = []
    for bad in lines:
        with tmpstore() as s:
            put(s, first + bad + b"\n")
            for call in (s.state, lambda: s.say("bot", "x"), lambda: s.note("bob", "x")):
                try:
                    call()
                except core.Refused as e:
                    assert e.code == "corrupt_log"
                except BaseException as e:      # noqa: BLE001
                    crashed.append((bad[:60], type(e).__name__))
    assert not crashed, crashed
    for surrogate in ("\ud83d", "\udcff", "a\ud800b"):
        ev = {"seq": 2, "at": "t", "by": "web:bob", "type": "note", "text": surrogate, "sig": "00" * 32}
        assert core.verify(b"s" * 16, ev) is False, surrogate


def test_a_choose_ask_without_options_lists_that_problem_once():
    """A choose ask with no options is one problem (options are required), not three."""
    for blank in (DROP, None, []):
        e = errs(ask("choose", options=blank))
        assert [(x["field"], x["code"]) for x in e] == [("options", "required")], (blank, e)
    e = errs(ask("choose", options="x"))                          # a wrong type is its own single problem
    assert [(x["field"], x["code"]) for x in e] == [("options", "bad_value")], e
    e = errs(ask("choose", options=[{"value": "a", "label": "A"}]))   # too few: one problem
    assert [(x["field"], x["code"]) for x in e] == [("options", "bad_value")], e


def test_reopen_never_writes_a_line_the_reader_refuses():
    """What reopen writes is what the reader accepts: the answer_seq in the log is an int, whatever type the caller passed."""
    with tmpstore() as s:
        s.post("bot", [ask()])
        ev = answer(s)
        r = refused_or_ok(lambda: s.reopen("bob", "tea-word", float(ev["seq"])))
        s.state()                                               # must not raise corrupt_log
        assert r is None or type(r["answer_seq"]) is int, r
        assert [e["seq"] for e in s.events()][:2] == [1, 2]


def test_a_hand_written_gate_that_is_not_an_object_is_corrupt_not_a_crash():
    """An answer whose `gate` is not an object is a corrupt log line, never a crash in a later verb."""
    a = ok(ask())
    first = (json.dumps({"seq": 1, "at": "t", "by": "agent:bot", "type": "ask", "ask": a}) + "\n").encode()
    for gate in ("x", ["x"], 5, 1.5, True):
        ev = {"seq": 2, "at": "t", "by": "web:bob", "type": "answer", "id": "tea-word", "value": "yes",
              "subject": core.subject_hash(a), "gate": gate}
        with tmpstore() as s:
            put(s, first + (json.dumps(ev) + "\n").encode())
            try:
                s.reopen("bob", "tea-word", 2)
            except core.Refused:
                pass                                            # corrupt_log or already_applied: both are answers
            except BaseException as e:  # noqa: BLE001
                raise AssertionError(f"gate={gate!r}: {type(e).__name__}: {e}") from None


def test_a_console_secret_that_is_not_utf8_is_refused_not_a_traceback():
    """A CONSOLE_SECRET made of bytes that are not UTF-8 is used as the bytes it is, or refused; never a traceback."""
    code = ("import sys, tempfile; sys.path.insert(0, %r); import core\n"
            "s = core.Store(tempfile.mkdtemp())\n"
            "try:\n    print('secret', len(s.secret()))\n"
            "except core.Refused as e:\n    print('refused', e.code)\n") % os.path.abspath(os.path.join(HERE, ".."))
    envb = {k.encode(): v.encode() for k, v in os.environ.items()}
    envb[b"CONSOLE_SECRET"] = b"caf\xe9-caf\xe9-caf\xe9-caf\xe9"
    p = subprocess.run([sys.executable, "-c", code], env=envb, capture_output=True, timeout=30)
    out = p.stdout.decode().strip()
    assert p.returncode == 0 and out in ("secret 19", "refused bad_request"), (p.returncode, out, p.stderr.decode()[-200:])


def test_a_number_just_above_max_is_out_of_range():
    """A number is compared with its bounds exactly (as decimals), not through floats: 100.0000000000000001 is above 100."""
    a = ok(ask("provide", input={"type": "number", "min": 0, "max": 100}))
    for over in ("100.0000000000000001", "1e2000000000000000000000000000000000000000000000000000", "100.00000000000000000001"):
        v, e = core.check_value(a, over)
        assert v is None and e["reason"] in ("range", "number"), (over, v)
    assert core.check_value(a, "100.0")[1] is None and core.check_value(a, "0")[1] is None
    lo = ok(ask("provide", input={"type": "number", "min": 1}))
    assert core.check_value(lo, "0.99999999999999999")[1]["reason"] == "range"


def test_a_hand_written_ask_with_an_odd_nested_field_is_corrupt_not_a_crash():
    """A stored ask whose nested fields have the wrong shape (group, recommend, options, input, gate) is a corrupt line, never a crash in a page or a verb."""
    a = ok(ask("choose", id="pick"))
    for field, odd in (("group", ["x"]), ("group", {}), ("recommend", "x"), ("recommend", 5), ("recommend", {"a": 1}),
                       ("options", 5), ("options", "x"), ("options", None), ("options", ["x"]), ("input", "x")):
        line = {"seq": 1, "at": "t", "by": "agent:bot", "type": "ask", "ask": dict(a, **{field: odd})}
        with tmpstore() as s:
            put(s, (json.dumps(line) + "\n").encode())
            try:
                st = s.state()
                core.groups(st)
                for x in st["asks"].values():
                    core.check_value(x["ask"], "a")
                    (x["ask"].get("recommend") or {}).get("value")
                    x["ask"]["recommend"]["value"]
            except core.Refused:
                continue
            except BaseException as e:  # noqa: BLE001
                raise AssertionError(f"{field}={odd!r}: {type(e).__name__}: {e}") from None
            raise AssertionError(f"{field}={odd!r} was accepted")


def test_a_huge_number_bound_is_refused_not_a_crash():
    for bound in (10 ** 400, -10 ** 400):
        e = errs(ask("provide", input={"type": "number", "max": bound}))
        assert [(x["field"], x["code"]) for x in e] == [("input.max", "bad_type")], e
    assert ok(ask("provide", input={"type": "number", "min": 0, "max": 10 ** 6}))


def test_an_error_document_never_echoes_a_hostile_id():
    nested = [[["deep"]]] * 3
    for hostile in ({"a": 1}, nested, 5, True, ""):
        with tmpstore() as s:
            with assert_raises("invalid_ask") as caught:
                s.post("bot", [dict(ask(), id=hostile)])
        labels = [x["ask"] for x in caught.err.params["errors"]]
        assert labels and all(isinstance(l, str) and len(l) < 80 for l in labels), labels
    with tmpstore() as s:                                        # a real id is still the label
        with assert_raises("invalid_ask") as caught:
            s.post("bot", [dict(ask(), id="tea-word", title=DROP)])
        assert {x["ask"] for x in caught.err.params["errors"]} == {"tea-word"}


def test_a_log_line_with_nan_or_infinity_is_corrupt_for_every_reader():
    for token in ("NaN", "Infinity", "-Infinity", "1e999"):
        with tmpstore() as s:
            s.post("bot", [ask()])
            line = ('{"seq": 2, "at": "t", "by": "web:bob", "type": "note", "text": "x", "extra": %s}\n' % token).encode()
            put(s, open(s.path, "rb").read() + line)
            for call in (s.state, lambda: s.note("bob", "y")):
                with assert_raises("corrupt_log"):
                    call()
    with tmpstore() as s:                                        # an ordinary float is still an event
        s.post("bot", [ask()])
        put(s, open(s.path, "rb").read() + b'{"seq": 2, "at": "t", "by": "web:bob", "type": "note", "text": "x", "n": 1.5}\n')
        assert s.state()["seq"] == 2


def test_matches_takes_only_plain_ascii_numbers_by_value():
    assert core.matches("12", 12) and core.matches(12, "12.0") and core.matches("+12", "12") and core.matches(".5", 0.5)
    for spelled in ("1_2", "\uff11\uff12", " 12 ", "12 ", "1e", "0x0c"):
        assert not core.matches("12", spelled) and not core.matches(spelled + "0", "120"), spelled
    assert core.matches("same words", "same words") and not core.matches("nan", "inf")


if __name__ == "__main__":
    _t.main(globals())
