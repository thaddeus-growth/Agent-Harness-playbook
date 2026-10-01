#!/usr/bin/env python3
"""Ad-copy compliance lint (kit/copylint.py): banned terms from an owner's
rule file, and product facts said the wrong way.

  1. banned(): category scoping (all / * everywhere, a category only in
     itself, none when no category), literal and regex terms, one finding
     per distinct regex match, retired rows never fire, the finding's shape.
  2. The confirmed flag: a pending rule still fires, says it is a draft
     (confirmed False, *_draft code); a confirmed one does not.
  3. Rows read from an owner TSV (kit.registry.read_tsv) lint as dicts.
  4. A broken rule is refused, never skipped: bad regex, match, severity,
     status, missing id or term, a fact with no value.
  5. CJK numeral normalisation: 一 两 十二 二十 一〇二, full-width digits,
     3 == 3.0.
  6. facts(): the dosage said two ways in the reference ad (每天3次 and
     每天一次) is found, where a literal-term list alone sees nothing;
     a fact with no pattern or retired is skipped; draft vs confirmed.
  7. copylint.py's msg() calls are closed over its fragment.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import copylint, messages  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.registry import read_tsv  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

COLS = ("id", "category", "term", "match", "severity", "law_ref", "note",
        "status")
TSV = "\t".join(COLS) + "\n" + "\n".join("\t".join(r) for r in [
    ("B001", "all", "最(好|佳|有效)", "regex", "error", "law 9", "superlative",
     "pending"),
    ("B002", "*", "第一", "literal", "error", "law 9", "absolute claim",
     "confirmed"),
    ("B004", "drug", "根治|保证有效", "regex", "error", "law 16", "guarantee",
     "pending"),
    ("B008", "drug", "都能用", "", "warn", "", "widens the indication",
     "pending"),
    ("B010", "food", "治疗", "literal", "error", "law 18", "no cure claims",
     "confirmed"),
    ("B099", "all", "神药", "literal", "error", "law 9", "old rule",
     "retired"),
]) + "\n"


def rows() -> list[dict]:
    p = Path(tmp_dir("copylint-")) / "banned_terms.tsv"
    p.write_text(TSV, encoding="utf-8")
    return read_tsv(p, COLS, id_col="id")


def hits(texts, category, rs=None) -> set[str]:
    return {f["rule"] for f in copylint.banned(rs or rows(), texts, category)}


def test_banned() -> None:
    print("[1] banned terms: scope, match, retired, shape")
    line = [("scene 1", "最有效的神药，第一名，保证根治，治疗都能用")]
    check("drug: all, * and drug rows fire; food and retired do not",
          hits(line, "drug") == {"B001", "B002", "B004", "B008"},
          hits(line, "drug"))
    check("food: all, * and food rows fire; drug rows stop",
          hits(line, "food") == {"B001", "B002", "B010"}, hits(line, "food"))
    check("no category: only all / * rows",
          hits(line, "") == {"B001", "B002"}, hits(line, ""))
    check("a retired row never fires, in any category",
          all("B099" not in hits(line, c) for c in ("drug", "food", "")))
    fs = copylint.banned(rows(), line, "drug")
    b004 = [f["term"] for f in fs if f["rule"] == "B004"]
    check("a regex finding carries the text it matched", b004 == ["根治"],
          b004)
    two = copylint.banned(rows(), [("a", "保证根治，根治！保证有效")], "drug")
    check("the same match twice in one text is one finding",
          sorted(f["term"] for f in two) == ["保证有效", "根治"],
          [f["term"] for f in two])
    f = next(f for f in fs if f["rule"] == "B001")
    check("a finding is {severity, rule, where, term, confirmed, law, "
          "message}", set(f) == {"severity", "rule", "where", "term",
                                 "confirmed", "law", "message"}, f)
    check("…with the matched text, where, law and severity",
          (f["term"], f["where"], f["law"], f["severity"])
          == ("最有效", "scene 1", "law 9", "error"), f)
    check("a warn row's severity is kept, an empty match is literal",
          [x["severity"] for x in fs if x["rule"] == "B008"] == ["warn"])
    check("a literal term is not a regex", not copylint.banned(
        [{"id": "L1", "category": "all", "term": "a.c", "match": "literal",
          "severity": "error", "status": "confirmed"}], [("x", "abc")], ""))
    check("each text is linted separately, with its own where",
          [x["where"] for x in copylint.banned(
              rows(), [("s1", "第一"), ("s2", "fine"), ("s3", "第一")], "")]
          == ["s1", "s3"])
    check("clean copy has no findings",
          copylint.banned(rows(), [("s", "每天按说明书服用")], "drug") == [])


def test_confirmed_flag() -> None:
    print("[2] a draft rule says so")
    fs = {f["rule"]: f for f in copylint.banned(
        rows(), [("s", "最好的第一")], "drug")}
    d, c = fs["B001"], fs["B002"]
    check("a pending rule fires, confirmed False, *_draft code",
          d["confirmed"] is False
          and d["message"].code == "copylint_banned_term_draft", d)
    check("its text says it is not confirmed and not legal advice",
          "not confirmed" in d["message"] and "not legal advice"
          in d["message"], d["message"])
    check("a confirmed rule: confirmed True, copylint_banned_term",
          c["confirmed"] is True
          and c["message"].code == "copylint_banned_term", c)
    check("the message's params carry where, term, rule, note",
          c["message"].params == {"where": "s", "term": "第一",
                                  "rule": "B002", "note": "absolute claim"},
          c["message"].params)
    r = {"id": "N1", "category": "all", "term": "x", "severity": "error"}
    check("an empty status is pending (a draft)",
          copylint.banned([r], [("s", "x")], "")[0]["confirmed"] is False)


def test_owner_tsv() -> None:
    print("[3] rows from an owner TSV")
    rs = rows()
    check("read_tsv rows lint as dicts", len(rs) == 6
          and hits([("s", "第一")], "", rs) == {"B002"})


def test_broken_rules() -> None:
    print("[4] a broken rule is refused, never skipped")
    base = {"id": "R1", "category": "food", "term": "x", "match": "literal",
            "severity": "error", "status": "pending"}
    cases = [("a bad regex", {"term": "(", "match": "regex"},
              "copylint_rule_regex_invalid"),
             ("match outside literal|regex", {"match": "glob"},
              "copylint_rule_field_invalid"),
             ("severity outside error|warn", {"severity": "info"},
              "copylint_rule_field_invalid"),
             ("status outside its set", {"status": "maybe"},
              "copylint_rule_field_invalid"),
             ("no id", {"id": ""}, "copylint_rule_field_invalid"),
             ("no term", {"term": ""}, "copylint_rule_field_invalid")]
    for label, change, code in cases:
        e = raises(lambda: copylint.banned([{**base, **change}],
                                           [("s", "x")], "drug"),
                   HarnessError)
        check(f"{label}: refused, even out of scope ({code})",
              e is not None and e.message.code == code,
              e and getattr(e.message, "code", e))
    e = raises(lambda: copylint.facts(
        {"k": {"pattern": "(\\d)", "status": "pending"}}, [("s", "1")]),
        HarnessError)
    check("a fact with a pattern and no value is refused",
          e is not None and e.message.code == "copylint_rule_field_invalid"
          and e.message.params["rule"] == "fact:k",
          e and getattr(e.message, "params", e))
    e = raises(lambda: copylint.facts(
        {"k": {"value": "1", "pattern": "([", "status": "pending"}},
        [("s", "1")]), HarnessError)
    check("a fact with a bad pattern is refused",
          e is not None and e.message.code == "copylint_rule_regex_invalid")


def test_numerals() -> None:
    print("[5] CJK numerals normalised")
    for raw, want in [("一", "1"), ("两", "2"), ("兩", "2"), ("三", "3"), ("十", "10"),
                      ("十二", "12"), ("二十", "20"), ("二十三", "23"),
                      ("一百零五", "105"), ("一〇二", "102"),
                      ("３", "3"), (" 3 ", "3"), ("每天三次", "每天3次"),
                      ("兩年保固", "2年保固"), ("十年保固", "10年保固")]:
        check(f"{raw!r} -> {want!r}", copylint.norm(raw) == want,
              copylint.norm(raw))
    dose = {"d": {"value": "3", "status": "confirmed",
                  "pattern": r"每天\s*([0-9一二两三四五六七八九十]+)\s*次"}}
    for said in ("每天3次", "每天三次", "每天 ３ 次"):
        check(f"{said} says 3", copylint.facts(dose, [("s", said)]) == [])
    check("3.0 == 3", copylint.facts(
        {"d": {**dose["d"], "value": "3.0"}}, [("s", "每天3次")]) == [])
    check("a value given as a number compares too", copylint.facts(
        {"d": {**dose["d"], "value": 3}}, [("s", "每天三次")]) == [])
    check("十二 is not 1 then 2", len(copylint.facts(
        {"d": {**dose["d"], "value": "102"}}, [("s", "每天十二次")])) == 1)


REFERENCE = [
    ("scene 12 line", "每天3次，精华层层渗透滋养，补气助阳，益精生血。"),
    ("scene 12 caption", "每天3次"),
    ("scene 20 line", "就记住每天一次，里面内含多种生血精华成分，会层层滋养。"),
]
DOSAGE = {"dosage_per_day": {
    "value": "3", "status": "pending",
    "pattern": r"每天\s*([0-9一二两三四五六])\s*次"}}


def test_dosage_contradiction() -> None:
    print("[6] the dosage said two ways")
    check("the literal-term list alone sees nothing in the reference ad",
          copylint.banned(rows(), REFERENCE, "drug") == [],
          copylint.banned(rows(), REFERENCE, "drug"))
    fs = copylint.facts(DOSAGE, REFERENCE)
    check("the fact check finds exactly the one wrong mention",
          [(f["rule"], f["where"], f["term"]) for f in fs]
          == [("fact:dosage_per_day", "scene 20 line", "每天一次")], fs)
    f = fs[0]
    check("it is an error, from a pending fact: confirmed False, draft code",
          f["severity"] == "error" and f["confirmed"] is False
          and f["message"].code == "copylint_fact_mismatch_draft", f)
    check("its params say what was said and what the fact is",
          f["message"].params == {"where": "scene 20 line", "said": "每天一次",
                                  "fact": "dosage_per_day", "value": "3"},
          f["message"].params)
    conf = {"dosage_per_day": {**DOSAGE["dosage_per_day"],
                               "status": "confirmed", "law_ref": "label"}}
    f = copylint.facts(conf, REFERENCE)[0]
    check("from a confirmed fact: confirmed True, copylint_fact_mismatch",
          f["confirmed"] is True and f["law"] == "label"
          and f["message"].code == "copylint_fact_mismatch", f)
    fixed = [(w, t.replace("每天一次", "每天三次")) for w, t in REFERENCE]
    check("said as 每天三次 it agrees", copylint.facts(DOSAGE, fixed) == [])
    check("a fact with no pattern is skipped, a retired one never fires",
          copylint.facts({"pack": {"value": "16", "status": "pending"},
                          "d": {**DOSAGE["dosage_per_day"],
                                "status": "retired"}}, REFERENCE) == [])
    check("a pattern with no group compares the whole match",
          len(copylint.facts({"k": {"value": "3次", "pattern": "[0-9一]次",
                                    "status": "confirmed"}}, REFERENCE)) == 1)
    check("a fact's own severity is kept", copylint.facts(
        {"d": {**DOSAGE["dosage_per_day"], "severity": "warn"}},
        REFERENCE)[0]["severity"] == "warn")


def test_closure() -> None:
    print("[7] copylint.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "copylint.tsv"}
    check("the fragment holds copylint_* codes only",
          own and all(c.startswith("copylint_") for c in own), sorted(own))
    probs = messages.check_registry_closed(_shop.KIT, ["copylint.py"], own,
                                           strict_kit=True)
    check("every msg() in copylint.py is literal, registered with exact "
          "params, and every copylint.tsv code is emitted", probs == [],
          probs)


def main() -> int:
    _shop.use()
    for fn in (test_banned, test_confirmed_flag, test_owner_tsv,
               test_broken_rules, test_numerals, test_dosage_contradiction,
               test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
