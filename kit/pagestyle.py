"""One look for the pages a skill pack writes to guide making and to show the reasoning behind it (a plan's map, the next
batch, a make page, an insight page), so a person reads every such page the same way. Design and rules for any
skill pack: docs/page-style.md.

The rules the style carries, and the part that keeps each:

    a state is colour AND word AND icon     chip(state), vchip(state, label): five states (untested ○, testing ◐,
                                            promising ▲, down ▼, closed ✕: the states of kit.pathweights), colour-blind
                                            safe (Okabe-Ito blue for better, orange for worse, grey for closed), never
                                            colour alone; each state's text reads on its fill at 4.5:1 or more in the
                                            light theme and both dark ones, and nothing dims it (no opacity; the map's
                                            hover darkens a fill only as far as the text still reads 4.5:1)
    a status bar and number tiles first     bar(counts), legend(counts), tiles(items), before the decision; a count
                                            is a whole number of items (anything else refused)
    one decision per page                   decision(title, body, command, hint): the one thing a person decides, with
                                            ONE copy button and the hint where to paste it (a command without its hint
                                            refused); the page itself runs nothing. Commands, flags, ids and hashes
                                            appear only there (data-zone="decision") and in folds (data-zone="tech":
                                            details(), owner()), so a test reads the rest
    at most three warnings                  warnings(items): at most three shown on the page, all its boxes together;
                                            the rest one line that names the owner's fold, where they are
    a node map, one cell per item           node_map(tree, heads): columns of nodes linked left to right, the last
                                            column a waffle of cells (one per item, as many a line as the column's
                                            width holds, so none is past the map's edge), each a link with its title;
                                            a node with nothing under it says why. A cell without a title or a link,
                                            and a node with nothing under it and no why, are refused (ValueError)
    cards that unfold row by row            fold_row(...) around card(...): its numbers, ten dots (dots(p): "about n
                                            times in 10"), if it works / if it does not (story()), codes behind a fold;
                                            every card inside a row
    the owner's fold                        owner(inner): how it was worked out, folded, the last thing on the page;
                                            the more line names it by its title up to the colon, so `owner` and `more`
                                            are renamed together (wordset() refuses one that no longer names the other)
    one document                            page(title, body, data): doctype, lang, a CSP that allows no network
                                            (default-src 'none'; inline style and script; an image only inline, data:),
                                            light and dark tokens on :root, the page data as strict JSON for agents
                                            (NaN and Infinity refused; escaped: no `</`), a little vanilla JS that
                                            holds no word (copy buttons, expand all, a link opens the fold it points
                                            into, print opens every fold); filled in one pass, so a text that holds a
                                            placeholder's name stays text; no grid wider than a 320px phone's column

Every visible word is the skill pack's: Style(words, lang) lays a skill pack's words over EN (the defaults; ZH is a ready
Simplified Chinese set, ZH_HANT the same words in Traditional) and refuses an unknown key, an empty word, a template without its {n} or a more line that does
not name the owner's fold (ValueError, naming the key). The module-level functions are an English Style's. Nothing
here reads a file: a skill pack reads its own term table and passes the words.

problems(page) is the check a skill pack's own tests run on every page it writes, one line for each rule a page can
break: the skeleton (doctype, lang, the CSP, the style's stylesheet and script as they are, nothing loaded from
outside the page, exactly two scripts, the page data escaped and strict JSON), the skill pack's own CSS dimming or
recolouring no state, a status bar or number tiles before the decision, exactly one decision zone, at most one copy
button, only there and with its hint, no command, flag, id or hash in the visible text outside the zones (plus the
skill pack's own patterns), at most three warnings on the page, a more line that names the owner's fold, the owner's fold
folded and last, every card inside a row, a state's icon (and, given a Style, its word) on every chip, node and cell, a
title and a link on every cell and none past the map's edge, every in-page link pointing at something on the page, a
node with nothing under it saying why, no grid wider than a phone. What only the module's own code can break (the
colours' contrast, the one-pass fill, the script holding no word) its test checks. zones(page) is the visible text by
zone it reads.

Pure functions of their arguments, every text escaped here. Stdlib only.
Test: kit/tests/test_pagestyle.py.
"""

from __future__ import annotations

import html
import json
import math
import re
from html.parser import HTMLParser
from typing import Iterable, Mapping, Sequence

STATES = ("untested", "testing", "promising", "down", "closed")
ICON = {"untested": "○", "testing": "◐", "promising": "▲", "down": "▼", "closed": "✕"}
# the order a status bar, a legend line and a waffle show states in: what moves first, what is not made yet last
ORDER = ("promising", "testing", "down", "closed", "untested")
WARNINGS_MAX = 3
PHONE, GUTTER = 320, 16                         # the narrowest phone a page is read on, and the page's side gutter
NONE = "—"                                      # an amount that is not there

# (key, what it is): every word a page shows that the caller does not pass in
WORDS = (
    ("state_untested", "the untested state (nothing made on it yet)"),
    ("state_testing", "the testing state (made; the numbers cannot judge it yet)"),
    ("state_promising", "the promising state (likely better than the account)"),
    ("state_down", "the down state (likely worse: the next batch makes less of it)"),
    ("state_closed", "the closed state (a person closed it)"),
    ("sep", "between the parts of a legend line"),
    ("money", "an amount of money, {n} the number (the currency is the skill pack's: '¥{n}', '${n}', '{n} €')"),
    ("dots", "the line beside ten dots, {n} of them filled"),
    ("warnings", "the warnings box's title"),
    ("more", "the one line for the warnings not shown, {n} their count: it says where they are"),
    ("if_works", "a card's 'if it works' line"),
    ("if_fails", "a card's 'if it does not' line"),
    ("details", "the fold of a card's codes"),
    ("owner", "the owner's fold: how it was worked out"),
    ("copy", "the copy button"),
    ("copied", "the copy button, once copied"),
    ("selected", "the copy button when the browser refuses to copy: the command is selected"),
    ("expand_all", "the button that unfolds every row"),
    ("collapse_all", "the same button once every row is unfolded"),
)
TEMPLATES = ("money", "dots", "more")           # the words that must hold {n}

EN = {
    "state_untested": "untested", "state_testing": "testing", "state_promising": "promising", "state_down": "down",
    "state_closed": "closed",
    "sep": ", ",
    "money": "{n}",
    "dots": "about {n} times in 10 better than the account",
    "warnings": "Watch",
    "more": "{n} more, under “For the owner” at the foot of the page.",
    "if_works": "If it works",
    "if_fails": "If it does not",
    "details": "Details",
    "owner": "For the owner: how it was worked out",
    "copy": "Copy",
    "copied": "Copied",
    "selected": "Selected: press ⌘C or Ctrl+C",
    "expand_all": "Expand all",
    "collapse_all": "Collapse all",
}

ZH = {
    "state_untested": "未测", "state_testing": "在测", "state_promising": "有希望", "state_down": "降权",
    "state_closed": "关闭",
    "sep": "，",
    "money": "{n}",
    "dots": "10 次里约 {n} 次比账户平均好",
    "warnings": "留意",
    "more": "另有 {n} 条，在页尾“给负责人看”里。",
    "if_works": "如果行",
    "if_fails": "如果不行",
    "details": "详情",
    "owner": "给负责人看：怎么算的",
    "copy": "复制",
    "copied": "已复制",
    "selected": "已选中，按 ⌘C",
    "expand_all": "全部展开",
    "collapse_all": "全部收起",
}

ZH_HANT = {
    "state_untested": "未測", "state_testing": "在測", "state_promising": "有希望", "state_down": "降權",
    "state_closed": "關閉",
    "sep": "，",
    "money": "{n}",
    "dots": "10 次里約 {n} 次比賬戶平均好",
    "warnings": "留意",
    "more": "另有 {n} 條，在頁尾“給負責人看”裡。",
    "if_works": "如果行",
    "if_fails": "如果不行",
    "details": "詳情",
    "owner": "給負責人看：怎麼算的",
    "copy": "複製",
    "copied": "已複製",
    "selected": "已選中，按 ⌘C",
    "expand_all": "全部展開",
    "collapse_all": "全部收起",
}

_LANG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
_SLACK = 1e-9                                   # 0.3 x 10 is 3, not 3.0000000000000004


def fold_name(summary: str) -> str:
    """The owner's fold as a more line names it: its title up to the first colon (: or ：), or the whole title."""
    return re.split(r"[:：]", summary, maxsplit=1)[0].strip() or summary.strip()


def wordset(over: Mapping[str, str] | None = None) -> dict[str, str]:
    """EN with `over` laid over it. ValueError, naming the key: an unknown key, a word that is not a non-empty string,
    a template (TEMPLATES) without its {n}, a `more` that does not name the owner's fold (fold_name(owner)): the two
    are renamed together."""
    if over is not None and not isinstance(over, Mapping):
        raise ValueError(f"pagestyle words: a mapping of key -> word, not {type(over).__name__}")
    out = dict(EN)
    for k, v in (over or {}).items():
        if k not in EN:
            raise ValueError(f"pagestyle words: unknown key {k!r} (the keys: {', '.join(EN)})")
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"pagestyle words: {k} must be a word, not {v!r}")
        out[k] = v
    for k in TEMPLATES:
        if "{n}" not in out[k]:
            raise ValueError(f"pagestyle words: {k} must hold {{n}}, the number: {out[k]!r}")
    if fold_name(out["owner"]) not in out["more"]:
        raise ValueError(f"pagestyle words: more must name the owner's fold, {fold_name(out['owner'])!r} (owner up to "
                         f"its colon), where the warnings not shown are: rename owner and more together")
    return out


def _state(state) -> str:
    if state not in ICON:
        raise ValueError(f"pagestyle: unknown state {state!r} (one of {', '.join(STATES)})")
    return state


def _text(v) -> str:
    """A text a caller may leave None: None is empty."""
    return "" if v is None else str(v)


def _count(counts: Mapping[str, int], state: str) -> int:
    """counts[state] as a whole number of items, missing or None 0; ValueError for anything else, naming the state."""
    v = counts.get(state)
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"pagestyle counts: {state} must be a whole number of items, not {v!r}")
    return v


# ---- pure helpers: no word -----------------------------------------------------------

def e(s) -> str:
    """Text made safe for HTML, in an element or an attribute; None is empty."""
    return html.escape("" if s is None else str(s), quote=True)


def money(x, fmt: str = "{n}") -> str:
    """An amount of money, whole, with thousands separators, in `fmt` ({n} the number: '¥{n}'); — for none. A minus
    sign goes before the currency, and an amount that rounds to 0 has none."""
    if x is None:
        return NONE
    n = f"{abs(x):,.0f}"
    return ("-" if x < 0 and n.strip("0,") else "") + fmt.replace("{n}", n)


def amount(x) -> str:
    """A count that may be fractional (conversions): 3, 2.4, 0.6; a small fraction is never shown as 0 (<0.1); — for
    none."""
    if x is None:
        return NONE
    if 0 < abs(x) < 0.05:
        return "<0.1" if x > 0 else "-<0.1"
    return f"{x:.1f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else f"{x:.0f}"


def tenths(p: float) -> int:
    """P as "about n times in 10", rounded toward a half, so 0.79 reads 7 (not 8, the promising line) and 0.21 reads 3
    (not 2, the down line): a number shown never crosses the line its state was judged on (kit.pathweights.tenths)."""
    return math.floor(10 * p + _SLACK) if p >= 0.5 else math.ceil(10 * p - _SLACK)


def text_width(text: str, size: float) -> float:
    """About how wide `text` is at font `size` px: a CJK character (from U+2E80) a full em, any other 0.58 em."""
    return sum(size if ord(ch) > 0x2E7F else size * 0.58 for ch in text)


def fit(text: str, px: float, size: float) -> str:
    """`text` cut with … to fit `px` pixels at font `size` (text_width())."""
    if text_width(text, size) <= px:
        return text
    out = ""
    for ch in text:
        if text_width(out + ch + "…", size) > px:
            return out + "…"
        out += ch
    return out


def tiles(items: Iterable[Sequence[str]]) -> str:
    """Number tiles: [(big, label, small)], each a string (escaped here)."""
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><b>{e(big)}</b><span>{e(label)}<br><small>{e(small)}</small></span></div>'
        for big, label, small in items) + "</div>"


def details(summary: str, inner: str, cls: str = "tech") -> str:
    """A fold for codes and commands (data-zone="tech": the only place, beside the decision, they may appear). `inner`
    is HTML (the caller escapes)."""
    return f'<details class="{e(cls)}" data-zone="tech"><summary>{e(summary)}</summary>{inner}</details>'


def page_data(data) -> str:
    """Strict JSON that is safe inside <script type="application/json">: <, > and & escaped, so it never holds `</`.
    ValueError for data that is not JSON (NaN and Infinity among it: an agent's parser refuses them)."""
    try:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except ValueError as err:
        raise ValueError(f"pagestyle page data is not JSON: {err} (NaN and Infinity are refused: write None or a "
                         f"string)") from err
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---- the node map ---------------------------------------------------------------------

SQ, GAP = 13, 3                                 # a waffle cell and the gap between cells (px)


def node_map(tree: list[dict], heads: Sequence[str], widths: Sequence[int] | None = None, *, label: str = "") -> str:
    """An SVG of nodes in columns, linked left to right; the last column is a waffle of cells, one per item.

    tree: the first column's nodes. A node: {"label", "sub", "state" (one of STATES, or None: a neutral box), "href",
    "cls" (extra classes: held, blocked), "children": [the next column's nodes]}; a node of the second-to-last column
    carries "cells": [{"state", "href", "title", "proposal"}] (its waffle) and "caption" (the line above it). A node
    with no children before that column draws its "empty" text (why it has none) in a dashed box. A text left None is
    empty. A stated node's label starts with its state's icon, which fitting the label to its box never cuts. A waffle
    line holds as many cells as the waffle column's width does, so no cell is past the map's edge. ValueError for a
    cell without a title or a link (href), a node with nothing under it and no "empty", a waffle column narrower than a
    cell. `heads` names the columns (two or more); `widths` (one per column, in px) defaults to (156, 208, 188, 252)
    for four columns, else 200 a column and 252 for the waffle (15 cells a line)."""
    k = len(heads)
    if k < 2:
        raise ValueError("pagestyle node_map: at least two columns, the nodes and the waffle")
    widths = tuple(widths) if widths else ((156, 208, 188, 252) if k == 4 else tuple([200] * (k - 1) + [252]))
    if len(widths) != k:
        raise ValueError(f"pagestyle node_map: {len(widths)} widths for {k} columns")
    if any(isinstance(w, bool) or not isinstance(w, (int, float)) or not w > 0 for w in widths):
        raise ValueError(f"pagestyle node_map: each width a number of px above 0, not {widths!r}")
    if widths[-1] < SQ:
        raise ValueError(f"pagestyle node_map: the waffle column holds at least one cell ({SQ}px), not {widths[-1]}px")
    per_line = int((widths[-1] + GAP) // (SQ + GAP))                    # cells a waffle line: none past its width
    xs, x = [], 0
    for i, w in enumerate(widths):
        xs.append(x)
        x += w + (20 if i < k - 1 else 0)
    parts: list[str] = []
    links: list[tuple[float, float, float, float]] = []
    y = 0.0

    def box(node, depth, top, height, mid) -> None:
        st = node.get("state")
        cls = "node" + (f" s-{_state(st)}" if st else " plain") + "".join(f" {c}" for c in (node.get("cls") or "").split())
        w = widths[depth]
        icon = f"{ICON[st]} " if st else ""                              # never cut: only the label is fitted
        text = icon + fit(_text(node.get("label")), w - 24 - text_width(icon, 13), 13)
        inner = (f'<rect x="{xs[depth]}" y="{top:.1f}" width="{w}" height="{height:.1f}" rx="8"/>'
                 f'<text class="t1" x="{xs[depth] + 12}" y="{mid - 4:.1f}">{e(text)}</text>'
                 + (f'<text class="t2" x="{xs[depth] + 12}" y="{mid + 15:.1f}">{e(fit(_text(node["sub"]), w - 24, 12))}</text>'
                    if node.get("sub") else ""))
        g = f'<g class="{e(cls)}">{inner}</g>'
        parts.append(f'<a href="{e(node["href"])}">{g}</a>' if node.get("href") else g)

    def lay(node, depth) -> float:
        """Draw `node` (in column `depth`) and what is under it from y down; return its middle."""
        nonlocal y
        if depth == k - 2:                                               # a node with a waffle beside it
            cells = node.get("cells") or []
            lines = max(1, math.ceil(len(cells) / per_line))
            h = max(64, lines * (SQ + GAP) + 40)
            top, mid = y, y + h / 2
            box(node, depth, top + 6, h - 12, top + 6 + 21 + 4)
            wx, wy = xs[k - 1], top + 12
            parts.append(f'<text class="t2" x="{wx}" y="{wy + 4:.1f}">{e(fit(_text(node.get("caption")), widths[-1], 12))}</text>')
            for i, c in enumerate(cells):
                st = _state(c.get("state"))
                if not _text(c.get("title")).strip() or not _text(c.get("href")).strip():
                    raise ValueError(f"pagestyle node_map: cell {i} of {_text(node.get('label'))!r} has no "
                                     f"{'title' if not _text(c.get('title')).strip() else 'link (href)'}: every cell "
                                     f"says what it is and links to it")
                cx, cy = wx + (i % per_line) * (SQ + GAP), wy + 14 + (i // per_line) * (SQ + GAP)
                rect = (f'<rect class="sq s-{st}{" proposal" if c.get("proposal") else ""}" x="{cx}" '
                        f'y="{cy:.1f}" width="{SQ}" height="{SQ}" rx="2"><title>{e(c["title"])}</title></rect>')
                parts.append(f'<a href="{e(c["href"])}">{rect}</a>')
            y += h
            return mid
        kids = node.get("children") or []
        start = y
        mids = []
        if not kids:
            why = _text(node.get("empty"))
            if not why.strip():
                raise ValueError(f"pagestyle node_map: {_text(node.get('label'))!r} has nothing under it and does not "
                                 f"say why (its \"empty\")")
            h = 52
            mids.append(y + h / 2)
            parts.append(f'<g class="leaf empty"><rect x="{xs[depth + 1]}" y="{y + 6:.1f}" width="{widths[depth + 1]}" '
                         f'height="{h - 12}" rx="8"/><text x="{xs[depth + 1] + 12}" y="{y + h / 2 + 5:.1f}">'
                         f'{e(fit(why, widths[depth + 1] - 24, 12))}</text></g>')
            y += h
        for kid in kids:
            mids.append(lay(kid, depth + 1))
            y += 8 if depth + 1 < k - 2 else 0
        if kids and depth + 1 < k - 2:
            y -= 8
        mid = (start + y) / 2
        h = max(56, y - start - 12)
        box(node, depth, mid - h / 2, h, mid)
        for m in mids:
            links.append((xs[depth] + widths[depth], mid, xs[depth + 1], m))
        return mid

    for node in tree:
        lay(node, 0)
        y += 14
    curves = "".join(f'<path d="M{x1},{y1:.1f} C{(x1 + x2) / 2},{y1:.1f} {(x1 + x2) / 2},{y2:.1f} {x2},{y2:.1f}"/>'
                     for x1, y1, x2, y2 in links)
    width = xs[-1] + widths[-1]
    head = "".join(f'<text class="col" x="{xs[i]}" y="14">{e(t)}</text>' for i, t in enumerate(heads))
    return (f'<div class="scroll"><svg class="map" viewBox="0 -6 {width} {y + 24:.0f}" width="{width}" height="{y + 24:.0f}" '
            f'role="img" aria-label="{e(label)}">{head}<g transform="translate(0,26)"><g class="links">{curves}</g>'
            f'{"".join(parts)}</g></svg></div>')


# ---- the zones and the check -----------------------------------------------------------

class _Zones(HTMLParser):
    """Visible text by zone: inside an element with data-zone (nested <details> and all) its zone, else "rest";
    <script> and <style> are not visible text (an SVG <title>, a tooltip, is); attributes are not text. On the way it
    notes the shape problems() reads: the start tag a status bar or tiles and the decision first open at, the hints
    in the decision, the cards outside a row, each owner's fold (open or not) and whether anything follows one in
    <main>."""
    VOID = {"meta", "br", "input", "img", "link", "hr", "wbr", "source", "col", "area", "base", "embed", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, tuple[str, ...]]] = []
        self.text: dict[str, list[str]] = {"decision": [], "tech": [], "rest": []}
        self.hidden = 0
        self.counts: dict[str, int] = {"decision": 0, "tech": 0}
        self.copies: list[str] = []
        self.tags = 0                                   # start tags so far
        self.first: dict[str, int] = {}                 # "lead" (a bar or tiles), "decision": the tag it opens at
        self.hints = 0                                  # hints inside the decision
        self.loose_cards = 0                            # cards outside a row that unfolds
        self.owners: list[bool] = []                    # each owner's fold: open?
        self.owner_closed = False
        self.after_owner = False                        # something inside <main> after an owner's fold

    def zone(self) -> str:
        for _, z, _ in reversed(self.stack):
            if z:
                return z
        return "rest"

    def _within(self, tag: str, cls: str | None = None) -> bool:
        return any(t == tag and (cls is None or cls in c) for t, _, c in self.stack)

    def _open(self, tag: str, a: dict) -> tuple[tuple[str, ...], str | None]:
        """What a start tag tells, void or not."""
        self.tags += 1
        cls = tuple((a.get("class") or "").split())
        z = a.get("data-zone")
        if z:
            self.counts[z] = self.counts.get(z, 0) + 1
            if z == "decision":
                self.first.setdefault("decision", self.tags)
        if "bar" in cls or "tiles" in cls:
            self.first.setdefault("lead", self.tags)
        if self.owner_closed and self._within("main"):
            self.after_owner = True
        if tag == "article" and "card" in cls and not self._within("details", "row"):
            self.loose_cards += 1
        if tag == "details" and "owner" in cls:
            self.owners.append("open" in a)
        return cls, z

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls, z = self._open(tag, a)
        if tag in self.VOID:
            return
        if tag in ("script", "style"):
            self.hidden += 1
        self.stack.append((tag, z, cls))
        if tag == "button" and "data-copy" in a:
            self.copies.append(self.zone())
        if "hint" in cls and self.zone() == "decision":
            self.hints += 1

    def handle_startendtag(self, tag, attrs):
        self._open(tag, dict(attrs))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if any(t == "details" and "owner" in c for t, _, c in self.stack[i:]):
                    self.owner_closed = True
                del self.stack[i:]
                break
        if tag in ("script", "style"):
            self.hidden -= 1

    def handle_data(self, data):
        if self.owner_closed and data.strip() and self._within("main"):
            self.after_owner = True
        if not self.hidden:
            self.text.setdefault(self.zone(), []).append(data)


def _parse(page: str) -> _Zones:
    z = _Zones()
    z.feed(page)
    z.close()
    return z


def zones(page: str) -> dict:
    """{"decision", "tech", "rest": the visible text there, "counts": {zone: elements that open it}, "copies": [the
    zone of each copy button]}."""
    z = _parse(page)
    return {**{k: "\n".join(v) for k, v in z.text.items()}, "counts": z.counts, "copies": z.copies}


# what may appear only in the decision and the folds: a flag, a hash or hex id (either case), a snake_case code; each
# bounded by ASCII classes only, so one written straight after a CJK character is still seen
CODE_PATTERNS = (
    ("a flag", re.compile(r"(?<![A-Za-z0-9_-])--[a-z][a-z0-9-]*")),
    ("a hash or an id", re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,}(?![0-9A-Za-z])",
                                   re.I)),
    ("a code", re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])")),
)
_DATA_OPEN = '<script type="application/json" id="page-data">'
# what loads something from outside the page: a src, srcset or poster that is not data:; an SVG image, use or feImage
# whose href is neither in the page (#) nor data:; in the CSS a url() that is neither, or an @import
_LOAD_ATTR = re.compile(r"""<[A-Za-z][^>]*?\s(?:src|srcset|poster)\s*=\s*(?:"\s*(?!data:)[^"\s]|'\s*(?!data:)[^'\s]|"""
                        r"""(?!data:)[^\s"'>])""", re.I)
_LOAD_HREF = re.compile(r"""<(?:image|use|feimage)\b[^>]*?\s(?:xlink:)?href\s*=\s*["']?\s*(?!#|data:)[^\s"'>]""", re.I)
_LOAD_CSS = re.compile(r"""url\(\s*["']?\s*(?!data:|#)[^\s"')]|@import""", re.I)
# a state's text and its fill (the pairs the contrast is worked out on): a skill pack's CSS leaves them as they are
_STATE_TOKENS = ("ink", "u-f", "t-f", "p-f", "p-t", "d-f", "d-t", "c-f", "c-t")
_LENGTH = re.compile(r"(\d*\.?\d+)(px|r?em)", re.I)


def _split(s: str, seps: str) -> list[str]:
    """`s` split at the `seps` outside any parentheses."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in seps and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    return [p.strip() for p in out + [cur] if p.strip()]


def _grid_px(value: str) -> float:
    """At least how many px the columns of a grid-template-columns value take: its fixed tracks added up (px; em and
    rem at 16px), alone or inside repeat() (an auto-fill or auto-fit repeat once), minmax() and clamp() (the floor) or
    max(); a track that gives way to its container (fr, %, auto, min() with a %, fit-content(), calc()) adds 0."""
    total = 0.0
    for tok in _split(value, " \t\r\n\f"):
        m = re.fullmatch(r"([A-Za-z-]+)\((.*)\)", tok, re.S)
        if not m:
            n = _LENGTH.fullmatch(tok)
            total += float(n.group(1)) * (1 if n.group(2).lower() == "px" else 16) if n else 0
            continue
        fn, args = m.group(1).lower(), _split(m.group(2), ",")
        if fn == "repeat" and len(args) == 2:
            total += (int(args[0]) if args[0].isdigit() else 1) * _grid_px(args[1])
        elif fn in ("minmax", "clamp") and args:
            total += _grid_px(args[0])
        elif fn in ("max", "min") and args:
            total += (max if fn == "max" else min)(_grid_px(a) for a in args)
    return total


def _dims(css: str) -> list[str]:
    """Each declaration of `css` that dims or recolours a state's text: an opacity below 1, a filter, a state token."""
    out = [m.group(0) for m in re.finditer(r"(?<![\w-])opacity\s*:\s*(\d*\.?\d+)(%?)", css)
           if float(m.group(1)) / (100 if m.group(2) else 1) < 1]
    out += [m.group(0) for m in re.finditer(r"(?<![\w-])filter\s*:\s*(?!none\b)[^;}]*", css)]
    out += [m.group(0) for m in re.finditer(r"--(?:%s)\s*:[^;}]*" % "|".join(map(re.escape, _STATE_TOKENS)), css)]
    return out


def _plain(s: str) -> str:
    """The text of an HTML snippet: tags dropped, entities read."""
    return html.unescape(re.sub(r"<[^>]*>", "", s))


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def problems(page: str, *, commands: Iterable[str | re.Pattern] = (), style: "Style | None" = None) -> list[str]:
    """Each rule of the style `page` breaks, one line each (an empty list: it keeps them all; the module docstring and
    docs/page-style.md list them). `commands`: the skill pack's own patterns for a command (its CLI's name, say), looked
    for in the visible text outside the decision and the folds beside CODE_PATTERNS; `style`: the Style that wrote it,
    so the lang and each chip's and cell's word are checked too."""
    out: list[str] = []
    if not page.startswith("<!doctype html>"):
        out.append("the page does not start with <!doctype html>")
    lang = re.search(r'<html lang="([^"]*)"', page)
    if not lang or not lang.group(1):
        out.append("the <html> element has no lang")
    elif style is not None and lang.group(1) != style.lang:
        out.append(f"lang is {lang.group(1)!r}, the style's is {style.lang!r}")
    if f'<meta http-equiv="Content-Security-Policy" content="{CSP}">' not in page:
        out.append("the page does not carry the style's CSP (default-src 'none')")
    if f"<style>\n{CSS}" not in page:
        out.append("the page's stylesheet does not start with the style's own (CSS, as it is)")
    if f"<script>{SCRIPT}</script>" not in page:
        out.append("the style's script is not on the page as it is (SCRIPT)")
    low = page.lower()
    for tag in ("<form", "<link", "<iframe", "<object", "<embed", "<base"):
        if tag in low:
            out.append(f"{tag}> on the page: nothing is sent or loaded")
    sheets = re.findall(r"<style\b[^>]*>(.*?)</style>", page, re.S | re.I)
    attrs = [html.unescape(v) for _, v in re.findall(r"""<[A-Za-z][^>]*?\sstyle\s*=\s*(["'])(.*?)\1""", page, re.S)]
    css = "\n".join(sheets + attrs)
    if _LOAD_ATTR.search(page) or _LOAD_HREF.search(page) or _LOAD_CSS.search(css):
        out.append("a remote or relative src, href, url() or @import on the page: an image is inline (data:) or none")
    dims = _dims("\n".join([s.replace(CSS, "", 1) for s in sheets] + attrs))
    if dims:
        out.append(f"the skill pack's CSS dims or recolours a state's text: {dims[0]}")
    n = len(re.findall(r"<script\b", page, re.I))
    if n != 2:
        out.append(f"{n} scripts, not two (the page data and the style's script)")
    if _DATA_OPEN not in page:
        out.append("no page data (<script type=\"application/json\" id=\"page-data\">)")
    else:
        i = page.index(_DATA_OPEN) + len(_DATA_OPEN)
        raw = page[i:page.find("</script>", i)]
        if "<" in raw or ">" in raw or "&" in raw:
            out.append("the page data holds <, > or & as they are")
        try:
            json.loads(raw, parse_constant=_no_constant)
        except ValueError:
            out.append("the page data is not JSON (strict: no NaN or Infinity)")
    p = _parse(page)
    other = sorted(set(p.counts) - {"decision", "tech"})
    if other:
        out.append(f"a data-zone other than decision or tech: {', '.join(other)}")
    if p.counts.get("decision", 0) != 1:
        out.append(f"{p.counts.get('decision', 0)} decision zones, not one")
    lead, decide = p.first.get("lead"), p.first.get("decision")
    if lead is None or (decide is not None and lead > decide):
        out.append("no status bar or number tiles before the decision")
    if len(p.copies) > 1:
        out.append(f"{len(p.copies)} copy buttons, not at most one")
    if any(c != "decision" for c in p.copies):
        out.append("a copy button outside the decision")
    if "decision" in p.copies and not p.hints:
        out.append("a command without its hint, where to paste it")
    rest = "\n".join(p.text.get("rest", []))
    pats = list(CODE_PATTERNS) + [(f"a command ({c if isinstance(c, str) else c.pattern})",
                                   re.compile(c) if isinstance(c, str) else c) for c in commands]
    for what, pat in pats:
        hits = pat.findall(rest)
        if hits:
            out.append(f"{what} outside the decision and the folds: {', '.join(map(str, hits[:3]))}")
    shown = sum(box.count("<li>") for box in re.findall(r'<section class="warn">(.*?)</section>', page, re.S))
    if shown > WARNINGS_MAX:
        out.append(f"{shown} warnings shown on the page, at most {WARNINGS_MAX}")
    mores = [_plain(m) for m in re.findall(r'<li class="more">(.*?)</li>', page, re.S)]
    folds = [_plain(s) for s in re.findall(r'<details class="owner"[^>]*>\s*<summary>(.*?)</summary>', page, re.S)]
    if mores and not folds:
        out.append("a more line, but no owner's fold where the rest are")
    elif mores and any(fold_name(folds[0]) not in m for m in mores):
        out.append(f"a more line that does not name the owner's fold, {fold_name(folds[0])!r}")
    if len(p.owners) > 1:
        out.append(f"{len(p.owners)} owner's folds, not at most one")
    if any(p.owners):
        out.append("the owner's fold is open: it starts folded")
    if p.after_owner:
        out.append("something after the owner's fold: it is the last thing on the page")
    if p.loose_cards:
        out.append(f"cards outside a row that unfolds (fold_row()): {p.loose_cards}")
    for kind, st, body in re.findall(r'<span class="(v?chip) s-([a-z]+)[^"]*"[^>]*>(.*?)</span>', page, re.S):
        if st not in ICON:
            out.append(f"a {kind} of an unknown state {st!r}")
            continue
        if not body.startswith(f'<b aria-hidden="true">{ICON[st]}</b>'):
            out.append(f"a {kind} of state {st} without its icon {ICON[st]}")
        elif style is not None and e(style.word(st)) not in body:
            out.append(f"a {kind} of state {st} without its word {style.word(st)!r}")
    for st, text in re.findall(r'<g class="node s-([a-z]+)[^"]*"><rect[^>]*/><text class="t1"[^>]*>([^<]*)</text>', page):
        if st not in ICON or not text.startswith(f"{ICON[st]} "):
            out.append(f"a map node of state {st} without its icon")
    cells = re.findall(r'(<a href="[^"]*">)?<rect class="sq s-([a-z]+)[^"]*"[^>]*>(?:<title>(.*?)</title>)?', page,
                       re.S)
    untitled = sum(1 for _, _, t in cells if not _plain(t).strip())
    if untitled:
        out.append(f"{untitled} of {len(cells)} map cells without a title")
    unlinked = sum(1 for a, _, _ in cells if not a)
    if unlinked:
        out.append(f"{unlinked} of {len(cells)} map cells that are not a link")
    if style is not None:
        wordless = [st for _, st, t in cells if st in ICON and _plain(t).strip() and style.word(st) not in _plain(t)]
        if wordless:
            out.append(f"a map cell of state {wordless[0]} whose title does not hold its word "
                       f"{style.word(wordless[0])!r}")
    past = sum(1 for width, svg in re.findall(r'<svg class="map" viewBox="\S+ \S+ ([\d.]+) [\d.]+"[^>]*>(.*?)</svg>',
                                              page, re.S)
               for x, w in re.findall(r'<rect class="sq [^"]*" x="([\d.]+)"[^>]*? width="([\d.]+)"', svg)
               if float(x) + float(w) > float(width))
    if past:
        out.append(f"map cells past the map's edge: {past}")
    mute = sum(1 for g in re.findall(r'<g class="leaf empty">(.*?)</g>', page, re.S) if not _plain(g).strip())
    if mute:
        out.append(f"map nodes with nothing under them that do not say why: {mute}")
    ids = {html.unescape(i) for i in re.findall(r'\sid="([^"]*)"', page)}
    dangling = [h for h in (html.unescape(h) for h in re.findall(r'\shref="#([^"]*)"', page)) if h and h not in ids]
    if dangling:
        out.append(f"a link to #{dangling[0]}, and nothing on the page has that id")
    for v in re.findall(r"grid-template-columns\s*:\s*([^;}]*)", css):
        if _grid_px(v) > PHONE - 2 * GUTTER:
            out.append(f"a grid wider than a {PHONE}px phone's column ({PHONE - 2 * GUTTER}px): grid-template-columns:"
                       f"{v.strip()} takes {_grid_px(v):g}px")
            break
    return out


# ---- the style: the parts that show words ------------------------------------------------

class Style:
    """The page style with a skill pack's words (laid over EN: wordset()) and its pages' lang. Every name the module
    exports (__all__) but Style and DEFAULT is here too, so a Style stands in for the module: the parts that show a
    word are its methods (money(x) in its own template), the pure helpers and the constants its attributes, and its
    problems() checks its own words and lang."""

    STATES, ICON, ORDER, WARNINGS_MAX, PHONE, GUTTER, NONE = STATES, ICON, ORDER, WARNINGS_MAX, PHONE, GUTTER, NONE
    WORDS, TEMPLATES, EN, ZH, ZH_HANT, CODE_PATTERNS = WORDS, TEMPLATES, EN, ZH, ZH_HANT, CODE_PATTERNS
    e, amount, tenths, fit, text_width = (staticmethod(e), staticmethod(amount), staticmethod(tenths), staticmethod(fit),
                                          staticmethod(text_width))
    tiles, details, node_map, page_data, zones = (staticmethod(tiles), staticmethod(details), staticmethod(node_map),
                                                  staticmethod(page_data), staticmethod(zones))
    wordset, fold_name = staticmethod(wordset), staticmethod(fold_name)

    def __init__(self, words: Mapping[str, str] | None = None, lang: str = "en"):
        if not isinstance(lang, str) or not _LANG.fullmatch(lang):
            raise ValueError(f"pagestyle lang: a language tag (en, zh-CN), not {lang!r}")
        self.words = wordset(words)
        self.lang = lang

    def word(self, state: str) -> str:
        return self.words[f"state_{_state(state)}"]

    def money(self, x) -> str:
        return money(x, self.words["money"])

    # ---- small parts

    def chip(self, state: str, extra: str = "", proposal: bool = False) -> str:
        """A state as colour, icon and word; `extra` (text) after the word; a proposal dashed."""
        cls = f"chip s-{_state(state)}" + (" proposal" if proposal else "")
        return f'<span class="{cls}"><b aria-hidden="true">{ICON[state]}</b>{e(self.word(state))}{e(extra)}</span>'

    def vchip(self, state: str, label: str, note: str = "") -> str:
        """A value in its state's colour: icon, `label`, then `note` and the state's word, small."""
        w = self.word(state)
        return (f'<span class="vchip s-{state}" title="{e(w)}"><b aria-hidden="true">{ICON[state]}</b>{e(label)}'
                f'<small>{e(f"{note} · {w}" if note else w)}</small></span>')

    def legend_text(self, counts: Mapping[str, int]) -> str:
        """The states with a count, in ORDER: "promising 2, testing 6". A count is a whole number of items (missing or
        None: 0); anything else is refused (ValueError), as in bar() and legend()."""
        n = {s: _count(counts, s) for s in ORDER}
        return self.words["sep"].join(f"{self.word(s)} {n[s]}" for s in ORDER if n[s])

    def bar(self, counts: Mapping[str, int], big: bool = False) -> str:
        """A status bar: one segment per state with a count, in ORDER, each growing by its count."""
        n = {s: _count(counts, s) for s in ORDER}
        segs = "".join(f'<i class="s-{s}" style="flex-grow:{n[s]}" title="{e(self.word(s))} {n[s]}"></i>'
                       for s in ORDER if n[s])
        return f'<span class="bar{" big" if big else ""}" role="img" aria-label="{e(self.legend_text(counts))}">{segs}</span>'

    def legend(self, counts: Mapping[str, int]) -> str:
        """Every state's chip with its count (0 too)."""
        return '<div class="legend">' + "".join(f'<span class="lg">{self.chip(s)}<small>{_count(counts, s)}</small></span>'
                                                for s in STATES) + "</div>"

    def dots(self, p, text: str | None = None) -> str:
        """Ten dots, tenths(p) of them filled, and the line that says so (`text`, {n} the count; words["dots"]);
        nothing for no p."""
        if p is None:
            return ""
        n = tenths(p)
        line = (self.words["dots"] if text is None else text).replace("{n}", str(n))
        return (f'<span class="dots" role="img" aria-label="{e(line)}">'
                + "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(10)) + f'</span><span>{e(line)}</span>')

    def decision(self, title: str, body: str, command: str | None = None, hint: str | None = None,
                 cmd_id: str = "cmd1") -> str:
        """The page's one decision (data-zone="decision"). `body` is HTML (the caller escapes); the command is shown in
        full with ONE copy button (its words, its own label among them, in its own attributes), and `hint` says where
        to paste it: a command without its hint is refused (ValueError). No command: no button."""
        cmd = ""
        if command:
            if not _text(hint).strip():
                raise ValueError("pagestyle decision: a command needs its hint, where to paste it")
            w = self.words
            cmd = (f'<div class="cmd"><code id="{e(cmd_id)}">{e(command)}</code>'
                   f'<button type="button" data-copy="{e(cmd_id)}" data-label="{e(w["copy"])}" '
                   f'data-done="{e(w["copied"])}" data-select="{e(w["selected"])}">{e(w["copy"])}</button></div>'
                   f'<p class="hint">{e(hint)}</p>')
        return f'<section class="decide" data-zone="decision"><h2>{e(title)}</h2>{body}{cmd}</section>'

    def warnings(self, items: Sequence[str], limit: int = WARNINGS_MAX, more: str | None = None) -> str:
        """At most `limit` (1 to WARNINGS_MAX) warnings (HTML items, the caller escapes); the rest are one line (`more`,
        {n} their count; words["more"]) that names the owner's fold, and the caller shows them there. Nothing to warn:
        no box. A page shows at most WARNINGS_MAX in all its boxes together (problems())."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= WARNINGS_MAX:
            raise ValueError(f"pagestyle warnings: a limit of 1 to {WARNINGS_MAX}, not {limit!r}")
        if not items:
            return ""
        line = self.words["more"] if more is None else more
        rest = (f'<li class="more">{e(line.replace("{n}", str(len(items) - limit)))}</li>'
                if len(items) > limit else "")
        return (f'<section class="warn"><h2>{e(self.words["warnings"])}</h2><ul>'
                f'{"".join(f"<li>{x}</li>" for x in items[:limit])}{rest}</ul></section>')

    def story(self, works: str | None, fails: str | None) -> str:
        """What happens next if it works, and if it does not (text); nothing for neither."""
        lines = []
        if works:
            lines.append(f'<li class="ok"><b>{e(self.words["if_works"])}</b><span>{e(works)}</span></li>')
        if fails:
            lines.append(f'<li class="bad"><b>{e(self.words["if_fails"])}</b><span>{e(fails)}</span></li>')
        return f'<ul class="story">{"".join(lines)}</ul>' if lines else ""

    def owner(self, inner: str, summary: str | None = None) -> str:
        """The owner's fold, folded, the last thing on the page: how it was worked out (`inner`, HTML), and the warnings
        that did not fit. `summary` replaces its title on this page; a more line names the title up to its colon
        (fold_name()), so a page with one keeps the two in step (problems())."""
        s = self.words["owner"] if summary is None else summary
        return f'<details class="owner" data-zone="tech"><summary>{e(s)}</summary><div class="ow">{inner}</div></details>'

    def card(self, state: str, *, anchor: str, head: str = "", combo: str = "", nums: Sequence[str] = (), p=None,
             works: str | None = None, fails: str | None = None, note: str = "", tech: str = "",
             proposal: bool = False, proposal_word: str = "") -> str:
        """One unfolded item, inside a row (fold_row()'s body): its state chip (`proposal_word` after the word), `head`
        (HTML) beside it, `combo` (HTML: what it is made of), its numbers (text), ten dots for p, if it works / if it
        does not, a note (HTML) and its codes behind a fold (`tech`, HTML)."""
        num = f'<p class="nums">{" · ".join(e(x) for x in nums)}</p>' if nums else ""
        judge = f'<p class="judge">{self.dots(p)}</p>' if p is not None else ""
        return (f'<article class="card s-{_state(state)}{" proposal" if proposal else ""}" id="{e(anchor)}">'
                f'<header>{self.chip(state, proposal_word, proposal)}{head}</header>'
                + (f'<div class="combo">{combo}</div>' if combo else "") + num + judge + note + self.story(works, fails)
                + (details(self.words["details"], f"<p>{tech}</p>") if tech else "") + "</article>")

    def fold_row(self, state: str, *, anchor: str, title: str, sub: str = "", right: str = "", body: str = "",
                 opened: bool = False) -> str:
        """A row of the page that unfolds: chip, title and sub (text), `right` (HTML: counts and a bar), `body`
        (HTML: its cards)."""
        return (f'<details class="row"{" open" if opened else ""} id="{e(anchor)}"><summary>{self.chip(state)}'
                f'<span class="rt"><b>{e(title)}</b><span class="muted">{e(sub)}</span></span>'
                f'<span class="rn">{right}</span></summary><div class="rbody">{body}</div></details>')

    def expand_all(self) -> str:
        """The button that unfolds every row and folds them again (its two words in its own attributes)."""
        w = self.words
        return (f'<button type="button" data-expand="rows" data-more="{e(w["expand_all"])}" '
                f'data-less="{e(w["collapse_all"])}">{e(w["expand_all"])}</button>')

    # ---- the page

    def page(self, title: str, body: str, data=None, *, extra_css: str = "") -> str:
        """The whole document: `body` (HTML) inside <main>, `data` as the page data (JSON) for agents. One pass over
        the template, so a text that holds a placeholder's name is never filled in. `extra_css` is the skill pack's own
        (it may not close the style element)."""
        if "</" in extra_css:
            raise ValueError("pagestyle page: extra_css may not hold </")
        fill = {"LANG": e(self.lang), "TITLE": e(title), "CSS": CSS + extra_css, "BODY": body,
                "DATA": page_data(data if data is not None else {}), "SCRIPT": SCRIPT}
        return re.sub(r"__(LANG|TITLE|CSS|BODY|DATA|SCRIPT)__", lambda m: fill[m.group(1)], PAGE)

    def problems(self, page: str, *, commands: Iterable[str | re.Pattern] = ()) -> list[str]:
        """problems(page, commands=commands, style=self): this style's words and lang checked too."""
        return problems(page, commands=commands, style=self)


# no network at all: nothing is fetched, the page's own host included ('self' is never allowed). An image may only be
# inline (data:, which makes no request), so a make page can show a frame of a reference in the one document.
CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "base-uri 'none'; form-action 'none'")

PAGE = """<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="Content-Security-Policy" content=\"""" + CSP + """\">
<title>__TITLE__</title>
<style>
__CSS__
</style></head><body>
<main>__BODY__</main>
<script type="application/json" id="page-data">__DATA__</script>
<script>__SCRIPT__</script>
</body></html>
"""

# a planning board: blue for what looks better (Okabe-Ito), orange for what looks worse, grey for what a person closed;
# a state's text token on its fill: untested and testing --ink, promising --p-t, down --d-t, closed --c-t. Nothing
# dims a state's text (no opacity); the map's hover darkens a fill (brightness .92) only as far as its text still reads
# 4.5:1, which the test works out
CSS = """:root{--bg:#F2F4F6;--paper:#FFFFFF;--ink:#15202B;--muted:#5A6774;--rule:#D8DEE5;--soft:#E9EDF1;--accent:#0B5CAD;
--u-f:#FFFFFF;--u-l:#9AA4AF;--t-f:#DCEBF7;--t-l:#4A9FD8;--p-f:#0072B2;--p-l:#005286;--p-t:#FFFFFF;--d-f:#F0B43C;--d-l:#B07A00;--d-t:#2B1D00;
--c-f:#5D636B;--c-l:#3D4247;--c-t:#FFFFFF;--link:#B9C2CC;--u-bar:#E2E7EC;
--f-body:system-ui,-apple-system,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
--f-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0E141A;--paper:#151D25;--ink:#E3E9EF;--muted:#94A2AF;
--rule:#2A3541;--soft:#1B2530;--accent:#6CB4F0;--u-f:#151D25;--u-l:#5C6875;--t-f:#163049;--t-l:#56B4E9;--p-f:#2F8FD3;--p-l:#7CC0F0;
--p-t:#071420;--d-f:#E69F00;--d-l:#FFC24D;--d-t:#1E1400;--c-f:#6E747C;--c-l:#9AA0A8;--c-t:#FFFFFF;--link:#3A4653;--u-bar:#2C3743;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0E141A;--paper:#151D25;--ink:#E3E9EF;--muted:#94A2AF;--rule:#2A3541;--soft:#1B2530;--accent:#6CB4F0;
--u-f:#151D25;--u-l:#5C6875;--t-f:#163049;--t-l:#56B4E9;--p-f:#2F8FD3;--p-l:#7CC0F0;--p-t:#071420;--d-f:#E69F00;--d-l:#FFC24D;
--d-t:#1E1400;--c-f:#6E747C;--c-l:#9AA0A8;--c-t:#FFFFFF;--link:#3A4653;--u-bar:#2C3743;color-scheme:dark}
*{box-sizing:border-box} html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 var(--f-body);overflow-wrap:anywhere}
main{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:24px 56px;display:grid;gap:20px}
h1,h2,h3,h4{margin:0;line-height:1.3;text-wrap:balance} h1{font-size:clamp(24px,4.4vw,34px)} h2{font-size:19px} h4{font-size:16px}
p{margin:0} .muted{color:var(--muted)} small{font-size:12px} code{font:12.5px var(--f-mono);background:var(--soft);padding:1px 5px;border-radius:4px}
.eyebrow{font-size:13px;letter-spacing:.08em;color:var(--muted);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.syn{font-size:12px;letter-spacing:0;padding:1px 8px;border-radius:999px;border:1px dashed var(--muted)}
section,.space{background:var(--paper);border:1px solid var(--rule);border-radius:12px;padding:18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{background:var(--paper);border:1px solid var(--rule);border-radius:12px;padding:14px 16px;display:flex;gap:12px;align-items:center}
.tile b{font-size:28px;font-variant-numeric:tabular-nums;line-height:1;white-space:nowrap;flex:none} .tile span{font-size:14px;line-height:1.4}
.tile small{color:var(--muted)}
.space{display:grid;gap:10px}
.bar{display:inline-flex;height:10px;width:120px;border-radius:5px;overflow:hidden;background:var(--soft);vertical-align:middle;border:1px solid var(--rule)}
.bar.big{width:100%;height:22px;border-radius:6px}
.bar i{display:block;min-width:2px} .bar i.s-untested{background:var(--u-bar)} .bar i.s-testing{background:var(--t-l)}
.bar i.s-promising{background:var(--p-f)} .bar i.s-down{background:var(--d-f)} .bar i.s-closed{background:var(--c-f)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px} .lg{display:inline-flex;gap:6px;align-items:center} .lg small{font-variant-numeric:tabular-nums}
.chip{display:inline-flex;align-items:center;gap:4px;font-size:13px;padding:1px 9px;border-radius:999px;border:1.5px solid;white-space:nowrap}
.chip b{font-size:11px}
.s-untested.chip{background:var(--u-f);border-color:var(--u-l);color:var(--ink)}
.s-testing.chip{background:var(--t-f);border-color:var(--t-l);color:var(--ink)}
.s-promising.chip{background:var(--p-f);border-color:var(--p-l);color:var(--p-t)}
.s-down.chip{background:var(--d-f);border-color:var(--d-l);color:var(--d-t)}
.s-closed.chip{background:var(--c-f);border-color:var(--c-l);color:var(--c-t);text-decoration:line-through}
.chip.proposal{border-style:dashed;border-color:var(--ink)}
.decide{border:2px solid var(--d-l);display:grid;gap:10px}
.decide .dq{font-size:15px}
.cmd{display:flex;gap:8px;align-items:stretch;flex-wrap:wrap} .cmd code{flex:1 1 320px;min-width:0;padding:10px;display:block;white-space:pre-wrap;word-break:break-all}
button{font:inherit;font-size:14px;padding:8px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--paper);cursor:pointer}
button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--t-l);outline-offset:2px}
.hint{font-size:13px;color:var(--muted)}
.batch{display:grid;gap:10px} .moves{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.move{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start;padding:10px 12px;border-radius:10px;background:var(--soft)}
.move .mk{font-size:12px;padding:2px 8px;border-radius:6px;white-space:nowrap}
.move.known .mk{background:var(--p-f);color:var(--p-t)} .move.maybe .mk{background:var(--t-f);border:1px solid var(--t-l)}
.move.wait .mk{background:var(--paper);border:1px dashed var(--muted)}
.move div{display:grid;gap:2px;min-width:0} .move small{color:var(--muted)}
.warn ul{margin:8px 0 0;padding-left:20px;display:grid;gap:8px}
.warn a{color:var(--accent)} .warn .more{list-style:none;margin-left:-20px;color:var(--muted);font-size:13px}
.mapbox{display:grid;gap:8px} .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border-top:1px solid var(--rule);padding-top:8px}
svg.map{display:block;font-family:var(--f-body)}
svg .col{font-size:12px;fill:var(--muted);letter-spacing:.06em}
svg .links path{fill:none;stroke:var(--link);stroke-width:1.5}
svg .t1{font-size:13px;font-weight:600;fill:var(--ink)} svg .t2{font-size:12px;fill:var(--muted)}
svg .node rect{stroke-width:1.5} svg .node.plain rect{fill:var(--soft);stroke:var(--rule)} svg .node.held rect{stroke-dasharray:4 3}
svg .node.s-untested rect{fill:var(--u-f);stroke:var(--u-l)} svg .node.s-testing rect{fill:var(--t-f);stroke:var(--t-l)}
svg .node.s-promising rect{fill:var(--p-f);stroke:var(--p-l)} svg .node.s-down rect{fill:var(--d-f);stroke:var(--d-l)}
svg .node.s-closed rect{fill:var(--c-f);stroke:var(--c-l)}
svg .node.s-promising text{fill:var(--p-t)} svg .node.s-closed text{fill:var(--c-t)} svg .node.s-down text{fill:var(--d-t)}
svg .node.blocked rect{stroke-dasharray:5 4}
svg .leaf.empty rect{fill:none;stroke:var(--u-l);stroke-dasharray:5 4} svg .leaf.empty text{font-size:12px;fill:var(--muted)}
svg .sq{stroke-width:1.2} svg .sq.s-untested{fill:var(--u-f);stroke:var(--u-l)} svg .sq.s-testing{fill:var(--t-f);stroke:var(--t-l)}
svg .sq.s-promising{fill:var(--p-f);stroke:var(--p-l)} svg .sq.s-down{fill:var(--d-f);stroke:var(--d-l)} svg .sq.s-closed{fill:var(--c-f);stroke:var(--c-l)}
svg .sq.proposal{stroke:var(--ink);stroke-width:1.6;stroke-dasharray:2 1.5}
svg a:hover rect{filter:brightness(.92)}
.rows{display:grid;gap:10px;background:none;border:0;padding:0} .rowhead{display:flex;justify-content:space-between;align-items:center;gap:12px}
.rowhead button{background:var(--paper);color:var(--accent)}
details.row{background:var(--paper);border:1px solid var(--rule);border-radius:12px}
details.row>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;padding:12px 16px}
details.row>summary::-webkit-details-marker{display:none}
.rt{display:grid;min-width:0} .rt .muted{font-size:13px} .rn{font-size:13px;color:var(--muted);display:grid;gap:4px;justify-items:end;text-align:right}
.rbody{padding:0 16px 16px;display:grid;gap:14px}
.group{border:1px solid var(--rule);border-radius:10px;padding:14px;display:grid;gap:12px;background:var(--bg)}
.group>header{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center} .group h4 span{font-weight:400;color:var(--muted)}
.vars{display:grid;gap:6px} .var{display:grid;grid-template-columns:7.5em 1fr;gap:8px;align-items:start}
.vname{font-size:13px;color:var(--muted);display:grid} .vname em{font-style:normal;font-size:11px;color:var(--accent)}
.vals{display:flex;flex-wrap:wrap;gap:6px}
.vchip{display:inline-flex;gap:6px;align-items:baseline;font-size:13px;padding:2px 10px;border-radius:8px;border:1.5px solid}
.vchip.s-untested{background:var(--u-f);border-color:var(--u-l)} .vchip.s-testing{background:var(--t-f);border-color:var(--t-l)}
.vchip.s-promising{background:var(--p-f);border-color:var(--p-l);color:var(--p-t)} .vchip.s-down{background:var(--d-f);border-color:var(--d-l);color:var(--d-t)}
.vchip.s-closed{background:var(--c-f);border-color:var(--c-l);color:var(--c-t)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr));gap:10px}
.card{background:var(--paper);border:1px solid var(--rule);border-left:6px solid var(--u-l);border-radius:10px;padding:12px 14px;display:grid;gap:6px;min-width:0}
.card.s-testing{border-left-color:var(--t-l)} .card.s-promising{border-left-color:var(--p-f)} .card.s-down{border-left-color:var(--d-f)}
.card.s-closed{border-left-color:var(--c-f)} .card.proposal{border:2px dashed var(--d-l);border-left:6px solid var(--d-f)}
.card header{display:flex;gap:8px;align-items:center;flex-wrap:wrap} .card .why{font-size:13px;color:var(--muted)}
.combo{display:flex;flex-wrap:wrap;gap:4px 10px;font-weight:600;font-size:14px} .pv small{font-weight:400;color:var(--muted);margin-right:4px}
.nums{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.judge{display:flex;gap:8px;align-items:center;font-size:13px}
.dots{display:inline-flex;gap:3px} .dots i{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--p-f)} .dots i.on{background:var(--p-f)}
.story{margin:0;padding:0;list-style:none;display:grid;grid-template-columns:auto 1fr;gap:4px 6px;font-size:13.5px}
.story li{display:grid;grid-column:1/-1;grid-template-columns:auto 1fr;grid-template-columns:subgrid;gap:6px}
.story b{font-size:12px;padding:1px 6px;border-radius:5px;height:fit-content;text-align:center}
.story .ok b{background:var(--p-f);color:var(--p-t)} .story .bad b{background:var(--d-f);color:var(--d-t)}
.note{font-size:13px;color:var(--muted)}
details.tech summary,details.todo summary{cursor:pointer;font-size:13px;color:var(--accent)} details.tech p{font-size:12.5px;color:var(--muted)}
details.todo ul{margin:6px 0 0;padding-left:18px;columns:2 280px;font-size:13px;color:var(--muted)}
details.owner{background:var(--paper);border:1px solid var(--rule);border-radius:12px;padding:14px 18px}
details.owner summary{cursor:pointer;font-weight:600} .ow{display:grid;gap:8px;margin-top:10px;font-size:14px;max-width:72ch}
@media (max-width:640px){details.row>summary{grid-template-columns:auto 1fr} .rn{grid-column:1/-1;justify-items:start;text-align:left}
.var{grid-template-columns:1fr} .tile b{font-size:24px}}
@media print{details{display:block} .scroll{overflow:visible} *{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

# no word: a button's words are its own data attributes (decision(), expand_all()), so one script serves any language.
# A copy button's label is its data-label (taken from its text on the first click when missing), never the text it
# shows at the time: a second click while it says "Copied" still comes back to "Copy", and so does the fallback (the
# command selected) after a while; each new word cancels the pending return of the last
SCRIPT = """
document.addEventListener('click',function(ev){var b=ev.target.closest('button[data-copy]');if(!b)return;var el=document.getElementById(b.getAttribute('data-copy'));if(!el)return;
if(!b.hasAttribute('data-label'))b.setAttribute('data-label',b.textContent);var t=el.textContent,w=b.getAttribute('data-label');
var show=function(k,ms){b.textContent=b.getAttribute(k)||w;clearTimeout(b.copyBack);b.copyBack=setTimeout(function(){b.textContent=w},ms)};
var sel=function(){var r=document.createRange();r.selectNodeContents(el);var s=window.getSelection();s.removeAllRanges();s.addRange(r);show('data-select',4000)};
try{navigator.clipboard.writeText(t).then(function(){show('data-done',1600)},sel)}catch(e){sel()}});
document.addEventListener('click',function(ev){var all=ev.target.closest('button[data-expand]');if(!all)return;var open=all.getAttribute('data-open')!=='1';
document.querySelectorAll('details.row').forEach(function(d){d.open=open});all.setAttribute('data-open',open?'1':'0');all.textContent=all.getAttribute(open?'data-less':'data-more')});
document.addEventListener('click',function(ev){var a=ev.target.closest('a[href^="#"]');if(!a)return;var t=document.getElementById(a.getAttribute('href').slice(1));if(!t)return;
var p=t;while(p){if(p.tagName==='DETAILS')p.open=true;p=p.parentElement}});
window.addEventListener('beforeprint',function(){document.querySelectorAll('details').forEach(function(d){d.open=true})});
"""

Style.CSS, Style.CSP, Style.SCRIPT, Style.PAGE = CSS, CSP, SCRIPT, PAGE

# the module's own parts are an English style's
DEFAULT = Style()
word, legend_text, chip, vchip, bar, legend, dots = (DEFAULT.word, DEFAULT.legend_text, DEFAULT.chip, DEFAULT.vchip,
                                                     DEFAULT.bar, DEFAULT.legend, DEFAULT.dots)
decision, warnings, story, owner, card, fold_row = (DEFAULT.decision, DEFAULT.warnings, DEFAULT.story, DEFAULT.owner,
                                                    DEFAULT.card, DEFAULT.fold_row)
expand_all, page = DEFAULT.expand_all, DEFAULT.page

__all__ = ["STATES", "ICON", "ORDER", "WARNINGS_MAX", "PHONE", "GUTTER", "NONE", "WORDS", "TEMPLATES", "EN", "ZH", "ZH_HANT",
           "CSS", "CSP", "SCRIPT", "PAGE", "CODE_PATTERNS", "Style", "DEFAULT", "wordset", "fold_name", "e", "money",
           "amount", "tenths", "text_width", "fit", "tiles", "details", "page_data", "node_map", "zones", "problems",
           "word", "legend_text", "chip", "vchip", "bar", "legend", "dots", "decision", "warnings", "story", "owner",
           "card", "fold_row", "expand_all", "page"]
