"""ui_rules.tsv is the console's UI checklist; each check here is named after
its rule id and looks at what a machine can: pages rendered from fixtures in
both languages, the stylesheet parsed, and console.js run under node against
a stub DOM (skipped, with a line saying so, where node is not installed).
The rules a machine cannot judge (is it calm, is it clear) are for a person
looking at the pages in a browser; this file keeps the rest from quietly
breaking.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _t  # noqa: E402
import core  # noqa: E402
import i18n  # noqa: E402
import pages  # noqa: E402
from test_pages import Doc, Log, Node, T, ask_raw, ctx, full_log, history_log  # noqa: E402

LANGS = i18n.LANGS


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------- the css --

def parse_css(text):
    """[(media, [selectors], {prop: value})], @media flattened, comments gone."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []

    def block(s, media):
        i = 0
        while True:
            j = s.find("{", i)
            if j < 0:
                return
            depth, k = 1, j + 1
            while depth:
                depth += (s[k] == "{") - (s[k] == "}")
                k += 1
            head, body = s[i:j].strip(), s[j + 1:k - 1]
            if head.startswith("@media"):
                block(body, head[6:].strip())
            elif not head.startswith("@"):
                decls = {}
                for d in body.split(";"):
                    if ":" in d:
                        p, v = d.split(":", 1)
                        decls[p.strip()] = v.strip()
                rules.append((media, [x.strip() for x in head.split(",")], decls))
            i = k
    block(text, "")
    return rules


CSS = parse_css(read("static", "console.css"))


def decls(selector, media=""):
    """Everything set on exactly this selector (later rules win), under this media condition."""
    out = {}
    for m, sels, d in CSS:
        if m == media and selector in sels:
            out.update(d)
    return out


def px(v):
    m = re.fullmatch(r"([\d.]+)(px|rem)", v.strip())
    assert m, v
    return float(m.group(1)) * (16 if m.group(2) == "rem" else 1)


def css_classes():
    return {c for _, sels, _ in CSS for s in sels for c in re.findall(r"\.([A-Za-z_][\w-]*)", s)}


# ---------------------------------------------------------- the fixtures --

def numbers_log():
    L = Log()
    L.ask(ask_raw("confirm", "t1", evidence=[{"table": {"columns": ["Name", "Units"], "rows": [["A", "12"], ["B", "7"]]}}]))
    return L


def over_log():
    L = Log()
    for i in range(12):
        L.ask(ask_raw("confirm", f"k{i}"))
    return L


def problem_log():
    L = history_log()
    L.add("answer", "web:alice", id="ghost", value="yes", subject="x")
    return L


RES_TITLE, RES_VALUE, RES_MESSAGE, ERR_DETAIL = "Ask title here", "Given value here", "Harness message here", "Technical detail here"


def every_page(lang):
    """A page of every kind, in every state that draws something different."""
    c = ctx(lang, flash={"kind": "ok", "text": "Done"})
    return [
        pages.render_inbox(full_log().state(), c),
        pages.render_inbox(full_log().state(), ctx(lang, user=None)),
        pages.render_inbox(full_log().state(), ctx(lang, relay=None)),
        pages.render_inbox(full_log().state(), ctx(lang, relay=[["queue", "approve"]])),
        pages.render_inbox(Log().state(), ctx(lang)),
        pages.render_inbox(over_log().state(), ctx(lang)),
        pages.render_inbox(numbers_log().state(), ctx(lang)),
        pages.render_history(problem_log().state(), ctx(lang)),
        pages.render_history(Log().state(), ctx(lang)),
        pages.render_result(ctx(lang), [{"ok": True, "id": "a", "title": RES_TITLE, "value": RES_VALUE, "code": None, "message": RES_MESSAGE},
                                        {"ok": False, "id": "b", "title": RES_TITLE, "value": None, "code": "gate_refused", "message": RES_MESSAGE}]),
        pages.render_error(ctx(lang), "corrupt_log", ERR_DETAIL),
        pages.render_error(ctx(lang), "gate_timeout", RES_MESSAGE),
    ]


def docstring_classes():
    doc = ast.get_docstring(ast.parse(read("pages.py")))
    block = doc[doc.index("CSS classes"):]
    names = set()
    for line in block.splitlines()[1:]:
        m = re.match(r"^\s+([a-z][a-z\- ]*?)\s{2,}\S", line)
        if m:
            names |= set(m.group(1).split())
    return names


BANNED = [re.compile(p, re.I) for p in (r"--\w", r"\bjson\b", r"\.(py|jsonl?|tsv)\b", r"exit code", r"\b[a-z]+_[a-z]+\b",
                                        r"\$\w", r"\bargv\b", r"\bflag\b", r"\bstdin\b", r"\bcommand\b")]


# ------------------------------------------------------------ the rules --

def rules():
    rows = [line.rstrip("\n").split("\t") for line in read("ui_rules.tsv").splitlines()]
    return rows[0], rows[1:]


def test_the_rules_file_is_the_checklist_and_each_rule_has_a_check_named_after_it():
    head, rows = rules()
    assert head == ["id", "when", "do", "example"]
    assert [r[0] for r in rows] == [f"U{n:02d}" for n in range(1, 17)]
    for r in rows:
        assert len(r) == 4 and all(c.strip() and c == c.strip() for c in r), r
    mine = {n for n in globals() if re.fullmatch(r"test_u\d\d_\w+", n)}
    for rid, *_ in rows:
        assert any(n.startswith(f"test_{rid.lower()}_") for n in mine), f"no check for {rid}"
    assert {n[5:8].upper() for n in mine} == {r[0] for r in rows}      # and none for a rule that does not exist


def test_u01_plain_words_technical_detail_only_in_folded_tech():
    for w in i18n.WORDS.values():
        for lang in LANGS:
            for rx in BANNED:
                assert not rx.search(w[lang]), (w[lang], rx.pattern)
    for lang in LANGS:
        for src in every_page(lang):
            d = Doc(src)
            visible = " ".join(t for t in d.without("tech") if t.strip())
            for rx in BANNED:
                assert not rx.search(visible), (lang, rx.pattern, rx.search(visible).group(0))
    # ...and the technical detail is where it must be: folded, holding what would otherwise leak
    d = Doc(pages.render_inbox(full_log().state(), ctx()))
    tech = d.find("details", "tech")
    assert "open" not in tech.attrs and "unit_cost" in tech.text() and "facts confirm" in tech.text()
    assert "unit_cost" not in " ".join(d.without("tech"))
    e = Doc(pages.render_error(ctx(), "corrupt_log", "events.jsonl line 3"))
    assert "events.jsonl" in e.find("details", "tech").text() and "jsonl" not in " ".join(e.without("tech"))


def test_u02_one_column_top_down_grouped_never_a_grid_of_cards():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        assert len(d.find_all("main")) == 1
        order = [(k.attrs.get("class") or k.tag).split()[0] for k in d.main.kids if isinstance(k, Node)]
        assert order.index("say") < order.index("jump") < order.index("group") < order.index("msgs")
        for g in d.find_all("section", "group"):
            assert [k.tag for k in g.find("div", "list").kids if isinstance(k, Node)] == \
                   ["details"] * len(g.find_all("details", "ask"))
        assert all(a.parent.classes == {"list"} for a in d.find_all("details", "ask"))
        assert len([a for a in d.find_all("details", "ask") if "open" in a.attrs]) == 1
        assert not [n for n in d.walk() if any("card" in c or "grid" in c for c in n.classes)]
        for tb in d.find_all("table"):                    # a table lives inside a row, in its own scroll box
            assert tb.inside("div", "scroll") and tb.inside("details", "ask") and tb.inside("figure", "tablebox")
    for _, sels, dcl in CSS:                              # the css does not lay rows out as a grid either
        if any(s in (".list", ".group", "main", ".ask") for s in sels):
            assert "grid" not in dcl.get("display", "") and "grid-template-columns" not in dcl, sels
    assert px(decls("main")["max-width"]) == 46 * 16


def test_u03_suggestion_is_preselected_and_marked_and_no_is_explained_before_any_control():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        for a in d.find_all("details", "ask"):
            inner = a.find("div", "inner")
            walk = list(inner.walk())
            form = inner.find("form")
            assert inner.find("p", "ifno") and walk.index(inner.find("p", "ifno")) < walk.index(form)
            rec = a.find("summary").find_all("span", "chip")
            if any("ok" in c.classes for c in rec):                    # it has a suggestion...
                assert inner.find("p", "suggest") and walk.index(inner.find("p", "suggest")) < walk.index(form)
                radios = form.find_all("input", type="radio")
                if radios:                                             # ...so one option is already chosen
                    assert len([r for r in radios if "checked" in r.attrs]) == 1
                    assert form.find("span", "chip") is not None       # ...and marked
                else:
                    assert "value" in [i for i in form.find_all("input") if i.attrs["type"] != "hidden"][0].attrs


def test_u04_fixed_answers_are_radios_typing_only_for_provide_with_type_and_unit():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        for a in d.find_all("details", "ask"):
            f = a.find("form", "answer")
            kinds = {i.attrs["type"] for i in f.find_all("input") if i.attrs["type"] != "hidden"}
            if a.find("span", "unit") or a.find("input", type="date") or a.find("input", type="url") or a.find("input", type="text") \
                    or a.find("input", type="number"):
                assert kinds <= {"number", "date", "url", "text"} and len(kinds) == 1     # one typed input
            else:
                assert kinds == {"radio"}                                                  # a fixed set: radios
            assert not any("pattern" in i.attrs or "|" in i.attrs.get("placeholder", "") for i in f.find_all("input"))
        p1 = d.find("span", "unit")
        assert p1.text() == "USD" and p1.parent.find("input", type="number").attrs["min"] == "0"


def test_u05_a_comment_is_optional_and_an_empty_one_is_fine():
    for f in Doc(pages.render_inbox(full_log().state(), ctx())).find_all("form", "answer"):
        ta = f.find("textarea", name="comment")
        assert ta is not None and "required" not in ta.attrs and ta.inside("details", "more")
        assert "open" not in ta.parent.attrs and T("comment.toggle") in ta.parent.find("summary").text()
    # every required control in the form is a radio or the typed answer; the comment can stay empty
    for f in Doc(pages.render_inbox(full_log().state(), ctx())).find_all("form", "answer"):
        assert not [n for n in f.walk() if "required" in n.attrs and n.attrs.get("name") == "comment"]


def test_u06_an_answered_ask_leaves_the_waiting_page_and_is_in_history_with_all_of_it():
    L = full_log()
    before = Doc(pages.render_inbox(L.state(), ctx()))
    assert "Title of c1" in before.main.text()
    L.add("answer", "web:alice", id="c1", value="yes", subject=core.subject_hash(L.state()["asks"]["c1"]["ask"]),
          suggested=True, comment="Agreed.")
    for lang in LANGS:
        after = Doc(pages.render_inbox(L.state(), ctx(lang)))
        assert "Title of c1" not in after.main.text() and len(after.find_all("details", "ask")) == 7
        h = Doc(pages.render_history(L.state(), ctx(lang)))
        row = [r for r in h.find_all("details", "ask") if "Title of c1" in r.text()][0]
        text = row.text()
        assert "Why for c1." in text and "Nothing changes for c1." in text            # what was shown
        assert "alice" in row.find("span", "age").text()                              # who, and when
        assert T("chip.suggested", lang) in text                                       # suggested, not changed
        assert T("state.waiting", lang) in text and row.find("form", "reopen")         # and where the loop stands
    L.add("applied", "agent:bot", id="c1", where="the plan")
    assert "the plan" in Doc(pages.render_history(L.state(), ctx())).find("dl", "rec").text()
    # ...and where the loop stands shows on the row's edge too, not only in a chip
    rows = Doc(pages.render_history(history_log().state(), ctx())).find_all("details", "ask")
    assert {r.find("span", "ttl").text(): (r.classes & {"waiting", "applied", "withdrawn"}) for r in rows} == {
        "Changed one": {"waiting"}, "Withdrawn one": {"withdrawn"}, "Gated one": {"waiting"},
        "Waiting one": {"waiting"}, "Applied one": {"applied"}}
    edge = lambda sel: decls(sel)["box-shadow"]
    assert "--warn" in edge(".done.waiting") and "--warn" in edge(".done.waiting[open]")
    assert "--ok" in edge(".done.applied") and "--ok" in edge(".done.applied[open]")
    assert decls(".done.withdrawn .ttl")["color"] == "var(--ink2)"


def test_u07_the_page_says_how_much_of_the_budget_is_used_and_when_it_is_over():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        assert d.find("p", "sub").text() == T("budget.line", lang, open=8, max=core.MAX_OPEN)
        assert len(d.find("span", "meter").find_all("b")) == 8
        o = Doc(pages.render_inbox(over_log().state(), ctx(lang)))
        assert o.find("p", "sub").text() == T("budget.over", lang, open=12, over=2, max=core.MAX_OPEN)
        assert "over" in o.find("span", "meter").classes
    assert decls(".meter.over + .sub")["color"] == "var(--bad)"


def test_u08_same_bar_everywhere_usable_at_375_no_sideways_scroll_big_tap_targets():
    bars = set()
    for lang in LANGS:
        for src in every_page(lang):
            d = Doc(src)
            bar = d.find("header", "bar")
            tabs = bar.find_all("a")
            assert [a.attrs["href"] for a in tabs if a.parent.tag == "nav"] == ["./", "history"]
            assert bar.find("a", "lang") and bar.find("a", "brand")
            assert d.find("meta", name="viewport").attrs["content"] == "width=device-width, initial-scale=1"
            bars.add((lang, tuple((a.attrs["href"].split("?")[0], a.text().split()[0]) for a in tabs if a.parent.tag == "nav")))
    assert len(bars) == 2                                  # one bar per language, nothing page-specific in it
    for sel in (".btn", ".opt", "summary", ".tabs a", ".lang", ".jump a", "textarea",
                'input[type="number"]', 'input[type="text"]', 'input[type="date"]', 'input[type="url"]'):
        got = [d.get("min-height") for m, sels, d in CSS if not m and any(s == sel for s in sels) and "min-height" in d]
        assert got and all(px(v) >= 44 for v in got), (sel, got)
    small = {d["min-height"] for m, sels, d in CSS if m and "min-height" in d}
    assert all(px(v) >= 44 for v in small)
    assert decls(".bar")["position"] == "sticky"
    # a source link is small text: an invisible area around it makes it a 44 px target
    assert decls("a.src")["position"] == "relative"
    reach = px(decls("a.src::after")["inset"].split()[0].lstrip("-"))
    assert decls("a.src::after")["position"] == "absolute" and 2 * reach + 15 >= 44
    assert decls(".age", "(max-width: 480px)")["margin-left"] == "0"        # an age that wraps starts its line
    assert decls(".ask > summary:hover", "(hover: hover)")                   # a hover a tap cannot leave stuck
    assert not decls(".ask > summary:hover")
    phone = decls(".tabs", "(max-width: 480px)")                # the tabs get their own row under 480 px
    assert phone["order"] == "3" and phone["flex"].endswith("100%")
    assert decls(".scroll")["overflow-x"] == "auto"           # a wide table scrolls in its box, not the page
    for sel in ("html", "body", "main"):
        assert "hidden" not in decls(sel).get("overflow-x", "") and "hidden" not in decls(sel).get("overflow", "")
    assert decls("body")["overflow-wrap"] == "anywhere"        # a long word cannot push the page sideways
    assert decls(".opt")["display"] == "flex" and "width" not in decls(".opt")     # radios are full-width rows
    assert [d for m, sels, d in CSS if any(":has(input:checked)" in s for s in sels) and "border-color" in d]
    assert decls(":focus-visible")["outline"].startswith("3px solid")       # the ring is on everything, and visible
    assert decls("*", "(prefers-reduced-motion: reduce)")["transition"].startswith("none")
    fonts = decls(":root")["--font"]
    assert all(f in fonts for f in ("PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC"))
    assert decls("body")["font"].startswith("16px/1.55")


def test_u08_the_stylesheet_defines_every_colour_it_uses_in_both_schemes():
    css = re.sub(r"/\*.*?\*/", "", read("static", "console.css"), flags=re.S)
    used = set(re.findall(r"var\((--[\w-]+)\)", css))
    light = set(decls(":root"))
    dark = set(decls(":root", "(prefers-color-scheme: dark)"))
    assert used <= light, sorted(used - light)
    assert (light - {"--font", "--mono", "color-scheme"}) == dark, sorted((light - dark) ^ (dark - light))
    assert decls(":root")["color-scheme"] == "light dark"


def luminance(hex_):
    if len(hex_) == 4:                                   # #fff
        hex_ = "#" + "".join(c * 2 for c in hex_[1:])
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
    return .2126 * lin(r) + .7152 * lin(g) + .0722 * lin(b)


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def test_u08_text_and_buttons_are_readable_in_both_schemes():
    for scheme, tok in (("light", decls(":root")), ("dark", decls(":root", "(prefers-color-scheme: dark)"))):
        pairs = [("--ink", "--bg"), ("--ink", "--surface"), ("--ink2", "--surface"), ("--ink2", "--sunk"),
                 ("--accent", "--surface"), ("--on-accent", "--accent"), ("--on-warn", "--warn-solid"),
                 ("--ok", "--ok-bg"), ("--warn", "--warn-bg"), ("--bad", "--bad-bg"), ("--ink", "--tint")]
        for fg, bg in pairs:
            assert contrast(tok[fg], tok[bg]) >= 4.5, (scheme, fg, bg, round(contrast(tok[fg], tok[bg]), 2))


def test_u09_the_consoles_words_come_only_from_the_dictionary_the_agents_are_shown_as_written():
    # pages.py has no sentence of its own
    tree = ast.parse(read("pages.py"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            assert not re.fullmatch(r"[A-Za-z][A-Za-z ,.'’:-]*", node.value) or " " not in node.value.strip(), node.value
    for name in ("pages.py", "serve.py"):
        if os.path.exists(os.path.join(ROOT, name)):
            src = read(name)
            assert not re.search(r"""(?:\bt|\braw)\(\s*f["']""", src), f"{name}: a key built in an f-string cannot be checked"
    # a Chinese page has no English of the console's own: take away everything the agent or the operator wrote
    L = full_log()
    L.add("answer", "web:alice", id="c1", value="yes", subject=core.subject_hash(L.state()["asks"]["c1"]["ask"]),
          suggested=True, comment="Agreed to this.", gate={"ok": True, "verb": ["x"], "message": "written fine"})
    L.add("applied", "agent:bot", id="c1", where="the plan today")
    L.add("withdraw", "agent:bot", id="c2", reason="Not needed any more.")
    agent = set()
    fields = {"title", "why", "group", "if_no", "effect", "label", "value", "source", "quote", "caption", "columns",
              "rows", "note", "because", "text", "comment", "where", "reason", "message", "unit", "ask", "gate",
              "evidence", "options", "recommend", "expect"}

    def strings(x, agents=False):                       # only what an agent or a person wrote, and only whole words' worth
        if isinstance(x, str):
            if agents and len(x) >= 4:
                agent.add(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                strings(v, agents or k in fields)
        elif isinstance(x, list):
            for v in x:
                strings(v, agents)
    for log in (L, over_log(), numbers_log(), problem_log()):
        for e in log.events:
            strings({k: v for k, v in e.items() if k in fields})
    agent |= {"Northwind Console", "alice", "Done", RES_TITLE, RES_VALUE, RES_MESSAGE, ERR_DETAIL}
    for lang in ("zh", "en"):
        srcs = every_page(lang) + [pages.render_inbox(L.state(), ctx(lang)), pages.render_history(L.state(), ctx(lang))]
        english_runs = 0
        for src in srcs:
            d = Doc(src)
            for t in d.without("tech"):
                for a in sorted(agent, key=len, reverse=True):
                    t = t.replace(a, " ")
                if re.search(r"[A-Za-z]{3,}(?: +[A-Za-z]{2,})+", t):
                    english_runs += 1
                    assert lang == "en", f"English in a Chinese page: {t!r}"
        assert (english_runs > 0) == (lang == "en")            # (the check does find English when there is some)
    # and the agent's words are exactly as written: escaped, nothing else done to them
    L2 = Log()
    L2.ask(ask_raw("confirm", "q", title="  <b>Odd</b> title & \"more\"  "[2:-2], why="Line 1\nLine 2  spaced"))
    d = Doc(pages.render_inbox(L2.state(), ctx("zh")))
    assert d.find("span", "ttl").text() == '<b>Odd</b> title & "more"'
    assert "".join(d.find("p", "why").texts()) == "Line 1\nLine 2  spaced"


def test_u09_every_class_the_pages_use_is_documented_and_styled_and_nothing_else_is():
    used = set()
    for lang in LANGS:
        for src in every_page(lang):
            for n in Doc(src).walk():
                used |= n.classes
    js = read("static", "console.js")
    for lit in re.findall(r'className\s*=\s*"([^"]+)"|make\([^,()]+,\s*"[a-z]+",\s*"([^"]+)"', js):
        for cls in lit:
            used |= set(cls.split())
    documented = docstring_classes()
    styled = css_classes()
    assert used == documented, (sorted(used - documented), sorted(documented - used))
    assert used == styled, (sorted(used - styled), sorted(styled - used))
    assert len(used) > 60


# ------------------------------------------------ console.js, run against a stub --

def script_code():
    js = read("static", "console.js")
    return js, re.sub(r"//[^\n]*|/\*.*?\*/", "", js, flags=re.S)


_RUN: list = []


def script_run():
    """What console.js did in each scenario of NODE_SCRIPT, run under node
    against a tiny stub DOM; None (with one line saying so) where node is not
    installed. Run once, shared by the checks below."""
    if not _RUN:
        if not shutil.which("node"):
            print("SKIP: node is not installed; console.js was not run")
            _RUN.append(None)
        else:
            with _t.tmpdir() as tmp:
                for name, src in (("console.js", script_code()[0]), ("run.js", NODE_SCRIPT)):
                    with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                        f.write(src)
                chk = subprocess.run(["node", "--check", os.path.join(tmp, "console.js")], capture_output=True, text=True)
                assert chk.returncode == 0, chk.stderr
                run = subprocess.run(["node", os.path.join(tmp, "run.js"), os.path.join(tmp, "console.js")],
                                     capture_output=True, text=True, timeout=30)
                assert run.returncode == 0, run.stderr + run.stdout
                _RUN.append(json.loads(run.stdout))
    return _RUN[0]


BAR = {"cls": "banner live", "text": "CHANGED", "role": "status", "href": "http://x/inbox"}


def test_u10_the_script_writes_no_words_and_reloads_only_behind_a_check_that_nothing_was_touched():
    d = Doc(pages.render_inbox(full_log().state(), ctx()))
    b = d.find("body").attrs
    assert b["data-poll"] == "poll" and b["data-new"] == T("banner.new") and b["data-lost"] == T("banner.lost")
    assert b["data-seq"] and b["data-open"]
    js, code = script_code()
    assert len(js.splitlines()) <= 100, len(js.splitlines())                     # a script this small can be read whole
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write", "new Function"):
        assert bad not in js, bad
    assert "try" in code and "catch" in code                                    # it degrades to nothing
    assert re.search(r"untouched\(\)\)\s*location\.reload\(\);\s*else\s", code), "the poll's reload must sit behind the untouched check"
    assert code.count("location.reload") == 2                                   # that one, and a page restored from the back button
    assert "dataset.new" in code and "dataset.lost" in code and "dataset.busy" in code
    for check in ("window.scrollY", "forms.some(edited)", "shape() === was"):    # what "untouched" is made of
        assert check in re.search(r"function untouched\(\)[^\n]*", code).group(0), check
    words = re.sub(r'className\s*=\s*"[^"]*"', "", code)                       # (class names are not words)
    words = re.sub(r'make\([^,()]+,\s*"[a-z]+",\s*"[^"]+"', "", words)
    for lit in re.findall(r"""(?<!\\)(["'])((?:(?!\1).)*)\1""", words):          # it writes no words of its own
        assert lit[1] == "use strict" or not re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", lit[1]), lit[1]
    got = script_run()
    if got is None:
        return
    assert got["same_seq"] == {"reloads": 0, "title": "(3) Waiting · X", "bars": []}
    assert got["new_seq"] == {"reloads": 1, "title": "(4) Waiting · X", "bars": []}     # untouched: it follows the log by itself
    assert set(got["touched"]) == {"typed", "scrolled", "opened", "closed"}
    for name, r in got["touched"].items():                       # touched in any of these ways: left alone, one bar however many polls
        assert r["reloads"] == 0 and r["bars"] == [BAR], (name, r)
        assert r["after_undo"] == {"reloads": 1, "bars": [BAR]}, (name, r)   # put back as it was, it is untouched again
    assert got["no_poll"] == {"timers": 0} and got["broken_page_threw"] is False


def test_u10_the_tab_title_follows_every_poll_even_when_the_page_is_left_alone_or_hidden():
    got = script_run()
    if got is None:
        return
    assert got["title_dirty"] == {"titles": ["(7) Waiting · X", "Waiting · X"], "reloads": 0, "bars": [BAR]}
    h = got["title_hidden"]
    assert h == {"fetches": 1, "title": "(1) Waiting · X", "reloads": 0, "bars": [], "after_shown": 1}, h   # polled, never reloaded while hidden


def test_u10_the_answer_all_button_is_off_while_a_row_is_edited_and_says_why():
    got = script_run()
    if got is None:
        return
    g = got["group"]
    assert g["start"] == {"disabled": False, "hints": 0}
    assert g["edited"] == {"disabled": True, "hints": [{"text": "BUSY", "role": "status", "cls": "hint busy"}]}    # said once, in the page's words
    assert g["reverted"] == {"disabled": False, "hints": 0}
    assert decls(".all .busy")["color"] == "var(--warn)"


def test_u15_an_answer_on_its_way_cannot_go_twice_be_reloaded_away_or_changed_by_the_wheel():
    for lang in LANGS:                    # every form is sent by a real submit button: it is what the script switches off
        for src in every_page(lang):
            for f in Doc(src).find_all("form"):
                assert [b.attrs.get("type") for b in f.find_all("button")] == ["submit"], f.attrs
    got = script_run()
    if got is None:
        return
    s = got["submit"]
    assert s["first_prevented"] is False and s["sent_disabled"] is True            # the first click still sends
    assert s["second_prevented"] is True and s["other_first_prevented"] is False     # a second one does not; another form still can
    assert s["others_disabled"] == [False, False]
    assert s["after"] == {"fetches": 0, "reloads": 0}                                # nothing polls or reloads once an answer is sent
    assert s["in_flight"] == {"reloads": 0, "title": "(2) Waiting · X", "bars": []}    # a poll already on its way changes nothing either
    assert got["note_submit"] == {"fetches": 0, "reloads": 0}                        # any form, not only an answer
    w = got["wheel"]
    assert w == {"blurred": True, "prevented": False, "elsewhere_keeps_focus": True, "text_keeps_focus": True}, w
    assert got["pageshow"] == {"plain": 0, "restored": 1}     # the back button must not bring back a page with dead buttons


def test_u16_when_the_console_cannot_be_reached_a_bar_says_so_and_goes_when_it_can_again():
    for lang in LANGS:
        assert Doc(pages.render_history(full_log().state(), ctx(lang))).find("body").attrs["data-lost"] == T("banner.lost", lang)
    got = script_run()
    if got is None:
        return
    lost = got["lost"]
    assert lost["bars_after_each_failed_poll"] == [0, 0, 1, 1]                       # the third in a row, once
    assert lost["bar"] == {"cls": "banner lost", "text": "LOST", "role": "status", "href": None}
    assert lost["after_recovery"] == {"bars": 0, "comment": "keep me", "reloads": 0}   # gone by itself, nothing typed touched
    assert lost["streak_broken"] == 0 and lost["status_502"] == {"bars": 1, "reloads": 0}    # three in a row; a bad reply is a failed poll
    assert lost["recovered_new_seq"] == ["banner live"]                              # and the page's news still gets through
    bar = decls(".banner.lost")
    assert bar["position"] == "fixed" and bar["color"] == "var(--bad)"
    for both in (".banner.live ~ .banner.lost", ".banner.lost ~ .banner.live"):      # both at once: one above the other
        assert px(decls(both)["bottom"]) > px(decls(".banner.lost")["bottom"]) + 44


NODE_SCRIPT = r"""
const fs = require("fs"), vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
const settle = () => new Promise((r) => setImmediate(() => setImmediate(r)));

// A tiny DOM: a tree of elements, selectors of the forms `tag`, `.class` and
// `tag.class`, and events that bubble up to the document.
class El {
  constructor(tag, cls = "", props = {}) {
    Object.assign(this, { tag, className: cls, dataset: {}, kids: [], parent: null, on: {}, disabled: false,
                          open: false, values: {} }, props);
  }
  add(...els) { for (const e of els) { e.parent = this; this.kids.push(e); } return this; }
  appendChild(e) { this.add(e); }
  remove() { this.parent.kids.splice(this.parent.kids.indexOf(this), 1); this.parent = null; }
  setAttribute(k, v) { this[k] = v; }
  addEventListener(type, fn) { (this.on[type] = this.on[type] || []).push(fn); }
  *walk() { for (const k of this.kids) { yield k; yield* k.walk(); } }
  is(sel) {
    const [, tag, cls] = /^([a-z]*)(?:\.([\w-]+))?$/.exec(sel);
    return (!tag || this.tag === tag) && (!cls || this.className.split(" ").includes(cls));
  }
  querySelectorAll(sel) { return [...this.walk()].filter((e) => e.is(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { let e = this.parent; while (e && !e.is(sel)) e = e.parent; return e; }
}

// One inbox: two asks in a group with their forms, the group's answer-all form,
// the note form, a number box and a text box. Time is the test's: the poll's
// timer runs when `tick()` says so, and a reply is what `reply()` last set.
function page(opts = {}) {
  const doc = new El("#document"), win = new El("#window", "", { scrollY: 0 });
  const body = new El("body", "", { dataset: opts.noPoll ? {} : { poll: "poll", seq: "5", open: "3", new: "CHANGED", lost: "LOST" } });
  const answer = () => new El("form", "answer", { values: { token: "t", value: "yes", comment: "" } });
  const a1 = answer(), a2 = answer(), b1 = new El("button"), b2 = new El("button");
  a1.add(new El("details", "more"), b1);
  a2.add(new El("details", "more"), b2);
  const allForm = new El("form", "all", { values: { token: "t" }, dataset: { busy: "BUSY" } });
  const allButton = new El("button");
  allForm.add(allButton);
  const ask1 = new El("details", "ask", { open: true }).add(a1), ask2 = new El("details", "ask").add(a2);
  const note = new El("form", "noteform", { values: { token: "t", text: "" } }), noteButton = new El("button");
  note.add(noteButton);
  const num = new El("input", "", { type: "number" }), text = new El("input", "", { type: "text" });
  const main = new El("main").add(new El("section", "group").add(ask1, ask2, allForm), note, num, text);
  doc.add(body.add(main));
  Object.assign(doc, { body, title: "Waiting · X", hidden: !!opts.hidden, activeElement: null,
                       createElement: (tag) => new El(tag) });
  for (const e of [num, text]) e.blur = () => { if (doc.activeElement === e) doc.activeElement = null; };
  const location = { href: "http://x/inbox", reloads: 0, reload() { this.reloads++; } };
  const timers = [];
  let fetches = 0, reply = { seq: 5, open: 3 }, held = null;
  const answered = (r) => ({ ok: true, json: () => Promise.resolve(r) });
  const fetch = () => {
    fetches++;
    if (held) return held.promise;
    if (reply.fail) return Promise.reject(new Error("unreachable"));
    if (reply.status) return Promise.resolve({ ok: false, status: reply.status, json: () => Promise.resolve({ error: "bad gateway" }) });
    return Promise.resolve(answered(reply));
  };
  const sandbox = {
    document: opts.broken ? { get body() { throw new Error("no body"); } } : doc,
    window: win, location, fetch, URLSearchParams, String,
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    FormData: class { constructor(f) { this.e = Object.entries(f.values); } [Symbol.iterator]() { return this.e[Symbol.iterator](); } },
  };
  vm.runInNewContext(code, sandbox);
  const bars = () => body.kids.filter((k) => k.className.startsWith("banner"))
    .map((k) => ({ cls: k.className, text: k.textContent, role: k.role, href: k.href === undefined ? null : k.href }));
  return {
    doc, win, body, main, location, timers, a1, a2, b1, b2, ask1, ask2, allForm, allButton, note, noteButton, num, text, bars,
    fetches: () => fetches, reply: (r) => { reply = r; },
    hold: () => { let done; held = { promise: new Promise((r) => { done = r; }), done }; },
    release: (r) => { const h = held; held = null; h.done(answered(r)); },
    fire(target, type, extra = {}) {
      const ev = { type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
      for (let n = target; n; n = n.parent) for (const fn of n.on[type] || []) fn(ev);
      return ev;
    },
    edit(form, name, value) { form.values[name] = value; this.fire(form, "input"); },
    tick: async () => { await timers[0].fn(); await settle(); },
  };
}

(async () => {
  const out = {};
  const seen = (p) => ({ reloads: p.location.reloads, title: p.doc.title, bars: p.bars() });
  const poll = async (p, r) => { p.reply(r); await p.tick(); return seen(p); };

  // an untouched page follows the log by itself
  out.same_seq = await poll(page(), { seq: 5, open: 3 });
  out.new_seq = await poll(page(), { seq: 6, open: 4 });

  // a touched page is left alone and a bar offers the reload, once; put back, it is untouched again
  const touch = {
    typed: [(p) => p.edit(p.a1, "comment", "x"), (p) => p.edit(p.a1, "comment", "")],
    scrolled: [(p) => { p.win.scrollY = 120; }, (p) => { p.win.scrollY = 0; }],
    opened: [(p) => { p.ask2.open = true; }, (p) => { p.ask2.open = false; }],
    closed: [(p) => { p.ask1.open = false; }, (p) => { p.ask1.open = true; }],
  };
  out.touched = {};
  for (const [name, [doit, undo]] of Object.entries(touch)) {
    const p = page();
    doit(p);
    await poll(p, { seq: 6, open: 4 });
    const r = await poll(p, { seq: 6, open: 4 });
    undo(p);
    const back = await poll(p, { seq: 6, open: 4 });
    out.touched[name] = { reloads: r.reloads, bars: r.bars, after_undo: { reloads: back.reloads, bars: back.bars } };
  }
  out.no_poll = { timers: page({ noPoll: true }).timers.length };
  let threw = false;
  try { page({ broken: true }); } catch (e) { threw = true; }
  out.broken_page_threw = threw;

  // the title follows every poll: on a page left alone, and on a hidden one
  let p = page();
  p.edit(p.a1, "comment", "x");
  const titles = [(await poll(p, { seq: 6, open: 7 })).title, (await poll(p, { seq: 6, open: 0 })).title];
  out.title_dirty = { titles, reloads: p.location.reloads, bars: p.bars() };
  p = page({ hidden: true });
  const hid = await poll(p, { seq: 9, open: 1 });
  out.title_hidden = { fetches: p.fetches(), title: hid.title, reloads: hid.reloads, bars: hid.bars };
  p.doc.hidden = false;
  p.fire(p.doc, "visibilitychange");
  await settle();
  out.title_hidden.after_shown = p.location.reloads;

  // the answer-all button and its reason
  p = page();
  const hints = () => p.allForm.querySelectorAll(".busy");
  out.group = { start: { disabled: p.allButton.disabled, hints: hints().length } };
  p.edit(p.a1, "comment", "x");
  p.edit(p.a1, "comment", "xy");
  out.group.edited = { disabled: p.allButton.disabled,
                       hints: hints().map((h) => ({ text: h.textContent, role: h.role, cls: h.className })) };
  p.edit(p.a1, "comment", "");
  out.group.reverted = { disabled: p.allButton.disabled, hints: hints().length };

  // an answer on its way: the first send goes, a second does not, nothing polls or reloads
  p = page();
  const first = p.fire(p.a1, "submit");
  out.submit = { first_prevented: first.defaultPrevented, sent_disabled: p.b1.disabled,
                 others_disabled: [p.b2.disabled, p.allButton.disabled] };
  out.submit.second_prevented = p.fire(p.a1, "submit").defaultPrevented;
  out.submit.other_first_prevented = p.fire(p.a2, "submit").defaultPrevented;
  p.reply({ seq: 9, open: 1 });
  const before = p.fetches();
  await p.tick();
  out.submit.after = { fetches: p.fetches() - before, reloads: p.location.reloads };
  p = page();
  p.hold();
  const pending = p.tick();
  p.fire(p.a1, "submit");
  p.release({ seq: 9, open: 2 });
  await pending;
  await settle();
  out.submit.in_flight = seen(p);
  p = page();
  p.fire(p.note, "submit");
  p.reply({ seq: 9, open: 1 });
  const b4 = p.fetches();
  await p.tick();
  out.note_submit = { fetches: p.fetches() - b4, reloads: p.location.reloads };

  // the wheel over a focused number box blurs it; elsewhere and on text it does nothing
  p = page();
  p.doc.activeElement = p.num;
  const over = p.fire(p.num, "wheel");
  out.wheel = { blurred: p.doc.activeElement === null, prevented: over.defaultPrevented };
  p = page();
  p.doc.activeElement = p.num;
  p.fire(p.main, "wheel");
  out.wheel.elsewhere_keeps_focus = p.doc.activeElement === p.num;
  p = page();
  p.doc.activeElement = p.text;
  p.fire(p.text, "wheel");
  out.wheel.text_keeps_focus = p.doc.activeElement === p.text;

  // a page that comes back from the back button is reloaded, a fresh one is not
  p = page();
  p.fire(p.win, "pageshow", { persisted: false });
  const plain = p.location.reloads;
  p.fire(p.win, "pageshow", { persisted: true });
  out.pageshow = { plain, restored: p.location.reloads };

  // three failed polls in a row show the bar, the next good one takes it away and touches nothing typed
  p = page();
  p.reply({ fail: true });
  const shown = [];
  for (let i = 0; i < 4; i++) { await p.tick(); shown.push(p.bars().length); }
  out.lost = { bars_after_each_failed_poll: shown, bar: p.bars()[0] };
  p.edit(p.a1, "comment", "keep me");
  await poll(p, { seq: 5, open: 3 });
  out.lost.after_recovery = { bars: p.bars().length, comment: p.a1.values.comment, reloads: p.location.reloads };
  p = page();
  for (const r of [{ fail: true }, { fail: true }, { seq: 5, open: 3 }, { fail: true }, { fail: true }]) await poll(p, r);
  out.lost.streak_broken = p.bars().length;
  p = page();
  for (let i = 0; i < 3; i++) await poll(p, { status: 502 });
  out.lost.status_502 = { bars: p.bars().length, reloads: p.location.reloads };      // a proxy's error is no news: it must not reload
  p = page();
  p.edit(p.a1, "comment", "typed");
  for (let i = 0; i < 3; i++) await poll(p, { fail: true });
  await poll(p, { seq: 6, open: 1 });
  out.lost.recovered_new_seq = p.bars().map((b) => b.cls);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_u11_an_answer_that_writes_to_a_harness_shows_what_before_and_what_after():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        ask = [a for a in d.find_all("details", "ask") if "Title of p1" in a.text()][0]
        tech = ask.find("details", "tech")
        walk = list(ask.walk())
        assert walk.index(tech) < walk.index(ask.find("button")) and "items.unit_cost" in tech.text()
        assert T("gate.note", lang) in ask.find("form").text()
        assert [a for a in d.find_all("details", "ask") if "Title of c1" in a.text()][0].find("details", "tech") is None
        ok = Doc(pages.render_result(ctx(lang), [{"ok": True, "id": "p1", "title": "P", "value": "4.6", "code": None, "message": "unit cost confirmed"}]))
        assert T("result.wrote", lang, message="unit cost confirmed") in ok.text()
        no = Doc(pages.render_result(ctx(lang), [{"ok": False, "id": "p1", "title": "P", "value": None, "code": "gate_refused", "message": "daily limit not confirmed"}]))
        assert T("result.refused", lang, message="daily limit not confirmed") in no.text() and T("err.gate_refused", lang) in no.text()
        assert "confirmed" not in Doc(pages.render_result(ctx(lang), [{"ok": True, "id": "p", "title": "P", "value": "y", "code": None, "message": ""}])).text().replace("Answer saved", "")


def test_u12_a_link_is_http_or_https_only_and_opens_in_a_new_tab_safely():
    L = Log()
    ev = [{"label": "Good", "value": "a", "source": "Src", "url": "https://example.com/a?b=1&c=2"},
          {"label": "Bad1", "value": "b", "source": "Js", "url": "javascript:alert(1)"},
          {"label": "Bad2", "value": "c", "source": "Data", "url": "data:text/html;base64,AAAA"},
          {"label": "Bad3", "value": "d", "source": "Proto", "url": "//evil.example/x"},
          {"quote": "q", "source": "Quote", "url": "http://example.com/q"},
          {"quote": "q", "source": "QuoteBad", "url": "file:///etc/passwd"}]
    L.raw_ask(ask_raw("confirm", "l1", evidence=ev))
    for lang in LANGS:
        d = Doc(pages.render_inbox(L.state(), ctx(lang)))
        links = [a for a in d.main.find_all("a") if a.attrs.get("href", "").startswith(("http", "javascript", "data", "//", "file"))]
        assert [a.attrs["href"] for a in links] == ["https://example.com/a?b=1&c=2", "http://example.com/q"]
        for a in links:
            assert a.attrs["target"] == "_blank" and a.attrs["rel"] == "noopener noreferrer"
        for bad in ("javascript:", "data:", "file:", "//evil"):
            assert bad not in d.source
        assert [s.text() for s in d.find_all("span", "src")] == ["Js", "Data", "Proto", "QuoteBad"]
    # a page's own links (the tabs, back) stay relative and open in the same tab
    for a in Doc(pages.render_inbox(L.state(), ctx())).find("header", "bar").find_all("a"):
        assert "target" not in a.attrs and not a.attrs["href"].startswith(("http", "//"))


def test_u13_an_approval_reads_heavier_than_a_confirmation_and_is_never_answered_in_a_group():
    for lang in LANGS:
        d = Doc(pages.render_inbox(full_log().state(), ctx(lang)))
        for a in d.find_all("details", "ask"):
            chip = a.find("summary").find("span", "chip")
            approves = chip.text() == T("step.approve", lang)
            assert ("approve" in a.classes) == approves and ("warn" in chip.classes) == approves
            assert bool(a.find("p", "effect")) == approves                 # what approving does, in its own box
        assert len(d.find_all("details", "approve")) == 1
    for steps, button in ((("confirm", "confirm"), True), (("confirm", "approve"), False), (("approve", "approve"), False)):
        L = Log()
        for i, step in enumerate(steps):
            L.ask(ask_raw(step, f"k{i}", group="G"))
        assert bool(Doc(pages.render_inbox(L.state(), ctx())).find("form", "all")) == button, steps
    edge = lambda sel: decls(sel)["box-shadow"]
    assert "--warn" in edge(".ask.approve") and "--warn" in edge(".ask.approve[open]")
    assert decls(".approve .btn.primary")["background"] == "var(--warn-solid)"          # amber while Approve is picked...
    assert decls('.approve .answer:has(input[value="no"]:checked) .btn.primary')["background"] == "var(--accent)"   # ...plain for Reject
    assert decls(".effect")["background"] == "var(--warn-bg)"


def test_u14_an_answer_the_console_cannot_write_draws_no_form_and_says_so_before_any_click():
    for lang in LANGS:
        for relay in (None, [], [["queue", "approve"]]):
            d = Doc(pages.render_inbox(full_log().state(), ctx(lang, relay=relay)))
            ask = [a for a in d.find_all("details", "ask") if "Title of p1" in a.text()][0]
            assert not ask.find_all("form") and ask.find("p", "banner").text() == T("gate.norelay", lang)
            numbers = [g for g in d.find_all("section", "group") if g.find("h2").text().startswith("Numbers")][0]
            assert numbers.find("form", "all") is None                        # and no group button that would send it
        ok = Doc(pages.render_inbox(full_log().state(), ctx(lang, relay=[["facts", "confirm"]])))
        assert [a for a in ok.find_all("details", "ask") if "Title of p1" in a.text()][0].find("form", "answer")


if __name__ == "__main__":
    _t.main(globals())
