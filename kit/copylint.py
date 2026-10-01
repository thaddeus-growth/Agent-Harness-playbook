"""Ad-copy compliance lint, before any money is spent on the copy: banned
terms from an owner's rule file, and product facts said the wrong way.

What it guards:

  * banned(rows, texts, category) -> [finding]. `rows` are the dicts of an
    owner TSV (kit.registry.read_tsv reads one) with columns
        id        the rule's id, unique
        category  "all" or "*" (every category), or one category name
        term      the literal text or the regex
        match     literal | regex (empty = literal)
        severity  error | warn
        note      why the term is banned, in the owner's words (optional)
        law_ref   the clause it rests on (optional)
        status    pending | confirmed | retired (empty = pending)
    `texts` is [(where, text)]: every line the ad says or shows, with a
    label saying where it is. A row fires when its category is "all",
    "*" or `category`; a retired row never fires. A literal term fires
    once per text; a regex fires once per distinct match in a text.
  * facts(facts, texts) -> [finding]. `facts` is {key: {value, pattern,
    status, severity?, law_ref?}}; `pattern` is a regex whose group 1 is
    the value the copy says (the whole match when it has no group). Every
    mention must say the fact's value; a fact with no pattern is not
    lintable here and a retired one never fires. Values are compared
    after normalising: NFKC (full-width digits), CJK numerals as digits
    (一 -> 1, 两 / 兩 -> 2, 十二 -> 12, 二十 -> 20, 一〇二 -> 102), and two
    numbers are equal when their values are (3 == 3.0).
  * A finding is {severity, rule, where, term, confirmed, law, message}.
    A rule whose status is not `confirmed` still fires, but the finding
    says so: confirmed False, and its message is the *_draft code, which
    says the rule is a draft the owner has not confirmed. A draft rule is
    never read as legal advice.
  * A broken rule is refused, never skipped: a bad regex, a match,
    severity or status outside its set, a missing id or term, a fact with
    a pattern and no value (HarnessError, coded).

Paid for: the client's own reference ad said its dosage two ways (每天3次
in one line, 每天一次 in another); the literal banned-term list had no way
to see it. The fact check did.

Test: kit/tests/test_copylint.py.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from kit.contract import HarnessError
from kit.messages import msg

MATCHES = ("literal", "regex")
SEVERITIES = ("error", "warn")
STATUSES = ("pending", "confirmed", "retired")
ANY_CATEGORY = ("all", "*")

CJK_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
_CJK_RUN = re.compile("[" + "".join(CJK_DIGITS) + "".join(CJK_UNITS) + "]+")


def _cjk_int(run: str) -> str:
    if not any(c in CJK_UNITS for c in run):        # 一〇二: digit by digit
        return "".join(str(CJK_DIGITS[c]) for c in run)
    total = num = 0
    for c in run:
        if c in CJK_DIGITS:
            num = CJK_DIGITS[c]
        else:
            total += (num or 1) * CJK_UNITS[c]
            num = 0
    return str(total + num)


def norm(s: str) -> str:
    """The text a value is compared as: NFKC, CJK numerals as digits,
    whitespace stripped."""
    s = unicodedata.normalize("NFKC", str(s))
    return _CJK_RUN.sub(lambda m: _cjk_int(m.group(0)), s).strip()


def _same(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _field_invalid(rule: str, column: str, value, allowed: str) -> HarnessError:
    return HarnessError(msg(
        "copylint_rule_field_invalid",
        f"rule {rule}: {column} {value!r} is not allowed ({allowed}); fix "
        f"the rule file, nothing was linted",
        rule=rule, column=column, value=str(value), allowed=allowed))


def _compile(rule: str, pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as e:
        raise HarnessError(msg(
            "copylint_rule_regex_invalid",
            f"rule {rule}: {pattern!r} is not a valid regex ({e}); fix the "
            f"rule file, nothing was linted",
            rule=rule, pattern=pattern, error=str(e))) from None


def _status(rule: str, raw) -> str:
    status = (raw or "").strip() or "pending"
    if status not in STATUSES:
        raise _field_invalid(rule, "status", raw, " | ".join(STATUSES))
    return status


def _rules(rows: Iterable[dict], category: str) -> list[tuple]:
    """Every row checked (all of them, so a broken row is refused even
    when it is out of scope), then the live in-scope ones compiled."""
    live = []
    for r in rows:
        rid = (r.get("id") or "").strip()
        if not rid:
            raise _field_invalid("(no id)", "id", r.get("id") or "",
                                 "a non-empty id")
        term = r.get("term") or ""
        if not term:
            raise _field_invalid(rid, "term", term, "a non-empty term")
        match = (r.get("match") or "").strip() or "literal"
        if match not in MATCHES:
            raise _field_invalid(rid, "match", r.get("match"),
                                 " | ".join(MATCHES))
        sev = (r.get("severity") or "").strip()
        if sev not in SEVERITIES:
            raise _field_invalid(rid, "severity", sev, " | ".join(SEVERITIES))
        status = _status(rid, r.get("status"))
        pat = _compile(rid, term) if match == "regex" else None
        cat = (r.get("category") or "").strip()
        if status == "retired" or not (cat in ANY_CATEGORY
                                       or (category and cat == category)):
            continue
        live.append((rid, term, pat, sev, status == "confirmed",
                     (r.get("note") or "").strip(),
                     (r.get("law_ref") or "").strip()))
    return live


def _terms(term: str, pat: re.Pattern | None, text: str) -> list[str]:
    if pat is None:
        return [term] if term in text else []
    seen: list[str] = []
    for m in pat.finditer(text):
        if m.group(0) and m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def banned(rows: Iterable[dict], texts: Iterable[tuple[str, str]],
           category: str) -> list[dict]:
    """Each banned term the copy says, from the owner's rule rows, scoped
    to `category` (rows of category all / * apply everywhere)."""
    rules = _rules(rows, category)
    out = []
    for where, text in texts:
        for rid, term, pat, sev, confirmed, note, law in rules:
            for hit in _terms(term, pat, text or ""):
                if confirmed:
                    m = msg("copylint_banned_term",
                            f"{where}: {hit!r} is banned by rule {rid}"
                            + (f": {note}" if note else ""),
                            where=where, term=hit, rule=rid, note=note)
                else:
                    m = msg("copylint_banned_term_draft",
                            f"{where}: {hit!r} is banned by draft rule {rid} "
                            f"(not confirmed by the owner; not legal advice)"
                            + (f": {note}" if note else ""),
                            where=where, term=hit, rule=rid, note=note)
                out.append({"severity": sev, "rule": rid, "where": where,
                            "term": hit, "confirmed": confirmed, "law": law,
                            "message": m})
    return out


def facts(facts: dict, texts: Iterable[tuple[str, str]]) -> list[dict]:
    """Each mention of a fact that says something other than its value."""
    texts = list(texts)
    live = []
    for key, f in (facts or {}).items():
        f = f or {}
        rule = f"fact:{key}"
        if not f.get("pattern"):
            continue
        status = _status(rule, f.get("status"))
        sev = (f.get("severity") or "error").strip()
        if sev not in SEVERITIES:
            raise _field_invalid(rule, "severity", sev, " | ".join(SEVERITIES))
        if str(f.get("value") if f.get("value") is not None else "").strip() \
                == "":
            raise _field_invalid(rule, "value", "", "a value to compare with")
        pat = _compile(rule, f["pattern"])
        if status != "retired":
            live.append((key, rule, pat, str(f["value"]), sev,
                         status == "confirmed",
                         (f.get("law_ref") or "").strip()))
    out = []
    for where, text in texts:
        for key, rule, pat, value, sev, confirmed, law in live:
            for m in pat.finditer(text or ""):
                said = m.group(1) if m.groups() else m.group(0)
                if said is None or _same(said, value):
                    continue
                if confirmed:
                    note = msg("copylint_fact_mismatch",
                               f"{where}: says {m.group(0)!r} but {key} is "
                               f"{value!r}",
                               where=where, said=m.group(0), fact=key,
                               value=value)
                else:
                    note = msg("copylint_fact_mismatch_draft",
                               f"{where}: says {m.group(0)!r} but {key} is "
                               f"{value!r} (a value the owner has not "
                               f"confirmed yet)",
                               where=where, said=m.group(0), fact=key,
                               value=value)
                out.append({"severity": sev, "rule": rule, "where": where,
                            "term": m.group(0), "confirmed": confirmed,
                            "law": law, "message": note})
    return out
