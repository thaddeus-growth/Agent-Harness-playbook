#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Turn picked intake items into owner-console asks.

    intake_to_asks.py --intake FILE... --pick IID,IID... [--max N]
                      [--audience client|builder] [--console-dir DIR] [--ask PATH]

Prints the asks as one JSON array, ready for `console/ask.py add FILE`. On a
refusal it prints {ok: false, code, message, problems} and exits 2, and no ask
is printed. It writes nothing: posting is the agent's step.

    word      -> confirm   "Call it "<word>", meaning ...?"   quote as evidence
    story     -> confirm   want / so_that in why, done_when and the human step as evidence
    number    -> provide   input number, the unit, recommend = the quoted value
                           (a range: its low end, and the range is shown)
    question  -> choose    when it lists options, else provide text
    goal      -> refused:  a goal is the why of its stories, not an ask

Every ask has evidence with a source, a recommendation with its reason and
what "no" means. Its id is "intake-<iid>", so an item is never asked twice
under two ids. What apply_answers.py later writes is shown here in full: the
intake schema keeps every such text within the console's limits.

Refused (each is a test in build/tests/test_intake_to_asks.py): an intake file
check_intake.py does not pass; more picks than --max (at most 10, the owner's
budget); an unknown, repeated or goal iid; a question with no suggestion; an
item for the other audience; with --console-dir, an item already answered or
withdrawn there, or more new asks than the open budget leaves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_intake as ci  # noqa: E402

ASK = os.path.join(HERE, "..", "console", "ask.py")
MAX_OPEN = 10                 # the console's budget; --max can only lower it
TITLE, WHY = 120, 400         # the console's limits for these two fields
PREFIX = "intake-"
KIND = {"words": "word", "stories": "story", "numbers": "number", "questions": "question"}
GROUP = {"words": "Words we use", "stories": "What to build",
         "numbers": "Numbers you gave us", "questions": "Open questions"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
HUMAN_STEP = {"none": "none: it runs without asking you",
              "confirm": "confirm: you confirm what it shows",
              "approve": "approve: you approve each action before it happens"}


class Refusal(Exception):
    def __init__(self, code, message, problems=None):
        super().__init__(message)
        self.doc = {"ok": False, "code": code, "message": message,
                    "problems": problems or []}


def ask_id(iid: str) -> str:
    return PREFIX + iid


def shorten(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word with an ellipsis. Only for a
    title: the full text is always in why or the evidence too."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "…"


def clause(text: str) -> str:
    """A sentence made to sit inside a question: no end stop, and a lower-case
    start unless it is a name or an acronym ("NPR", "Sencha")."""
    text = text.strip().rstrip(".。!！")
    first = text.split()[0] if text.split() else ""
    if first.lower() in ("a", "an", "the", "two", "one", "every", "each", "all", "any", "how",
                         "what", "when") and first[:1].isupper() and (first[1:].islower() or len(first) == 1):
        text = text[0].lower() + text[1:]
    return text


def source_text(source: str) -> str:
    """How a source reads to the owner: 'meeting 2026-03-02 00:12:31'."""
    return source if source.startswith("doc:") else "meeting " + source


def quote_ev(item: dict) -> dict:
    return {"quote": item["quote"], "source": source_text(item["source"])}


def said_ev(item: dict) -> dict:
    """The evidence an item gives when it is quoted, else where it was raised."""
    if item.get("quote"):
        return quote_ev(item)
    return {"label": "Raised in", "value": source_text(item["source"]),
            "source": source_text(item["source"])}


def unsure_ev(item: dict) -> list[dict]:
    if not item.get("unsure"):
        return []
    return [{"label": "We are not sure", "value": item.get("note") or "we may have misheard this",
             "source": "our notes of the meeting"}]


def when(x: dict) -> str:
    return x["meeting"].get("date") or x["item"]["source"][:10]


# ------------------------------------------------------------ one bucket --

def word_ask(iid, x):
    it = x["item"]
    word, meaning = it["word"], it["meaning"]
    because = (f"It is the word you used in the meeting on {when(x)}."
               if not it.get("unsure") else
               f"It is the word we heard in the meeting on {when(x)}; we are not sure we heard it right.")
    return {
        "id": ask_id(iid), "step": "confirm", "kind": "word", "group": GROUP["words"],
        "title": shorten(f'Call it "{word}", meaning {clause(meaning)}?', TITLE),
        "why": (f"Reports and pages will use your words. If yes, the glossary gets "
                f'"{word}" with this meaning: {meaning}'),
        "evidence": [quote_ev(it)] + unsure_ev(it),
        "recommend": {"value": "yes", "because": because},
        "if_no": f'We do not use "{word}" for it. Name the word you want in the comment, or we ask you again.',
    }


def story_ask(iid, x):
    it = x["item"]
    ev = [quote_ev(it)]
    ev += [{"label": f"Done when {CIRCLED[n]}", "value": d} for n, d in enumerate(it["done_when"])]
    ev.append({"label": "Your step", "value": HUMAN_STEP[it["human_step"]]})
    ev += unsure_ev(it)
    because = (f"It is what you asked for in the meeting on {when(x)}."
               if not it.get("unsure") else
               f"It is what we heard in the meeting on {when(x)}, but we are not sure; see the note.")
    return {
        "id": ask_id(iid), "step": "confirm", "kind": "story", "group": GROUP["stories"],
        "title": shorten(f'Build this: "I want {clause(it["want"])}"?', TITLE),
        "why": f"I want {it['want']}, so that {it['so_that']}.",
        "evidence": ev,
        "recommend": {"value": "yes", "because": because},
        "if_no": ("Nothing is built for it. If only part is wrong, say what to change in the "
                  "comment; we ask again with your change."),
    }


def number_ask(iid, x):
    it = x["item"]
    unit, value = it["unit"], ci.value_text(it["value"])
    rng = ci.value_range(it["value"]) if isinstance(it["value"], str) else None
    ev = [quote_ev(it),
          {"label": "What it applies to", "value": it["applies_to"]},
          {"label": "Unit", "value": unit}]
    if rng:
        ev.append({"label": "You gave a range", "value": f"{value} {unit}"})
        because = (f"You gave a range, {value} {unit}. We start from the low end, so no "
                   "figure is higher than you said.")
        rec = rng[0]
    else:
        because = f"It is the number you said in the meeting on {when(x)}."
        rec = value
    ev += unsure_ev(it)
    spec = {"type": "number"}
    if len(unit) <= 12:                     # the console's limit for a unit; longer ones are in the evidence
        spec["unit"] = unit
    return {
        "id": ask_id(iid), "step": "provide", "kind": "number", "group": GROUP["numbers"],
        "title": shorten(f"What is {clause(it['applies_to'])}, in {unit}?", TITLE),
        "why": (f"You gave this number in the meeting on {when(x)}. It is kept as a pending "
                "value: nothing uses it until it is confirmed."),
        "evidence": ev,
        "input": spec,
        "recommend": {"value": rec, "because": because},
        "if_no": ("Type the right number. Until you confirm one, nothing uses it and reports "
                  "mark it as an estimate."),
    }


def question_ask(iid, x, idx):
    it = x["item"]
    sug = it.get("suggest")
    if not sug:
        raise Refusal("no_suggestion", f"{iid} has no suggestion; add `suggest` "
                      "{value, because} to the intake item: an ask always carries one",
                      [{"iid": iid, "code": "no_suggestion", "message": "no suggest"}])
    ev = [said_ev(it)]
    for other in it.get("about") or []:
        o = idx[other]["item"]
        if o is not it:
            ev.append(said_ev(o))
    ev += unsure_ev(it)
    why = f"It was left open in the meeting on {when(x)}. {it['text']}"
    ask = {
        "id": ask_id(iid), "kind": "question", "group": GROUP["questions"],
        "title": shorten(it["text"], TITLE),
        "why": why if len(why) <= WHY else it["text"],
        "evidence": ev[:12],
        "recommend": {"value": sug["value"], "because": sug["because"]},
    }
    if it.get("options"):
        ask["step"] = "choose"
        ask["options"] = [{"value": o, "label": o} for o in it["options"]]
        ask["if_no"] = "If none fits, write your answer in the comment. It stays open until you do."
    else:
        ask["step"] = "provide"
        ask["input"] = {"type": "text"}
        ask["if_no"] = ("If you do not know yet, say so. It stays open and we ask again "
                        "at the next review.")
    return ask


def to_ask(iid: str, idx: dict) -> dict:
    x = idx[iid]
    b = x["bucket"]
    if b == "words":
        return word_ask(iid, x)
    if b == "stories":
        return story_ask(iid, x)
    if b == "numbers":
        return number_ask(iid, x)
    if b == "questions":
        return question_ask(iid, x, idx)
    raise Refusal("not_askable", f"{iid} is a goal: it is the why of the stories it "
                  "explains, not an ask of its own",
                  [{"iid": iid, "code": "not_askable", "message": "a goal is not asked"}])


# ---------------------------------------------------------------- console --

def run_ask(ask_path: str, console_dir: str, *args: str) -> dict:
    """One ask.py call; its JSON document. Never the log itself."""
    p = subprocess.run([sys.executable, ask_path, "--dir", console_dir, *args],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        raise Refusal("console_unreadable", f"ask.py gave no JSON: {(p.stdout + p.stderr)[:300]}") from None
    if not doc.get("ok"):
        raise Refusal("console_refused", f"ask.py refused: {doc.get('message')}", [doc])
    return doc


def plan_against_console(picks: list[str], ask_path: str, console_dir: str, limit: int):
    """Never ask again what is settled, and stay inside the open budget."""
    doc = run_ask(ask_path, console_dir, "list", "--status", "all")
    status = {r["id"]: r["status"] for r in doc.get("asks", [])}
    settled = [i for i in picks if status.get(ask_id(i)) in ("answered", "withdrawn")]
    if settled:
        raise Refusal("already_asked", f"already answered or withdrawn in the console: "
                      f"{', '.join(settled)}; an answered item is not asked again",
                      [{"iid": i, "code": "already_asked", "message": status[ask_id(i)]}
                       for i in settled])
    new = [i for i in picks if ask_id(i) not in status]
    room = limit - doc.get("open", 0)
    if len(new) > room:
        raise Refusal("over_budget", f"{doc.get('open', 0)} asks are open; {len(new)} new "
                      f"ones would pass the limit of {limit}. Pick at most {max(room, 0)}.")


# ------------------------------------------------------------------- main --

def build(intake: list[str], picks: list[str], limit: int = MAX_OPEN,
          audience: str | None = None, console_dir: str | None = None,
          ask_path: str = ASK) -> list[dict]:
    if not 1 <= limit <= MAX_OPEN:
        raise Refusal("bad_request", f"--max is 1 to {MAX_OPEN}: it can lower the owner's budget, never raise it")
    if not picks:
        raise Refusal("bad_request", "--pick names no iid")
    dup = sorted({i for i in picks if picks.count(i) > 1})
    if dup:
        raise Refusal("duplicate_pick", f"picked twice: {', '.join(dup)}")
    if len(picks) > limit:
        raise Refusal("too_many", f"{len(picks)} picks; at most {limit} asks at a time. "
                      "The rest wait for the next round.")
    report = ci.check(intake)
    if not report["ok"]:
        raise Refusal("intake_invalid", f"check_intake.py found {len(report['problems'])} "
                      "problems; fix the intake first", report["problems"])
    docs, _ = ci.load(intake)
    idx = ci.index(docs)
    unknown = [i for i in picks if i not in idx]
    if unknown:
        raise Refusal("unknown_iid", f"no intake item has iid {', '.join(unknown)}",
                      [{"iid": i, "code": "unknown_iid", "message": "not found"} for i in unknown])
    if audience:
        other = [i for i in picks if idx[i]["item"].get("audience") != audience]
        if other:
            raise Refusal("wrong_audience", f"{', '.join(other)} are not for the {audience}: "
                          "each person answers in a console of their own",
                          [{"iid": i, "code": "wrong_audience",
                            "message": idx[i]["item"].get("audience")} for i in other])
    if console_dir:
        plan_against_console(picks, ask_path, console_dir, limit)
    return [to_ask(i, idx) for i in picks]


class Parser(argparse.ArgumentParser):
    """argparse that never prints: a mistake becomes the one JSON document."""

    def error(self, message):
        raise Refusal("bad_request", message)

    def print_help(self, file=None):
        raise Help(self.format_help())


class Help(Exception):
    """--help: the usage, as the one JSON document every outcome is."""


def main(argv: list[str] | None = None) -> int:
    p = Parser(prog="intake_to_asks.py", description=__doc__.split("\n")[0])
    p.add_argument("--intake", nargs="+", required=True, metavar="FILE")
    p.add_argument("--pick", required=True, metavar="IID,IID...")
    p.add_argument("--max", type=int, default=MAX_OPEN, metavar="N",
                   help=f"at most N asks (default and ceiling {MAX_OPEN})")
    p.add_argument("--audience", choices=("client", "builder"))
    p.add_argument("--console-dir", metavar="DIR",
                   help="check the picks against this console (ask.py list --status all)")
    p.add_argument("--ask", default=ASK, metavar="PATH", help="the console's ask.py")
    try:
        a = p.parse_args(argv)
        picks = [s.strip() for s in a.pick.split(",") if s.strip()]
        out = build(a.intake, picks, a.max, a.audience, a.console_dir, a.ask)
    except Help as h:
        sys.stdout.write(json.dumps({"ok": True, "help": str(h)}) + "\n")
        return 0
    except Refusal as e:
        out = e.doc
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 2 if isinstance(out, dict) else 0


if __name__ == "__main__":
    sys.exit(main())
