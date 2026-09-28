#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Check meeting-intake files before any item reaches the owner.

    check_intake.py FILE...

Prints one JSON document, {ok, files, items, problems: [{iid, code, message,
file, path}]}, and exits 0 when there is no problem, 2 when there is one.
All files are checked together, because an iid is unique across all of them
and a contradiction can span two meetings.

What is checked (each rule is a test in build/tests/test_check_intake.py):
- The shape: templates/intake.schema.json, read from disk, so the schema is
  the one place the shape lives. Its x-code / x-missing name the problem code.
- Every item has a source: a meeting timestamp "YYYY-MM-DD hh:mm:ss" on a real
  date, or a document reference "doc:<name>".
- Every number has a quote and a unit, is digits or a range the client gave,
  and carries no rounded or estimated marker (~, about, approx., 大概...).
- A contradiction is a question: two numbers with one key and two values, or
  one word with two meanings, need a question whose `about` names both.
- iids are unique across files; `about` names only iids that exist.
- A question's suggestion is one of its options, and options differ.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "..", "templates", "intake.schema.json")
BUCKETS = {"goals": "g", "words": "w", "stories": "s", "numbers": "n", "questions": "q"}

# every problem code this checker can report, and what it means
CODES = {
    "bad_json": "The file cannot be read, or is not JSON.",
    "missing_field": "A required field is missing.",
    "unknown_field": "A field the schema does not know; a typo must surface, not vanish.",
    "bad_type": "A field has the wrong type.",
    "bad_value": "A field has a value the schema does not allow.",
    "too_long": "A field is longer than the console can show without cutting it.",
    "too_short": "A list has fewer items than it needs.",
    "too_many": "A list has more items than the console can show.",
    "bad_date": "A date is not a real YYYY-MM-DD date.",
    "bad_iid": "An iid is not the bucket's letter and a number (g1, w3, s2, n7, q4).",
    "no_consent": "The meeting has no consent line.",
    "no_source": "An item has no source; no source, no item.",
    "bad_source": "A source is neither 'YYYY-MM-DD hh:mm:ss' on a real date nor 'doc:<name>'.",
    "no_quote": "An item that is asked needs the client's exact words.",
    "no_unit": "A number has no unit.",
    "value_not_number": "A number's value is not digits or a range; the client's words go in the quote.",
    "estimate_marker": "A number carries a rounded or estimated marker; never estimate or round.",
    "bad_range": "A range runs backwards.",
    "duplicate_iid": "The same iid twice, in one file or across files.",
    "unknown_ref": "A question's `about` names an iid that does not exist.",
    "contradiction_not_asked": "Two items disagree and no question names both.",
    "bad_suggest": "A question's suggestion is not one of its options.",
    "duplicate_option": "A question lists the same option twice.",
}

# a rounded or estimated marker, in a number's value, unit or key
ESTIMATE = re.compile(
    r"~|≈|±|≈|\bapprox|\babout\b|\baround\b|\broughly\b|\bcirca\b|\bca\.|\best\b|\best\.|"
    r"\bestimat|\bround(?:ed|ing)?\b|\bguess|\bor so\b|-ish\b|"
    r"大概|大约|约|左右|将近|接近|差不多|估计|估算|预估|上下", re.I)
KEY_ESTIMATE = re.compile(r"(?:^|_)(?:est|estimate|estimated|approx|rounded|guess)(?:_|$)")
TIMESTAMP = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}) [0-9]{2}:[0-5][0-9]:[0-5][0-9]$")
RANGE = re.compile(r"^([0-9]+(?:\.[0-9]+)?) ?[-–] ?([0-9]+(?:\.[0-9]+)?)$")
KNOWN_KEYWORDS = {"$comment", "$id", "$ref", "$defs", "title", "description", "type",
                  "properties", "required", "additionalProperties", "items", "enum",
                  "pattern", "minLength", "maxLength", "minItems", "maxItems",
                  "x-code", "x-missing"}


# ------------------------------------------------------------- the schema --

def load_schema(path: str = SCHEMA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve(root: dict, sub: dict) -> dict:
    """A local "$ref": "#/$defs/x" followed; an x-code/x-missing next to the
    $ref wins over the target's."""
    seen = 0
    while "$ref" in sub:
        ref = sub["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        sub = {**target, **{k: v for k, v in sub.items() if k != "$ref"}}
        seen += 1
        if seen > 20:
            raise ValueError(f"$ref loop at {ref}")
    return sub


def _type_ok(v, t) -> bool:
    types = t if isinstance(t, list) else [t]
    for x in types:
        if x == "object" and isinstance(v, dict):
            return True
        if x == "array" and isinstance(v, list):
            return True
        if x == "string" and isinstance(v, str):
            return True
        if x == "boolean" and isinstance(v, bool):
            return True
        if x == "integer" and isinstance(v, int) and not isinstance(v, bool):
            return True
        if x == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return True
        if x == "null" and v is None:
            return True
    return False


def validate(root: dict, value, sub: dict | None = None, path: str = "") -> list[dict]:
    """Every way `value` breaks the schema: [{path, code, message}]. Only the
    keywords in KNOWN_KEYWORDS exist here, and a test checks the schema uses
    no other, so no rule in it can be silently skipped."""
    sub = _resolve(root, root if sub is None else sub)
    out: list[dict] = []
    code = sub.get("x-code")

    def bad(default, message):
        out.append({"path": path, "code": code or default, "message": message})

    if "type" in sub and not _type_ok(value, sub["type"]):
        bad("bad_type", f"{path or 'the file'} must be {sub['type'] if isinstance(sub['type'], str) else ' or '.join(sub['type'])}")
        return out
    if "enum" in sub and value not in sub["enum"]:
        bad("bad_value", f"{path} is one of {sub['enum']}, not {value!r}")
    if isinstance(value, str):
        if "minLength" in sub and len(value.strip()) < sub["minLength"]:
            out.append({"path": path, "code": sub.get("x-missing", "missing_field"),
                        "message": f"{path} is empty"})
        elif "maxLength" in sub and len(value) > sub["maxLength"]:
            out.append({"path": path, "code": "too_long",
                        "message": f"{path} is {len(value)} characters; at most {sub['maxLength']}"})
        if "pattern" in sub and value.strip() and not re.search(sub["pattern"], value):
            bad("bad_value", f"{path} does not have the right form: {value[:60]!r}")
    if isinstance(value, list):
        if "minItems" in sub and len(value) < sub["minItems"]:
            out.append({"path": path, "code": "too_short",
                        "message": f"{path} needs at least {sub['minItems']} items"})
        if "maxItems" in sub and len(value) > sub["maxItems"]:
            out.append({"path": path, "code": "too_many",
                        "message": f"{path} has {len(value)} items; at most {sub['maxItems']}"})
        if "items" in sub:
            for i, x in enumerate(value):
                out += validate(root, x, sub["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = sub.get("properties", {})
        for k in sub.get("required", []):
            if k not in value or value[k] is None:
                target = _resolve(root, props.get(k, {}))
                out.append({"path": f"{path}.{k}" if path else k,
                            "code": target.get("x-missing", "missing_field"),
                            "message": f"{k} is required{' in ' + path if path else ''}"})
        if sub.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    out.append({"path": f"{path}.{k}" if path else k, "code": "unknown_field",
                                "message": f"unknown field {k!r}; allowed: {sorted(props)}"})
        for k, s in props.items():
            if k in value and value[k] is not None:
                out += validate(root, value[k], s, f"{path}.{k}" if path else k)
    return out


def schema_keywords(node) -> set:
    """Every keyword the schema uses (the names under properties and $defs are
    names, not keywords)."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            found.add(k)
            if k in ("properties", "$defs"):
                for x in v.values():
                    found |= schema_keywords(x)
            elif k != "enum":
                found |= schema_keywords(v)
    elif isinstance(node, list):
        for x in node:
            found |= schema_keywords(x)
    return found


# ------------------------------------------------------------ the helpers --

def items(doc: dict):
    """(bucket, index, item) for every item of one intake document."""
    for bucket in BUCKETS:
        for i, it in enumerate(doc.get(bucket) or [] if isinstance(doc, dict) else []):
            if isinstance(it, dict):
                yield bucket, i, it


def source_refs(source: str) -> list[str]:
    return [r for r in (source or "").split("; ") if r]


def value_text(v) -> str:
    """A number's value as text: 45 -> '45', 4.5 -> '4.5', '20 – 30' -> '20–30'."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    m = RANGE.match(str(v).strip())
    if m:
        return f"{m.group(1)}–{m.group(2)}"
    return str(v).strip()


def value_range(v) -> tuple[str, str] | None:
    """(low, high) when the value is a range, else None."""
    if isinstance(v, str):
        m = RANGE.match(v.strip())
        if m:
            return m.group(1), m.group(2)
    return None


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _no_constant(name):
    raise ValueError(f"{name} is not a JSON number")


def _finite(text):
    x = float(text)
    if x - x != 0:
        raise ValueError(f"{text} is too large for a number")
    return x


def load(paths: list[str]) -> tuple[list[tuple[str, dict]], list[dict]]:
    """([(path, doc)], problems) for the files that read as JSON (NaN and
    Infinity are not JSON, and a number must never become one)."""
    docs, problems = [], []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f, parse_constant=_no_constant, parse_float=_finite)
        except (OSError, ValueError, RecursionError) as e:
            problems.append({"iid": None, "code": "bad_json", "file": p, "path": "",
                             "message": f"cannot read {os.path.basename(p)}: {e}"})
            continue
        docs.append((p, doc))
    return docs, problems


def index(docs: list[tuple[str, dict]]) -> dict:
    """iid -> {bucket, item, file, meeting}: the first place each iid appears."""
    out = {}
    for p, doc in docs:
        meeting = doc.get("meeting") if isinstance(doc, dict) else None
        for bucket, _i, it in items(doc):
            iid = it.get("iid")
            if isinstance(iid, str) and iid not in out:
                out[iid] = {"bucket": bucket, "item": it, "file": p,
                            "meeting": meeting if isinstance(meeting, dict) else {}}
    return out


# ------------------------------------------------------------- the checks --

def check(paths: list[str], schema: dict | None = None) -> dict:
    """The whole report for these files, as check_intake.py prints it."""
    schema = schema or load_schema()
    docs, problems = load(paths)
    seen: dict[str, str] = {}               # iid -> file of its first appearance
    count = 0

    def add(file, path, iid, code, message):
        problems.append({"iid": iid, "code": code, "message": message,
                         "file": file, "path": path})

    for p, doc in docs:
        name = os.path.basename(p)
        where = {}                           # path prefix -> iid, for the schema's problems
        for bucket, i, it in items(doc):
            iid = it.get("iid") if isinstance(it.get("iid"), str) else None
            where[f"{bucket}[{i}]"] = iid
        for e in validate(schema, doc):
            prefix = re.match(r"^([a-z]+\[[0-9]+\])", e["path"])
            iid = where.get(prefix.group(1)) if prefix else None
            add(p, e["path"], iid, e["code"], f"{name}: {e['message']}")
        if not isinstance(doc, dict):
            continue
        for bucket, i, it in items(doc):
            count += 1
            path = f"{bucket}[{i}]"
            iid = it.get("iid") if isinstance(it.get("iid"), str) else None
            if iid:
                if iid in seen:
                    other = os.path.basename(seen[iid])
                    add(p, path + ".iid", iid, "duplicate_iid",
                        f"{name}: {iid} is used again (first in {other}); an iid is never reused")
                else:
                    seen[iid] = p
            for ref in source_refs(it.get("source") if isinstance(it.get("source"), str) else ""):
                m = TIMESTAMP.match(ref)
                if m:
                    try:
                        datetime.strptime(m.group(1), "%Y-%m-%d")
                    except ValueError:
                        add(p, path + ".source", iid, "bad_source",
                            f"{name}: {m.group(1)} is not a real date")
            if bucket == "numbers":
                _check_number(add, p, name, path, iid, it)
            if bucket == "questions":
                _check_question(add, p, name, path, iid, it)
        meeting = doc.get("meeting")
        if isinstance(meeting, dict) and isinstance(meeting.get("date"), str):
            try:
                datetime.strptime(meeting["date"], "%Y-%m-%d")
            except ValueError:
                add(p, "meeting.date", None, "bad_date",
                    f"{name}: {meeting['date']} is not a real date")

    _check_refs_and_contradictions(add, docs)
    problems.sort(key=lambda x: (paths.index(x["file"]) if x["file"] in paths else 0,
                                 x["path"], x["code"]))
    return {"ok": not problems, "files": len(paths), "items": count, "problems": problems}


def _check_number(add, p, name, path, iid, it):
    v = it.get("value")
    for field, text in (("value", v if isinstance(v, str) else None),
                        ("unit", it.get("unit") if isinstance(it.get("unit"), str) else None)):
        if text and ESTIMATE.search(text):
            add(p, f"{path}.{field}", iid, "estimate_marker",
                f"{name}: {iid} {field} {text!r} is marked as rounded or estimated; "
                "write the number the client said and keep their words in the quote")
    key = it.get("key")
    if isinstance(key, str) and KEY_ESTIMATE.search(key):
        add(p, f"{path}.key", iid, "estimate_marker",
            f"{name}: {iid} key {key!r} names an estimate")
    r = value_range(v)
    if r and float(r[0]) > float(r[1]):
        add(p, f"{path}.value", iid, "bad_range", f"{name}: {iid} range {v!r} runs backwards")


def _check_question(add, p, name, path, iid, it):
    opts = it.get("options")
    if isinstance(opts, list):
        texts = [o for o in opts if isinstance(o, str)]
        if len({_norm(o) for o in texts}) != len(texts):
            add(p, f"{path}.options", iid, "duplicate_option",
                f"{name}: {iid} lists the same option twice")
        sug = it.get("suggest")
        if isinstance(sug, dict) and isinstance(sug.get("value"), str) and sug["value"] not in texts:
            add(p, f"{path}.suggest.value", iid, "bad_suggest",
                f"{name}: {iid} suggests {sug['value']!r}, which is not one of its options")


def _check_refs_and_contradictions(add, docs):
    idx = index(docs)
    covered: set[frozenset] = set()
    for p, doc in docs:
        for bucket, i, it in items(doc):
            if bucket != "questions" or not isinstance(it.get("about"), list):
                continue
            about = [a for a in it["about"] if isinstance(a, str)]
            for a in about:
                if a not in idx:
                    add(p, f"questions[{i}].about", it.get("iid"), "unknown_ref",
                        f"{os.path.basename(p)}: {it.get('iid')} is about {a}, which no intake file has")
                elif a == it.get("iid"):
                    add(p, f"questions[{i}].about", it.get("iid"), "unknown_ref",
                        f"{os.path.basename(p)}: {a} cannot be about itself")
            for x in about:
                for y in about:
                    if x < y:
                        covered.add(frozenset((x, y)))

    def clash(groups, what):
        for members in groups.values():
            for n, (a, fa, sa) in enumerate(members):
                for b, fb, sb in members[n + 1:]:
                    if sa != sb and frozenset((a, b)) not in covered:
                        add(fb, "", b, "contradiction_not_asked",
                            f"{os.path.basename(fb)}: {b} and {a} ({os.path.basename(fa)}) give "
                            f"{what}; list both in a question's `about`")

    numbers: dict[str, list] = {}
    words: dict[str, list] = {}
    for iid, x in idx.items():
        it = x["item"]
        if x["bucket"] == "numbers" and isinstance(it.get("key"), str):
            said = (value_text(it.get("value")), _norm(it.get("unit", "")))
            numbers.setdefault(it["key"], []).append((iid, x["file"], said))
        if x["bucket"] == "words" and isinstance(it.get("word"), str):
            words.setdefault(_norm(it["word"]), []).append(
                (iid, x["file"], _norm(it.get("meaning", ""))))
    clash(numbers, "two values for one key")
    clash(words, "two meanings for one word")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        doc = {"ok": argv[:1] in (["-h"], ["--help"]), "usage": "check_intake.py FILE...",
               "codes": CODES}
        if not doc["ok"]:
            doc.update(code="bad_request", message="name at least one intake file")
    else:
        doc = check(argv)
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return 0 if doc["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
