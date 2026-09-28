#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The decision record of one console, for a team to read.

    digest.py --console-dir DIR [--format md|json] [--asks FILE...]
              [--title TEXT] [--ask PATH]

It runs `ask.py list --status all` and `ask.py answers --all` (the console's
own CLI, as a subprocess; the log file itself is never opened here) and shows
every ask: the question, why, the evidence, the suggestion, the answer (value,
who, when, whether it took the suggestion), the owner's comment, the team's
advice when the console has any, and where it was applied or why it was
withdrawn.

`ask.py list` names each ask but does not repeat its evidence or suggestion.
Where the console has `ask.py digest --format json`, the evidence comes from
it, as the owner was shown it. An older console has no such verb: then it
comes from the ask JSON the agent posted (--asks, e.g. what intake_to_asks.py
printed). Advice (the team's views: `answers`' `advice` rows, `list`'s
counts) is shown when the console gives it; when it is absent nothing is said.

Markdown (the default) is what a team lead forwards or posts; JSON is the
same record for a program. Prints one document; exit 0, or 2 with
{ok: false, code, message} when the console cannot be read.

Rules (each is a test in build/tests/test_digest.py): every ask is listed,
whatever its status; the answer names who and when and whether it took the
suggestion; the log is only read through ask.py; text from the log cannot
break the page (no raw HTML, no table breaks, no links it did not have).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ASK = os.path.join(HERE, "..", "console", "ask.py")
DETAILS = ("why", "evidence", "recommend", "if_no", "options", "effect", "input")


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.doc = {"ok": False, "code": code, "message": message}


def run_ask(ask_path: str, console_dir: str, *args: str) -> dict:
    """One ask.py call and its JSON document."""
    try:
        p = subprocess.run([sys.executable, ask_path, "--dir", console_dir, *args],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refusal("console_unreadable", f"cannot run ask.py: {e}") from None
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        raise Refusal("console_unreadable", f"ask.py gave no JSON: {(p.stdout + p.stderr)[:300]}") from None
    if not isinstance(doc, dict) or not doc.get("ok"):
        raise Refusal(doc.get("code", "console_refused") if isinstance(doc, dict) else "console_refused",
                      f"ask.py refused: {doc.get('message') if isinstance(doc, dict) else doc}")
    return doc


def load_posted(paths: list[str]) -> dict:
    """{id: ask} from ask JSON files (one ask or an array); a later file wins."""
    out = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            raise Refusal("bad_request", f"cannot read {p}: {e}") from None
        for a in doc if isinstance(doc, list) else [doc]:
            if isinstance(a, dict) and isinstance(a.get("id"), str):
                out[a["id"]] = a
    return out


# ---------------------------------------------------------------- collect --

def try_ask(ask_path: str, console_dir: str, *args: str) -> dict | None:
    """An ask.py verb an older console may not have: its document, or None."""
    try:
        return run_ask(ask_path, console_dir, *args)
    except Refusal as e:
        if e.doc["code"] == "bad_request":          # the verb or flag is unknown there
            return None
        raise


def _views(raw) -> list[dict]:
    """The team's views of one ask, whatever shape the console gave them."""
    out = []
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, dict):
            out.append({k: v.get(k) for k in ("by", "at", "stance", "reason", "on", "current", "verified")})
    return out


def collect(console_dir: str, ask_path: str = ASK, asks_files: list[str] | None = None,
            title: str | None = None) -> dict:
    listed = run_ask(ask_path, console_dir, "list", "--status", "all")
    answers = run_ask(ask_path, console_dir, "answers", "--all")
    record = try_ask(ask_path, console_dir, "digest", "--format", "json") or {}
    shown = {r.get("id"): r for r in record.get("asks", []) if isinstance(r, dict)}
    posted = load_posted(asks_files or [])
    by_id = {r.get("id"): r for r in answers.get("answers", []) if isinstance(r, dict)}
    team: dict = {}
    for v in answers.get("advice", []) if isinstance(answers.get("advice"), list) else []:
        if isinstance(v, dict):
            team.setdefault(v.get("id"), []).append(v)
    asks = []
    for row in listed.get("asks", []):
        if not isinstance(row, dict):
            continue
        i = row.get("id")
        rec = shown.get(i) or {}
        nested = row.get("ask") if isinstance(row.get("ask"), dict) else {}
        inline = {k: row.get(k, nested.get(k)) for k in DETAILS if row.get(k, nested.get(k)) is not None}
        if rec:                                  # what the owner was shown, as the console keeps it
            sug = rec.get("suggestion") if isinstance(rec.get("suggestion"), dict) else None
            details = {k: rec.get(k) for k in ("why", "evidence", "if_no", "options", "effect")}
            details["recommend"] = {"value": sug.get("value"), "because": sug.get("because")} if sug else None
            origin = "console"
        elif inline:
            details, origin = inline, "console"
        elif i in posted:
            details = {k: posted[i][k] for k in DETAILS if k in posted[i]}
            origin = "posted file"
        else:
            details, origin = {}, None
        a = by_id.get(i) or {}
        given = rec.get("answer") if isinstance(rec.get("answer"), dict) else {}
        brief = row.get("answer") if isinstance(row.get("answer"), dict) else {}
        answer = None
        if a or given or brief:
            src = a or given or brief
            value = src.get("value")
            answer = {"value": value, "label": given.get("label") or _label(details, value),
                      "by": a.get("by") or given.get("by"), "at": src.get("at"), "seq": src.get("seq"),
                      "comment": a.get("comment") or given.get("comment") or "",
                      "suggested": bool(src.get("suggested")),
                      "verified": a.get("verified", given.get("verified")),
                      "revised": bool(a.get("revised") or given.get("revised")),
                      "apply": a.get("apply")}
        views = _views(team.get(i)) or _views(rec.get("advice"))
        other = row.get("advice", nested.get("advice"))
        advice = {"views": views,
                  "counts": other if isinstance(other, dict) and all(isinstance(n, int) for n in other.values()) else None,
                  "other": None if isinstance(other, dict) or other is None else other}
        if not (advice["views"] or advice["other"] not in (None, "", []) or
                (advice["counts"] and any(advice["counts"].values()))):
            advice = None
        asks.append({
            "id": i, "title": a.get("title") or rec.get("title") or row.get("title") or "",
            "step": row.get("step"), "kind": row.get("kind"), "group": row.get("group") or "",
            "status": row.get("status"), "rev": row.get("rev"), "opened_at": row.get("opened_at"),
            "why": details.get("why"), "evidence": details.get("evidence") or [],
            "recommend": details.get("recommend"), "if_no": details.get("if_no"),
            "options": details.get("options"), "effect": details.get("effect"),
            "details_from": origin,
            "answer": answer, "advice": advice,
            "applied": row.get("applied"), "withdrawn": row.get("withdrawn"),
        })
    return {"ok": True, "title": title or "Decision record", "dir": listed.get("dir"),
            "seq": listed.get("seq"), "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "counts": counts(asks), "asks": asks}


def _label(details: dict, value):
    for o in details.get("options") or []:
        if isinstance(o, dict) and o.get("value") == value and o.get("label") not in (None, value):
            return o["label"]
    return None


def counts(asks: list[dict]) -> dict:
    answered = [a for a in asks if a["answer"]]
    rec = [a for a in answered if a["recommend"] is not None or a["answer"]["suggested"]]
    return {"asks": len(asks), "answered": len(answered),
            "took_suggestion": sum(1 for a in answered if a["answer"]["suggested"]),
            "overrode": sum(1 for a in rec if not a["answer"]["suggested"] and a["recommend"] is not None),
            "open": sum(1 for a in asks if a["status"] == "open"),
            "withdrawn": sum(1 for a in asks if a["status"] == "withdrawn"),
            "applied": sum(1 for a in asks if a["applied"]),
            "without_details": sum(1 for a in asks if not a["details_from"])}


# --------------------------------------------------------------- markdown --

_ESC = {c: "\\" + c for c in "\\`[]<>|"}


def md(text) -> str:
    """Text from the log on one line, unable to open HTML, a link or a table cell."""
    s = " ".join(str(text if text is not None else "").split())
    return "".join(_ESC.get(c, c) for c in s)


def when(iso) -> str:
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return md(iso)


def who(by) -> str:
    return md(str(by).split(":", 1)[-1]) if by else "the owner"


def short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def answer_text(a: dict) -> str:
    """The answer in the words it was picked in (an option's label, a value
    with its unit), else as given."""
    return md(a.get("label") or a["value"])


def suggestion_text(x: dict) -> str | None:
    rec = x.get("recommend")
    if not isinstance(rec, dict):
        return None
    v = md(rec.get("value"))
    lab = _label(x, rec.get("value"))
    if lab:
        v += f" ({md(lab)})"
    because = md(rec.get("because")) if rec.get("because") else ""
    return f"{v}. {because}".strip()


def evidence_lines(ev: list) -> list[str]:
    out = []
    for e in ev:
        if not isinstance(e, dict):
            continue
        src = f" ({md(e['source'])})" if e.get("source") and e["source"] != e.get("value") else ""
        url = f" ({md(e['url'])})" if e.get("url") else ""
        if "table" in e and isinstance(e["table"], dict):
            t = e["table"]
            cols = [md(c) for c in t.get("columns") or []]
            out.append(f"  - {md(t.get('caption') or 'Table')}:")
            for r in t.get("rows") or []:
                cells = [f"{c}: {md(v)}" for c, v in zip(cols, r)]
                out.append(f"    - {' · '.join(cells)}")
        elif "quote" in e:
            out.append(f'  - "{md(e["quote"])}"{src}{url}')
        else:
            out.append(f"  - {md(e.get('label'))}: {md(e.get('value'))}{src}{url}")
    return out


def _plain(v) -> str:
    return md(v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False))


ABOUT = {("ask", True): "about the question", ("ask", False): "about the question",
         ("answer", True): "about the answer", ("answer", False): "about an earlier answer"}


def advice_lines(advice) -> list[str]:
    """The team's views: one line each (who agrees or disagrees, when, why);
    counts when that is all the console gives; nothing when there are none."""
    if not advice:
        return []
    out = []
    if advice.get("views"):
        out.append("- **Advice from the team:**")
        for v in advice["views"]:
            said = {"agree": "agrees", "disagree": "disagrees"}.get(v.get("stance"), _plain(v.get("stance")))
            about = ABOUT.get((v.get("on"), v.get("current")), "")
            why = f": {md(v['reason'])}" if v.get("reason") else ""
            bad = " (its signature does not check)" if v.get("verified") is False else ""
            out.append(f"  - {who(v.get('by'))} {said}{' ' + about if about else ''}, {when(v.get('at'))}{why}{bad}")
    elif advice.get("counts"):
        parts = [f"{n} {_plain(k)}" for k, n in advice["counts"].items() if n]
        out.append(f"- **Advice from the team:** {', '.join(parts)}")
    other = advice.get("other")
    if isinstance(other, list):
        out += ["- **Advice:**"] + [f"  - {_plain(x)}" for x in other]
    elif other not in (None, ""):
        out.append(f"- **Advice:** {_plain(other)}")
    return out


def status_text(x: dict) -> str:
    if x["status"] == "withdrawn":
        return "withdrawn"
    if x["status"] == "open":
        return "waiting"
    return "applied" if x["applied"] else "answered, not yet applied"


def render_md(d: dict) -> str:
    c = d["counts"]
    L = [f"# {md(d['title'])}", "",
         f"From the owner console, {when(d['taken_at'])} (log seq {d['seq']}).",
         f"{c['asks']} asks: {c['answered']} answered ({c['took_suggestion']} as suggested, "
         f"{c['overrode']} not), {c['open']} waiting, {c['withdrawn']} withdrawn; {c['applied']} applied."]
    if c["without_details"]:
        L.append(f"{c['without_details']} asks show no evidence or suggestion: the console's output "
                 "does not carry them; pass the posted asks with --asks.")
    L += ["", "| # | Question | Answer | As suggested | Status |", "| --- | --- | --- | --- | --- |"]
    for n, x in enumerate(d["asks"], 1):
        a = x["answer"]
        took = "—" if not a else ("yes" if a["suggested"] else ("no" if x["recommend"] else "—"))
        L.append(f"| {n} | {short(md(x['title']))} | {short(answer_text(a), 40) if a else '—'} | {took} | {status_text(x)} |")
    groups: dict[str, list] = {}
    for n, x in enumerate(d["asks"], 1):
        groups.setdefault(x["group"] or "Other", []).append((n, x))
    for g, xs in groups.items():
        L += ["", f"## {md(g)}"]
        for n, x in xs:
            L += ["", f"### {n}. {md(x['title'])}"]
            a = x["answer"]
            if a:
                took = ("Took the suggestion." if a["suggested"] else
                        "Did not take the suggestion." if x["recommend"] else "")
                L.append(f"- **Answer:** {answer_text(a)}, by {who(a['by'])}, {when(a['at'])}. {took}".rstrip())
                if a.get("verified") is False:
                    L.append("- **Warning:** the signature does not check; do not act on this answer.")
                if a.get("revised"):
                    L.append("- **Note:** the ask changed after the owner saw it.")
            elif x["status"] == "withdrawn":
                w = x["withdrawn"] or {}
                L.append(f"- **Withdrawn:** {md(w.get('reason'))}, {when(w.get('at'))}.")
            else:
                L.append(f"- **Answer:** none yet; asked {when(x['opened_at'])}.")
            sug = suggestion_text(x)
            if sug:
                L.append(f"- **Suggested:** {sug}")
            if x.get("why"):
                L.append(f"- **Why asked:** {md(x['why'])}")
            ev = evidence_lines(x["evidence"])
            if ev:
                L += ["- **Evidence:**"] + ev
            if x.get("if_no"):
                L.append(f"- **If no:** {md(x['if_no'])}")
            if a and a["comment"]:
                L.append(f"- **Comment:** {md(a['comment'])}")
            L += advice_lines(x["advice"])
            if x["applied"]:
                ap = x["applied"]
                L.append(f"- **Applied:** {md(ap.get('where'))}, {when(ap.get('at'))}.")
            elif a:
                L.append("- **Applied:** not yet.")
            L.append(f"- *{md(x['id'])} · {md(x['step'])} · asked {when(x['opened_at'])}*")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------- main --

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("bad_request", message)

    def print_help(self, file=None):
        raise Help(self.format_help())


class Help(Exception):
    """--help: the usage, as the one JSON document every outcome is."""


def main(argv: list[str] | None = None) -> int:
    p = Parser(prog="digest.py", description=__doc__.split("\n")[0])
    p.add_argument("--console-dir", metavar="DIR", help="the console's folder (default: $CONSOLE_DIR; never guessed)")
    p.add_argument("--format", choices=("md", "json"), default="md")
    p.add_argument("--asks", nargs="+", default=[], metavar="FILE", help="the ask JSON that was posted")
    p.add_argument("--title", help="the heading (default: Decision record)")
    p.add_argument("--ask", default=ASK, metavar="PATH", help="the console's ask.py")
    try:
        a = p.parse_args(argv)
        folder = a.console_dir or os.environ.get("CONSOLE_DIR")
        if not folder:
            raise Refusal("no_dir", "name the console's folder with --console-dir or CONSOLE_DIR")
        d = collect(folder, a.ask, a.asks, a.title)
    except Help as h:
        sys.stdout.write(json.dumps({"ok": True, "help": str(h)}) + "\n")
        return 0
    except Refusal as e:
        sys.stdout.write(json.dumps(e.doc, ensure_ascii=False) + "\n")
        return 2
    if a.format == "json":
        sys.stdout.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_md(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
