"""pages.py: what each page draws and, as much, what it refuses to draw.

Both languages, every step, the group button rule, read-only, a gate without
a relay and what a gated choice would write, escaping of every agent-supplied
field (each carries a `<script>` payload with quotes), links only http(s),
history states, the unsigned chip, the budget line, ages, the poll attributes,
and what a refusal gives back. Fixtures are hand-made event logs (no clock, no
folder), so every time is fixed; one test goes through a real Store. `Doc`,
`full_log`, `ctx` and friends are shared with test_ui_rules.py.
"""

from __future__ import annotations

import html
import os
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import _t  # noqa: E402
import core  # noqa: E402
import i18n  # noqa: E402
import pages  # noqa: E402

LANGS = i18n.LANGS
SECRET = b"s" * 32
T0 = "2026-09-28T10:00:00Z"
NOW = "2026-09-28T12:05:00Z"          # 2 h 5 min after T0


# ------------------------------------------------------------ a tiny DOM --

class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.parent, self.kids = tag, parent, []
        self.attrs = {k: (v if v is not None else "") for k, v in attrs}

    @property
    def classes(self):
        return set(self.attrs.get("class", "").split())

    def walk(self):
        for k in self.kids:
            if isinstance(k, Node):
                yield k
                yield from k.walk()

    def find_all(self, tag=None, cls=None, **attrs):
        out = []
        for n in self.walk():
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes:
                continue
            if any(n.attrs.get(a.rstrip("_")) != v for a, v in attrs.items()):
                continue
            out.append(n)
        return out

    def find(self, tag=None, cls=None, **attrs):
        got = self.find_all(tag, cls, **attrs)
        return got[0] if got else None

    def text(self):
        """Visible text, whitespace collapsed, a space between elements."""
        return " ".join(" ".join(k if isinstance(k, str) else k.text() for k in self.kids).split())

    def texts(self):
        for k in self.kids:
            if isinstance(k, str):
                yield k
            else:
                yield from k.texts()

    def inside(self, tag=None, cls=None):
        n = self.parent
        while n:
            if (not tag or n.tag == tag) and (not cls or cls in n.classes):
                return True
            n = n.parent
        return False

    def without(self, cls):
        """The texts of this node, skipping any subtree with class `cls`."""
        for k in self.kids:
            if isinstance(k, str):
                yield k
            elif cls not in k.classes:
                yield from k.without(cls)


class _Parser(HTMLParser):
    VOID = {"meta", "link", "input", "br", "hr", "img"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#root")

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        if tag not in self.VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


class Doc(Node):
    def __init__(self, source: str):
        p = _Parser()
        p.feed(source)
        p.close()
        super().__init__("#doc")
        self.kids = p.root.kids
        self.source = source

    @property
    def main(self):
        return self.find("main")


# -------------------------------------------------------------- fixtures --

class Log:
    """A hand-made event log; human events are signed with SECRET."""

    def __init__(self):
        self.events = []

    def add(self, type, by, **fields):
        e = {"seq": len(self.events) + 1, "at": T0, "by": by, "type": type,
             **{k: v for k, v in fields.items() if v is not None}}
        if type in core.ROLES["human"]:
            e["sig"] = core.sign(SECRET, e)
        self.events.append(e)
        return e

    def ask(self, raw, by="agent:bot"):
        ask, errors = core.validate_ask(raw)
        assert not errors, errors
        self.add("ask", by, ask=ask)
        return ask

    def raw_ask(self, ask, by="agent:bot"):
        """An ask event that skipped validation (a log edited by hand)."""
        self.add("ask", by, ask=ask)
        return ask

    def state(self):
        return core.fold(self.events)


def ask_raw(step, id, **over):
    a = {"id": id, "step": step, "kind": "test", "group": "Words",
         "title": f"Title of {id}", "why": f"Why for {id}.",
         "evidence": [{"label": "Fact", "value": "42", "source": "Ledger"}],
         "if_no": f"Nothing changes for {id}."}
    if step in ("confirm", "approve"):
        a["recommend"] = {"value": "yes", "because": f"Because of {id}."}
    if step == "approve":
        a["effect"] = f"Effect of {id}."
    if step == "choose":
        a["options"] = [{"value": "a", "label": "Option A", "note": "Note A"},
                        {"value": "b", "label": "Option B"},
                        {"value": "c", "label": "Option C", "note": "Note C"}]
        a["recommend"] = {"value": "b", "because": f"Because of {id}."}
    if step == "provide":
        a["input"] = {"type": "number", "min": 0, "max": 50, "unit": "USD"}
        a["recommend"] = {"value": "4.2", "because": f"Because of {id}."}
    a.update(over)
    return {k: v for k, v in a.items() if v is not None}


GATE = {"verb": ["facts", "confirm", "unit_cost"], "value_arg": True,
        "expect": {"items": {"unit_cost": "$value"}}}
RELAY = [["facts", "confirm"], ["queue", "approve"]]          # what the operator allows: GATE's verb starts with the first


def full_log() -> Log:
    """Every step, five groups (one unnamed), a gated ask, two messages."""
    L = Log()
    L.ask(ask_raw("approve", "a1", group="Money",
                  evidence=[{"table": {"caption": "Spend", "columns": ["Ad", "Cost", "Note"],
                                       "rows": [["Search", "USD 15", "ok"], ["Pages", "USD 10", "ok"]]}},
                            {"label": "Sales", "value": "USD 340", "source": "History", "url": "https://example.com/a?x=1&y=2"},
                            {"quote": "Line one.\nLine two.", "source": "the call"}]))
    L.ask(ask_raw("confirm", "c1", group="Words"))
    L.ask(ask_raw("confirm", "c2", group="Words", recommend={"value": "no", "because": "Because no."}))
    L.ask(ask_raw("choose", "o1", group="Priorities"))
    L.ask(ask_raw("provide", "p1", group="Numbers", gate=GATE))
    L.ask(ask_raw("provide", "p2", group="Numbers", input={"type": "date"}, recommend=None))
    L.ask(ask_raw("provide", "p3", group="Numbers", input={"type": "url"},
                  recommend={"value": "https://example.com/x", "because": "Only page found."}))
    L.ask(ask_raw("provide", "p4", group="", input={"type": "text"},
                  recommend={"value": "sampler", "because": "Their word."}))
    L.add("say", "agent:bot", text="Hello.\nSecond line.")
    L.add("note", "web:alice", text="A note from the human.")
    return L


def ctx(lang="en", **over):
    c = {"lang": lang, "title": "Northwind Console", "user": "alice", "token": "tok123",
         "relay": RELAY, "now": NOW, "secret": SECRET, "flash": None}
    c.update(over)
    return c


def inbox(log_or_state, lang="en", **over):
    st = log_or_state.state() if isinstance(log_or_state, Log) else log_or_state
    return Doc(pages.render_inbox(st, ctx(lang, **over)))


def history(log_or_state, lang="en", **over):
    st = log_or_state.state() if isinstance(log_or_state, Log) else log_or_state
    return Doc(pages.render_history(st, ctx(lang, **over)))


def T(key, lang="en", **p):
    return i18n.t(key, lang, **p)


def ask_node(doc, title_part):
    for d in doc.find_all("details", "ask"):
        if title_part in d.find("summary").text():
            return d
    raise AssertionError(f"no ask with {title_part!r}")


def form_of(node):
    return node.find("form", "answer")


def state_hash(log, id):
    return log.state()["asks"][id]["hash"]


# ------------------------------------------------------ the document shell --

def test_every_page_is_a_complete_document_in_both_languages():
    log = full_log()
    for lang in LANGS:
        srcs = {
            "inbox": pages.render_inbox(log.state(), ctx(lang)),
            "history": pages.render_history(log.state(), ctx(lang)),
            "result": pages.render_result(ctx(lang), [{"ok": True, "id": "c1", "title": "T", "value": "yes", "code": None, "message": ""}]),
            "error": pages.render_error(ctx(lang), "not_open"),
        }
        for name, src in srcs.items():
            d = Doc(src)
            assert src.startswith("<!doctype html>"), name
            assert d.find("html").attrs["lang"] == lang, name
            assert d.find("meta", name="viewport").attrs["content"].startswith("width=device-width"), name
            assert d.find("link", rel="stylesheet").attrs["href"] == "static/console.css", name
            js = d.find("script")
            assert js.attrs["src"] == "static/console.js" and "defer" in js.attrs, name
            assert len(d.find_all("main")) == 1 and len(d.find_all("h1")) == 1, name
            # the CSP forbids these: none may be drawn
            assert d.find_all("script") == [js], f"{name}: only the one external script"
            assert "style=" not in src, name
            assert not [n for n in d.walk() if any(a.startswith("on") for a in n.attrs)], name
            # every URL is relative: a proxy that strips a prefix needs no configuration
            for n in d.walk():
                for a in ("href", "src", "action"):
                    v = n.attrs.get(a, "")
                    if v and not (n.tag == "a" and n.attrs.get("target") == "_blank"):
                        assert not v.startswith(("/", "http:", "https:")), (name, a, v)


def test_top_bar_marks_the_current_tab_and_links_the_other_language():
    log = full_log()
    for lang in LANGS:
        other = "zh" if lang == "en" else "en"
        for name, doc, tab in (("inbox", inbox(log, lang), "./"), ("history", history(log, lang), "history")):
            bar = doc.find("header", "bar")
            tabs = bar.find("nav", "tabs").find_all("a")
            assert [a.attrs["href"] for a in tabs] == ["./", "history"]
            assert [a.attrs.get("aria-current") for a in tabs].count("page") == 1
            assert [a for a in tabs if a.attrs.get("aria-current") == "page"][0].attrs["href"] == tab
            assert T("nav.waiting", lang) in tabs[0].text() and T("nav.history", lang) in tabs[1].text()
            lk = bar.find("a", "lang")
            assert lk.attrs["href"] == f"?lang={other}" and lk.attrs["lang"] == other
            assert lk.text() == T("lang.other", lang)
            assert bar.find("a", "brand").text() == "Northwind Console"
        # a POST answer's page is not a GET url: its language link goes to ./
        for doc in (Doc(pages.render_result(ctx(lang), [])), Doc(pages.render_error(ctx(lang), "forbidden"))):
            assert doc.find("a", "lang").attrs["href"] == f"./?lang={other}"
            assert not [a for a in doc.find("nav", "tabs").find_all("a") if "aria-current" in a.attrs]


def test_waiting_count_in_the_bar_only_when_something_waits():
    log = full_log()
    assert inbox(log).find("nav", "tabs").find("span", "count").text() == "8"
    assert inbox(Log()).find("nav", "tabs").find("span", "count") is None


def test_poll_attributes_on_pages_that_have_a_state():
    log = full_log()
    for lang in LANGS:
        for doc in (inbox(log, lang), history(log, lang)):
            b = doc.find("body").attrs
            assert b["data-seq"] == str(len(log.events)) and b["data-open"] == "8", b
            assert b["data-poll"] == "poll"
            assert b["data-new"] == T("banner.new", lang) and b["data-lost"] == T("banner.lost", lang)
    # a POST's answer page has no poll: reloading it would send the form again
    for src in (pages.render_result(ctx(), []), pages.render_error(ctx(), "changed")):
        b = Doc(src).find("body").attrs
        assert "data-poll" not in b and "data-seq" not in b, b


def test_language_falls_back_to_english_for_an_unknown_one():
    d = Doc(pages.render_inbox(full_log().state(), ctx("fr")))
    assert d.find("html").attrs["lang"] == "en"


def test_flash_is_shown_escaped_with_its_kind():
    for kind, cls in (("ok", "ok"), ("error", "bad")):
        d = inbox(full_log(), flash={"kind": kind, "text": "<b>Saved</b> & done"})
        f = d.find("p", "flash")
        assert cls in f.classes and f.text() == "<b>Saved</b> & done"
        assert "<b>Saved</b>" not in d.source
    assert inbox(full_log()).find("p", "flash") is None


def test_flash_text_words_a_finished_reopen_or_note_and_nothing_else():
    for lang in LANGS:
        assert pages.flash_text(lang, "reopened") == T("flash.reopened", lang)
        assert pages.flash_text(lang, "noted") == T("flash.noted", lang)
        for key in ("answered", "", "REOPENED", "reopened ", None, 7, ["reopened"], "err.forbidden", "flash.noted"):
            assert pages.flash_text(lang, key) is None, key         # a link cannot make the page say anything else
    assert pages.flash_text("fr", "noted") == T("flash.noted", "en")   # an unknown language is English
    assert T("flash.reopened", "en") != T("flash.noted", "en") and T("flash.reopened", "zh") != T("flash.noted", "zh")
    # and the words reach the top of the page, as text, in the console's own kind of banner
    for lang in LANGS:
        for page in (inbox(full_log(), lang, flash={"kind": "ok", "text": pages.flash_text(lang, "reopened")}),
                     history(history_log(), lang, flash={"kind": "ok", "text": pages.flash_text(lang, "noted")})):
            f = page.main.kids[0]
            assert f.tag == "p" and "flash" in f.classes and "ok" in f.classes and f.attrs["role"] == "status"


# --------------------------------------------------------------- the inbox --

def test_inbox_reads_top_down_in_one_column():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        main = d.main
        order = [k.attrs.get("class", k.tag).split()[0] for k in main.kids if isinstance(k, Node)]
        # head, the agent's message, the group links, one section per group, messages
        assert order == ["head", "say", "jump", "group", "group", "group", "group", "group", "msgs"], order
        assert main.find("h1").text() == T("page.waiting", lang)
        groups = main.find_all("section", "group")
        assert [g.find("h2").text() if g.find("h2") else "" for g in groups] == \
               [f"Money 1", "Words 2", "Priorities 1", "Numbers 3", f"{T('group.none', lang)} 1"]
        for g in groups:                      # each group is one list of asks, never a grid of cards
            rows = g.find("div", "list").kids
            assert rows and all(isinstance(r, Node) and r.tag == "details" and "ask" in r.classes for r in rows)
        assert len(main.find_all("details", "ask")) == 8


def test_first_ask_of_the_first_group_is_open_and_only_it():
    d = inbox(full_log())
    opened = [a for a in d.find_all("details", "ask") if "open" in a.attrs]
    assert len(opened) == 1 and "Title of a1" in opened[0].find("summary").text()


def test_group_links_only_with_two_groups_and_each_counts():
    d = inbox(full_log())
    links = d.find("nav", "jump").find_all("a")
    assert [a.attrs["href"] for a in links] == ["#g1", "#g2", "#g3", "#g4", "#g5"]
    assert [a.find("span", "count").text() for a in links] == ["1", "2", "1", "3", "1"]
    assert [d.find("section", id=f"g{i}") is not None for i in range(1, 6)] == [True] * 5
    one = Log()
    one.ask(ask_raw("confirm", "only", group="Words"))
    solo = inbox(one)
    assert solo.find("nav", "jump") is None
    assert solo.find("section", "group").find("h2").text().startswith("Words")
    # a single unnamed group needs no heading at all
    bare = Log()
    bare.ask(ask_raw("confirm", "only", group=None))
    assert inbox(bare).find("section", "group").find("h2") is None


def test_the_agents_latest_message_is_on_top_and_the_thread_below():
    log = full_log()
    log.add("say", "agent:bot", text="Newer news.")
    d = inbox(log)
    say = d.find("aside", "say")
    assert say.find("p", "txt").text() == "Newer news."
    assert say.find("p", "who").text() == T("say.from", age="2 h ago")
    msgs = d.find("section", "msgs")
    items = msgs.find_all("li", "msg")
    # the latest agent message is on top, so the thread does not repeat it
    assert [i.find("p", "txt").text() for i in items] == ["Hello. Second line.", "A note from the human."]
    assert [("you" in i.classes) for i in items] == [False, True]             # the human's is marked
    assert msgs.find("h2").text() == T("messages.head")
    only = Log()
    only.add("say", "agent:bot", text="Only word.")
    d = inbox(only)                                                            # nothing but the banner: no thread, still the form
    assert d.find("aside", "say") and not d.find("section", "msgs").find_all("li", "msg")
    assert d.find("section", "msgs").find("h2") is None and d.find("section", "msgs").find("form", "noteform")


def test_a_message_keeps_its_line_breaks_for_the_css_to_show():
    d = inbox(full_log())
    say = d.find("aside", "say").find("p", "txt")
    assert "Hello.\nSecond line." in "".join(say.texts())


def test_message_thread_is_capped_with_a_line_saying_so():
    log = full_log()
    for i in range(30):
        log.add("say", "agent:bot", text=f"m{i}")
    d = inbox(log)
    items = d.find("section", "msgs").find_all("li", "msg")
    assert len(items) == pages.SHOW_MESSAGES and items[-1].find("p", "txt").text() == "m28"     # m29 is on top
    assert T("messages.more", n=pages.SHOW_MESSAGES) in d.find("section", "msgs").text()


def test_empty_inbox_is_pleasant():
    for lang in LANGS:
        d = inbox(Log(), lang)
        e = d.find("div", "empty")
        assert e.find("p", "big").text() == T("empty.title", lang)
        link = e.find("a")
        assert link.attrs["href"] == "history" and link.text() == T("empty.link", lang)
        assert e.find("p", "hint").text() == T("empty.hint", lang)                 # it says what happens next
        assert "clear" in e.classes                                               # the tick is drawn for "nothing waits" only
        assert d.find("section", "group") is None and d.find("nav", "jump") is None
    assert inbox(full_log()).find("div", "empty") is None
    assert "clear" not in history(Log()).find("div", "empty").classes


def test_budget_line_and_segments_and_over():
    log = Log()
    for i in range(3):
        log.ask(ask_raw("confirm", f"k{i}"))
    for lang in LANGS:
        d = inbox(log, lang)
        assert d.find("p", "sub").text() == T("budget.line", lang, open=3, max=10)
        meter = d.find("span", "meter")
        assert "over" not in meter.classes
        assert len(meter.find_all("b")) == 3 and len(meter.find_all("i")) == 7
    over = Log()                              # the fold has no budget; 12 open is a state that can exist
    for i in range(12):
        over.ask(ask_raw("confirm", f"k{i}"))
    for lang in LANGS:
        d = inbox(over, lang)
        assert d.find("p", "sub").text() == T("budget.over", lang, open=12, over=2, max=10)
        meter = d.find("span", "meter")
        assert "over" in meter.classes and len(meter.find_all("b")) == 10 and not meter.find_all("i")


def test_summary_has_step_title_suggestion_and_age():
    d = inbox(full_log())
    s = ask_node(d, "Title of a1").find("summary")
    chips = [c.text() for c in s.find_all("span", "chip")]
    assert chips == [T("step.approve"), T("suggested.chip", answer=T("ans.approve"))]
    assert "warn" in s.find_all("span", "chip")[0].classes and "ok" in s.find_all("span", "chip")[1].classes
    assert s.find("span", "ttl").text() == "Title of a1"
    assert s.find("span", "age").text() == "2 h ago"
    # no suggestion, no chip
    s2 = ask_node(d, "Title of p2").find("summary")
    assert [c.text() for c in s2.find_all("span", "chip")] == [T("step.provide")]
    s3 = ask_node(d, "Title of o1").find("summary")
    assert s3.find_all("span", "chip")[1].text() == T("suggested.chip", answer="Option B")
    s4 = ask_node(d, "Title of c2").find("summary")
    assert s4.find_all("span", "chip")[1].text() == T("suggested.chip", answer=T("ans.no"))
    s5 = ask_node(d, "Title of p1").find("summary")
    assert s5.find_all("span", "chip")[1].text() == T("suggested.chip", answer="4.2 USD")


def test_inside_an_ask_the_order_is_why_evidence_suggestion_effect_no_form():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        inner = ask_node(d, "Title of a1").find("div", "inner")
        seq = [(k.attrs.get("class", k.tag).split() or [k.tag])[0] for k in inner.kids if isinstance(k, Node)]
        assert seq == ["why", "ev", "suggest", "effect", "ifno", "answer"], seq
        assert inner.find("p", "why").text() == "Why for a1."
        assert inner.find("p", "suggest").text() == f"{T('suggest.lead', lang, answer=T('ans.approve', lang))} — Because of a1."
        assert inner.find("p", "effect").text() == f"{T('effect.lead', lang)} Effect of a1."
        assert inner.find("p", "ifno").text() == f"{T('ifno.lead_reject', lang)} Nothing changes for a1."
        assert inner.find("p", "effect").find("b", "lead") is not None
    # a confirm has no effect box, and only an approval's row is marked as one
    assert ask_node(inbox(full_log()), "Title of c1").find("p", "effect") is None
    d = inbox(full_log())
    assert [a.find("span", "ttl").text() for a in d.find_all("details", "approve")] == ["Title of a1"]
    odd = Log()                                    # a log edited by hand: an effect on a confirm is not shown as if it approved something
    odd.raw_ask(ask_raw("confirm", "e1", effect="Sneaky effect."))
    assert inbox(odd).find("p", "effect") is None and "Sneaky effect." not in inbox(odd).source


def test_a_choice_says_what_happens_if_none_fits_and_the_other_steps_say_what_no_means():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        for title, lead in (("Title of o1", "ifno.lead_choose"), ("Title of c1", "ifno.lead"), ("Title of a1", "ifno.lead_reject"),
                            ("Title of p1", "ifno.lead"), ("Title of p2", "ifno.lead")):
            p = ask_node(d, title).find("p", "ifno")
            assert p.find("b", "lead").text() == T(lead, lang), title
            assert p.text() == f"{T(lead, lang)} Nothing changes for {title[-2:]}.", title
        assert len({T(k, lang) for k in ("ifno.lead", "ifno.lead_choose", "ifno.lead_reject")}) == 3   # each step says its own
        # History says the same about what the person was shown
        L = full_log()
        for id in ("o1", "c1"):
            L.add("answer", "web:alice", id=id, value="a" if id == "o1" else "yes",
                  subject=core.subject_hash(L.state()["asks"][id]["ask"]), suggested=False)
        h = history(L, lang)
        assert ask_node(h, "Title of o1").find("p", "ifno").find("b", "lead").text() == T("ifno.lead_choose", lang)
        assert ask_node(h, "Title of c1").find("p", "ifno").find("b", "lead").text() == T("ifno.lead", lang)


def test_evidence_facts_quotes_and_tables():
    d = inbox(full_log())
    ev = ask_node(d, "Title of a1").find("div", "ev")
    assert ev.find("h3").text() == T("evidence.head")
    fig = ev.find("figure", "tablebox")
    assert fig.find("figcaption").text() == "Spend"
    box = fig.find("div", "scroll")                       # the table scrolls in its own box
    assert box.attrs["role"] == "region" and box.attrs["tabindex"] == "0" and box.attrs["aria-label"] == "Spend"
    assert [th.text() for th in box.find_all("th")] == ["Ad", "Cost", "Note"]
    assert [td.text() for td in box.find_all("td")][:3] == ["Search", "USD 15", "ok"]
    assert all(th.attrs["scope"] == "col" for th in box.find_all("th"))
    fact = ev.find("li", "fact")
    assert fact.find("span", "k").text() == "Sales" and fact.find("span", "v").text() == "USD 340"
    a = fact.find("a", "src")
    assert a.attrs["href"] == "https://example.com/a?x=1&y=2" and a.text() == "History"
    q = ev.find("blockquote", "quote")
    assert q.find("footer").text() == "the call" and "Line one.\nLine two." in "".join(q.find("p").texts())
    # the same ask's table has no caption: the box still has a name
    log = Log()
    log.ask(ask_raw("confirm", "t1", evidence=[{"table": {"columns": ["A"], "rows": [["1"]]}}]))
    box = inbox(log).find("div", "scroll")
    assert box.attrs["aria-label"] == T("evidence.table") and inbox(log).find("figcaption") is None


def test_a_column_of_numbers_lines_up_on_the_right_header_included():
    log = Log()
    log.ask(ask_raw("confirm", "t1", evidence=[{"table": {
        "columns": ["Name", "Units", "Mixed"],
        "rows": [["Sencha", "1,200", "5"], ["Chai", "-3.5", "n/a"], ["Jasmine", "$40", "7"]]}}]))
    t = inbox(log).find("table")
    heads = t.find_all("th")
    assert ["num" in h.classes for h in heads] == [False, True, False]
    for row in t.find("tbody").find_all("tr"):
        assert ["num" in c.classes for c in row.find_all("td")] == [False, True, False]


# ----------------------------------------------------------------- controls --

def test_confirm_and_approve_are_two_radios_with_the_right_words():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        for title, yes, no in (("Title of c1", "ans.yes", "ans.no"), ("Title of a1", "ans.approve", "ans.reject")):
            f = form_of(ask_node(d, title))
            radios = f.find_all("input", type="radio")
            assert [r.attrs["value"] for r in radios] == ["yes", "no"] and {r.attrs["name"] for r in radios} == {"value"}
            labels = [l.find("span", "lab").text() for l in f.find_all("label", "opt")]
            assert labels[0].startswith(T(yes, lang)) and labels[1] == T(no, lang)
            # radios and the comment are all there is to type into
            assert {(i.attrs["type"]) for i in f.find_all("input")} == {"radio", "hidden"}


def test_choose_is_one_radio_per_option_with_its_note_under_it():
    d = inbox(full_log())
    f = form_of(ask_node(d, "Title of o1"))
    opts = f.find_all("label", "opt")
    assert [o.find("input").attrs["value"] for o in opts] == ["a", "b", "c"]
    assert [o.find("span", "lab").text() for o in opts] == ["Option A Note A", f"Option B {T('chip.suggested')}", "Option C Note C"]
    assert [bool(o.find("span", "desc")) for o in opts] == [True, False, True]


def test_the_suggestion_is_prechecked_and_it_is_the_only_one():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        for title, want in (("Title of c1", "yes"), ("Title of c2", "no"), ("Title of a1", "yes"), ("Title of o1", "b")):
            radios = form_of(ask_node(d, title)).find_all("input", type="radio")
            assert [r.attrs["value"] for r in radios if "checked" in r.attrs] == [want], title
            tags = form_of(ask_node(d, title)).find_all("span", "chip")
            assert [t.text() for t in tags] == [T("chip.suggested", lang)]     # and it is marked
    # never a pre-checked radio without a suggestion: choose always has one, so test the provide side
    p2 = form_of(ask_node(inbox(full_log()), "Title of p2"))
    assert "value" not in p2.find("input", type="date").attrs


def test_the_one_answer_a_declined_gate_allows_is_checked_but_called_suggested_only_if_it_was():
    gate = {"verb": ["queue", "approve", "7"], "expect": {"ids": [7]}}
    for lang in LANGS:
        for step, yes_no, recommend in (("approve", "reject", "yes"), ("approve", "reject", "no"),
                                        ("confirm", "no", "yes"), ("confirm", "no", "no")):
            log = Log()
            log.ask(ask_raw(step, "g1", gate=gate, recommend={"value": recommend, "because": "Because."}))
            d = inbox(log, lang, relay=None)                              # the console cannot run this gate
            f = form_of(d.find("details", "ask"))
            radios = f.find_all("input", type="radio")
            assert [r.attrs["value"] for r in radios] == ["no"] and "checked" in radios[0].attrs   # only a refusal, pre-checked
            chips = f.find_all("span", "chip")
            # the label says "Suggested" only when the agent did suggest saying no
            assert [c.text() for c in chips] == ([T("chip.suggested", lang)] if recommend == "no" else []), (step, recommend)
            assert d.find("p", "suggest").find("b", "lead").text() == T(
                "suggest.lead", lang, answer=T(f"ans.{yes_no}" if recommend == "no" else f"ans.{'approve' if step == 'approve' else 'yes'}", lang))
    # an ask the console can run is marked as before, whichever way it points
    log = Log()
    log.ask(ask_raw("approve", "g2", gate=gate))
    f = form_of(inbox(log, relay=[["queue", "approve"]]).find("details", "ask"))
    assert [c.text() for c in f.find_all("span", "chip")] == [T("chip.suggested")]


def test_provide_asks_draw_one_input_of_their_type_prefilled_with_the_suggestion():
    d = inbox(full_log())
    n = form_of(ask_node(d, "Title of p1"))
    i = n.find("input", type="number")
    assert (i.attrs["name"], i.attrs["value"], i.attrs["step"], i.attrs["min"], i.attrs["max"]) == ("value", "4.2", "any", "0", "50")
    assert "required" in i.attrs and n.find("span", "unit").text() == "USD"
    assert n.find("p", "hint").text() == T("hint.between", min=0, max=50)
    assert n.find("label").attrs["for"] == i.attrs["id"]
    assert form_of(ask_node(d, "Title of p2")).find("input", type="date") is not None
    u = form_of(ask_node(d, "Title of p3"))
    assert u.find("input", type="url").attrs["value"] == "https://example.com/x" and u.find("p", "hint").text() == T("hint.url")
    t = form_of(ask_node(d, "Title of p4"))
    assert t.find("input", type="text").attrs["value"] == "sampler"
    for f in (n, u, t):                                            # one typed input, no radios
        assert not f.find_all("input", type="radio")
        assert len([x for x in f.find_all("input") if x.attrs["type"] != "hidden"]) == 1
    assert not any("pattern" in x.attrs for x in d.find_all("input"))


def test_number_hints_for_one_sided_ranges():
    for spec, want in (({"type": "number", "min": 1}, T("hint.atleast", min=1)),
                       ({"type": "number", "max": 9.5}, T("hint.atmost", max=9.5))):
        log = Log()
        log.ask(ask_raw("provide", "n1", input=spec, recommend=None))
        f = form_of(inbox(log).find("details", "ask"))
        assert f.find("p", "hint").text() == want
    log = Log()
    log.ask(ask_raw("provide", "n1", input={"type": "number"}, recommend=None))
    assert form_of(inbox(log).find("details", "ask")).find("p", "hint") is None


def test_the_button_says_what_the_checked_radio_does():
    for lang in LANGS:
        d = inbox(full_log(), lang)
        b = form_of(ask_node(d, "Title of a1")).find("button", "btn")
        assert [s.text() for s in b.find_all("span")] == [T("ans.approve", lang), T("ans.reject", lang)]
        assert [s.attrs["class"] for s in b.find_all("span")] == ["yes", "no"]
        b = form_of(ask_node(d, "Title of c1")).find("button", "btn")
        assert [s.text() for s in b.find_all("span")] == [T("btn.confirm", lang), T("btn.confirm_no", lang)]
        for title in ("Title of o1", "Title of p1"):
            b = form_of(ask_node(d, title)).find("button", "btn")
            assert b.text() == T("btn.send", lang) and "primary" in b.classes and b.attrs["type"] == "submit"


def test_each_ask_has_its_own_form_that_carries_what_was_shown():
    log = full_log()
    d = inbox(log)
    forms = d.find_all("form", "answer")
    assert len(forms) == 8
    for f in forms:
        assert f.attrs["method"] == "post" and f.attrs["action"] == "answer"
        h = {i.attrs["name"]: i.attrs["value"] for i in f.find_all("input", type="hidden")}
        assert set(h) == {"token", "id", "hash"} and h["token"] == "tok123"
        assert h["hash"] == state_hash(log, h["id"])
    # the hash is that of the ask as it is now: a revised ask changes it
    log.ask(ask_raw("confirm", "c1", title="Title of c1 revised"))
    h2 = {i.attrs["name"]: i.attrs["value"] for i in form_of(ask_node(inbox(log), "revised")).find_all("input", type="hidden")}
    assert h2["hash"] == state_hash(log, "c1") != h["hash"]


def test_comment_is_optional_and_folded():
    for f in inbox(full_log()).find_all("form", "answer"):
        more = f.find("details", "more")
        assert "open" not in more.attrs
        ta = more.find("textarea")
        assert ta.attrs["name"] == "comment" and "required" not in ta.attrs and ta.attrs["maxlength"] == "500"
        assert ta.attrs["aria-label"] == T("comment.toggle")


# ------------------------------------------------------------ group button --

def test_group_button_only_when_every_ask_has_a_suggestion_and_there_are_two():
    log = full_log()
    d = inbox(log)
    alls = d.find_all("form", "all")
    assert len(alls) == 1                                       # only "Words": c1 + c2
    f = alls[0]
    assert f.attrs["method"] == "post" and f.attrs["action"] == "answer_all"
    pairs = [i.attrs["value"] for i in f.find_all("input", name="pair")]
    assert pairs == [f"c1:{state_hash(log, 'c1')}", f"c2:{state_hash(log, 'c2')}"]
    assert f.find("input", name="token").attrs["value"] == "tok123"
    assert f.find("button").text() == T("all.button", n=2) and "secondary" in f.find("button").classes
    assert f.find("p", "gnote").text() == T("all.note")
    assert f.attrs["data-busy"] == T("all.busy")          # the words the script shows while the button is off
    assert f.inside("section", "group") and f.parent.find("h2").text().startswith("Words")
    # "Numbers" has a provide ask without a suggestion: no button. Money, Priorities: one ask.
    for g in d.find_all("section", "group"):
        assert bool(g.find("form", "all")) == g.find("h2").text().startswith("Words")
    # give p2 a suggestion and the group gets its button, too (p1 is gated but the relay is on)
    log.ask(ask_raw("provide", "p2", group="Numbers", input={"type": "date"},
                    recommend={"value": "2026-10-01", "because": "Planned."}))
    numbers = [g for g in inbox(log).find_all("section", "group") if g.find("h2").text().startswith("Numbers")][0]
    assert len(numbers.find("form", "all").find_all("input", name="pair")) == 3
    assert numbers.find("form", "all").find("button").text() == T("all.button", n=3)


def test_the_reason_the_group_button_may_be_off_is_given_in_both_languages():
    for lang in LANGS:
        f = inbox(full_log(), lang).find("form", "all")
        assert f.attrs["data-busy"] == T("all.busy", lang) and f.attrs["data-busy"] != T("all.busy", "zh" if lang == "en" else "en")
        assert "data-busy" not in f.find("p", "gnote").attrs and "busy" not in f.find("p", "gnote").classes   # only the script draws it


def test_group_button_is_not_drawn_without_a_person_or_a_gate_that_cannot_run_here():
    log = Log()
    log.ask(ask_raw("confirm", "k1", group="G"))
    log.ask(ask_raw("provide", "k2", group="G", gate=GATE))
    assert inbox(log).find("form", "all") is not None
    assert inbox(log, user=None).find("form", "all") is None
    for relay in (None, [], [["queue", "approve"]],                    # none at all, or only other verbs
                  [["facts", "confirm", "unit_cost", "more"]],          # a longer prefix is not a prefix of the verb
                  [[]], True):                                          # an empty prefix allows nothing; a bool is no list
        assert inbox(log, relay=relay).find("form", "all") is None, relay      # k2 could not be answered
    assert inbox(log, relay=[["facts"]]).find("form", "all") is not None      # a shorter prefix is enough


def test_group_button_is_never_drawn_for_a_group_that_holds_an_approval():
    two = Log()
    two.ask(ask_raw("confirm", "k1", group="G"))
    two.ask(ask_raw("confirm", "k2", group="G"))
    assert inbox(two).find("form", "all") is not None                  # the same group without an approval has it
    for steps in (("approve", "approve"), ("confirm", "approve"), ("approve", "choose")):
        log = Log()
        for i, step in enumerate(steps):
            log.ask(ask_raw(step, f"k{i}", group="G"))
        for lang in LANGS:
            assert inbox(log, lang).find("form", "all") is None, steps
            assert len(inbox(log, lang).find_all("form", "answer")) == 2      # each is still answered ask by ask
    # not even a gated approval that the console can run
    gated = Log()
    gated.ask(ask_raw("confirm", "k1", group="G"))
    gated.ask(ask_raw("approve", "k2", group="G", gate={"verb": ["queue", "approve", "7"], "expect": {"ids": [7]}}))
    assert inbox(gated).find("form", "all") is None


# -------------------------------------------- read-only, and a gate without a relay --

def test_read_only_draws_no_form_and_says_so():
    for lang in LANGS:
        d = inbox(full_log(), lang, user=None)
        assert not d.find_all("form")
        assert d.find("p", "banner").text() == T("ro.banner", lang)
        assert "tok123" not in d.source                  # the page token is only for people who can answer
        assert d.find("footer", "foot").text().endswith(T("foot.ro", lang))
        h = history(full_log(), lang, user=None)
        assert not h.find_all("form") and h.find("p", "banner")
    assert inbox(full_log()).find("p", "banner") is None
    assert len(ask_node(inbox(full_log(), user=None), "Title of c1").find_all("details", "more")) == 0


def test_a_gated_ask_the_console_cannot_run_says_to_ask_the_agent_and_draws_no_form():
    for lang in LANGS:
        # no relay at all, or a relay that does not allow this ask's verb: the same note, before any click
        for relay in (None, [], [["queue", "approve"]]):
            d = inbox(full_log(), lang, relay=relay)
            p1 = ask_node(d, "Title of p1")
            assert form_of(p1) is None and not p1.find_all("form")
            note = p1.find("p", "banner")
            assert note.text() == T("gate.norelay", lang)
            assert "facts" not in note.text() and "unit_cost" not in note.text()      # no command text, only the sentence
            assert len(d.find_all("form", "answer")) == 7           # every other ask still has its form
    assert form_of(ask_node(inbox(full_log()), "Title of p1")) is not None
    # an ask allowed by prefix gets its form; the operator's list decides per ask, not the ask itself
    log = Log()
    log.ask(ask_raw("provide", "q1", group="G", gate=GATE))
    log.ask(ask_raw("provide", "q2", group="G", gate={**GATE, "verb": ["facts", "restore", "q2"]}))
    d = inbox(log, relay=[["facts", "confirm"]])
    assert form_of(ask_node(d, "Title of q1")) is not None and form_of(ask_node(d, "Title of q2")) is None


def test_a_gated_ask_shows_what_will_be_written_folded_before_the_button():
    for lang in LANGS:
        p1 = ask_node(inbox(full_log(), lang), "Title of p1")
        inner = p1.find("div", "inner")
        kids = [k for k in inner.kids if isinstance(k, Node)]
        tech = p1.find("details", "tech")
        assert "open" not in tech.attrs and tech.find("summary").text() == T("gate.tech", lang)
        assert kids.index(tech) < kids.index(p1.find("form", "answer"))
        assert tech.find("code").text() == "facts confirm unit_cost"
        assert [x.text() for x in tech.find_all("dt")] == ["items.unit_cost"]
        assert [x.text() for x in tech.find_all("dd")] == [T("gate.value", lang)]       # "$value" is not shown
        f = form_of(p1)
        note = [h for h in f.find_all("p", "hint") if h.text() == T("gate.note", lang)]
        assert len(note) == 1                                  # (the other hint is the number's range)
        order = list(f.walk())
        assert order.index(note[0]) < order.index(f.find("button"))
    # an ask without a gate has none of it
    assert ask_node(inbox(full_log()), "Title of c1").find("details", "tech") is None


def test_a_gated_choice_lists_what_each_option_would_write_not_your_answer():
    """The label is the agent's word for an option; the value is what the harness
    is told. The folded box gives both, one row per option."""
    log = Log()
    opts = [{"value": "9999", "label": "Yes, keep 50 dollars"}, {"value": "50", "label": "No, lower it to 40", "note": "n"},
            {"value": "x y", "label": "Neither"}]
    log.ask(ask_raw("choose", "g1", options=opts, recommend={"value": "9999", "because": "b"},
                    gate={"verb": ["facts", "confirm", "daily_cap_usd"], "value_arg": True,
                          "expect": {"items": {"daily_cap_usd": "$value"}, "on": True}}))
    for lang in LANGS:
        d = inbox(log, lang)
        tech = d.find("details", "tech")
        assert [x.text() for x in tech.find_all("dt")] == [o["label"] for o in opts]            # one row per option, by its label
        assert [x.text() for x in tech.find_all("dd")] == [f"items.daily_cap_usd = {o['value']}; on = true" for o in opts]
        assert T("gate.value", lang) not in tech.text() and "$value" not in d.source            # never the placeholder
        assert tech.find("code").text() == "facts confirm daily_cap_usd" and "open" not in tech.attrs
        f = form_of(d.find("details", "ask"))
        assert d.find("details", "tech").parent.kids.index(tech) < d.find("details", "ask").find("div", "inner").kids.index(f)
    # a choice with no gate has no such box, and a typed answer still says "your answer": the person wrote the value
    assert inbox(full_log()).find("details", "ask").find("details", "tech") is None
    assert [x.text() for x in ask_node(inbox(full_log()), "Title of p1").find_all("dd")] == [T("gate.value")]


def test_a_payload_shows_every_scalar_and_index():
    log = Log()
    log.ask(ask_raw("confirm", "g1", gate={"verb": ["queue", "approve"],
                                           "expect": {"ids": [7, 8], "on": True, "n": None, "who": "$value"}}))
    tech = inbox(log).find("details", "tech")
    got = dict(zip([x.text() for x in tech.find_all("dt")], [x.text() for x in tech.find_all("dd")]))
    assert got == {"ids[0]": "7", "ids[1]": "8", "on": "true", "n": "null", "who": T("gate.value")}


# ---------------------------------------------- escaping, and links --

class Payloads:
    """Distinct hostile strings; each starts with `">` to break out of an attribute."""

    def __init__(self):
        self.given = []

    def __call__(self, short=False):
        n = len(self.given) + 1
        p = f'"><u>{n}</u>' if short else f'"><script>alert({n})</script>'
        self.given.append(p)
        return p


def evil_log(P):
    """Every agent-supplied string is a distinct payload, quotes and all."""
    L = Log()
    ev = lambda: [{"label": P(), "value": P(), "source": P(), "url": "https://example.com/p?a=1&b=2"},
                  {"quote": P(), "source": P()},
                  {"table": {"caption": P(), "columns": [P(), P()], "rows": [[P(), P()]]}}]

    def one(step, id, **kw):
        group = kw.pop("group", None) or P()
        L.ask(ask_raw(step, id, group=group, title=P(), why=P(), evidence=ev(), if_no=P(), **kw))
    one("approve", "e1", effect=P(), recommend={"value": "yes", "because": P()})
    opts = [{"value": P(), "label": P(), "note": P()}, {"value": "b", "label": P()}]
    one("choose", "e2", options=opts, recommend={"value": opts[0]["value"], "because": P()},
        gate={"verb": ["facts", "confirm"], "value_arg": True, "expect": {P(): "$value"}})
    one("provide", "e3", input={"type": "text", "unit": P(True)},
        recommend={"value": P(), "because": P()},
        gate={"verb": ["facts", "confirm"], "value_arg": True, "expect": {P(): "$value", "k": P()}})
    shared = P()                                       # two suggested asks in one group: the group button is drawn
    one("confirm", "e4", group=shared)
    one("confirm", "e5", group=shared)
    L.add("say", "agent:bot", text=P())               # the agent's name is not shown; its text is
    L.add("note", "web:" + P(True), text=P())
    a = L.ask(ask_raw("confirm", "h1", group="G", title=P(), why=P(), if_no=P(), evidence=[{"label": P(), "value": P()}]))
    L.add("answer", "web:alice", id="h1", value="yes", comment=P(), subject=core.subject_hash(a), suggested=True,
          gate={"ok": True, "verb": ["x"], "message": P()})
    L.add("applied", "agent:bot", id="h1", where=P())
    L.ask(ask_raw("confirm", "h2", group="G", title=P(), why=P(), if_no=P(), evidence=[{"label": P(), "value": P()}]))
    L.add("withdraw", "agent:bot", id="h2", reason=P())
    return L


def test_every_agent_supplied_string_is_escaped_on_every_page():
    P = Payloads()
    L = evil_log(P)
    c_title, c_user, c_token, c_flash = P(), P(True), P(), P()
    r_title, r_value, r_msg, r_comment = P(), P(True), P(), P()
    problem, said = P(), P()
    assert len(P.given) > 40
    for lang in LANGS:
        c = ctx(lang, title=c_title, user=c_user, token=c_token, flash={"kind": "error", "text": c_flash})
        inbox_src = pages.render_inbox(L.state(), c)
        hist_src = pages.render_history(L.state(), c)
        result_src = pages.render_result(c, [
            {"ok": False, "id": "x", "title": r_title, "value": None, "code": "gate_refused", "message": r_msg, "comment": r_comment},
            {"ok": True, "id": "y", "title": r_title, "value": r_value, "code": None, "message": r_msg}])
        error_src = pages.render_error(c, "corrupt_log", problem)
        said_src = pages.render_error(c, "gate_timeout", said)
        for src in (inbox_src, hist_src, result_src, error_src, said_src):
            assert src.count("<script") == 1, "only the one external script may exist"
            assert "<u>" not in src and "onerror" not in src
        shown = inbox_src + hist_src + result_src + error_src + said_src
        for p in P.given:                                     # ...and nothing was dropped: each is shown, as text
            assert html.escape(p, quote=True) in shown, p
        # the attributes keep the whole payload inside them
        d = Doc(inbox_src)
        assert {i.attrs["value"] for i in d.find_all("input", name="token")} == {c_token}
        ask_events = [e["ask"] for e in L.events if e["type"] == "ask"]
        assert ask_events[1]["options"][0]["value"] in [r.attrs["value"] for r in d.find_all("input", type="radio")]
        assert d.find("input", type="text").attrs["value"] == ask_events[2]["recommend"]["value"]
        assert d.find("form", "all") is not None
        assert d.find("a", "brand").text() == c_title
        assert d.find("title").text().endswith(c_title)
        assert Doc(result_src).find("p", "ttl").text() == r_title
        assert T("result.comment", lang, comment=r_comment) in Doc(result_src).text()
        assert len(d.find_all("details", "tech")) == 2            # the gated choice's rows (e2) are drawn, and escaped, too
        assert problem in Doc(error_src).find("details", "tech").text()


def test_a_hostile_ask_id_from_a_hand_edited_log_is_escaped_in_every_attribute():
    hostile = '"><u>1</u>'
    L = Log()
    L.raw_ask(ask_raw("confirm", hostile, group="G"))
    L.raw_ask(ask_raw("provide", hostile + "2", group="G"))
    L.raw_ask(ask_raw("confirm", "ok1", group="G"))
    L.add("answer", "web:alice", id="ok1", value="yes", subject="x", suggested=True)
    L.raw_ask(ask_raw("confirm", hostile + "3", group="H"))
    L.add("answer", "web:alice", id=hostile + "3", value="yes", subject=core.subject_hash(L.state()["asks"][hostile + "3"]["ask"]), suggested=True)
    inbox_src, hist_src = pages.render_inbox(L.state(), ctx()), pages.render_history(L.state(), ctx())
    assert "<u>" not in inbox_src and "<u>" not in hist_src
    d = Doc(inbox_src)
    ids = [i.attrs["value"] for i in d.find_all("input", name="id")]
    assert ids == [hostile, hostile + "2"], ids
    field = d.find("input", type="number")
    assert field.attrs["id"] == "v-" + hostile + "2" and field.parent.find("label").attrs["for"] == field.attrs["id"]
    assert [i.attrs["value"].split(":")[0] for i in d.find_all("input", name="pair")] == [hostile, hostile + "2"]
    h = Doc(hist_src)
    assert [i.attrs["value"] for i in h.find_all("input", name="id")] == [hostile + "3", "ok1"]      # newest first


def test_only_plain_http_links_become_links():
    bad = ["javascript:alert(1)", "data:text/html,x", "//evil.example/x", "ftp://example.com/x",
           "HTTP://example.com/x", "http://", "https://example.com/a b", "https://example.com/x\n",
           'https://example.com/"onmouseover="x', "https://example.com/<b>", "vbscript:x", " https://example.com/x", "http:///etc/passwd", "https:///x"]
    good = ["https://example.com/a?x=1&y=2", "http://example.com:8080/p", "https://例え.jp/x"]
    L = Log()
    ev = [{"label": f"L{n}", "value": "v", "source": f"S{n}", "url": u} for n, u in enumerate(bad + good)]
    ev.append({"quote": "q", "source": "QS", "url": "javascript:alert(2)"})
    ev.append({"quote": "q2", "source": "QS2", "url": "https://example.com/quote"})
    L.raw_ask(ask_raw("confirm", "l1", evidence=ev))             # skipped validate_ask: a log edited by hand
    d = inbox(L)
    links = d.find_all("a", "src")
    hrefs = [a.attrs["href"] for a in links]
    assert hrefs == good + ["https://example.com/quote"], hrefs
    for a in links:
        assert a.attrs["target"] == "_blank" and a.attrs["rel"] == "noopener noreferrer"
        assert a.text() and not a.text().startswith("http")   # a source's name, not the raw address, when there is one
    assert "javascript:" not in d.source and "vbscript:" not in d.source
    spans = [s.text() for s in d.find_all("span", "src")]
    assert spans[:len(bad)] == [f"S{n}" for n in range(len(bad))]
    # a link with no source name is written by its host
    L2 = Log()
    L2.ask(ask_raw("confirm", "l2", evidence=[{"label": "A", "value": "b", "url": "https://docs.example.com/a/b"}]))
    assert inbox(L2).find("a", "src").text() == "docs.example.com"


def test_pages_is_pure_it_imports_no_io_and_no_clock():
    import ast
    tree = ast.parse(open(os.path.join(HERE, "..", "pages.py"), encoding="utf-8").read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
    assert mods == {"__future__", "html", "urllib.parse", "core", "i18n"}, mods
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"open", "print", "input", "exec", "eval", "__import__"}, calls
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("now", "today", "time", "environ")]


def test_multiline_agent_text_carries_the_class_that_keeps_its_line_breaks():
    L = full_log()
    a = L.state()["asks"]["c1"]["ask"]
    L.add("answer", "web:alice", id="c1", value="yes", subject=core.subject_hash(a), suggested=True, comment="x\ny")
    d = inbox(L)
    assert "txt" in d.find("aside", "say").find("p").classes
    assert all("txt" in i.find("p").classes for i in d.find_all("li", "msg"))
    assert all("txt" in p.classes for p in d.find_all("p", "why"))
    assert "txt" in ask_node(d, "Title of a1").find("blockquote", "quote").find("p").classes
    h = history(L)
    assert "txt" in h.find("dl", "rec").find("span", "txt").classes and "txt" in h.find("p", "why").classes


def test_forms_stop_the_browser_restoring_an_old_choice_over_the_suggestion():
    d = inbox(full_log())
    forms = d.find_all("form")
    assert len(forms) == 8 + 1 + 1                     # eight asks, one group button, the note
    assert all(f.attrs.get("autocomplete") == "off" and f.attrs["method"] == "post" for f in forms)
    assert all(f.attrs.get("autocomplete") == "off" for f in history(history_log()).find_all("form"))


# ----------------------------------------------------------------- history --

def history_log() -> Log:
    L = Log()
    a1 = L.ask(ask_raw("confirm", "h1", title="Applied one"))
    a2 = L.ask(ask_raw("choose", "h2", title="Waiting one"))
    a3 = L.ask(ask_raw("provide", "h3", title="Gated one", gate=GATE))
    L.ask(ask_raw("confirm", "h4", title="Withdrawn one"))
    a5 = L.ask(ask_raw("confirm", "h5", title="Changed one"))
    L.ask(ask_raw("confirm", "open1", title="Still open"))
    L.add("answer", "web:alice", id="h1", value="yes", subject=core.subject_hash(a1), suggested=True)
    L.add("applied", "agent:bot", id="h1", where="the ad plan")
    L.add("answer", "web:bob", id="h2", value="a", comment="Prefer A.\nSee the call.", subject=core.subject_hash(a2), suggested=False)
    L.add("answer", "web:alice", id="h3", value="4.6", subject=core.subject_hash(a3), suggested=False,
          gate={"ok": True, "verb": ["facts", "confirm", "unit_cost"], "message": "unit cost confirmed"})
    L.add("withdraw", "agent:bot", id="h4", reason="Replaced by a bigger question.")
    L.add("answer", "web:alice", id="h5", value="no", subject=core.subject_hash(a5), suggested=False)
    return L


def test_history_lists_closed_asks_newest_first_and_never_open_ones():
    for lang in LANGS:
        d = history(history_log(), lang)
        rows = d.find_all("details", "ask")
        titles = [r.find("span", "ttl").text() for r in rows]
        assert titles == ["Changed one", "Withdrawn one", "Gated one", "Waiting one", "Applied one"], titles
        assert all("done" in r.classes for r in rows) and not any("open" in r.attrs for r in rows)
        assert "Still open" not in d.main.text()
        assert d.find("p", "sub").text() == T("history.sub", lang, n=5, m=3)      # 5 closed, 3 not applied yet
        assert d.find("h1").text() == T("page.history", lang)
    e = history(Log())
    assert e.find("p", "big").text() == T("history.empty") and not e.find_all("details")


def test_history_shows_what_was_shown_who_when_and_how_it_was_answered():
    d = history(history_log())
    r = ask_node(d, "Waiting one")
    assert r.find("span", "age").text() == "bob · 2 h ago"
    assert r.find("span", "chip").text() == "Option A"                      # the label, not the stored value
    inner = r.find("div", "inner")
    assert inner.find("h3").text() == T("history.shown")
    assert inner.find("p", "why").text() == "Why for h2." and "Nothing changes for h2." in inner.find("p", "ifno").text()
    assert inner.find("div", "ev") is not None and inner.find("div", "ev").find("h3") is None
    rec = dict(zip([x.text() for x in inner.find("dl", "rec").find_all("dt")], [x.text() for x in inner.find("dl", "rec").find_all("dd")]))
    assert rec[T("form.answer")] == "Option A"
    assert rec[T("history.comment")] == "Prefer A. See the call."
    assert "Prefer A.\nSee the call." in "".join(inner.find("dl", "rec").find_all("span", "txt")[0].texts())
    assert rec[T("history.compare")] == T("history.changed")               # bob did not take the suggestion
    ok = dict(zip([x.text() for x in ask_node(d, "Applied one").find("dl", "rec").find_all("dt")],
                  [x.text() for x in ask_node(d, "Applied one").find("dl", "rec").find_all("dd")]))
    assert ok[T("history.compare")] == T("chip.suggested")


def test_history_says_where_the_loop_stands_and_offers_reopen_only_while_waiting():
    for lang in LANGS:
        d = history(history_log(), lang)
        # answered, not applied: waiting chip, and a Reopen form bound to that answer
        w = ask_node(d, "Waiting one")
        assert [c.text() for c in w.find("summary").find_all("span", "chip")][1] == T("state.waiting", lang)
        assert {"done", "waiting"} <= w.classes and not w.classes & {"applied", "withdrawn"}       # the row says it, too
        f = w.find("form", "reopen")
        assert f.attrs["method"] == "post" and f.attrs["action"] == "reopen"
        h = {i.attrs["name"]: i.attrs["value"] for i in f.find_all("input", type="hidden")}
        assert h == {"token": "tok123", "id": "h2", "answer_seq": str(history_log().state()["asks"]["h2"]["answer"]["seq"])}
        assert f.find("button").text() == T("reopen.button", lang)
        # applied: where, and no reopen
        a = ask_node(d, "Applied one")
        assert a.find("form", "reopen") is None
        assert T("state.applied_where", lang, where="the ad plan") in a.find("dl", "rec").text()
        assert a.find("summary").find_all("span", "chip")[1].text() == T("state.applied", lang)
        assert "applied" in a.classes and "waiting" not in a.classes
        # withdrawn: why, and no reopen and no answer
        x = ask_node(d, "Withdrawn one")
        assert x.find("form", "reopen") is None
        assert T("state.withdrawn_reason", lang, reason="Replaced by a bigger question.") in x.find("dl", "rec").text()
        assert x.find("summary").find("span", "chip").text() == T("state.withdrawn", lang)
        assert "withdrawn" in x.classes and not x.classes & {"waiting", "applied"}
        assert T("form.answer", lang) not in x.find("dl", "rec").text()
        assert len(d.find_all("form", "reopen")) == 2                        # h2 and h5; h1 is applied, h3 already wrote the system
    assert not history(history_log(), user=None).find_all("form")


def test_an_answer_the_system_already_took_is_not_offered_a_reopen_and_says_why():
    for lang in LANGS:
        d = history(history_log(), lang)
        g = ask_node(d, "Gated one")                     # answered through a gate that wrote
        assert g.find("form", "reopen") is None and not g.find_all("form")
        status = g.find("dl", "rec").find_all("dd")[-1]
        assert status.find("span", "chip").text() == T("state.waiting", lang)      # it still waits for the agent
        assert status.find("p", "hint").text() == T("reopen.written", lang)
        for other in ("Waiting one", "Changed one"):     # an answer with no gate can still be taken back, and says nothing of it
            r = ask_node(d, other)
            assert r.find("form", "reopen") and T("reopen.written", lang) not in r.text()
        assert T("reopen.written", lang) not in history(history_log(), lang, user=None).text()      # nobody to tell
    # only a gate that ran ok counts: a recorded gate that did not is still an answer that can be reopened (core agrees)
    L = Log()
    a = L.ask(ask_raw("confirm", "r1"))
    L.add("answer", "web:alice", id="r1", value="yes", subject=core.subject_hash(a), suggested=True, gate={"ok": False, "verb": ["x"]})
    assert history(L).find("form", "reopen") is not None and T("reopen.written") not in history(L).text()


def test_history_shows_the_gate_result_and_a_revised_ask():
    L = Log()
    v1 = L.ask(ask_raw("confirm", "r1", title="Title v1", why="Why v1."))
    L.ask(ask_raw("confirm", "r1", title="Title v2", why="Why v2."))            # the ask changed under the human
    L.add("answer", "web:alice", id="r1", value="yes", subject=core.subject_hash(v1), suggested=True, revised=True,
          gate={"ok": True, "verb": ["x"], "message": "written to the sheet"})
    d = history(L)
    r = ask_node(d, "Title v1")                                              # what was shown, not what it is now
    assert "Title v2" not in d.main.text() and r.find("p", "why").text() == "Why v1."
    rec = r.find("dl", "rec")
    assert T("history.revised") in rec.text() and T("history.gate") in rec.text() and "written to the sheet" in rec.text()
    # a gate result with no words still says it is done
    L2 = Log()
    a = L2.ask(ask_raw("confirm", "r2"))
    L2.add("answer", "web:alice", id="r2", value="yes", subject=core.subject_hash(a), suggested=True, gate={"ok": True, "verb": ["x"]})
    assert T("history.gate_done") in history(L2).find("dl", "rec").text()


def test_unsigned_answers_and_notes_get_a_warning_chip_and_unverifiable_ones_none():
    L = history_log()
    L.add("note", "web:alice", text="hello")
    L.events[-1]["sig"] = "0" * 64                                           # a note the console did not sign
    for e in L.events:
        if e["type"] == "answer" and e["id"] == "h2":
            e["value"] = "b"                                                 # tampered after signing
    for lang in LANGS:
        d = history(L, lang)
        chips = lambda t: [c.text() for c in ask_node(d, t).find("summary").find_all("span", "chip")]
        assert chips("Waiting one")[-1] == T("sig.bad", lang)
        assert T("sig.bad", lang) not in chips("Applied one") and T("sig.bad", lang) not in chips("Gated one")
        assert T("sig.bad", lang) not in chips("Withdrawn one")             # agent events are not signed at all
        n = inbox(L, lang).find("section", "msgs").find_all("li", "msg")[-1]
        assert n.find("span", "chip").text() == T("sig.bad", lang)
    # no secret: nothing can be said either way
    d = history(L, secret=None)
    assert not [c for c in d.find_all("span", "chip") if c.text() == T("sig.bad")]
    assert T("sig.bad") not in inbox(L, secret=None).main.text()


def test_history_lists_the_events_the_fold_ignored():
    L = history_log()
    L.add("answer", "web:alice", id="ghost", value="yes", subject="x")
    L.add("answer", "web:alice", id="h1", value="no", subject="x")           # already answered
    L.events.append(dict(L.events[0]))                                       # a copied line: its seq does not grow
    for lang in LANGS:
        d = history(L, lang)
        s = d.find("section", "problems")
        assert s.find("h2").text() == T("problems.head", lang)
        lines = [li.text() for li in s.find_all("li")]
        assert lines == [T("problems.line", lang, seq=str(len(L.events) - 2), why=T("problem.unknown_id", lang)),
                         T("problems.line", lang, seq=str(len(L.events) - 1), why=T("problem.not_open", lang)),
                         T("problems.line", lang, seq="1", why=T("problem.bad_seq", lang))]
    assert history(history_log()).find("section", "problems") is None


def test_history_is_capped():
    L = Log()
    for i in range(pages.SHOW_HISTORY + 5):
        L.ask(ask_raw("confirm", f"x{i}"))
        L.add("withdraw", "agent:bot", id=f"x{i}", reason="r")
    d = history(L)
    assert len(d.find_all("details", "ask")) == pages.SHOW_HISTORY
    assert d.find("p", "hint").text() == T("history.more", n=pages.SHOW_HISTORY)


# ------------------------------------------------------------------- ages --

def test_ages_are_worked_out_from_ctx_now_never_the_clock():
    L = Log()
    L.ask(ask_raw("confirm", "z"))
    for now, en, zh in (("2026-09-28T10:00:30Z", "just now", "刚刚"), ("2026-09-28T10:05:00Z", "5 min ago", "5 分钟前"),
                        ("2026-09-28T13:59:59Z", "3 h ago", "3 小时前"), ("2026-10-01T10:00:00Z", "3 d ago", "3 天前"),
                        ("2026-09-28T09:00:00Z", "just now", "刚刚")):           # a clock that is behind
        assert inbox(L, "en", now=now).find("span", "age").text() == en
        assert inbox(L, "zh", now=now).find("span", "age").text() == zh
    assert inbox(L, now="garbage").find("span", "age").text() == ""


def test_the_seconds_scale_agrees_with_datetime_across_month_year_and_leap_days():
    from datetime import datetime, timezone
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    stamps = ["2024-02-28T00:00:00Z", "2024-02-29T23:59:59Z", "2024-03-01T00:00:00Z", "2023-12-31T23:59:59Z",
              "2024-01-01T00:00:00Z", "2100-02-28T12:00:00Z", "2100-03-01T12:00:00Z", "2026-09-28T12:05:07Z",
              "1999-12-31T23:59:59Z", "2000-02-29T00:00:00Z", "2000-03-01T00:00:00Z"]
    real = [datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() for s in stamps]
    ours = [pages._secs(s) for s in stamps]
    for i in range(len(stamps)):
        for j in range(len(stamps)):
            assert ours[j] - ours[i] == real[j] - real[i], (stamps[i], stamps[j])
    assert pages._secs("nope") is None and pages._secs(None) is None


# ------------------------------------------------- result and error pages --

def test_result_page_words_each_line_and_has_one_way_out():
    for lang in LANGS:
        lines = [{"ok": True, "id": "a", "title": "Ask A", "value": "Yes", "code": None, "message": ""},
                 {"ok": True, "id": "b", "title": "Ask B", "value": "4.2 USD", "code": None, "message": "unit cost confirmed"},
                 {"ok": False, "id": "c", "title": "Ask C", "value": None, "code": "gate_refused", "message": "daily limit not confirmed"},
                 {"ok": False, "id": "d", "title": "Ask D", "value": None, "code": "changed", "message": "The ask changed since the page was loaded."},
                 {"ok": False, "id": "e", "title": "", "value": None, "code": "brand_new_code", "message": "x"},
                 {"ok": False, "id": "f", "title": "Ask F", "value": None, "code": "bad_value", "message": "m", "reason": "range"},
                 {"ok": False, "id": "g", "title": "Ask G", "value": None, "code": "bad_value", "message": "m"},
                 {"ok": False, "id": "h", "title": "Ask H", "value": None, "code": "gate_changed", "message": "it would write 9, not 4"},
                 {"ok": False, "id": "i", "title": "Ask I", "value": None, "code": "gate_timeout", "message": "it may have written"}]
        d = Doc(pages.render_result(ctx(lang), lines))
        assert d.find("h1").text() == T("result.check", lang)           # a gate_timeout line: something may have been written
        assert Doc(pages.render_result(ctx(lang), lines[:8])).find("h1").text() == T("result.some", lang)
        late = [dict(lines[0], ok=False, code="not_recorded", message="")]
        assert Doc(pages.render_result(ctx(lang), late)).find("h1").text() == T("result.check", lang)
        res = d.find_all("li", "res")
        assert [("ok" in r.classes, "bad" in r.classes) for r in res] == [(True, False)] * 2 + [(False, True)] * 7
        assert res[0].text() == f"Ask A {T('result.line_ok', lang, value='Yes')}"
        assert T("result.wrote", lang, message="unit cost confirmed") in res[1].text()
        assert T("err.gate_refused", lang) in res[2].text()
        assert T("result.refused", lang, message="daily limit not confirmed") in res[2].text()   # the harness's own words
        assert res[3].text() == f"Ask D {T('err.changed', lang)}"
        assert "The ask changed since" not in res[3].text()                # core's English is not the console's word
        assert res[4].text() == T("err.other", lang)                       # an unknown code is not a crash
        assert res[5].text() == f"Ask F {T('bad_value.range', lang)}" and res[6].text() == f"Ask G {T('err.bad_value', lang)}"
        for r, code, msg in ((res[7], "gate_changed", "it would write 9, not 4"), (res[8], "gate_timeout", "it may have written")):
            assert T("err." + code, lang) in r.text() and T("result.refused", lang, message=msg) in r.text(), code
        back = [a for a in d.main.find_all("a")]
        assert [a.attrs["href"] for a in back] == ["./"] and back[0].text() == T("result.back", lang)
        assert not d.find_all("form")
    only = [{"ok": True, "id": "a", "title": "t", "value": None, "code": None, "message": ""}]
    assert Doc(pages.render_result(ctx(), only)).find("li", "res").text() == f"t {T('result.ok')}"
    assert Doc(pages.render_result(ctx(), only)).find("h1").text() == T("result.ok")
    bad = [dict(only[0], ok=False, code="not_open")]
    assert Doc(pages.render_result(ctx(), bad)).find("h1").text() == T("result.none")


def test_a_refusal_gives_the_typed_comment_back_escaped_and_a_saved_answer_does_not():
    typed = "We call them <b>bulk</b> bags,\nnot loose leaf."
    for lang in LANGS:
        lines = [{"ok": False, "id": "a", "title": "Ask A", "value": None, "code": "changed", "message": "", "comment": typed},
                 {"ok": False, "id": "b", "title": "Ask B", "value": None, "code": "gate_refused", "message": "daily limit", "comment": "again"},
                 {"ok": True, "id": "c", "title": "Ask C", "value": "Yes", "code": None, "message": "", "comment": "kept in the log"},
                 {"ok": False, "id": "d", "title": "Ask D", "value": None, "code": "not_open", "message": "", "comment": ""},
                 {"ok": False, "id": "e", "title": "Ask E", "value": None, "code": "not_open", "message": ""}]
        d = Doc(pages.render_result(ctx(lang), lines))
        res = d.find_all("li", "res")
        one = res[0].find_all("p")[1:]                                                       # (the first is the ask's title)
        assert one[-1].text() == T("result.comment", lang, comment=typed.replace("\n", " "))
        assert "txt" in one[-1].classes and f"bulk</b> bags,\nnot" in "".join(one[-1].texts())      # its line break is kept
        assert "<b>bulk</b>" not in d.source and "&lt;b&gt;bulk&lt;/b&gt;" in d.source
        assert one[0].text() == T("err.changed", lang)                                    # the console's sentence comes first
        two = [p.text() for p in res[1].find_all("p")[1:]]
        assert two == [T("err.gate_refused", lang), T("result.refused", lang, message="daily limit"), T("result.comment", lang, comment="again")]
        assert "kept in the log" not in res[2].text()                                       # a saved answer's comment is saved, not repeated
        assert res[3].text() == res[4].text().replace("Ask E", "Ask D") == f"Ask D {T('err.not_open', lang)}"    # none typed, no line


def test_a_harness_that_gave_no_clear_answer_says_the_answer_may_have_been_saved():
    assert "gate_unsure" in pages.UNSURE and "gate_unsure" in pages.HARNESS_SAYS
    for lang in LANGS:
        line = {"ok": False, "id": "a", "title": "Ask A", "value": None, "code": "gate_unsure", "message": "garbled <i>output</i>"}
        d = Doc(pages.render_result(ctx(lang), [line]))
        assert d.find("h1").text() == T("result.check", lang)                               # check before going on, not "nothing was saved"
        assert d.find("li", "res").text() == f"Ask A {T('err.gate_unsure', lang)} {T('result.refused', lang, message='garbled <i>output</i>')}"
        assert "<i>output</i>" not in d.source
        e = Doc(pages.render_error(ctx(lang), "gate_unsure", "garbled <i>output</i>"))
        assert e.find("div", "res").find("p").text() == T("err.gate_unsure", lang)
        assert T("result.refused", lang, message="garbled <i>output</i>") in e.find("div", "res").text() and e.find("details", "tech") is None


def test_an_error_page_says_nothing_about_who_is_asking():
    for lang in LANGS:
        for code in ("forbidden", "not_found", "too_large", "bad_host", "not_open", "server_error"):
            for user in (None, "alice"):
                d = Doc(pages.render_error(ctx(lang, user=user), code))
                foot = d.find("footer", "foot")
                assert [p.text() for p in foot.find_all("p")] == [T("foot.line", lang)], (code, user)   # no "Read-only", no name
                assert T("foot.ro", lang) not in d.text() and "alice" not in d.text()
    # the pages that do know the person still say so
    assert T("foot.user", user="alice") in Doc(pages.render_result(ctx(), [])).find("footer", "foot").text()
    assert T("foot.user", user="alice") in inbox(full_log()).find("footer", "foot").text()
    assert T("foot.ro") in inbox(full_log(), user=None).find("footer", "foot").text()


def test_error_page_words_the_code_and_keeps_technical_text_folded():
    for lang in LANGS:
        for code in list(core.CODES) + ["not_found", "method_not_allowed", "bad_host", "server_error"]:
            d = Doc(pages.render_error(ctx(lang), code))
            assert d.find("h1").text() == T("err.head", lang)
            assert d.find("div", "res").find("p").text() == T("err." + code, lang), code
        d = Doc(pages.render_error(ctx(lang), "corrupt_log", "events.jsonl line 3 is not a valid event"))
        assert "jsonl" not in "".join(d.main.without("tech"))
        assert "events.jsonl line 3" in d.find("details", "tech").text()
        assert [a.attrs["href"] for a in d.main.find_all("a")] == ["./"]
        assert Doc(pages.render_error(ctx(lang), "no_such_code")).find("div", "res").text() == T("err.other", lang)
        assert Doc(pages.render_error(ctx(lang), "not_open")).find("details", "tech") is None


def test_error_page_shows_the_harnesss_own_words_for_its_refusals_but_folds_the_rest():
    for lang in LANGS:
        for code in ("gate_refused", "gate_changed", "gate_timeout"):
            d = Doc(pages.render_error(ctx(lang), code, "the harness said <b>no</b>"))
            box = d.find("div", "res")
            assert box.find("p").text() == T("err." + code, lang)                # the console's sentence first
            assert T("result.refused", lang, message="the harness said <b>no</b>") in box.text()      # its words under it, as text
            assert d.find("details", "tech") is None and "<b>no</b>" not in d.source
        # another code keeps its detail folded, and a refusal with no words has no empty line
        assert "the harness said" not in Doc(pages.render_error(ctx(lang), "changed", "the harness said")).find("div", "res").text()
        assert len(Doc(pages.render_error(ctx(lang), "gate_timeout")).find("div", "res").find_all("p")) == 1


def test_answer_label_words_an_answer_the_way_the_human_saw_it():
    L = full_log()
    asks = {i: v["ask"] for i, v in L.state()["asks"].items()}
    for lang in LANGS:
        assert pages.answer_label(lang, asks["c1"], "yes") == T("ans.yes", lang)
        assert pages.answer_label(lang, asks["c1"], "no") == T("ans.no", lang)
        assert pages.answer_label(lang, asks["a1"], "yes") == T("ans.approve", lang)
        assert pages.answer_label(lang, asks["a1"], "no") == T("ans.reject", lang)
        assert pages.answer_label(lang, asks["o1"], "c") == "Option C"
        assert pages.answer_label(lang, asks["o1"], "gone") == "gone"          # an option that no longer exists
        assert pages.answer_label(lang, asks["p1"], "4.6") == "4.6 USD"
        assert pages.answer_label(lang, asks["p4"], "x y") == "x y"
    assert pages.answer_label("fr", asks["c1"], "yes") == "Yes"                   # an unknown language is English
    # the same words the pages use for it
    d = history(_answered(L, "a1", "no"), "zh")
    assert d.find("span", "chip").text() == T("ans.reject", "zh")


def _answered(L, id, value):
    L.add("answer", "web:alice", id=id, value=value, subject=core.subject_hash(L.state()["asks"][id]["ask"]), suggested=False)
    return L


# ------------------------------------------------- a real store, end to end --

def test_pages_render_what_a_real_store_wrote():
    with _t.tmpstore() as st:
        secret = st.ensure_secret()
        st.post("bot", [ask_raw("confirm", "r1", group="G"), ask_raw("confirm", "r2", group="G"),
                        ask_raw("provide", "r3", group="G", gate=GATE)])
        shown = st.state()["asks"]["r1"]["hash"]
        st.answer("alice", "r1", "yes", shown=shown, comment="fine")
        st.applied("bot", ["r1"], "the plan")
        st.answer("alice", "r3", "4.6", shown=st.state()["asks"]["r3"]["hash"],
                  gate={"ok": True, "verb": ["facts", "confirm", "unit_cost"], "message": "done"})
        st.say("bot", "hi")
        st.note("alice", "thanks")
        s = st.state()
        c = ctx(secret=secret, now=core.now())
        d = Doc(pages.render_inbox(s, c))
        assert [a.find("span", "ttl").text() for a in d.find_all("details", "ask")] == ["Title of r2"]
        h = Doc(pages.render_history(s, c))
        assert [a.find("span", "ttl").text() for a in h.find_all("details", "ask")] == ["Title of r3", "Title of r1"]
        assert T("sig.bad") not in h.main.text()                                          # the real signatures verify
        assert h.find("body").attrs["data-seq"] == str(s["seq"])
        assert d.find("aside", "say").find("p", "txt").text() == "hi"                       # the latest word is on top…
        assert [m.find("p", "txt").text() for m in d.find_all("li", "msg")] == ["thanks"]   # …not repeated below


if __name__ == "__main__":
    _t.main(globals())
