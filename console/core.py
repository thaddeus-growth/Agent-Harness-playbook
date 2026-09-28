"""The console's one contract: the event log, the ask it carries, and who may
write what. Stdlib-only; imported by ask.py (the agent's side), serve.py (the
human's side) and their tests. Nothing here prints, serves or shells out.

The store is ONE append-only file, `<CONSOLE_DIR>/events.jsonl`, one JSON
event per line. Nothing is ever edited or deleted; every state below is a
fold over the events (`fold()`), so the file is its own history and its own
backup.

    agent writes (Store.post / withdraw / applied / say)   ask  withdraw  applied  say
    human writes (Store.answer / reopen / note)            answer  reopen  note

`ROLES` is that split, and the `append` that `Store._txn` hands out refuses an
event type outside the caller's role, so the agent has no code path that writes an answer. A human
event is signed (HMAC-SHA256 over the event, `sig`) with the console's
secret; whoever holds the secret can `verify()` it. Like the harness's own
human gate this guards against an agent answering by accident (editing the
file, running the wrong verb), not against a determined one: keep the secret
out of the agent's reach (env `CONSOLE_SECRET`) if you want the second.

An ask is what the agent puts in front of the human. `validate_ask()` is the
one definition of a clear ask (the playbook's owner-queue rules, enforced):
a title, why now, evidence, a recommendation, and what "no" means; at most
`MAX_OPEN` open at once. `check_value()` is the one definition of a valid
answer. Everything the agent may send is in `ASK_FIELDS`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

try:  # POSIX; the store is single-machine by design
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

MAX_OPEN = 10                                   # the owner's attention budget
STEPS = ("confirm", "approve", "choose", "provide")
INPUT_TYPES = ("text", "number", "date", "url")
ROLES = {"agent": ("ask", "withdraw", "applied", "say"),
         "human": ("answer", "reopen", "note")}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}\Z")
# a token that can never be parsed as a flag: names, verbs, users
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,79}\Z")
URL_RE = re.compile(r"^https?://[^\s<>\"']{1,300}\Z")

LIMITS = {"id": 64, "kind": 40, "group": 60, "title": 120, "why": 400,
          "if_no": 200, "because": 200, "effect": 200, "label": 40,
          "value": 120, "source": 120, "quote": 300, "caption": 80,
          "cell": 80, "option_label": 60, "option_note": 120, "unit": 12,
          "answer": 500, "comment": 500, "note": 1000, "say": 400,
          "where": 200, "reason": 200, "evidence": 12, "options": 8,
          "columns": 6, "rows": 30, "verb": 6, "expect": 2000}

# every failure a verb can return: {ok: false, code, message, params}. The
# console words them for the human by `err.<code>` in i18n.json.
CODES = {
    "no_dir": "CONSOLE_DIR is not set (and no --dir given); the console has no default folder.",
    "invalid_ask": "The ask is not clear enough to send; see errors.",
    "id_used": "That id already belongs to an answered or withdrawn ask; ids are never reused.",
    "over_budget": "Too many open asks; withdraw or answer some first.",
    "unknown_id": "No ask has that id.",
    "not_open": "That ask is not open.",
    "not_answered": "That ask has no answer to apply.",
    "already_applied": "That answer was already applied; ask a new question instead.",
    "changed": "The ask changed since the page was loaded; reload and answer again.",
    "bad_value": "That answer is not valid for this ask.",
    "forbidden_event": "This side may not write that kind of event.",
    "corrupt_log": "events.jsonl has a line that is not valid; nothing was written.",
    "gate_unavailable": "This answer needs the harness's gate, which the console was not started with.",
    "gate_refused": "The harness refused the answer.",
    "gate_changed": "The harness would write something other than what was shown; nothing was written.",
    "bad_signature": "Some human events are not signed by this console's secret.",
    "no_secret": "There is no secret to verify with: set CONSOLE_SECRET or point at the folder the console wrote.",
    "gate_unsure": "The harness gave no clear answer; it may or may not have been written. Ask the agent to check.",
    "server_error": "Something went wrong on our side; nothing was saved.",
    "not_recorded": "The harness was written, but the console could not record the answer; ask the agent to check.",
    "gate_timeout": "The harness did not answer in time; ask the agent to check whether it was saved.",
    "bad_request": "The request is not one this console understands.",
    "forbidden": "Not allowed (no valid page token, host or user).",
    "too_large": "The request is too large.",
}
# what one field of an ask can be wrong with
FIELD_CODES = ("required", "too_long", "bad_type", "bad_value", "unknown_field",
               "too_many", "not_allowed", "duplicate")


class Refused(Exception):
    """A verb turned down, nothing written. `.doc()` is its one JSON document."""

    def __init__(self, code: str, message: str | None = None, **params):
        assert code in CODES, code
        super().__init__(message or CODES[code])
        self.code, self.params = code, params

    def doc(self) -> dict:
        return {"ok": False, "code": self.code, "message": str(self),
                "params": self.params}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def subject_hash(ask: dict) -> str:
    """What an answer is bound to: the ask exactly as it was shown."""
    return hashlib.sha256(canon(ask).encode()).hexdigest()[:32]


# ---------------------------------------------------------------- the ask --

# field -> (required for which steps or "all"/None, allowed steps or "all")
ASK_FIELDS = {
    "id":        ("all", "all"),
    "step":      ("all", "all"),
    "kind":      (None, "all"),        # what it is about: word, merge, value…
    "group":     (None, "all"),        # the section it sits in
    "title":     ("all", "all"),
    "why":       ("all", "all"),
    "evidence":  ("all", "all"),
    "options":   (("choose",), ("choose",)),
    "recommend": (("confirm", "approve", "choose"), "all"),
    "if_no":     ("all", "all"),
    "effect":    (("approve",), ("approve",)),
    "input":     (None, ("provide",)),
    "gate":      (None, "all"),
}


# what may not reach the owner's screen: controls, lone surrogates (they cannot
# be encoded), and the bidi overrides that make text read as something else
_DROP = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\u202a-\u202e\u2066-\u2069]")


def _clean(v: str, multiline: bool) -> str:
    """Tabs and newlines separate words (a newline stays in multiline text),
    every other unsafe character goes."""
    v = v.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    if not multiline:
        v = v.replace("\n", " ")
    return _DROP.sub("", v).strip()


def _text(errors, field, v, limit, *, multiline=False, required=True):
    """A trimmed string within `limit`, else None with the reason in errors."""
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            errors.append({"field": field, "code": "required",
                           "message": f"{field} is required"})
        return None
    if not isinstance(v, str):
        errors.append({"field": field, "code": "bad_type",
                       "message": f"{field} must be text"})
        return None
    v = _clean(v, multiline)
    if not v:
        if required:
            errors.append({"field": field, "code": "required",
                           "message": f"{field} is required"})
        return None
    if len(v) > limit:
        errors.append({"field": field, "code": "too_long",
                       "message": f"{field} is {len(v)} characters; at most {limit}"})
        return None
    return v


def _unknown(errors, field, d, allowed):
    for k in d:
        if k not in allowed:
            errors.append({"field": f"{field}.{k}" if field else k,
                           "code": "unknown_field",
                           "message": f"unknown field {k!r}; allowed: {sorted(allowed)}"})


def _url(errors, field, v):
    if v is None:
        return None
    if not isinstance(v, str) or not URL_RE.match(v.strip()) or _DROP.search(v):
        errors.append({"field": field, "code": "bad_value",
                       "message": f"{field} must be an http(s) link"})
        return None
    return v.strip()


def _evidence(errors, items):
    out = []
    if not isinstance(items, list) or not items:
        errors.append({"field": "evidence", "code": "required",
                       "message": "evidence needs at least one item (a fact, a quote or a table)"})
        return out
    if len(items) > LIMITS["evidence"]:
        errors.append({"field": "evidence", "code": "too_many",
                       "message": f"at most {LIMITS['evidence']} evidence items"})
        return out
    for i, it in enumerate(items):
        f = f"evidence[{i}]"
        if not isinstance(it, dict):
            errors.append({"field": f, "code": "bad_type",
                           "message": "an evidence item is an object"})
            continue
        if "table" in it:
            _unknown(errors, f, it, {"table"})
            tb = it["table"]
            if not isinstance(tb, dict):
                errors.append({"field": f + ".table", "code": "bad_type",
                               "message": "table is an object {caption, columns, rows}"})
                continue
            _unknown(errors, f + ".table", tb, {"caption", "columns", "rows"})
            cols, rows = tb.get("columns"), tb.get("rows")
            if not (isinstance(cols, list) and 1 <= len(cols) <= LIMITS["columns"]
                    and all(isinstance(c, str) and c.strip() for c in cols)):
                errors.append({"field": f + ".table.columns", "code": "bad_value",
                               "message": f"columns: 1 to {LIMITS['columns']} names"})
                continue
            if not (isinstance(rows, list) and 1 <= len(rows) <= LIMITS["rows"]
                    and all(isinstance(r, list) and len(r) == len(cols) for r in rows)):
                errors.append({"field": f + ".table.rows", "code": "bad_value",
                               "message": f"rows: 1 to {LIMITS['rows']}, each as long as columns"})
                continue
            names = [_text(errors, f + ".table.columns", c, LIMITS["cell"])
                     for c in cols]
            if None in names:
                continue
            cap = _text(errors, f + ".table.caption", tb.get("caption"),
                        LIMITS["caption"], required=False)
            cells = [[_text(errors, f + ".table.rows", "" if c is None else str(c),
                            LIMITS["cell"], required=False) or "" for c in r]
                     for r in rows]
            out.append({"table": {"caption": cap or "",
                                  "columns": names,
                                  "rows": cells}})
        elif "quote" in it:
            _unknown(errors, f, it, {"quote", "source", "url"})
            q = _text(errors, f + ".quote", it["quote"], LIMITS["quote"],
                      multiline=True)
            item = {"quote": q}
            s = _text(errors, f + ".source", it.get("source"),
                      LIMITS["source"], required=False)
            u = _url(errors, f + ".url", it.get("url"))
            if s:
                item["source"] = s
            if u:
                item["url"] = u
            out.append(item)
        else:
            _unknown(errors, f, it, {"label", "value", "source", "url"})
            lab = _text(errors, f + ".label", it.get("label"), LIMITS["label"])
            raw = it.get("value")
            val = _text(errors, f + ".value",
                        None if raw is None else str(raw), LIMITS["value"])
            item = {"label": lab, "value": val}
            s = _text(errors, f + ".source", it.get("source"),
                      LIMITS["source"], required=False)
            u = _url(errors, f + ".url", it.get("url"))
            if s:
                item["source"] = s
            if u:
                item["url"] = u
            out.append(item)
    return out


def _has_empty(v) -> bool:
    """An empty {} or [] anywhere: it would name nothing, so nothing would be checked."""
    if isinstance(v, (dict, list)):
        return not v or any(_has_empty(x) for x in (v.values() if isinstance(v, dict) else v))
    return False


def _walk_ok(v, depth=0):
    if depth > 4:
        return False
    if isinstance(v, str):
        return not _DROP.search(v)             # keys and values reach the owner's screen
    if isinstance(v, dict):
        return all(isinstance(k, str) and _walk_ok(k, depth + 1) and _walk_ok(x, depth + 1)
                   for k, x in v.items())
    if isinstance(v, list):
        return all(_walk_ok(x, depth + 1) for x in v)
    return (isinstance(v, (int, bool)) or v is None
            or (isinstance(v, float) and math.isfinite(v)))


def _binds_value(x) -> bool:
    """Is "$value" a value somewhere in `expect`? Only values are substituted
    (`expect_for`); a key named "$value" binds nothing."""
    if isinstance(x, dict):
        return any(_binds_value(v) for v in x.values())
    if isinstance(x, list):
        return any(_binds_value(v) for v in x)
    return x == "$value"


def _gate(errors, g, step):
    if not isinstance(g, dict):
        errors.append({"field": "gate", "code": "bad_type",
                       "message": "gate is an object {verb, expect, value_arg?}"})
        return None
    _unknown(errors, "gate", g, {"verb", "expect", "value_arg"})
    verb, expect = g.get("verb"), g.get("expect")
    ok = True
    if not (isinstance(verb, list) and 1 <= len(verb) <= LIMITS["verb"]
            and all(isinstance(x, str) and TOKEN_RE.match(x) for x in verb)):
        errors.append({"field": "gate.verb", "code": "bad_value",
                       "message": "verb: 1 to 6 plain words (letters, digits, _ . : @ / -), none starting with '-'"})
        ok = False
    if not (isinstance(expect, dict) and expect and _walk_ok(expect) and not _has_empty(expect)
            and len(canon(expect)) <= LIMITS["expect"]):
        errors.append({"field": "gate.expect", "code": "required",
                       "message": "expect: the non-empty payload the harness must bind the code to "
                                  "(what the human is told will be written); use \"$value\" for the answer"})
        ok = False
    va = g.get("value_arg", False)
    if not isinstance(va, bool):
        errors.append({"field": "gate.value_arg", "code": "bad_type",
                       "message": "value_arg is true or false"})
        ok = False
    elif va and step in ("confirm", "approve"):
        errors.append({"field": "gate.value_arg", "code": "not_allowed",
                       "message": "a yes/no answer has no value to pass"})
        ok = False
    elif step in ("choose", "provide") and ok and not (
            va and _binds_value(expect)):
        errors.append({"field": "gate.value_arg", "code": "required",
                       "message": "a choose or provide gate must carry the answer: "
                                  "value_arg true and \"$value\" in expect"})
        ok = False
    return {"verb": list(verb), "expect": expect, "value_arg": va} if ok else None


def validate_ask(raw) -> tuple[dict | None, list[dict]]:
    """(the ask as stored, []) or (None, every problem at once). Unknown fields
    are errors: a typo must surface, not vanish."""
    errors: list[dict] = []
    if not isinstance(raw, dict):
        return None, [{"field": "", "code": "bad_type",
                       "message": "an ask is a JSON object"}]
    _unknown(errors, "", raw, ASK_FIELDS)
    step = raw.get("step")
    if step not in STEPS:
        errors.append({"field": "step", "code": "bad_value",
                       "message": f"step is one of {list(STEPS)}"})
        step = None
    for f, (_req, allowed) in ASK_FIELDS.items():
        if step and f in raw and allowed != "all" and step not in allowed:
            errors.append({"field": f, "code": "not_allowed",
                           "message": f"{f} is only for step {'/'.join(allowed)}"})
        req = ASK_FIELDS[f][0]
        if (step and req not in (None, "all") and step in req
                and raw.get(f) in (None, "", [], {})):
            errors.append({"field": f, "code": "required",
                           "message": f"{f} is required for step {step}"})
    ask: dict = {"step": step}
    i = _text(errors, "id", raw.get("id"), LIMITS["id"])
    if i is not None and not ID_RE.match(i):
        errors.append({"field": "id", "code": "bad_value",
                       "message": "id: lowercase letters, digits, . _ -, starting with a letter or digit"})
    ask["id"] = i
    ask["kind"] = _text(errors, "kind", raw.get("kind"), LIMITS["kind"],
                        required=False) or "general"
    ask["group"] = _text(errors, "group", raw.get("group"), LIMITS["group"],
                         required=False) or ""
    ask["title"] = _text(errors, "title", raw.get("title"), LIMITS["title"])
    ask["why"] = _text(errors, "why", raw.get("why"), LIMITS["why"],
                       multiline=True)
    ask["evidence"] = _evidence(errors, raw.get("evidence"))
    ask["if_no"] = _text(errors, "if_no", raw.get("if_no"), LIMITS["if_no"])
    if step == "approve":
        ask["effect"] = _text(errors, "effect", raw.get("effect"),
                              LIMITS["effect"])
    if step == "choose":
        ask["options"] = _options(errors, raw.get("options"))
    if step == "provide":
        ask["input"] = _input(errors, raw.get("input"))
    if "recommend" in raw or step in ("confirm", "approve", "choose"):
        ask["recommend"] = _recommend(errors, raw.get("recommend"), ask, step)
    if "gate" in raw and raw["gate"] is not None:
        g = _gate(errors, raw["gate"], step)
        if g:
            ask["gate"] = g
    if errors:
        seen: set = set()
        return None, [e for e in errors if (e["field"], e["code"]) not in seen and not seen.add((e["field"], e["code"]))]
    return {k: v for k, v in ask.items() if v is not None}, []


def _options(errors, opts):
    out = []
    if opts in (None, "", [], {}):
        return out                 # required-ness was reported above
    if not (isinstance(opts, list) and 2 <= len(opts) <= LIMITS["options"]):
        errors.append({"field": "options", "code": "bad_value",
                       "message": f"options: 2 to {LIMITS['options']} choices"})
        return out
    seen = set()
    for i, o in enumerate(opts):
        f = f"options[{i}]"
        if not isinstance(o, dict):
            errors.append({"field": f, "code": "bad_type",
                           "message": "an option is {value, label, note?}"})
            continue
        _unknown(errors, f, o, {"value", "label", "note"})
        v = _text(errors, f + ".value", o.get("value"), LIMITS["value"])
        lab = _text(errors, f + ".label", o.get("label"), LIMITS["option_label"])
        note = _text(errors, f + ".note", o.get("note"), LIMITS["option_note"],
                     required=False)
        if v in seen:
            errors.append({"field": f + ".value", "code": "duplicate",
                           "message": f"option value {v!r} appears twice"})
        seen.add(v)
        item = {"value": v, "label": lab}
        if note:
            item["note"] = note
        out.append(item)
    return out


def _input(errors, spec):
    if spec is None:
        return {"type": "text"}
    if not isinstance(spec, dict):
        errors.append({"field": "input", "code": "bad_type",
                       "message": "input is {type, min?, max?, unit?}"})
        return {"type": "text"}
    _unknown(errors, "input", spec, {"type", "min", "max", "unit"})
    t = spec.get("type", "text")
    if t not in INPUT_TYPES:
        errors.append({"field": "input.type", "code": "bad_value",
                       "message": f"input.type is one of {list(INPUT_TYPES)}"})
        t = "text"
    out = {"type": t}
    for k in ("min", "max"):
        if k in spec:
            if t != "number":
                errors.append({"field": f"input.{k}", "code": "not_allowed",
                               "message": f"{k} is only for type number"})
            elif not _num(spec[k]):
                errors.append({"field": f"input.{k}", "code": "bad_type",
                               "message": f"{k} is a number"})
            else:
                out[k] = spec[k]
    if "min" in out and "max" in out and out["min"] > out["max"]:
        errors.append({"field": "input.max", "code": "bad_value",
                       "message": "max is below min: no answer would fit"})
    if "unit" in spec:
        u = _text(errors, "input.unit", spec["unit"], LIMITS["unit"],
                  required=False)
        if u:
            out["unit"] = u
    return out


def _num(x) -> bool:
    try:
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))
    except OverflowError:                        # an int too big for a float
        return False


def _recommend(errors, rec, ask, step):
    if rec is None:
        return None            # required-ness was reported above
    if not isinstance(rec, dict):
        errors.append({"field": "recommend", "code": "bad_type",
                       "message": "recommend is {value, because}"})
        return None
    _unknown(errors, "recommend", rec, {"value", "because"})
    raw = rec.get("value")
    v = _text(errors, "recommend.value", None if raw is None else str(raw),
              LIMITS["answer"])
    because = _text(errors, "recommend.because", rec.get("because"),
                    LIMITS["because"])
    if v is not None and step and not (step == "choose" and not ask.get("options")):
        probe = {"step": step, "options": ask.get("options") or [],
                 "input": ask.get("input") or {"type": "text"}}
        norm, err = check_value(probe, v)
        if err:
            errors.append({"field": "recommend.value", "code": "bad_value",
                           "message": "the recommendation is not a valid answer: " + err["message"]})
        else:
            v = norm
    return {"value": v, "because": because}


# ------------------------------------------------------------- the answer --

_NUMBER_RE = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?\Z")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def check_value(ask: dict, value) -> tuple[str | None, dict | None]:
    """(normalized answer, None) or (None, {code, reason, message}). The one
    definition of a valid answer, used before anything is written or run."""
    def bad(reason, message):
        return None, {"code": "bad_value", "reason": reason, "message": message}
    v = "" if value is None else str(value).strip()
    step = ask["step"]
    if step in ("confirm", "approve"):
        if v not in ("yes", "no"):
            return bad("yes_no", "answer yes or no")
        return v, None
    if step == "choose":
        if v not in {o["value"] for o in ask.get("options", [])}:
            return bad("choice", "pick one of the listed options")
        return v, None
    if not v:
        return bad("empty", "an answer is required")
    if len(v) > LIMITS["answer"]:
        return bad("too_long", f"at most {LIMITS['answer']} characters")
    if _DROP.search(v):
        return bad("control", "control characters are not allowed")
    spec = ask.get("input") or {"type": "text"}
    t = spec.get("type", "text")
    if t == "number":
        if not _NUMBER_RE.match(v):
            return bad("number", "a number is required")
        if not math.isfinite(float(v)):
            return bad("number", "a number is required")
        x = Decimal(v)                 # the bounds are compared exactly, not through a float
        if ("min" in spec and x < Decimal(str(spec["min"]))) or ("max" in spec and x > Decimal(str(spec["max"]))):
            return bad("range", "outside the allowed range")
    elif t == "date":
        try:
            if not _DATE_RE.match(v):
                raise ValueError(v)
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return bad("date", "a date as YYYY-MM-DD is required")
    elif t == "url" and not URL_RE.match(v):
        return bad("url", "an http(s) link is required")
    return v, None


def gate_runs(ask: dict, value: str) -> bool:
    """Does this answer have to pass the harness's gate? A "no" never does:
    nothing is confirmed by refusing."""
    return bool(ask.get("gate")) and not (
        ask["step"] in ("confirm", "approve") and value == "no")


def expect_for(ask: dict, value: str) -> dict:
    """The payload the harness must bind its code to: `expect` with the
    answer put where it says "$value"."""
    def sub(x):
        if x == "$value":
            return value
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        if isinstance(x, list):
            return [sub(v) for v in x]
        return x
    return sub(ask["gate"]["expect"])


def matches(want, got, _top: bool = True) -> bool:
    """`want` is what the human was shown, `got` what a harness would write.
    At the top level `got` may say more (its envelope: verb, market, version);
    everything `want` names must match exactly: a nested object key for key, so
    an extra item in `got` is refused. Scalars are equal, numbers by value
    (10, 10.0 and "10" agree)."""
    if isinstance(want, dict):
        return isinstance(got, dict) and (_top or set(want) == set(got)) and all(
            k in got and matches(v, got[k], False) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(got, list) and len(want) == len(got) and all(
            matches(a, b, False) for a, b in zip(want, got))
    if isinstance(want, bool) or isinstance(got, bool):
        return want is got
    if want == got:
        return True
    x, y = _decimal(want), _decimal(got)
    return x is not None and y is not None and x == y


def _decimal(v):
    """A finite number exactly, else None (floats would merge long ids)."""
    if isinstance(v, (dict, list)) or v is None:
        return None
    if isinstance(v, str) and not _NUMBER_RE.match(v):   # not " 12 ", "1_2" or full-width digits
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


# ---------------------------------------------------------------- signing --

def sign(secret: bytes, event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "sig"}
    return hmac.new(secret, canon(body).encode("utf-8", "surrogatepass"),
                    hashlib.sha256).hexdigest()


def verify(secret: bytes | None, event: dict) -> bool | None:
    """True/False, or None when there is no secret to check with."""
    if secret is None:
        return None
    sig = event.get("sig")
    if not isinstance(sig, str):
        return False
    return hmac.compare_digest(sign(secret, event).encode(),
                               sig.encode("utf-8", "surrogatepass"))


# --------------------------------------------------------------- the fold --

def fold(events: list[dict]) -> dict:
    """The state every page and verb reads:

    asks      id -> {id, ask, hash, rev, status: open|answered|withdrawn,
                     opened_seq, opened_at, by, answer, applied, withdrawn,
                     history, revs}        `answer` is the current answer event
    order     ids by first appearance
    messages  say (agent) and note (human) events, in order
    problems  events that broke the rules (an answer to a withdrawn ask…),
              which the fold ignored: {seq, type, id, code}
    seq       the last event's seq (seq only grows: an event that does not
              raise it is a problem `bad_seq` and ignored)
    """
    st = {"asks": {}, "order": [], "messages": [], "problems": [], "seq": 0}

    def bad(e, code):
        st["problems"].append({"seq": e.get("seq"), "type": e.get("type"),
                               "id": e.get("id"), "code": code})
    for e in events:
        if e.get("seq", 0) <= st["seq"]:
            bad(e, "bad_seq")            # a copied line must not count twice
            continue
        st["seq"] = e["seq"]
        t, i = e.get("type"), e.get("id")
        if t == "ask":
            a = e["ask"]
            cur = st["asks"].get(a["id"])
            h = subject_hash(a)
            if cur is None:
                st["asks"][a["id"]] = {
                    "id": a["id"], "ask": a, "hash": h, "rev": 1,
                    "status": "open", "opened_seq": e["seq"],
                    "opened_at": e["at"], "by": e.get("by"), "answer": None,
                    "applied": None, "withdrawn": None, "history": [],
                    "revs": {h: a}}
                st["order"].append(a["id"])
            elif cur["status"] == "open":
                cur.update(ask=a, hash=h, rev=cur["rev"] + 1)
                cur["revs"][h] = a
            else:
                bad(e, "id_used")
        elif t in ("withdraw", "answer", "reopen", "applied"):
            cur = st["asks"].get(i)
            if cur is None:
                bad(e, "unknown_id")
            elif t == "withdraw" and cur["status"] == "open":
                cur.update(status="withdrawn", withdrawn=e)
            elif t == "answer" and cur["status"] == "open":
                cur["answer"] = e
                cur["history"].append(e)
                cur["status"] = "answered"
                cur["revs"].setdefault(e.get("subject"), cur["ask"])
            elif t == "reopen" and cur["status"] == "answered" and not cur["applied"]:
                cur.update(status="open", answer=None)
                cur["history"].append(e)
            elif t == "applied" and cur["status"] == "answered" and not cur["applied"]:
                cur["applied"] = e
            else:
                bad(e, "not_open")
        elif t in ("say", "note"):
            st["messages"].append(e)
        else:
            bad(e, "unknown_type")
    return st


def open_asks(st: dict) -> list[dict]:
    return [st["asks"][i] for i in st["order"] if st["asks"][i]["status"] == "open"]


def groups(st: dict) -> list[tuple[str, list[dict]]]:
    """Open asks by their group, groups in order of first appearance."""
    out: dict[str, list] = {}
    for a in open_asks(st):
        out.setdefault(a["ask"].get("group", ""), []).append(a)
    return list(out.items())


# -------------------------------------------------------------- the store --

def _no_constant(name):
    raise ValueError(name)                       # NaN, Infinity, -Infinity are not JSON


def _finite_float(s):
    x = float(s)
    if not math.isfinite(x):                     # 1e999
        raise ValueError(s)
    return x


def _well_formed(e) -> bool:
    """The shape `fold` and the pages read, not today's validate_ask rules
    (those may tighten while old logs must stay readable)."""
    if not (isinstance(e, dict) and isinstance(e.get("type"), str)
            and isinstance(e.get("seq"), int) and not isinstance(e["seq"], bool)
            and all(isinstance(e.get(k), (str, type(None))) for k in ("id", "subject"))):
        return False
    try:
        canon(e).encode()                      # what sign and subject_hash will do with it
    except (UnicodeEncodeError, RecursionError):
        return False
    if not all(isinstance(e.get(k), str) for k in ("at", "by")):
        return False
    t = e["type"]
    if t == "answer" and not (isinstance(e.get("value"), str)
                              and isinstance(e.get("gate"), (dict, type(None)))):
        return False
    if t in ("say", "note") and not isinstance(e.get("text"), str):
        return False
    if t == "reopen" and not (isinstance(e.get("answer_seq"), int) and not isinstance(e["answer_seq"], bool)):
        return False
    if t != "ask":
        return True
    a = e.get("ask")
    return (isinstance(e.get("at"), str) and isinstance(a, dict)
            and isinstance(a.get("id"), str) and a.get("step") in STEPS
            and all(isinstance(a.get(k), str) for k in ("title", "why", "if_no"))
            and isinstance(a.get("evidence"), list) and _ask_shape_ok(a))


def _ask_shape_ok(a: dict) -> bool:
    """What the pages and `check_value` index into, when present."""
    rec, opts = a.get("recommend"), a.get("options")
    return (isinstance(a.get("group", ""), str)
            and (rec is None or (isinstance(rec, dict) and isinstance(rec.get("value"), str)))
            and (opts is None if a["step"] != "choose" else
                 isinstance(opts, list) and all(isinstance(o, dict) and isinstance(o.get("value"), str)
                                                and isinstance(o.get("label"), str) for o in opts))
            and isinstance(a.get("input", {}), dict) and isinstance(a.get("gate", {}), dict))


class Store:
    """`events.jsonl` in one folder. The folder is declared, never guessed."""

    def __init__(self, folder: str | None):
        if not folder:
            raise Refused("no_dir")
        self.dir = os.path.abspath(folder)
        self.path = os.path.join(self.dir, "events.jsonl")

    # -- reading --
    def events(self) -> list[dict]:
        try:
            with open(self.path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return []
        return self._parse(data)[0]

    def state(self) -> dict:
        return fold(self.events())

    @staticmethod
    def _parse(data: bytes) -> tuple[list[dict], int]:
        """(events, byte length of the complete lines). A last line with no
        newline is a write that never finished: not an event."""
        good = data.rfind(b"\n") + 1
        out = []
        for n, line in enumerate(data[:good].split(b"\n")[:-1], 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line, parse_constant=_no_constant, parse_float=_finite_float)
                if not _well_formed(e):
                    raise ValueError(line)
            except ValueError:
                raise Refused("corrupt_log", f"events.jsonl line {n} is not a valid event; nothing was written.",
                              line=n) from None
            out.append(e)
        return out, good

    # -- the secret --
    def secret(self) -> bytes | None:
        env = os.environ.get("CONSOLE_SECRET", "")
        if env:
            if len(env) < 16:
                raise Refused("bad_request", "CONSOLE_SECRET must be at least 16 characters")
            return os.fsencode(env)
        try:
            with open(os.path.join(self.dir, "secret"), "rb") as f:
                s = f.read().strip()
            return s if len(s) >= 16 else None
        except OSError:
            return None

    def ensure_secret(self) -> bytes:
        """The console's start: use the env's or the folder's secret, else
        make one (mode 0600). Only the console (the human's side) calls this."""
        s = self.secret()
        if s:
            return s
        os.makedirs(self.dir, exist_ok=True)
        s = secrets.token_hex(32).encode()
        path = os.path.join(self.dir, "secret")
        tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(s + b"\n")
        try:
            os.link(tmp, path)         # atomic: exactly one racer creates it
        except FileExistsError:
            won = self.secret()
            if won:
                return won
            os.replace(tmp, path)      # a too-short leftover: replace it
            return s
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return s

    # -- writing --
    @contextmanager
    def _txn(self, write=True):
        """The file locked; yields (state, append). A half-written last line
        from a crashed writer is cut off first. `append(role, type, **f)`
        adds one event (seq, at, by set here), signed if human, and refuses
        a type outside the role."""
        if not write:                  # a dry run: read, never create or repair
            events = self.events()
            yield {"st": fold(events), "seq": max((e["seq"] for e in events), default=0)}, None
            return
        os.makedirs(self.dir, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            data = b""
            while True:
                chunk = os.pread(fd, 1 << 20, len(data))
                if not chunk:
                    break
                data += chunk
            events, good = self._parse(data)
            if good != len(data):
                os.ftruncate(fd, good)
            box = {"st": fold(events),
                   "seq": max((e["seq"] for e in events), default=0)}

            def append(role, type, by, **fields):
                if type not in ROLES[role]:
                    raise Refused("forbidden_event", f"{role} may not write {type!r}")
                box["seq"] += 1
                ev = {"seq": box["seq"], "at": now(), "by": by, "type": type,
                      **{k: v for k, v in fields.items() if v is not None}}
                if role == "human":
                    s = self.secret()
                    if s is None:
                        s = self.ensure_secret()
                    ev["sig"] = sign(s, ev)
                data = memoryview((json.dumps(ev, ensure_ascii=False) + "\n").encode())
                while data:            # a short write is not a saved answer
                    data = data[os.write(fd, data):]
                os.fsync(fd)
                box["st"] = fold(events + [ev])
                events.append(ev)
                return ev
            yield box, append
        finally:
            os.close(fd)

    # ---- the agent's side ----
    def post(self, name: str, asks: list, *, max_open: int = MAX_OPEN,
             dry_run: bool = False) -> dict:
        by = self._by("agent", name)
        max_open = min(max_open, MAX_OPEN)
        clean, errors, seen = [], [], set()
        for n, raw in enumerate(asks):
            ask, errs = validate_ask(raw)
            label = raw["id"] if isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"] else f"#{n + 1}"
            errors += [{**e, "ask": label} for e in errs]
            if ask:
                if ask["id"] in seen:
                    errors.append({"ask": label, "field": "id", "code": "duplicate",
                                   "message": "the same id twice in one batch"})
                seen.add(ask["id"])
                clean.append(ask)
        if errors:
            raise Refused("invalid_ask", errors=errors)
        with self._txn(write=not dry_run) as (box, append):
            st = box["st"]
            new, updated, same = [], [], []
            for a in clean:
                cur = st["asks"].get(a["id"])
                if cur is None:
                    new.append(a)
                elif cur["status"] != "open":
                    raise Refused("id_used", id=a["id"], status=cur["status"])
                elif cur["hash"] == subject_hash(a):
                    same.append(a)
                else:
                    updated.append(a)
            opened = [x["id"] for x in open_asks(st)]
            if new and len(opened) + len(new) > max_open:
                raise Refused("over_budget", f"{len(opened)} asks are open and {len(new)} more would pass the limit of {max_open}; withdraw or merge some first.",
                              open=opened, adding=[a["id"] for a in new], max=max_open)
            if not dry_run:
                for a in new + updated:
                    append("agent", "ask", by, ask=a)
            return {"posted": [a["id"] for a in new],
                    "updated": [a["id"] for a in updated],
                    "unchanged": [a["id"] for a in same],
                    "open": len(opened) + len(new), "budget": max_open,
                    "seq": box["seq"], "dry_run": dry_run}

    def withdraw(self, name: str, ids: list[str], reason: str) -> dict:
        by = self._by("agent", name)
        reason = self._say(reason, "reason", "reason")
        ids = list(dict.fromkeys(ids))
        with self._txn() as (box, append):
            for i in ids:
                self._need(box["st"], i, "open")
            for i in ids:
                append("agent", "withdraw", by, id=i, reason=reason)
            return {"withdrawn": list(ids), "seq": box["seq"]}

    def applied(self, name: str, ids: list[str], where: str,
                seqs: dict | None = None) -> dict:
        """`seqs` {id: seq of the answer the agent read}: if the human has
        answered again since (reopen, new answer), that id is `changed`, so
        an answer is never marked applied that the agent never saw."""
        by = self._by("agent", name)
        where = self._say(where, "where", "where")
        ids = list(dict.fromkeys(ids))
        with self._txn() as (box, append):
            for i in ids:
                cur = self._need(box["st"], i, "answered")
                if cur["applied"]:
                    raise Refused("already_applied", id=i)
                if seqs and i in seqs and cur["answer"]["seq"] != seqs[i]:
                    raise Refused("changed", "The answer changed since you read it; run answers and apply the new one.",
                                  id=i)
            for i in ids:
                append("agent", "applied", by, id=i, where=where)
            return {"applied": list(ids), "seq": box["seq"]}

    def say(self, name: str, text: str) -> dict:
        by = self._by("agent", name)
        text = self._say(text, "say", "text")
        with self._txn() as (box, append):
            append("agent", "say", by, text=text)
            return {"seq": box["seq"]}

    # ---- the human's side ----
    def answer(self, user: str, id: str, value, *, shown: str, comment: str = "",
               gate: dict | None = None) -> dict:
        """Record one answer. `shown` is the hash of the ask as the page
        showed it. `gate` (the relay's result, already run and ok) means the
        harness was written, so the answer is recorded even if the ask was
        revised meanwhile, and says so."""
        by = self._by("human", user)
        comment = self._say(comment, "comment", "comment", required=False)
        with self._txn() as (box, append):
            cur = self._need(box["st"], id, "open")
            if shown not in cur["revs"]:
                raise Refused("changed", id=id)
            seen = cur["revs"][shown]            # what the owner was shown
            v, err = check_value(seen, value)
            if err:
                raise Refused("bad_value", err["message"], reason=err["reason"])
            ran = bool(gate) and gate.get("ok") is True
            if gate_runs(cur["ask"], v) and not ran:
                raise Refused("gate_unavailable", id=id)
            if cur["hash"] != shown and not (ran and gate_runs(seen, v)):
                raise Refused("changed", id=id)
            ev = append("human", "answer", by, id=id, value=v,
                        comment=comment or None, subject=shown,
                        suggested=(seen.get("recommend") or {}).get("value") == v,
                        gate=gate,
                        revised=(cur["hash"] != shown) or None)
            return ev

    def reopen(self, user: str, id: str, answer_seq: int) -> dict:
        by = self._by("human", user)
        with self._txn() as (box, append):
            cur = self._need(box["st"], id, "answered")
            if cur["applied"]:
                raise Refused("already_applied", id=id)
            if (cur["answer"].get("gate") or {}).get("ok"):
                raise Refused("already_applied", "The system was already updated by this answer; ask a new question instead.",
                              id=id)
            if cur["answer"]["seq"] != answer_seq:
                raise Refused("changed", id=id)
            return append("human", "reopen", by, id=id, answer_seq=cur["answer"]["seq"])

    def note(self, user: str, text: str) -> dict:
        by = self._by("human", user)
        text = self._say(text, "note", "text")
        with self._txn() as (box, append):
            return append("human", "note", by, text=text)

    # ---- helpers ----
    @staticmethod
    def _by(role: str, name: str) -> str:
        if not TOKEN_RE.match(name or ""):
            raise Refused("bad_request", f"{role} name must be a plain token")
        return f"agent:{name}" if role == "agent" else f"web:{name}"

    @staticmethod
    def _say(text, limit_key, field, required=True) -> str:
        errs: list = []
        v = _text(errs, field, text, LIMITS[limit_key], multiline=True,
                  required=required)
        if errs:
            raise Refused("bad_request", errs[0]["message"], field=field)
        return v or ""

    @staticmethod
    def _need(st: dict, id: str, status: str) -> dict:
        cur = st["asks"].get(id)
        if cur is None:
            raise Refused("unknown_id", id=id)
        if cur["status"] != status:
            raise Refused("not_answered" if status == "answered" else "not_open",
                          id=id, status=cur["status"])
        return cur


def check_comment(text) -> str:
    """The comment as it will be stored (cleaned, within its limit), or
    Refused: a server checks it before it runs a harness, never after."""
    return Store._say(text, "comment", "comment", required=False)
