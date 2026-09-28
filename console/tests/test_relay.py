"""relay.py against fake_harness.py (every mode) and small scripted harnesses.

Each test names the rule it holds: run the gate twice, never send the code for
a payload that was not shown (an extra item is not the same write; the
harness's envelope may say more), a second call that gives no clear answer is
`gate_unsure`, one argument per thing the human typed and one `--name=value`
element for everything the console adds (a code starting with `-` included),
the allowlist, the timeouts (and the whole process tree dying), no code
anywhere, one clean line of text, and no exception for anything a harness can
do.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import logging
import os
import re
import stat
import sys
import threading
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import _t  # noqa: E402
import core  # noqa: E402
import fake_harness  # noqa: E402
import relay  # noqa: E402

FAKE = os.path.join(HERE, "fake_harness.py")
VERBS = [["facts", "confirm"], ["queue", "approve"]]
VERB = ["facts", "confirm", "unit_cost"]
ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
CH = "confirm_code_required"

# a scripted harness: answers call n with step n (the last step repeats) and
# records each call's argv, so a test can say what was and was not run
CANNED = '''import json, os, sys
cfg = json.loads(os.environ["CANNED"])
n = sum(1 for _ in open(cfg["log"])) if os.path.exists(cfg["log"]) else 0
with open(cfg["log"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
step = cfg["steps"][min(n, len(cfg["steps"]) - 1)]
argv = sys.argv[1:]
code = next((x[len("--code="):] for x in argv if x.startswith("--code=")), "")
if "out_hex" in step:
    out = bytes.fromhex(step["out_hex"])
else:
    o = step.get("out", "")
    o = o if isinstance(o, str) else json.dumps(o)
    out = (o.replace("@CODE@", code) + "x" * step.get("pad", 0)).encode("utf-8", "surrogatepass")
sys.stdout.buffer.write(out)
sys.stdout.flush()
sys.exit(step.get("exit", 0))
'''

# a harness that writes on the second call and then dies (how: crash, signal,
# close = exits 0 with nothing on stdout, garbage = exits 0 with text)
WRITES_THEN_DIES = '''import json, os, signal, sys
if not any(x.startswith("--code=") for x in sys.argv[1:]):
    print(json.dumps({"code": "confirm_code_required", "params": {"confirm_code": "c0de1234"},
                      "subject": {"items": {"unit_cost": 12}}}))
    sys.exit(2)
with open(os.environ["WRITTEN"], "a") as f:
    f.write("unit_cost=12\\n")
how = os.environ["HOW"]
if how == "signal":
    os.kill(os.getpid(), signal.SIGKILL)
if how == "close":
    os.close(1)
    sys.exit(0)
if how == "garbage":
    print("all done")
    sys.exit(0)
print("Traceback (most recent call last):")
sys.exit(1)
'''

# a harness whose options are read by argparse: its code starts with a dash
ARGPARSE = '''import argparse, json, re, sys
p = argparse.ArgumentParser()
p.add_argument("words", nargs="*")
for name in ("--value", "--reason", "--code", "--relay-user", "--relay-at"):
    p.add_argument(name)
p.add_argument("--json", action="store_true")
a = p.parse_args()
if a.code is None:
    print(json.dumps({"code": "confirm_code_required", "params": {"confirm_code": "-abc-123"},
                      "subject": {"items": {"unit_cost": 12}}}))
    sys.exit(2)
ok = (a.code == "-abc-123" and a.relay_user == "web:alice"
      and re.fullmatch(r"\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\dZ", a.relay_at or ""))
print(json.dumps({"ok": bool(ok), "message": f"seen {a.code} {a.relay_user}"}))
sys.exit(0 if ok else 1)
'''


# ------------------------------------------------------------- fixtures --

def _valid(raw: dict) -> dict:
    ask, errs = core.validate_ask(raw)
    assert not errs, errs
    return ask


def _base(step: str, gate: dict, **more) -> dict:
    return _valid({"id": "k1", "step": step, "title": "Confirm a figure for Northwind Tea Co.",
                   "why": "The margin report needs it.",
                   "evidence": [{"label": "Invoice", "value": "12.00"}],
                   "if_no": "The report keeps the estimate.", "gate": gate, **more})


def provide_ask(**gate) -> dict:
    """A typed answer that is written by `facts confirm unit_cost --value=…`."""
    g = {"verb": list(VERB), "value_arg": True,
         "expect": {"items": {"unit_cost": "$value"}}}
    g.update(gate)
    return _base("provide", g, input={"type": "text"})


def approve_ask(step: str = "approve", **gate) -> dict:
    """A yes/no answer written by `queue approve q1` (no value to pass)."""
    g = {"verb": ["queue", "approve", "q1"],
         "expect": {"items": {"q1": "$value"}}}
    g.update(gate)
    more = {"recommend": {"value": "yes", "because": "It matches the plan."}}
    if step == "approve":
        more["effect"] = "The ad budget goes up."
    return _base(step, g, **more)


class Rig:
    """The fake harness in one mode, with a log of its calls and its codes."""

    def __init__(self, d: str, mode: str = "ok", **knobs):
        self.log = os.path.join(d, "calls.jsonl")
        self.state = os.path.join(d, "state.json")
        self.env = dict(os.environ, FAKE_HARNESS_MODE=mode,
                        FAKE_HARNESS_LOG=self.log, FAKE_HARNESS_STATE=self.state,
                        **knobs)
        self.cmd = [sys.executable, FAKE]

    def run(self, ask, value, **kw) -> dict:
        return relay.run(ask, value, **{
            "cmd": self.cmd, "verbs": VERBS, "user": "alice",
            "reason": "looks right", "timeout": 10.0, "env": self.env, **kw})

    def calls(self) -> list[dict]:
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def codes(self) -> dict:
        if not os.path.exists(self.state):
            return {}
        with open(self.state) as f:
            return json.load(f)["codes"]

    def reset(self) -> None:
        for p in (self.log, self.state):
            if os.path.exists(p):
                os.remove(p)


class Canned:
    """A harness that answers with the scripted steps."""

    def __init__(self, d: str, *steps: dict):
        self.script = os.path.join(d, "canned.py")
        with open(self.script, "w") as f:
            f.write(CANNED)
        self.log = os.path.join(d, "canned.jsonl")
        self.reset()
        self.env = dict(os.environ, CANNED=json.dumps({"log": self.log, "steps": list(steps)}))
        self.cmd = [sys.executable, self.script]

    def run(self, ask, value, **kw) -> dict:
        return relay.run(ask, value, **{
            "cmd": self.cmd, "verbs": VERBS, "user": "alice",
            "reason": "looks right", "timeout": 10.0, "env": self.env, **kw})

    def calls(self) -> list[dict]:
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def reset(self) -> None:
        if os.path.exists(self.log):
            os.remove(self.log)


def chal(code: str = "c0de1234", **over) -> dict:
    """A step: the harness's challenge for the standard provide ask, value 12."""
    doc = {"code": CH, "error": "A confirm code is required.",
           "params": {"confirm_code": code},
           "subject": {"verb": "facts confirm", "items": {"unit_cost": 12}}}
    doc.update(over)
    return {"out": doc, "exit": 2}


def done(**doc) -> dict:
    return {"out": {"ok": True, "message": "Confirmed unit_cost.", **doc}, "exit": 0}


def assert_shape(res: dict) -> None:
    assert set(res) == {"ok", "code", "message", "verb"}, res
    assert isinstance(res["ok"], bool) and isinstance(res["verb"], list)
    assert (res["code"] is None) == res["ok"], res
    assert res["code"] is None or res["code"] in core.CODES, res
    m = res["message"]
    assert isinstance(m, str) and len(m) <= 300 and "\n" not in m, m
    assert all(unicodedata.category(c) != "Cc" for c in m), repr(m)


def never_sent_a_code(calls: list[dict]) -> bool:
    return not any(w.startswith("--code") for c in calls for w in c["argv"])


@contextlib.contextmanager
def patched_environ(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update({k: v for k, v in kw.items() if v is not None})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def captured_log():
    """Every record the relay logs, at debug level."""
    records: list[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)
    lg = logging.getLogger("console.relay")
    old_level, h = lg.level, H(logging.DEBUG)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(h)
    try:
        yield records
    finally:
        lg.removeHandler(h)
        lg.setLevel(old_level)


def assert_stopped(*paths: str) -> None:
    """Each heartbeat file exists (the process ran) and has stopped growing."""
    for p in paths:
        assert os.path.exists(p) and os.path.getsize(p) > 0, f"{p} never beat"
    before = [os.path.getsize(p) for p in paths]
    time.sleep(0.4)
    assert before == [os.path.getsize(p) for p in paths], "a process is still running"


# ---------------------------------------------------------- the exchange --

def test_a_full_exchange_runs_the_gate_twice():
    with _t.tmpdir() as d:
        rig = Rig(d)
        res = rig.run(provide_ask(), "12.5", reason="checked the invoice")
        assert_shape(res)
        assert res["ok"] is True and res["code"] is None, res
        assert res["verb"] == VERB
        assert res["message"] == "Confirmed unit_cost."
        c1, c2 = rig.calls()
        first = VERB + ["--value=12.5", "--reason=checked the invoice", "--json"]
        assert c1["argv"] == first and c1["run"] == 1
        n = len(first)
        assert c2["run"] == 2 and c2["argv"][:n] == first
        # what the console adds is one `--name=value` element each (the log
        # shows the code as ***)
        assert c2["argv"][n:n + 2] == ["--code=***", "--relay-user=web:alice"]
        at = c2["argv"][n + 2]
        assert at.startswith("--relay-at=") and ISO.fullmatch(at[len("--relay-at="):]), at
        assert len(c2["argv"]) == n + 3
        assert not {"--code", "--relay-user", "--relay-at"} & set(c2["argv"])
        assert [c["used"] for c in rig.codes().values()] == [True]


def test_the_subject_is_matched_by_value_not_by_spelling():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for v, shown in (("1.50", 1.5), ("007", 7), ("-3", -3), ("12", 12)):
            rig.reset()
            res = rig.run(provide_ask(), v)
            assert res["ok"], (v, res)
            assert len(rig.calls()) == 2, (v, shown)


def test_a_yes_or_no_answer_passes_no_value():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for step in ("approve", "confirm"):
            rig.reset()
            res = rig.run(approve_ask(step), "yes")
            assert res["ok"], res
            c1, c2 = rig.calls()
            assert c1["argv"] == ["queue", "approve", "q1", "--reason=looks right", "--json"]
            assert not any(a.startswith("--value") for a in c2["argv"])


def test_a_no_never_reaches_the_harness():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for step, v in (("approve", "no"), ("confirm", "no"), ("approve", " no "),
                        ("approve", "maybe"), ("approve", "")):
            res = rig.run(approve_ask(step), v)
            assert not res["ok"] and res["code"] == "gate_refused", (step, v, res)
            assert_shape(res)
        assert rig.calls() == []
        # a typed "no" is a value, not a refusal: it is written like any other
        res = rig.run(provide_ask(), "no")
        assert res["ok"], res
        assert rig.calls()[0]["argv"][3] == "--value=no"


def test_a_value_the_ask_would_not_accept_never_runs():
    with _t.tmpdir() as d:
        rig = Rig(d)
        number = _base("provide", {"verb": list(VERB), "value_arg": True,
                                   "expect": {"items": {"unit_cost": "$value"}}},
                       input={"type": "number", "min": 0, "max": 100})
        for v in ("abc", "-5", "1000", "nan", "", "  "):
            res = rig.run(number, v)
            assert not res["ok"] and res["code"] == "gate_refused", (v, res)
        assert rig.calls() == []
        assert rig.run(number, "42.5")["ok"]


def test_a_document_that_is_not_a_challenge_is_the_answer():
    with _t.tmpdir() as d:
        c = Canned(d, {"out": {"ok": True, "message": "Already confirmed.",
                               "result": {"confirmed": {"unit_cost": 12}}}, "exit": 0})
        res = c.run(provide_ask(), "12")
        assert_shape(res)
        assert res["ok"] and res["message"] == "Already confirmed."
        assert len(c.calls()) == 1      # nothing more to run

        for step, ok, msg in (
                ({"out": {"error": "No such key: unit_cost", "code": "unknown_key"}, "exit": 2},
                 False, "No such key: unit_cost"),
                ({"out": {"ok": True}, "exit": 0}, True, ""),
                ({"out": {}, "exit": 1}, False, core.CODES["gate_refused"]),
                # the exit code is the harness's verdict, not a field it prints
                ({"out": {"ok": True, "message": "fine"}, "exit": 1}, False, "fine"),
                ({"out": {"error": "the error", "message": "the message"}, "exit": 3},
                 False, "the error"),
                ({"out": {"error": "ignored", "message": "the message", "result": "r"}, "exit": 0},
                 True, "the message"),
                ({"out": {"result": "just a result"}, "exit": 0}, True, "just a result")):
            c = Canned(d, step)
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert res["ok"] is ok and res["message"] == msg, (step, res)
            assert len(c.calls()) == 1 and never_sent_a_code(c.calls())
            if not ok:
                assert res["code"] == "gate_refused"


def test_no_secret_is_refused_in_the_harness_own_words():
    with _t.tmpdir() as d:
        rig = Rig(d, "no_secret")
        res = rig.run(provide_ask(), "12")
        assert_shape(res)
        assert res["code"] == "gate_refused" and not res["ok"]
        assert res["message"] == fake_harness.NO_SECRET
        assert len(rig.calls()) == 1 and rig.codes() == {}


def test_a_subject_that_is_not_what_was_shown_never_gets_the_code():
    with _t.tmpdir() as d:
        rig = Rig(d, "changed")
        res = rig.run(provide_ask(), "12")
        assert_shape(res)
        assert res["code"] == "gate_changed" and not res["ok"], res
        assert res["message"] == core.CODES["gate_changed"]
        assert len(rig.calls()) == 1 and never_sent_a_code(rig.calls())
        (code, entry), = rig.codes().items()
        assert entry["used"] is False and code not in json.dumps(res)

        for name, subject in (
                ("no subject", None),
                ("a string", "facts confirm unit_cost=12"),
                ("a list", [{"unit_cost": 12}]),
                ("another key", {"verb": "facts confirm", "items": {"other_cost": 12}}),
                ("no items", {"verb": "facts confirm"}),
                ("another value", {"items": {"unit_cost": 12.01}}),
                ("a value as text", {"items": {"unit_cost": "twelve"}}),
                ("a bool", {"items": {"unit_cost": True}}),
                ("an extra item", {"items": {"unit_cost": 12, "wallet_pct": 90}}),
                ("no item at all", {"items": {}}),
                ("the item in a list", {"items": [{"unit_cost": 12}]})):
            doc = chal()["out"]
            if subject is None:
                doc.pop("subject")
            else:
                doc["subject"] = subject
            c = Canned(d, {"out": doc, "exit": 2}, done())
            res = c.run(provide_ask(), "12")
            assert res["code"] == "gate_changed" and not res["ok"], (name, res)
            assert len(c.calls()) == 1 and never_sent_a_code(c.calls()), name
            assert "c0de1234" not in json.dumps(res)


def test_the_harness_envelope_may_say_more_than_the_ask_names():
    # what the ask names must match; the top level of the subject may carry
    # more (the harness's own verb, market, version…)
    with _t.tmpdir() as d:
        doc = chal()["out"]
        doc["subject"] = {"verb": "facts confirm", "market": "XX", "version": 3,
                          "items": {"unit_cost": "12.0"}}
        c = Canned(d, {"out": doc, "exit": 2}, done())
        assert c.run(provide_ask(), "12")["ok"]
        assert len(c.calls()) == 2
        # the fake harness's subject has a `verb` the ask does not name
        assert Rig(d).run(provide_ask(), "12")["ok"]


def test_an_item_the_ask_did_not_name_is_a_change_and_the_code_is_never_sent():
    with _t.tmpdir() as d:
        rig = Rig(d, "extra")
        res = rig.run(provide_ask(), "12")
        assert_shape(res)
        assert res["code"] == "gate_changed" and not res["ok"], res
        assert res["message"] == core.CODES["gate_changed"]
        assert len(rig.calls()) == 1 and never_sent_a_code(rig.calls())
        (code, entry), = rig.codes().items()
        assert entry["used"] is False and code not in json.dumps(res)
        assert fake_harness.EXTRA not in json.dumps(res)

        def subject(items):
            doc = chal()["out"]
            doc["subject"] = {"verb": "facts confirm", "items": items}
            return {"out": doc, "exit": 2}
        one = provide_ask(expect={"items": {"unit_cost": {"amount": "$value", "unit": "USD"}}})
        listed = provide_ask(expect={"items": [{"unit_cost": "$value"}]})
        for name, ask, items, same in (
                ("an item beside the named one", provide_ask(),
                 {"unit_cost": 12, "wallet_pct": 90}, {"unit_cost": 12}),
                ("an item ahead of it", provide_ask(),
                 {"a_first": 1, "unit_cost": 12}, {"unit_cost": 12}),
                ("an item with the same value", provide_ask(),
                 {"unit_cost": 12, "also": 12}, {"unit_cost": 12}),
                ("an item that is an object", provide_ask(),
                 {"unit_cost": 12, "also": {"a": 1}}, {"unit_cost": 12}),
                ("a key inside a named object", one,
                 {"unit_cost": {"amount": 12, "unit": "USD", "note": "x"}},
                 {"unit_cost": {"amount": 12, "unit": "USD"}}),
                ("a named key missing inside", one,
                 {"unit_cost": {"amount": 12}}, {"unit_cost": {"amount": 12, "unit": "USD"}}),
                ("an element in a list", listed,
                 [{"unit_cost": 12}, {"other": 1}], [{"unit_cost": 12}]),
                ("a key on the element of a list", listed,
                 [{"unit_cost": 12, "other": 1}], [{"unit_cost": 12}])):
            c = Canned(d, subject(items), done())
            res = c.run(ask, "12")
            assert res["code"] == "gate_changed" and not res["ok"], (name, res)
            assert len(c.calls()) == 1 and never_sent_a_code(c.calls()), name
            # the same subject without the extra is the write that was shown
            c = Canned(d, subject(same), done())
            assert c.run(ask, "12")["ok"], name
            assert len(c.calls()) == 2, name


def test_an_expected_key_the_subject_lacks_is_a_change():
    with _t.tmpdir() as d:
        named = provide_ask(expect={"verb": "facts confirm", "items": {"unit_cost": "$value"}})
        rig = Rig(d)
        assert rig.run(named, "12")["ok"]                   # the fake names its verb
        market = provide_ask(expect={"market": "XX", "items": {"unit_cost": "$value"}})
        rig.reset()
        res = rig.run(market, "12")                         # …and no market
        assert res["code"] == "gate_changed" and not res["ok"], res
        assert len(rig.calls()) == 1 and never_sent_a_code(rig.calls())

        def subject(**more):
            doc = chal()["out"]
            doc["subject"] = {"items": {"unit_cost": 12}, **more}
            return {"out": doc, "exit": 2}
        for name, ask, step, ok in (
                ("no verb", named, subject(), False),
                ("another verb", named, subject(verb="facts reject"), False),
                ("the verb, and more", named, subject(verb="facts confirm", market="XX"), True),
                ("no market", market, subject(verb="facts confirm"), False),
                ("the market", market, subject(market="XX", version=2), True),
                ("another market", market, subject(market="YY"), False)):
            c = Canned(d, step, done())
            res = c.run(ask, "12")
            assert res["ok"] is ok, (name, res)
            assert len(c.calls()) == (2 if ok else 1), name
            if not ok:
                assert res["code"] == "gate_changed" and never_sent_a_code(c.calls()), name


def test_a_subject_spelled_oddly_is_a_change_never_an_exception():
    with _t.tmpdir() as d:
        for name, items in (("a 4000-digit number", {"unit_cost": 10 ** 4000}),
                            ("a huge exponent", {"unit_cost": "1e999999999999999999999"}),
                            ("not a number", {"unit_cost": "0x10"}),
                            ("nested too deep to match", {"unit_cost": [[[[[[12]]]]]]}),
                            ("nothing", {"unit_cost": None}),
                            ("an object", {"unit_cost": {"amount": 12}})):
            doc = chal()["out"]
            doc["subject"] = {"items": items}
            c = Canned(d, {"out": doc, "exit": 2}, done())
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert res["code"] == "gate_changed" and not res["ok"], (name, res)
            assert len(c.calls()) == 1 and never_sent_a_code(c.calls()), name


def test_the_challenges_exit_status_does_not_matter():
    with _t.tmpdir() as d:
        for status in ("0", "2", "7"):
            rig = Rig(d, FAKE_HARNESS_CHALLENGE_EXIT=status)
            rig.reset()
            res = rig.run(provide_ask(), "12")
            assert res["ok"], (status, res)


def test_a_hung_first_call_times_out_and_the_whole_tree_is_killed():
    with _t.tmpdir() as d:
        rig = Rig(d, "slow", FAKE_HARNESS_GRANDCHILD="1")
        t0 = time.monotonic()
        res = rig.run(provide_ask(), "12", timeout=1.0)
        assert time.monotonic() - t0 < 5
        assert_shape(res)
        assert res["code"] == "gate_timeout" and not res["ok"], res
        assert res["message"] == core.CODES["gate_timeout"]
        assert len(rig.calls()) == 1
        assert_stopped(rig.log + ".beat.child", rig.log + ".beat.grandchild")


def test_a_hung_second_call_times_out_and_says_it_may_have_written():
    with _t.tmpdir() as d:
        rig = Rig(d, "slow", FAKE_HARNESS_SLOW_RUN="2", FAKE_HARNESS_GRANDCHILD="1")
        res = rig.run(provide_ask(), "12", timeout=1.5)
        assert_shape(res)
        assert res["code"] == "gate_timeout" and not res["ok"], res
        assert "may have written" in res["message"]
        c1, c2 = rig.calls()
        assert "--code=***" in c2["argv"]
        assert_stopped(rig.log + ".beat.child", rig.log + ".beat.grandchild")


def test_a_slow_harness_within_the_timeout_is_fine():
    with _t.tmpdir() as d:
        rig = Rig(d, "slow", FAKE_HARNESS_SLOW_SECS="0.3")
        res = rig.run(provide_ask(), "12", timeout=5.0)
        assert res["ok"], res


def test_a_refused_second_call_is_refused_with_the_harness_words():
    with _t.tmpdir() as d:
        rig = Rig(d, "fail")
        res = rig.run(provide_ask(), "12")
        assert_shape(res)
        assert res["code"] == "gate_refused" and not res["ok"]
        assert res["message"] == fake_harness.REFUSED
        assert len(rig.calls()) == 2
        for c in rig.codes():
            assert c not in json.dumps(res)


def test_a_banner_and_stderr_noise_are_not_the_answer():
    with _t.tmpdir() as d:
        rig = Rig(d, "noisy")
        res = rig.run(provide_ask(), "12")
        assert res["ok"] and res["message"] == "Confirmed unit_cost.", res
        assert len(rig.calls()) == 2


def test_the_document_is_all_of_stdout_or_its_last_line():
    with _t.tmpdir() as d:
        doc = {"ok": True, "message": "Confirmed.", "result": {"n": 1}}
        for name, out in (
                ("one line", json.dumps(doc)),
                ("pretty printed", json.dumps(doc, indent=2)),
                ("trailing blanks", json.dumps(doc) + "\n\n  \n"),
                ("a banner", "Harness 1.2 {starting}\n" + json.dumps(doc)),
                ("a json log line", '{"level": "info"}\n' + json.dumps(doc)),
                ("a windows newline", "banner\r\n" + json.dumps(doc) + "\r\n")):
            c = Canned(d, {"out": out, "exit": 0})
            res = c.run(provide_ask(), "12")
            assert res["ok"] and res["message"] == "Confirmed.", (name, res)
            c.reset()


def test_output_that_is_not_a_document_is_refused_never_raised():
    with _t.tmpdir() as d:
        junk = [{"out": "Segmentation fault (core dumped)"}, {"out": ""},
                {"out": "", "exit": 1}, {"out": "<html>502 Bad Gateway</html>", "exit": 1},
                {"out": "[" * 200000}, {"out": "{" * 200000, "exit": 1},
                {"out_hex": "fffe00ff"}, {"out": "42"}, {"out": "null"},
                {"out": '"done"'}, {"out": "true"}, {"out": '{"a": 1'},
                {"out": "{'single': 'quotes'}"}, {"out": "x", "pad": 3_000_000},
                {"out": json.dumps({"n": "9" * 5000})[:-1]}]
        for step in junk:
            c = Canned(d, step)
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert not res["ok"] and res["code"] == "gate_refused", (step.get("out", "")[:30], res)
            assert "Segmentation" not in res["message"] and "502" not in res["message"]
            assert len(c.calls()) == 1
        # a huge integer is a document (Python may refuse to read it): either way no crash
        c = Canned(d, {"out": '{"result": {"n": ' + "9" * 5000 + '}}', "exit": 0})
        assert_shape(c.run(provide_ask(), "12"))


def test_a_second_call_without_a_clear_answer_is_unsure():
    # the harness may have written before it stopped: neither "refused" nor "ok"
    with _t.tmpdir() as d:
        silent = [{"out": "boom"}, {"out": ""}, {"out": "", "exit": 1},
                  {"out": "boom", "exit": 1}, {"out": "boom", "exit": 3},
                  {"out": "Traceback (most recent call last):\n  boom", "exit": 1},
                  {"out": "<html>502 Bad Gateway</html>", "exit": 1},
                  {"out": "[" * 200000, "exit": 1}, {"out_hex": "fffe00ff", "exit": 1},
                  {"out": "x", "pad": 3_000_000}, {"out": '{"a": 1', "exit": 1},
                  # a document with no words in it (`error` or `message`), failing
                  {"out": {}, "exit": 1}, {"out": {"ok": False}, "exit": 1},
                  {"out": {"result": "no words of its own"}, "exit": 1},
                  {"out": {"error": ""}, "exit": 1}, {"out": {"error": " \n "}, "exit": 1},
                  {"out": {"error": 5}, "exit": 1}, {"out": {"error": None}, "exit": 2},
                  {"out": {"error": {"text": "refused"}}, "exit": 1},
                  {"out": {"message": ["refused"]}, "exit": 1},
                  {"out": [1, 2], "exit": 1}, {"out": "null", "exit": 1},
                  {"out": '"refused"', "exit": 1}]
        for step in silent:
            c = Canned(d, chal(), step)
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert res["code"] == "gate_unsure" and not res["ok"], (step, res)
            assert res["message"] == core.CODES["gate_unsure"]
            assert len(c.calls()) == 2, step
            assert "c0de1234" not in json.dumps(res)
            c.reset()


def test_a_second_call_that_says_it_refused_stays_refused():
    # the harness answered: it wrote nothing, and said why
    with _t.tmpdir() as d:
        for step, text in (
                ({"out": {"error": "Not allowed", "code": "denied"}, "exit": 1}, "Not allowed"),
                ({"out": {"message": "No thanks"}, "exit": 2}, "No thanks"),
                ({"out": {"error": "", "message": "the message"}, "exit": 1}, "the message"),
                ({"out": {"error": "the error", "message": "the message", "result": "r"}, "exit": 1},
                 "the error"),
                ({"out": "starting up\n" + json.dumps({"error": "After a banner"}), "exit": 1},
                 "After a banner")):
            c = Canned(d, chal(), step)
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert res["code"] == "gate_refused" and not res["ok"], (step, res)
            assert res["message"] == text, res
            assert len(c.calls()) == 2
            c.reset()


def test_a_second_call_that_exits_zero_is_the_harness_saying_it_worked():
    with _t.tmpdir() as d:
        for step, message in (({"out": {"ok": True}, "exit": 0}, ""),
                              ({"out": {"result": "just a result"}, "exit": 0}, "just a result"),
                              ({"out": {"message": "Done."}, "exit": 0}, "Done."),
                              ({"out": [1, 2], "exit": 0}, "")):
            c = Canned(d, chal(), step)
            res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert res["ok"] is True and res["message"] == message, (step, res)
            c.reset()


def test_a_harness_that_wrote_and_then_died_is_unsure_not_refused():
    with _t.tmpdir() as d:
        script, written = os.path.join(d, "writes_then_dies.py"), os.path.join(d, "WRITTEN")
        with open(script, "w") as f:
            f.write(WRITES_THEN_DIES)
        for how in ("crash", "signal", "close", "garbage"):
            if os.path.exists(written):
                os.remove(written)
            res = relay.run(provide_ask(), "12", cmd=[sys.executable, script], verbs=VERBS,
                            user="alice", reason="r", timeout=10.0,
                            env=dict(os.environ, HOW=how, WRITTEN=written))
            assert_shape(res)
            with open(written) as f:
                assert f.read() == "unit_cost=12\n", how       # it did write
            assert res["code"] == "gate_unsure" and not res["ok"], (how, res)
            assert "may or may not" in res["message"] and "nothing was written" not in res["message"]


def test_a_challenge_asked_again_is_a_refusal_that_wrote_nothing():
    with _t.tmpdir() as d:
        for status in (0, 2):
            c = Canned(d, chal(), dict(chal("another1"), exit=status))
            res = c.run(provide_ask(), "12")
            assert res["code"] == "gate_refused" and not res["ok"], res
            assert "again" in res["message"] and "nothing was written" in res["message"]
            assert "another1" not in json.dumps(res)
            c.reset()


def test_a_challenge_that_cannot_be_read_is_refused_even_at_exit_zero():
    with _t.tmpdir() as d:
        shapes = [{"code": CH}, {"code": CH, "params": {}},
                  {"code": CH, "params": "code"},
                  {"code": CH, "params": {"confirm_code": ""}},
                  {"code": CH, "params": {"confirm_code": 12345}},
                  {"code": CH, "params": {"confirm_code": "two words"}},
                  {"code": CH, "params": {"confirm_code": "line\nbreak"}},
                  {"code": CH, "params": {"confirm_code": "x" * 201}}]
        for doc in shapes:
            doc["subject"] = {"items": {"unit_cost": 12}}
            for status in (0, 2):
                c = Canned(d, {"out": doc, "exit": status}, done())
                res = c.run(provide_ask(), "12")
                assert res["code"] == "gate_refused" and not res["ok"], (doc, res)
                assert len(c.calls()) == 1
                c.reset()


def test_a_code_that_starts_with_a_dash_is_still_one_argument():
    with _t.tmpdir() as d:
        c = Canned(d, chal("-abc-123"), done())
        res = c.run(provide_ask(), "12")
        assert res["ok"], res
        argv = c.calls()[1]["argv"]
        i = argv.index("--code=-abc-123")
        assert argv[i + 1] == "--relay-user=web:alice" and argv[i + 2].startswith("--relay-at=")
        assert not {"--code", "--relay-user", "--relay-at"} & set(argv)
        assert "-abc-123" not in json.dumps(res)

        # why it matters: a harness that reads its options with argparse takes
        # `--code=-abc-123` but would call `--code -abc-123` a missing argument
        # (it got the code intact: the relay scrubs it from the harness's echo)
        script = os.path.join(d, "argparse_harness.py")
        with open(script, "w") as f:
            f.write(ARGPARSE)
        res = relay.run(provide_ask(), "12", cmd=[sys.executable, script], verbs=VERBS,
                        user="alice", reason="r", timeout=10.0)
        assert res["ok"] and res["message"] == "seen *** web:alice", res


def test_a_command_that_cannot_start_is_unavailable():
    with _t.tmpdir() as d:
        plain = os.path.join(d, "not-executable")
        with open(plain, "w") as f:
            f.write("#!/bin/sh\necho hi\n")
        os.chmod(plain, 0o644)
        garbage = os.path.join(d, "not-a-program")
        with open(garbage, "wb") as f:
            f.write(b"\x00\x01\x02 not a program")
        os.chmod(garbage, 0o755)
        for cmd in ([os.path.join(d, "no", "such", "harness")], ["no-such-harness-xyz"],
                    [plain], [d], [garbage]):
            res = relay.run(provide_ask(), "12", cmd=cmd, verbs=VERBS, user="alice",
                            reason="r", timeout=5.0)
            assert_shape(res)
            assert res["code"] == "gate_unavailable" and not res["ok"], (cmd, res)
            assert d not in res["message"], "the operator's paths stay off the page"
        rig = Rig(d)
        for cmd in ([], "harness", [sys.executable, 5], ["a\x00b"], None,
                    (sys.executable, FAKE)):
            res = rig.run(provide_ask(), "12", cmd=cmd)
            assert res["code"] == "gate_unavailable" and not res["ok"], (cmd, res)
        assert rig.calls() == []


def test_only_an_allowed_verb_runs():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for verbs, allowed in (
                ([["facts", "confirm"]], True),
                ([["facts"]], True),
                ([["facts", "confirm", "unit_cost"]], True),
                ([["queue", "approve"], ["facts", "confirm"]], True),
                ([["queue", "approve"]], False),
                ([], False), (None, False), ([[]], False), ([["facts", "confirm", "unit_cost", "x"]], False),
                ([["facts", "conf"]], False), ([["fact"]], False), ([["confirm"]], False),
                ([["FACTS", "confirm"]], False), (["facts confirm"], False),
                ([("facts", "confirm")], False)):
            rig.reset()
            res = rig.run(provide_ask(), "12", verbs=verbs)
            assert_shape(res)
            if allowed:
                assert res["ok"], (verbs, res)
            else:
                assert res["code"] == "gate_unavailable" and not res["ok"], (verbs, res)
                assert rig.calls() == [], f"{verbs} ran something"


def test_a_malformed_gate_never_runs():
    with _t.tmpdir() as d:
        rig = Rig(d)
        base = provide_ask()

        def with_gate(**changes):
            a = copy.deepcopy(base)
            for k, v in changes.items():
                if v is KeyError:
                    a["gate"].pop(k)
                else:
                    a["gate"][k] = v
            return a

        def without(key):
            a = copy.deepcopy(base)
            a.pop(key)
            return a
        bad = [without("gate"), without("step"), {**base, "gate": None}, {**base, "gate": "x"},
               {**base, "gate": []}, {**base, "step": "bogus"},
               with_gate(verb=KeyError), with_gate(verb="facts confirm"), with_gate(verb=[]),
               with_gate(verb=[""]), with_gate(verb=["facts", 5]),
               with_gate(verb=["facts", "confirm", "$(id)"]),
               with_gate(verb=["facts", "confirm", "unit_cost\n"]),
               with_gate(verb=["facts", "confirm", "--code"]),
               with_gate(verb=["facts", "confirm", "a b"]),
               with_gate(verb=["facts", "confirm", "a", "b", "c", "d", "e"]),
               with_gate(expect=KeyError), with_gate(expect={}), with_gate(expect="x"),
               with_gate(expect=[1]), with_gate(value_arg="yes"), with_gate(value_arg=1),
               None, [], "gate", 5]
        for ask in bad:
            res = rig.run(ask, "12")
            assert_shape(res)
            assert res["code"] == "gate_unavailable" and not res["ok"], (ask, res)
        assert rig.calls() == []


def test_what_the_human_types_is_one_argument_and_never_runs():
    with _t.tmpdir() as d:
        canary = os.path.join(d, "PWNED")
        rig = Rig(d)
        values = ["x; rm -rf /", "$(id)", "--code", "-1", "--value=1", "-", "--", "--json",
                  "--reason=evil", "a\nb", "a\tb", "值 ü 🙂", "'; touch X; '", '"q"', "back\\slash",
                  "a  b   c", "*", "~", "$HOME", "%s %d", "{}", "-- --code x",
                  f"x; touch {canary}", f"$(touch {canary})", f"`touch {canary}`",
                  f"x && touch {canary}", f"x | touch {canary}", f"x\ntouch {canary}",
                  f"x > {canary}"]
        for v in values:
            rig.reset()
            res = rig.run(provide_ask(), v)
            assert res["ok"], (v, res)
            c1, c2 = rig.calls()
            assert c1["argv"] == VERB + [f"--value={v}", "--reason=looks right", "--json"], v
            assert c2["argv"][:len(c1["argv"])] == c1["argv"], v
        assert not os.path.exists(canary)
        assert not os.path.exists("X")

        # the surrounding blanks of an answer are not part of it (the store
        # records the same normalized value the harness is told)
        rig.reset()
        assert rig.run(provide_ask(), " -1 ")["ok"]
        assert rig.calls()[0]["argv"][3] == "--value=-1"


def test_the_reason_is_one_line_of_at_most_200_characters_and_one_argument():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for given, sent in (
                ("ok", "ok"),
                ("line one\nline two", "line one line two"),
                ("  tabs\t\tand   spaces  ", "tabs and spaces"),
                ("sep\u2028para\u2029end\x85x", "sep para end x"),
                ("--code evil --relay-user web:root", "--code evil --relay-user web:root"),
                ("$(id); rm -rf /", "$(id); rm -rf /"),
                ("ctl\x00\x1b[31mred\x7f", "ctl [31mred"),
                ("bidi\u202ereversed", "bidi reversed"),
                ("\ud800bad", "bad"),
                ("x" * 500, "x" * 200),
                ("ünï 值", "ünï 值"),
                ("", ""), ("   ", ""), (None, "")):
            rig.reset()
            res = rig.run(provide_ask(), "12", reason=given)
            assert res["ok"], (given, res)
            c1, c2 = rig.calls()
            assert c1["argv"][-2:] == ["--reason=" + sent, "--json"], (given, c1["argv"])
            assert c2["argv"][:len(c1["argv"])] == c1["argv"]


def test_an_answer_that_cannot_be_an_argument_is_refused_not_raised():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for v in ("a\x00b", "\ud800", "x\x00", None, 12, b"12"):
            res = rig.run(provide_ask(), v)
            assert_shape(res)
            assert res["code"] == "gate_refused" and not res["ok"], (v, res)
        assert rig.calls() == []


def test_the_user_is_a_plain_token():
    with _t.tmpdir() as d:
        rig = Rig(d)
        for bad in ("", "a b", "-x", "alice\n", "a;b", "a" * 81, 5, None, "$(id)"):
            res = rig.run(provide_ask(), "12", user=bad)
            assert res["code"] == "gate_refused" and not res["ok"], (bad, res)
        assert rig.calls() == []
        for good in ("alice", "ann.lee@example", "a:b/c", "A" * 80):
            rig.reset()
            assert rig.run(provide_ask(), "12", user=good)["ok"], good
            assert "--relay-user=web:" + good in rig.calls()[1]["argv"]


def test_the_code_is_in_no_result_and_no_log_line():
    with _t.tmpdir() as d:
        rig = Rig(d)
        with captured_log() as records:
            res = rig.run(provide_ask(), "12")
        (code,) = rig.codes()
        assert res["ok"] and code not in json.dumps(res)
        texts = [r.getMessage() for r in records]
        assert texts and all(code not in t for t in texts), texts
        assert any("--code=***" in t for t in texts), texts
        assert all(r.levelno == logging.DEBUG for r in records), "values are debug-only"

        # a harness that echoes the code back has it scrubbed
        echo = {"error": "Code @CODE@ was rejected", "code": "x",
                "result": {"tried": "@CODE@", "nested": [{"k": "@CODE@"}], "@CODE@": 1}}
        ok = {"message": "Confirmed with @CODE@", "result": {"tried": "@CODE@", "nested": [["@CODE@"]]}}
        for step in ({"out": echo, "exit": 1}, {"out": ok, "exit": 0}):
            c = Canned(d, chal("s3cr3tC0de"), step)
            with captured_log() as records:
                res = c.run(provide_ask(), "12")
            assert_shape(res)
            assert "s3cr3tC0de" not in json.dumps(res), res
            assert all("s3cr3tC0de" not in r.getMessage() for r in records)
            if res["ok"]:
                assert "***" in res["message"]
            c.reset()


def test_the_harness_text_is_one_short_clean_line():
    with _t.tmpdir() as d:
        for text, want in (("line one\nline two", "line one line two"),
                           ("\x1b[31mred\x1b[0m done", "[31mred [0m done"),
                           ("\u202egnp.exe", "gnp.exe"),
                           ("tab\tand\r\nreturn", "tab and return"),
                           ("  padded  ", "padded"),
                           ("é 值 🙂", "é 值 🙂")):
            for step, code in (({"out": {"error": text}, "exit": 2}, "gate_refused"),
                               ({"out": {"message": text}, "exit": 0}, None)):
                res = Canned(d, step).run(provide_ask(), "12")
                assert_shape(res)
                assert res["code"] == code and res["message"] == want, (text, res)
        long = "word " * 200
        for step in ({"out": {"error": long}, "exit": 2}, {"out": {"message": long}, "exit": 0}):
            res = Canned(d, step).run(provide_ask(), "12")
            assert_shape(res)
            assert len(res["message"]) == 300 and res["message"].endswith("…"), res
        res = Canned(d, {"out": {"error": " \n\t "}, "exit": 2}).run(provide_ask(), "12")
        assert res["message"] == core.CODES["gate_refused"]


def test_the_result_is_the_four_keys_whatever_the_harness_prints():
    # the harness's own `result` (a dict, a list, a huge one) never comes back
    with _t.tmpdir() as d:
        def go(result, **doc):
            step = {"out": {"message": "m", "result": result, **doc}, "exit": 0}
            return Canned(d, step).run(provide_ask(), "12")
        for result in ({"a": 1, "b": [1, 2]}, [1, "x"], {"k": "x" * 3000}, "plain text"):
            res = go(result)
            assert_shape(res)
            assert res["ok"] and res["message"] == "m", res
        only = Canned(d, {"out": {"result": "plain text"}, "exit": 0}).run(provide_ask(), "12")
        assert_shape(only)
        assert only["message"] == "plain text"
        assert_shape(Canned(d, {"out": [1, 2], "exit": 0}).run(provide_ask(), "12"))
        for n in (500, 990):        # near the depth Python can read: no crash either way
            deep = "[" * n + "]" * n
            res = Canned(d, {"out": '{"message": "m", "result": ' + deep + "}", "exit": 0}).run(provide_ask(), "12")
            assert_shape(res)


@contextlib.contextmanager
def stdin_is_a_pipe():
    """File descriptor 0 is a pipe inside the block, whatever the runner's stdin
    is. Under a runner whose stdin is /dev/null, a relay that let its child
    inherit stdin would hand it /dev/null and pass for one that closed it."""
    r, w = os.pipe()
    try:
        saved = os.dup(0)
    except OSError:                     # fd 0 is closed
        saved = None
    os.dup2(r, 0)
    try:
        assert stat.S_ISFIFO(os.fstat(0).st_mode)
        yield
    finally:
        if saved is None:
            os.close(0)
        else:
            os.dup2(saved, 0)
            os.close(saved)
        os.close(r)
        os.close(w)


def test_the_child_has_no_terminal_no_stdin_and_not_the_consoles_secret():
    with _t.tmpdir() as d, stdin_is_a_pipe():
        rig = Rig(d)
        rig.env["CONSOLE_SECRET"] = "s" * 40
        assert rig.run(provide_ask(), "12")["ok"]
        assert len(rig.calls()) == 2
        for c in rig.calls():
            assert c["stdin_devnull"] is True, "no stdin, so no prompt can wait on a person"
            assert c["tty"] is False and c["session_leader"] is True
            assert c["console_secret"] is False
            assert c["cwd"] == os.getcwd()

        # env left out: the child inherits ours, minus the secret
        rig.reset()
        with patched_environ(CONSOLE_SECRET="t" * 40, **{k: v for k, v in rig.env.items()
                                                          if k.startswith("FAKE_HARNESS")}):
            res = relay.run(provide_ask(), "12", cmd=rig.cmd, verbs=VERBS,
                            user="alice", reason="r", timeout=10.0)
        assert res["ok"], res
        assert [c["console_secret"] for c in rig.calls()] == [False, False]
        assert [c["stdin_devnull"] for c in rig.calls()] == [True, True]
        assert "CONSOLE_SECRET" not in os.environ


def test_answers_in_parallel_do_not_share_anything():
    with _t.tmpdir() as d:
        rig = Rig(d)
        got: dict = {}

        def go(i):
            got[i] = rig.run(provide_ask(), str(i + 1), user=f"u{i}")
        threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(got) == list(range(6))
        for i, res in got.items():
            assert res["ok"], (i, res)
        assert len(rig.calls()) == 12 and len(rig.codes()) == 6
        for i in range(6):      # each answer's two calls carry its own value and its own user
            mine = [c for c in rig.calls() if f"--value={i + 1}" in c["argv"]]
            assert [c["run"] for c in sorted(mine, key=lambda c: c["run"])] == [1, 2], i
            assert f"--relay-user=web:u{i}" in [c for c in mine if c["run"] == 2][0]["argv"], i


def _fake(rig: Rig, *argv: str, mode: str = "ok") -> tuple[int, dict | str]:
    """One call of the fake harness itself, in this process."""
    out = io.StringIO()
    with patched_environ(FAKE_HARNESS_MODE=mode, FAKE_HARNESS_LOG=rig.log,
                         FAKE_HARNESS_STATE=rig.state), contextlib.redirect_stdout(out):
        status = fake_harness.main(list(argv))
    text = out.getvalue().strip()
    try:
        return status, json.loads(text.splitlines()[-1])
    except ValueError:
        return status, text


def test_the_fake_harness_is_a_faithful_stand_in():
    # relay.py's tests are only as good as the harness they run against
    with _t.tmpdir() as d:
        rig = Rig(d)
        base = ["facts", "confirm", "k", "--value=12", "--reason=r", "--json"]
        who = ["--relay-user=web:bob", "--relay-at=2030-01-02T03:04:05Z"]
        status, doc = _fake(rig, *base)
        assert status == 2 and doc["code"] == CH
        code = doc["params"]["confirm_code"]
        assert doc["subject"] == {"verb": "facts confirm", "items": {"k": 12}}
        assert _fake(rig, *base, "--value=abc", f"--code={code}", *who)[0] == 1   # another value
        assert _fake(rig, *base, "--code=nope", *who)[1]["code"] == "confirm_code_invalid"
        assert _fake(rig, *base, "--code=-nope", *who)[1]["code"] == "confirm_code_invalid"  # a dash is part of the code
        assert _fake(rig, *base, f"--code={code}")[0] == 2                        # no identity
        assert _fake(rig, *base, f"--code={code}", "--relay-user=bob",
                     "--relay-at=2030-01-02T03:04:05Z")[0] == 2
        assert _fake(rig, *base, f"--code={code}", "--relay-user=web:bob",
                     "--relay-at=yesterday")[0] == 2
        # the old two-element form is an unknown option, whichever flag is split
        # (a made-up code: this fake logs only `--code=…` with the code hidden)
        for split in (["--code", "dummy", *who], ["--code=dummy", "--relay-user", "web:bob",
                                                   "--relay-at=2030-01-02T03:04:05Z"],
                      ["--code=dummy", "--relay-user=web:bob", "--relay-at", "2030-01-02T03:04:05Z"]):
            status, doc = _fake(rig, *base, *split)
            assert status == 2 and doc["code"] == "bad_request" and "unknown option" in doc["error"], split
        status, doc = _fake(rig, *base, f"--code={code}", *who)     # the real code was not spent
        assert status == 0 and doc["ok"] is True
        assert doc["result"] == {"confirmed": {"k": 12}, "by": "web:bob",
                                 "at": "2030-01-02T03:04:05Z", "reason": "r"}
        status, doc = _fake(rig, *base, f"--code={code}", *who)                  # single use
        assert status == 1 and doc["code"] == "confirm_code_invalid"

        assert _fake(rig, "facts", "confirm", "k", "--value=1.50", "--json")[1]["subject"]["items"] == {"k": 1.5}
        assert _fake(rig, "facts", "confirm", "k", "--value=1e3", "--json")[1]["subject"]["items"] == {"k": "1e3"}
        assert _fake(rig, "queue", "approve", "q1", "q2", "--json")[1]["subject"]["items"] == {"q1": "yes", "q2": "yes"}
        assert _fake(rig, "facts", "confirm", "k", "--value=12", "--json", mode="changed")[1]["subject"]["items"] == {"k": 13}
        assert _fake(rig, "facts", "confirm", "k", "--value=a", "--json", mode="changed")[1]["subject"]["items"] == {"k": "a!"}
        status, doc = _fake(rig, *base, mode="extra")           # one more item, and it would be written
        assert status == 2 and doc["subject"] == {"verb": "facts confirm",
                                                  "items": {"k": 12, fake_harness.EXTRA: 12}}
        status, done_doc = _fake(rig, *base, f"--code={doc['params']['confirm_code']}", *who, mode="extra")
        assert status == 0 and done_doc["result"]["confirmed"] == {"k": 12, fake_harness.EXTRA: 12}
        assert "extra" in fake_harness.MODES
        assert _fake(rig, "facts", "confirm", "k", "--json", mode="no_secret")[0] == 2
        status, text = _fake(rig, "facts", "confirm", "k")
        assert status == 0 and "--json" in text                                  # not a document
        assert _fake(rig, "facts", "confirm", "k", "--value", "12", "--json")[0] == 2   # split form
        assert _fake(rig, "facts", "--json")[0] == 2                             # too few words
        assert _fake(rig, "facts", "confirm", "--json", mode="bogus")[0] == 2

        with open(rig.log) as f:
            logged = f.read()
        assert code not in logged and '"--code=***"' in logged                   # the code is never logged


def test_run_never_raises_whatever_the_harness_or_the_caller_does():
    with _t.tmpdir() as d:
        # a harness that crashes, one that is killed by a signal, one that closes
        # stdout: on the first call nothing can have been written, so a refusal
        crash = os.path.join(d, "crash.py")
        with open(crash, "w") as f:
            f.write("import os, signal, sys\n"
                    "if 'signal' in os.environ.get('HOW', ''):\n"
                    "    os.kill(os.getpid(), signal.SIGKILL)\n"
                    "if 'close' in os.environ.get('HOW', ''):\n"
                    "    os.close(1)\n"
                    "raise SystemExit('crashed')\n")
        for how in ("signal", "close", "crash"):
            res = relay.run(provide_ask(), "12", cmd=[sys.executable, crash], verbs=VERBS,
                            user="alice", reason="r", timeout=5.0,
                            env=dict(os.environ, HOW=how))
            assert_shape(res)
            assert res["code"] == "gate_refused" and not res["ok"], (how, res)


if __name__ == "__main__":
    _t.main(globals())
