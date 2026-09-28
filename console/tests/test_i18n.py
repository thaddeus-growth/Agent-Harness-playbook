"""i18n.py and i18n.json: both languages complete, the same {params} in both,
and the dictionary and the code that reads it agree in both directions:
every key the code asks for exists, every key is asked for, and a word exists
for every failure code, refusal reason and fold problem core can produce
(so a new one added to core fails here, not on a person's screen).
"""

from __future__ import annotations

import ast
import os
import re
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _t  # noqa: E402
import core  # noqa: E402
import i18n  # noqa: E402

CJK = re.compile(r"[一-鿿]")
# a family of keys built from data: prefix -> the keys core says must exist under it
EXTRA_ERR = {"not_found", "method_not_allowed", "bad_host", "server_error", "other"}   # serve's own, and the fallback


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def params(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def sources():
    return [n for n in ("pages.py", "serve.py") if os.path.exists(os.path.join(ROOT, n))]


def scan(name):
    """(keys asked for by literal, prefixes keys are built from, every string constant)."""
    literal, dynamic, consts = set(), set(), set()
    for node in ast.walk(ast.parse(read(name))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            consts.add(node.value)
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            fn = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if fn in ("t", "raw"):
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    literal.add(a.value)
                elif isinstance(a, ast.BinOp) and isinstance(a.op, ast.Add) and isinstance(a.left, ast.Constant):
                    dynamic.add(a.left.value)
    return literal, dynamic, consts


def test_the_dictionary_is_well_formed_and_both_languages_are_complete():
    assert i18n.LANGS == ("en", "zh")
    assert len(i18n.WORDS) > 100
    for key, v in i18n.WORDS.items():
        assert re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", key), key
        assert set(v) == {"en", "zh"}, f"{key}: exactly en and zh, got {sorted(v)}"
        for lang in i18n.LANGS:
            assert isinstance(v[lang], str) and v[lang].strip() == v[lang] and v[lang], (key, lang)


def test_both_languages_use_the_same_params():
    for key, v in i18n.WORDS.items():
        assert params(v["en"]) == params(v["zh"]), f"{key}: {params(v['en'])} vs {params(v['zh'])}"
    assert params(i18n.WORDS["budget.over"]["en"]) == {"open", "over", "max"}   # the check is not vacuous


def test_the_chinese_is_chinese_and_the_english_is_not():
    for key, v in i18n.WORDS.items():
        if key == "lang.other":           # each language names the other in its own script
            assert not CJK.search(v["zh"]) and CJK.search(v["en"])
            continue
        assert CJK.search(v["zh"]), f"{key}: zh has no Chinese: {v['zh']!r}"
        assert not CJK.search(v["en"]), f"{key}: en has Chinese: {v['en']!r}"
        assert "{" not in re.sub(r"\{\w+\}", "", v["zh"] + v["en"]), key            # no stray braces
    # a translation, not a copy: nothing but the params/punctuation may be equal
    for key, v in i18n.WORDS.items():
        assert v["en"] != v["zh"], key


def test_t_fills_params_and_refuses_what_it_cannot_say():
    assert i18n.t("budget.line", "en", open=3, max=10) == "3 of 10 asks open"
    assert i18n.t("budget.line", "zh", open=3, max=10) == "待处理 3 / 10 项"
    assert i18n.t("age.min", "en", n=5, unused="x") == "5 min ago"          # extra params are harmless
    for call, what in ((lambda: i18n.t("no.such.key", "en"), "key"), (lambda: i18n.t("nav.waiting", "fr"), "language"),
                       (lambda: i18n.t("budget.line", "en", open=3), "param"), (lambda: i18n.t("nav.waiting", None), "language")):
        try:
            call()
        except AssertionError:
            continue
        except Exception as e:      # a KeyError here is exactly the crash this contract rules out
            raise AssertionError(f"{what}: {type(e).__name__} instead of AssertionError")
        raise AssertionError(f"{what}: nothing raised")
    assert i18n.has("nav.waiting") and not i18n.has("nav.nothing")
    # a param's value is inserted verbatim, never formatted again
    assert i18n.t("state.applied_where", "en", where="{open} {x}") == "Applied · {open} {x}"


def test_every_key_the_code_asks_for_exists_and_every_key_is_asked_for():
    assert sources()
    literal, dynamic, consts = set(), set(), set()
    for name in sources():
        lit, dyn, con = scan(name)
        literal |= lit
        dynamic |= dyn
        consts |= con
    missing = sorted(k for k in literal if k not in i18n.WORDS)
    assert not missing, f"the code asks for words that do not exist: {missing}"
    assert len(literal) > 60                                   # the scan found the calls; it is not vacuous
    families = {p for p in consts if p.endswith(".") and any(k.startswith(p) for k in i18n.WORDS)}
    assert {"err.", "bad_value.", "problem.", "step."} <= families, families
    unused = sorted(k for k in i18n.WORDS
                    if k not in literal and k not in consts and not any(k.startswith(p) for p in families))
    assert not unused, f"words nothing uses: {unused}"
    # and each family holds exactly what core can produce, no stale words
    for prefix in families:
        keys = {k[len(prefix):] for k in i18n.WORDS if k.startswith(prefix) and k.count(".") == prefix.count(".")}
        assert keys, prefix


def test_a_word_for_every_failure_code_core_can_return():
    for code in core.CODES:
        assert f"err.{code}" in i18n.WORDS, f"no words for core code {code!r}"
    for code in EXTRA_ERR:
        assert f"err.{code}" in i18n.WORDS, code
    stale = [k for k in i18n.WORDS if k.startswith("err.") and k[4:] not in core.CODES and k[4:] not in EXTRA_ERR
             and k not in ("err.head", "err.details")]
    assert not stale, f"words for codes that no longer exist: {stale}"


def core_reasons():
    """Every reason check_value can return, from its source and by asking it."""
    src = read("core.py")
    body = src[src.index("def check_value"):src.index("def gate_runs")]
    from_source = set(re.findall(r'bad\("(\w+)"', body))
    probes = [({"step": "confirm"}, "maybe"), ({"step": "approve"}, ""),
              ({"step": "choose", "options": [{"value": "a"}]}, "z"),
              ({"step": "provide"}, ""), ({"step": "provide"}, "x" * 501), ({"step": "provide"}, "a\x07b"),
              ({"step": "provide", "input": {"type": "number"}}, "abc"),
              ({"step": "provide", "input": {"type": "number"}}, "nan"),
              ({"step": "provide", "input": {"type": "number", "min": 0, "max": 5}}, "9"),
              ({"step": "provide", "input": {"type": "date"}}, "2026-13-45"),
              ({"step": "provide", "input": {"type": "url"}}, "ftp://x")]
    by_asking = set()
    for ask, value in probes:
        v, err = core.check_value(ask, value)
        assert v is None and err["code"] == "bad_value"
        by_asking.add(err["reason"])
    return from_source, by_asking


def test_a_sentence_for_every_reason_an_answer_can_be_refused():
    from_source, by_asking = core_reasons()
    assert by_asking == from_source, (by_asking, from_source)      # the probes cover every reason in the source
    assert len(from_source) >= 9 and "control" in from_source
    for reason in from_source:
        assert f"bad_value.{reason}" in i18n.WORDS, f"no sentence for refusal reason {reason!r}"
    stale = [k for k in i18n.WORDS if k.startswith("bad_value.") and k[10:] not in from_source]
    assert not stale, stale


def test_a_word_for_every_problem_the_fold_can_report_and_every_step():
    src = read("core.py")
    fold = src[src.index("def fold"):src.index("def open_asks")]
    codes = set(re.findall(r'bad\(e, "(\w+)"\)', fold))
    assert codes >= {"id_used", "unknown_id", "not_open", "unknown_type", "bad_seq"}, codes
    for code in codes:
        assert f"problem.{code}" in i18n.WORDS, code
    assert "problem.other" in i18n.WORDS
    assert {k for k in i18n.WORDS if k.startswith("problem.")} == {f"problem.{c}" for c in codes} | {"problem.other"}
    for step in core.STEPS:
        assert f"step.{step}" in i18n.WORDS
    assert {k for k in i18n.WORDS if k.startswith("step.")} == {f"step.{s}" for s in core.STEPS}
    for word in ("yes", "no"):
        assert f"ans.{word}" in i18n.WORDS
    for k in ("age.now", "age.min", "age.hour", "age.day"):
        assert k in i18n.WORDS


def test_a_refusal_after_the_harness_may_have_been_written_never_says_nothing_was_saved():
    """The second harness call can write before it fails or stops (core.CODES
    says so), so these sentences claim nothing about what was saved; the
    timeout one and the no-clear-answer one tell the person to have the agent
    check."""
    for code in ("gate_refused", "gate_timeout", "gate_unsure", "not_recorded"):
        w = i18n.WORDS[f"err.{code}"]
        assert "nothing was saved" not in w["en"].lower() and "没有保存" not in w["zh"], code
    for code in ("gate_timeout", "gate_unsure"):
        w = i18n.WORDS[f"err.{code}"]
        assert "may or may not" in w["en"] and "agent" in w["en"] and "agent" in w["zh"] and "也可能没有" in w["zh"], code
    # a refusal that stopped before any code was sent still says so
    assert "Nothing was saved" in i18n.WORDS["err.gate_changed"]["en"] and "没有保存" in i18n.WORDS["err.gate_changed"]["zh"]


def test_the_number_and_date_sentences_say_the_exact_shape_core_accepts():
    for lang, want in (("en", ("plain number", "no commas")), ("zh", ("普通数字", "逗号"))):
        assert all(w in i18n.WORDS["bad_value.number"][lang] for w in want), lang
    assert "2026-10-01" in i18n.WORDS["bad_value.date"]["en"] and "2026-10-01" in i18n.WORDS["bad_value.date"]["zh"]
    for text in ("4.2", "2026-10-01"):          # and what they show as an example is itself valid
        v, err = core.check_value({"step": "provide", "input": {"type": "number" if "." in text else "date"}}, text)
        assert v == text and err is None


if __name__ == "__main__":
    _t.main(globals())
