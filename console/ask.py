#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The agent's side of the console: a thin CLI over core.Store.

Every outcome is ONE JSON document on stdout, exit 0 when it is ok and 2 when
it is not: a refusal is `{ok: false, code, message, params}` (core.Refused),
and so is a bad argument (`bad_request`); no usage text, no traceback for a
mistake the caller can fix. Text is never the contract; the JSON is, and it
only ever gains keys.

The folder is declared with --dir or $CONSOLE_DIR, never guessed from the cwd;
only the verbs that read the log look for it (`schema` and --help need none).
Every ok document carries `dir`, the absolute folder it used (null when none is
declared, as for `schema`): a relative or mistyped path shows up there, instead
of an agent and a console quietly working in two folders.
This side writes only the agent's events (ask, withdraw, applied, say). It has
no verb that answers, reopens, notes or advises, and it never makes the
console's secret: it only reads it to check signatures (`verify`, `verified`);
with no secret `verify` is refused (`no_secret`), it does not pass.

    add [FILE|-] [--dry-run] [--max-open N]   post one ask or an array, all or nothing
    list [--status open|answered|withdrawn|all]
    answers [--since SEQ] [--all]             what the human said, ready to apply
    wait [--since SEQ] [--timeout SEC]        block until the human says something new
    applied ID[@SEQ]... --where TEXT          the answer was applied, and where
    withdraw ID... --reason TEXT
    say TEXT                                  a message on top of the human's inbox
    schema                                    fields, limits, one example per step
    verify                                    check every human event's signature
    digest [--format md|json]                 the decision record, to forward

Recommended flow: add -> answers -> apply -> applied -> wait --since <the seq
answers returned>. `wait` hands over what `answers` does, so the loop goes round
with the seq of its reply. Never add's seq: an answer that landed between
`answers` and `add` would sit unread until the timeout. Each answer row carries
`apply` ("ID@SEQ"): pass it to `applied` as it is, and an answer that changed
since you read it is refused (`changed`), never marked applied.

`answers` and `wait` list the asks reopened and the notes written after
`--since`; of the notes only the newest 20, and `notes_truncated` says when
there were more (a first look, with no --since, is a look at the newest).
They also list, in `advice`, every view the team gave after `--since` (team
review: someone who may not decide agrees or disagrees, with a reason), never
cut short: a disagreement is turned into a new ask, not missed. `list` counts
each ask's views as it stands. `digest` renders the decision record (asks,
evidence, suggestion, answer and who gave it, the views, where it was applied)
as Markdown in `text`, or as data with --format json: it is still one JSON
document, like every other verb.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
from core import Refused, Store  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
HUMAN = core.ROLES["human"]
POLL = 0.5                      # seconds between looks at the file in `wait`
FLOW = "add -> answers -> apply -> applied -> wait --since <the seq answers returned>"
NOTES = 20                      # the newest notes a report shows; a first look is not a replay
SEQ = re.compile(r"[0-9]{1,15}\Z")       # ASCII digits, few enough for int()

# what each ask field is for, in the words an agent needs; the rules
# (required for, allowed for, limits) come from core, never from here
ABOUT = {
    "id": "Short and stable: lowercase letters, digits, . _ -. Posting the same id again while the ask is open revises it; an answered or withdrawn id is never reused.",
    "step": "What the human does: confirm (yes or no), approve (yes or no to something that changes things), choose (one option), provide (type a value).",
    "kind": "What it is about, in a word (word, merge, value...). Free text.",
    "group": "The section of the page it sits in; asks with the same group are shown together.",
    "title": "The question, on one line.",
    "why": "Why it is asked now, in plain facts.",
    "evidence": "One or more of: {label, value, source?, url?}, {quote, source?, url?}, {table: {caption?, columns, rows}}.",
    "options": "The choices, {value, label, note?}; at least 2.",
    "recommend": "{value, because}: the answer you suggest and why. The value must be a valid answer.",
    "if_no": "What 'no' would change (for choose and provide: what happens if none of it fits).",
    "effect": "What happens when they approve, in plain words.",
    "input": "How a provided value is typed: {type: text|number|date|url, min?, max?, unit?}.",
    "gate": "{verb: [words], expect: {payload}, value_arg?}: run the harness's own human gate when this is answered; use \"$value\" in expect for the answer.",
}
ANSWER_VALUES = ("The value of an answer is always text: yes or no for confirm and approve, "
                 "an option's value for choose, what was typed for provide.")


class Stop(Exception):
    """Argument parsing ended in a document instead of a Namespace."""

    def __init__(self, doc):
        self.doc = doc


class Parser(argparse.ArgumentParser):
    """argparse that never prints: an error or --help becomes a document."""

    def error(self, message):
        raise Stop(Refused("bad_request", message).doc())

    def print_help(self, file=None):
        raise Stop({"ok": True, "help": self.format_help()})


def _number(kind, low):
    def conv(text):
        try:
            v = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
        if not math.isfinite(v) or v < low:
            raise argparse.ArgumentTypeError(f"must be a number of at least {low}")
        return v
    return conv


def _id_at_seq(token):
    """`ID` or `ID@SEQ` (the seq is the answer's, as `answers` gives it):
    (id, seq or None). The last '@' splits, and ids never hold one."""
    i, at, tail = token.rpartition("@")
    if not at:
        return token, None
    if not (i and SEQ.match(tail)):
        raise argparse.ArgumentTypeError(
            f"{token!r} is not ID or ID@SEQ (SEQ: the digits of the answer's seq)")
    return i, int(tail)


def _parser() -> Parser:
    p = Parser(prog="ask.py", description="The agent's side of the console. "
               "Every outcome is one JSON document on stdout.")
    p.add_argument("--dir", help="the console folder (default: $CONSOLE_DIR; never guessed)")
    p.add_argument("--as", dest="name", default="main",
                   help="who is asking, logged as agent:NAME (default: main)")
    sub = p.add_subparsers(dest="verb", required=True, metavar="VERB")

    def verb(name, fn, help):
        v = sub.add_parser(name, help=help, description=help)
        v.set_defaults(run=fn)
        return v
    v = verb("add", cmd_add, "post one ask or an array of asks, all or nothing")
    v.add_argument("file", nargs="?", default="-", metavar="FILE",
                   help="a JSON file, or - for stdin (the default)")
    v.add_argument("--dry-run", action="store_true", help="check and count, write nothing")
    v.add_argument("--max-open", type=_number(int, 1), default=core.MAX_OPEN, metavar="N",
                   help="lower the limit of open asks to N; it can only lower it, "
                        "never raise it above %(default)s (default %(default)s)")
    v = verb("list", cmd_list, "the asks and where each stands")
    v.add_argument("--status", choices=("open", "answered", "withdrawn", "all"), default="open")
    v = verb("answers", cmd_answers, "answers not yet applied, asks reopened and notes since SEQ")
    v.add_argument("--since", type=_number(int, 0), default=0, metavar="SEQ",
                   help="only reopened asks and notes after this seq (the newest "
                        f"{NOTES} notes at most)")
    v.add_argument("--all", action="store_true", help="also the answers already applied")
    v = verb("wait", cmd_wait, "block until a human answer, reopen, note or view after SEQ, then say what `answers` says")
    v.add_argument("--since", type=_number(int, 0), default=None, metavar="SEQ",
                   help="default: the seq when this command starts")
    v.add_argument("--timeout", type=_number(float, 0), default=900, metavar="SEC",
                   help="give up after this long, with timed_out true (default %(default)s)")
    v = verb("applied", cmd_applied, "record that answers were applied, and where")
    v.add_argument("ids", nargs="+", metavar="ID", type=_id_at_seq,
                   help="ID, or ID@SEQ as an answer's `apply` gives it: then an answer "
                        "that changed since you read it is refused")
    v.add_argument("--where", required=True, help="what changed and where")
    v = verb("withdraw", cmd_withdraw, "take open asks back")
    v.add_argument("ids", nargs="+", metavar="ID")
    v.add_argument("--reason", required=True)
    v = verb("say", cmd_say, "a message the human sees on top of the inbox")
    v.add_argument("text", metavar="TEXT")
    verb("schema", cmd_schema, "the fields of an ask, the limits and one example per step")
    verb("verify", cmd_verify, "check every human event's signature with the console's secret")
    v = verb("digest", cmd_digest, "the decision record: every ask, what was shown, the answer and who gave it, "
             "the team's views and where it was applied")
    v.add_argument("--format", choices=("md", "json"), default="md",
                   help="md (default): Markdown in `text`, the file a team lead forwards; json: the same record as data")
    return p


# ------------------------------------------------------------------ verbs --

def _folder(a):
    """The declared folder as an absolute path, None when there is none."""
    folder = a.dir or os.environ.get("CONSOLE_DIR")
    return os.path.abspath(folder) if folder else None


def _store(a) -> Store:
    """Made by the verbs that read the log, so `schema` needs no folder."""
    return Store(_folder(a))


def _no_constant(name):
    raise ValueError(f"{name} is not allowed")


def _finite(text):
    x = float(text)
    if not math.isfinite(x):
        raise ValueError(f"{text} is too large for a number")
    return x


def _read_asks(path: str) -> list:
    if path == "-" and sys.stdin.isatty():
        raise Refused("bad_request", "no ask given: name a FILE, or pipe the JSON in with -")
    try:
        if path == "-":
            data = sys.stdin.buffer.read()
        else:
            with open(path, "rb") as f:
                data = f.read()
    except OSError as e:
        raise Refused("bad_request", f"cannot read {path}: {e.strerror or e}") from None
    try:                        # NaN and Infinity are not JSON, and an answer must never echo one back
        doc = json.loads(data, parse_constant=_no_constant, parse_float=_finite)
    except ValueError as e:
        raise Refused("bad_request", f"not valid JSON: {e}") from None
    if doc == []:
        raise Refused("bad_request", "the file holds no asks")
    return doc if isinstance(doc, list) else [doc]


def cmd_add(a):
    store = _store(a)
    return {"ok": True, **store.post(a.name, _read_asks(a.file),
                                     max_open=a.max_open, dry_run=a.dry_run)}


def _brief_event(e, *keys):
    return None if e is None else {"seq": e["seq"], "at": e["at"],
                                   **{k: e.get(k) for k in keys}}


def cmd_list(a):
    st = _store(a).state()
    rows = []
    for i in st["order"]:
        x = st["asks"][i]
        if a.status not in ("all", x["status"]):
            continue
        rows.append({"id": i, "step": x["ask"]["step"], "kind": x["ask"]["kind"],
                     "group": x["ask"]["group"], "title": x["ask"]["title"],
                     "status": x["status"], "rev": x["rev"], "opened_at": x["opened_at"],
                     "answer": _brief_event(x["answer"], "value", "suggested"),
                     "applied": _brief_event(x["applied"], "where"),
                     "withdrawn": _brief_event(x["withdrawn"], "reason"),
                     "advice": _counts(x)})
    return {"ok": True, "seq": st["seq"], "open": len(core.open_asks(st)), "asks": rows}


def _counts(x) -> dict:
    """The team's views of the ask as it stands (each person's last word)."""
    views = core.views(x)
    return {s: sum(e["stance"] == s for e in views) for s in core.STANCES}


def _advice_row(x, e, secret) -> dict:
    """One view, with what it was about: `on` the ask (its suggestion is
    `value`) or an answer (its `value`), and whether that is still `current`."""
    about, now = x["targets"].get(e["on_seq"]) or {}, core.target(x)
    value = (about.get("value") if about.get("type") == "answer"
             else ((about.get("ask") or {}).get("recommend") or {}).get("value"))
    return {"seq": e["seq"], "at": e["at"], "by": e["by"], "id": x["id"], "title": x["ask"]["title"],
            "on": about.get("type"), "on_seq": e["on_seq"], "value": value,
            "stance": e["stance"], "reason": e.get("reason", ""),
            "current": now is not None and now["seq"] == e["on_seq"],
            "verified": core.verify(secret, e)}


def _answer_row(x, secret):
    e = x["answer"]
    shown = x["revs"].get(e.get("subject"), x["ask"])      # what the human saw
    return {"seq": e["seq"], "at": e["at"], "by": e["by"], "id": x["id"],
            "apply": f"{x['id']}@{e['seq']}",
            "step": x["ask"]["step"], "kind": x["ask"]["kind"], "title": shown["title"],
            "value": e["value"], "comment": e.get("comment", ""),
            "suggested": bool(e.get("suggested")), "revised": bool(e.get("revised")),
            "gate": e.get("gate"), "verified": core.verify(secret, e),
            "applied": _brief_event(x["applied"], "where")}


def _report(events, secret, since, everything=False) -> dict:
    """What the human has said, from one snapshot of the log: the answers
    waiting to be applied, the asks reopened after `since`, the newest notes
    after it and every view of the team after it. `seq` is that snapshot's
    last event, the `since` to pass next time."""
    st = core.fold(events)
    asks = [st["asks"][i] for i in st["order"]]
    answers = [_answer_row(x, secret) for x in asks
               if x["status"] == "answered" and (everything or not x["applied"])]
    reopened = []
    for x in asks:
        last = x["history"][-1] if x["history"] else None
        if x["status"] == "open" and last and last["type"] == "reopen" and last["seq"] > since:
            reopened.append({"seq": last["seq"], "at": last["at"], "by": last["by"],
                             "id": x["id"], "title": x["ask"]["title"]})
    notes = [{"seq": e["seq"], "at": e["at"], "by": e["by"], "text": e["text"],
              "verified": core.verify(secret, e)}
             for e in st["messages"] if e["type"] == "note" and e["seq"] > since]
    advice = [_advice_row(x, e, secret) for x in asks for e in x["advice"] if e["seq"] > since]
    return {"ok": True, "seq": st["seq"], "open": len(core.open_asks(st)),
            "answers": sorted(answers, key=lambda r: r["seq"]),
            "reopened": sorted(reopened, key=lambda r: r["seq"]),
            "notes": notes[-NOTES:], "notes_truncated": len(notes) > NOTES,
            "advice": sorted(advice, key=lambda r: r["seq"])}


def cmd_answers(a):
    store = _store(a)
    return _report(store.events(), store.secret(), a.since, a.all)


def cmd_wait(a):
    store = _store(a)
    store.secret()                   # a CONSOLE_SECRET that is too short is refused now, not after the wait
    events = store.events()          # a folder or file that does not exist yet reads as empty
    since = max((e["seq"] for e in events), default=0) if a.since is None else a.since
    deadline = time.monotonic() + a.timeout
    while True:
        heard = any(e["type"] in HUMAN and e["seq"] > since for e in events)
        left = deadline - time.monotonic()
        if heard or left <= 0:
            return {**_report(events, store.secret(), since), "timed_out": not heard}
        time.sleep(min(POLL, left))
        events = store.events()


def _once(ids):
    """The same id twice would log a second event the fold has to throw away."""
    return list(dict.fromkeys(ids))


def cmd_applied(a):
    seqs = {}
    for i, seq in a.ids:
        if seqs.setdefault(i, seq) != seq:
            raise Refused("bad_request", f"{i} is given twice with different SEQ")
    return {"ok": True, **_store(a).applied(
        a.name, list(seqs), a.where, seqs={i: s for i, s in seqs.items() if s is not None})}


def cmd_withdraw(a):
    return {"ok": True, **_store(a).withdraw(a.name, _once(a.ids), a.reason)}


def cmd_say(a):
    return {"ok": True, **_store(a).say(a.name, a.text)}


def _steps(v) -> list:
    return list(core.STEPS) if v == "all" else list(v or ())


def _examples() -> dict:
    by_step = {}
    for name in sorted(os.listdir(EXAMPLES)):
        if name.endswith(".json"):
            with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
                ask = json.load(f)
            by_step[ask["step"]] = ask
    return {s: by_step[s] for s in core.STEPS if s in by_step}


def cmd_schema(a):
    fields = {}
    for f, (required, allowed) in core.ASK_FIELDS.items():
        fields[f] = {"required_for": _steps(required), "allowed_for": _steps(allowed),
                     "about": ABOUT[f]}
        if f in core.LIMITS:
            fields[f]["limit"] = core.LIMITS[f]
    return {"ok": True, "steps": list(core.STEPS), "fields": fields,
            "limits": dict(core.LIMITS), "max_open": core.MAX_OPEN,
            "answer_values": ANSWER_VALUES, "flow": FLOW, "examples": _examples()}


def cmd_verify(a):
    store = _store(a)
    secret = store.secret()
    if secret is None:
        raise Refused("no_secret")
    bad, unsigned, checked = [], [], 0
    for e in store.events():
        if e["type"] in HUMAN:
            checked += 1
            if not e.get("sig"):
                unsigned.append(e["seq"])
            elif not core.verify(secret, e):
                bad.append(e["seq"])
    doc = {"ok": not bad and not unsigned, "checked": checked, "bad": bad,
           "unsigned": unsigned, "secret": True}
    if not doc["ok"]:
        doc["code"] = "bad_signature"
        doc["message"] = (f"{len(bad) + len(unsigned)} of {checked} human events are not "
                          "signed by this console's secret.")
        doc["params"] = {"bad": bad, "unsigned": unsigned}
    return doc


# ----------------------------------------------------------------- digest --

STATUS_WORDS = {"open": "Open: waiting for an answer", "waiting": "Answered: waiting for the agent to apply",
                "applied": "Applied", "withdrawn": "Withdrawn"}
ABOUT_WORDS = {("ask", True): "about the question", ("ask", False): "about the question",
               ("answer", True): "about the answer", ("answer", False): "about an earlier answer"}
YES_NO = {("confirm", "yes"): "Yes", ("confirm", "no"): "No", ("approve", "yes"): "Approve", ("approve", "no"): "Reject"}


def _label(ask: dict, value):
    """An answer in the words it was picked in: an option's label, a value with its unit."""
    if value is None:
        return None
    v = str(value)
    if (ask.get("step"), v) in YES_NO:
        return YES_NO[(ask.get("step"), v)]
    if ask.get("step") == "choose":
        return next((o.get("label", v) for o in ask.get("options") or [] if o.get("value") == v), v)
    unit = (ask.get("input") or {}).get("unit") if ask.get("step") == "provide" else None
    return f"{v} {unit}" if unit else v


def _given(e, shown, secret) -> dict:
    """One answer as the record keeps it: what, who, when, and whether it was the suggestion."""
    return {"seq": e["seq"], "at": e["at"], "by": e["by"], "value": e["value"], "label": _label(shown, e["value"]),
            "suggested": bool(e.get("suggested")), "comment": e.get("comment", ""),
            "revised": bool(e.get("revised")), "gate": e.get("gate"), "verified": core.verify(secret, e)}


def _record(x, secret) -> dict:
    """One ask for the decision record: what the owner was shown (for an answer, the
    version they answered), the answer, answers taken back, the views, where it went."""
    ans, now = x["answer"], core.target(x)
    shown = x["revs"].get(ans.get("subject"), x["ask"]) if ans else x["ask"]
    rec = shown.get("recommend")
    reopens = {e["answer_seq"]: e for e in x["history"] if e["type"] == "reopen"}
    earlier = [{**_given(e, x["revs"].get(e.get("subject"), x["ask"]), secret),
                "reopened": _brief_event(reopens.get(e["seq"]), "by")}
               for e in x["history"] if e["type"] == "answer" and e is not ans]
    state = "applied" if x["applied"] else "waiting" if x["status"] == "answered" else x["status"]
    return {"id": x["id"], "title": shown["title"], "kind": shown.get("kind", "general"),
            "group": shown.get("group", ""), "step": shown["step"], "status": x["status"], "state": state,
            "asked": {"seq": x["opened_seq"], "at": x["opened_at"], "by": x["by"]},
            "why": shown["why"], "evidence": shown["evidence"], "options": shown.get("options"),
            "effect": shown.get("effect"), "if_no": shown["if_no"],
            "suggestion": ({"value": rec["value"], "label": _label(shown, rec["value"]), "because": rec.get("because")}
                           if rec else None),
            "answer": _given(ans, shown, secret) if ans else None, "earlier": earlier,
            "advice": [{"seq": e["seq"], "at": e["at"], "by": e["by"],
                        "on": (x["targets"].get(e["on_seq"]) or {}).get("type"), "on_seq": e["on_seq"],
                        "current": now is not None and now["seq"] == e["on_seq"],
                        "stance": e["stance"], "reason": e.get("reason", ""), "verified": core.verify(secret, e)}
                       for e in x["advice"]],
            "applied": _brief_event(x["applied"], "by", "where"),
            "withdrawn": _brief_event(x["withdrawn"], "by", "reason")}


def _md(text) -> str:
    """Text someone else wrote, as one line of Markdown that shows exactly those words."""
    return re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", " ".join(str(text).split()))


def _who(by) -> str:
    return _md(str(by or "").split(":", 1)[-1])


def _when(at) -> str:
    at = str(at or "")
    return f"{at[:10]} {at[11:16]} UTC" if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", at) else _md(at)


def _unsigned(row) -> str:
    return " (its signature does not check)" if row.get("verified") is False else ""


def _md_evidence(items) -> list[str]:
    out = []
    for it in items:
        src = "".join(f", {_md(it[k])}" for k in ("source", "url") if it.get(k))
        if "table" in it:
            tb = it["table"]
            out += ["", _md(tb.get("caption") or "Table"), "",
                    "| " + " | ".join(_md(c) for c in tb["columns"]) + " |",
                    "|" + " --- |" * len(tb["columns"])]
            out += ["| " + " | ".join(_md(c) for c in r) + " |" for r in tb["rows"]]
            out.append("")
        elif "quote" in it:
            out.append(f"- \u201c{_md(it['quote'])}\u201d{' (' + src[2:] + ')' if src else ''}")
        else:
            out.append(f"- {_md(it.get('label'))}: {_md(it.get('value'))}{' (' + src[2:] + ')' if src else ''}")
    return out


def _md_record(n: int, r: dict) -> list[str]:
    a = r["answer"]
    out = [f"## {n}. {_md(r['title'])}", "",
           f"`{r['id']}` · {_md(r['kind'])}{' · ' + _md(r['group']) if r['group'] else ''} · "
           f"asked {_when(r['asked']['at'])} by {_who(r['asked']['by'])} · **{STATUS_WORDS[r['state']]}**", "",
           f"**Why:** {_md(r['why'])}", "", "**Evidence:**", "", *_md_evidence(r["evidence"]), ""]
    if r["options"]:
        out += ["**Options:** " + " · ".join(_md(o["label"]) for o in r["options"]), ""]
    if r["suggestion"]:
        s = r["suggestion"]
        out += [f"**Suggested:** {_md(s['label'])}" + (f" — {_md(s['because'])}" if s.get("because") else ""), ""]
    if r["effect"]:
        out += [f"**If approved:** {_md(r['effect'])}", ""]
    out += [f"**If no:** {_md(r['if_no'])}", ""]
    for e in r["earlier"]:
        back = e["reopened"]
        took = f"; reopened {_when(back['at'])} by {_who(back['by'])}" if back else ""
        out += [f"**Answer taken back:** {_md(e['label'])}, by {_who(e['by'])}, {_when(e['at'])}{took}{_unsigned(e)}", ""]
    if a:
        took = ("the suggestion" if a["suggested"] else "not the suggestion") if r["suggestion"] else ""
        bits = [f"{_md(a['label'])}, by {_who(a['by'])}, {_when(a['at'])}"] + ([took] if took else [])
        if a["comment"]:
            bits.append(f"comment: \u201c{_md(a['comment'])}\u201d")
        if isinstance(a["gate"], dict):
            bits.append("written to the system" + (f": {_md(a['gate'].get('message'))}" if a["gate"].get("message") else ""))
        out += ["**Answer:** " + "; ".join(bits) + _unsigned(a), ""]
    if r["advice"]:
        out += ["**The team:**", ""]
        for v in r["advice"]:
            said = "agrees" if v["stance"] == "agree" else "disagrees"
            why = f": {_md(v['reason'])}" if v["reason"] else ""
            about = ABOUT_WORDS.get((v["on"], v["current"]), "")
            out.append(f"- {_who(v['by'])} {said} {about}, {_when(v['at'])}{why}{_unsigned(v)}")
        out.append("")
    if r["applied"]:
        out += [f"**Applied:** {_md(r['applied']['where'])}, {_when(r['applied']['at'])}", ""]
    if r["withdrawn"]:
        out += [f"**Withdrawn:** {_md(r['withdrawn']['reason'])}, {_when(r['withdrawn']['at'])}", ""]
    return out


def cmd_digest(a):
    store = _store(a)
    secret = store.secret()
    st = core.fold(store.events())
    records = [_record(st["asks"][i], secret) for i in st["order"]]
    if a.format == "json":
        return {"ok": True, "format": "json", "seq": st["seq"], "asks": records}
    count = {k: sum(r["state"] == k for r in records) for k in STATUS_WORDS}
    lines = ["# Decision record", "",
             f"{len(records)} asks: {count['applied']} applied, {count['waiting']} answered and waiting to be applied, "
             f"{count['open']} open, {count['withdrawn']} withdrawn. As of {_when(core.now())}.",
             "Signatures checked with this console's secret." if secret else
             "Signatures not checked: there is no secret here.", ""]
    for n, r in enumerate(records, 1):
        lines += _md_record(n, r)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"
    return {"ok": True, "format": "md", "seq": st["seq"], "text": text}


# ------------------------------------------------------------------- main --

def main(argv=None) -> int:
    try:
        a = _parser().parse_args(argv)
        doc = a.run(a)
        doc["dir"] = _folder(a)
    except Stop as e:
        doc = e.doc
    except Refused as e:
        doc = e.doc()
    except RecursionError:
        doc = Refused("bad_request", "the input is nested too deeply").doc()
    except OSError as e:
        doc = Refused("bad_request", f"cannot use the folder: {e}").doc()
    try:
        text = json.dumps(doc, ensure_ascii=False, allow_nan=False)
    except ValueError:              # a NaN or Infinity in the log: never let it out as "JSON"
        doc = Refused("corrupt_log", "events.jsonl holds NaN or Infinity, which JSON cannot carry.").doc()
        text = json.dumps(doc)
    out = (text + "\n").encode("utf-8", "backslashreplace")
    sys.stdout.buffer.write(out)
    sys.stdout.flush()
    return 0 if doc["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
