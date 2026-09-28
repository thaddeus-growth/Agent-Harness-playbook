"""The console's pages: pure functions from the folded state and a small ctx
to one complete HTML document. No I/O, no clock (`ctx["now"]` is the time),
no words of its own (they come from `i18n.json`), nothing the agent wrote
reaches a page unescaped, and a link is drawn only when it is http(s).
The CSP forbids inline script, style and handlers, so there are none, and the
page works with the script off. Every URL is relative, so a proxy that strips
a prefix needs no configuration.

    render_inbox(st, ctx)       the waiting page: one column, top down
    render_history(st, ctx)     what was answered or withdrawn, newest first
    render_result(ctx, lines)   the page after a POST
    render_error(ctx, code, message="")
    answer_label(lang, ask, value)   an answer as the human saw it, for a result line
    flash_text(lang, key)            the banner words for a finished redirect, or None

ctx: lang, title, user (None = read-only), token (the POST token), relay
(None or [] = no harness; else the verb prefixes the operator allows, each a
list of words), now (ISO Z), secret (bytes | None, to verify signatures),
flash ({kind: ok|error, text}, optional: a banner at the top of a page).
`lines` for a result: {ok, id, title, value, code, message}, plus an optional
`reason` (the `Refused.params["reason"]` of a bad_value) that picks the exact
sentence and an optional `comment` (what the person typed), given back on a
refusal so nothing typed is lost. For a refusal from the harness
(`HARNESS_SAYS`) `message` is the harness's own English, shown escaped under
the console's sentence.

The forms drawn here are what serve.py reads (all `method=post`):

    answer      token id hash value comment?      one ask
    answer_all  token pair=<id>:<hash> (repeated) a group, with its suggestions
    reopen      token id answer_seq
    note        token text

A result or error page is the answer to a POST, so it has no poll (a reload
would send the form again) and its language link goes to `./`. console.js sends
each form with fetch and shows that page's `res` (or a redirect's `flash`) lines in
a notice over the page it is on; a page without the script is used as it is. An error page
says nothing about who is asking: it may come from a request that never
reached a person.

CSS classes (console.css styles exactly these):
  bar bar-in brand                 top bar, its row, the console's title
  tabs count lang                  the two tabs, a number in a tab or a group link, the other-language link
  flash banner live lost           notices; console.js adds `live` (reload offered) and `lost` (console cannot be reached)
  ok warn bad mute                 status colours (chip, flash, res)
  head sub meter over              page head: title, budget line, budget segments
  say who jump                     the agent's latest message, a byline, the group links
  group list ask done              a group, its rows, one row (`done` in History)
  approve                          a row that approves something: it reads heavier than a confirmation
  waiting applied withdrawn        a History row's loop state: the agent still owes it, did it, or it was withdrawn
  ttl meta chip age inner          a row's title, chip line, chip, age, opened body
  why txt                          the agent's reason; `txt` keeps its line breaks
  ev facts fact k v src            evidence: the block, fact chips, label, value, source
  quote tablebox scroll num        evidence: a quote, a table, its scroll box, a number cell
  suggest effect ifno lead tech    boxes: suggestion, what approving does, what no means, folded detail
  answer opts opt lab desc         the row form, its radio set, one option, its label and note
  field unit hint more sr          a typed answer, its unit, small help, folded comment, screen-reader-only
  btn primary secondary yes no     buttons; `yes`/`no` are the two words a yes/no button switches between
  all gnote busy                   the group's answer-all form, its note, and the reason console.js gives while it is off
  msgs msg you noteform            messages, one message (`you` = the human's), the note form
  empty clear big                  an empty page; `clear` = nothing waits, drawn with a tick; its big line
  rec reopen problems              history: what was answered, the reopen form, ignored entries
  res                              one line of a result page
  toasts toast                     console.js's tray and one notice in it: the result lines of a form it sent in place
  foot                             footer
"""

from __future__ import annotations

import html
from urllib.parse import urlsplit

import core
import i18n

SHOW_MESSAGES = 20
SHOW_HISTORY = 100
# a payload's true, false and null, as they are written in it
JSON_WORDS = {"True": "true", "False": "false", "None": "null"}
# refusals whose message is the harness's own words, shown as it wrote them
HARNESS_SAYS = ("gate_refused", "gate_changed", "gate_timeout", "gate_unsure")
UNSURE = ("gate_timeout", "gate_unsure", "not_recorded")        # the harness may have been written
# what a finished redirect (`?done=<key>`) says: a page cannot be made to say anything else
FLASH = {"reopened": "flash.reopened", "noted": "flash.noted"}
# what a yes/no answer is called, per step
YES_NO = {("confirm", "yes"): "ans.yes", ("confirm", "no"): "ans.no",
          ("approve", "yes"): "ans.approve", ("approve", "no"): "ans.reject"}


# ---------------------------------------------------------------- helpers --

def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


class _T:
    """The words of one language. `t(key, **p)` is HTML-escaped (safe in text
    and in attributes); `t.raw` is plain text, for a value that is escaped
    later with everything around it."""

    def __init__(self, lang: str):
        self.lang = lang

    def raw(self, key: str, **p) -> str:
        return i18n.t(key, self.lang, **p)

    def __call__(self, key: str, **p) -> str:
        return _e(self.raw(key, **p))


def _who(by) -> str:
    """`web:alice` -> `alice`."""
    return str(by or "").split(":", 1)[-1]


def _safe(url):
    """The url if it is a plain http(s) link, else None. core validated it
    already; a log edited by hand must not be able to put `javascript:` here."""
    if not isinstance(url, str) or url != url.strip() or not core.URL_RE.match(url):
        return None
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    return url if p.scheme in ("http", "https") and p.netloc else None


def _secs(iso) -> int | None:
    """A count of seconds for `YYYY-MM-DDTHH:MM:SSZ`, on a scale where only
    differences mean anything (days-from-civil, so no datetime import), None
    if it is not that."""
    try:
        y, m, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
        hh, mm, ss = int(iso[11:13]), int(iso[14:16]), int(iso[17:19])
    except (TypeError, ValueError):
        return None
    y -= m <= 2
    era, yoe = divmod(y, 400)
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return (era * 146097 + doe) * 86400 + hh * 3600 + mm * 60 + ss


def _age(t: _T, then, now) -> str:
    """`2 h ago` as plain text; empty when a time cannot be read."""
    a, b = _secs(then), _secs(now)
    if a is None or b is None:
        return ""
    n = b - a
    if n < 60:
        return t.raw("age.now")
    if n < 3600:
        return t.raw("age.min", n=n // 60)
    if n < 86400:
        return t.raw("age.hour", n=n // 3600)
    return t.raw("age.day", n=n // 86400)


def _answer(t: _T, ask: dict, value) -> str:
    """An answer as the human saw it, plain text: Yes, an option's label,
    a value with its unit."""
    v, step = str(value), ask.get("step")
    if (step, v) in YES_NO:
        return t.raw(YES_NO[(step, v)])
    if step == "choose":
        for o in ask.get("options") or []:
            if o.get("value") == v:
                return str(o.get("label", v))
    if step == "provide":
        unit = (ask.get("input") or {}).get("unit")
        return f"{v} {unit}" if unit else v
    return v


def _err(t: _T, code, reason=None) -> str:
    """The sentence for a failure code; a code this dictionary does not know
    yet (a newer core) gets the generic one instead of a crash."""
    if code == "bad_value" and reason and i18n.has("bad_value." + str(reason)):
        return t("bad_value." + str(reason))
    if code and i18n.has("err." + str(code)):
        return t("err." + str(code))
    return t("err.other")


def _runs_here(ctx: dict, gate: dict) -> bool:
    """Can the console run this gate? Only a verb that starts with a prefix the
    operator allowed (relay.py checks the same before it runs anything), so a
    click never ends in a refusal that was known when the page was drawn."""
    relay = ctx.get("relay")
    verb = gate.get("verb") or []
    return isinstance(relay, (list, tuple)) and any(
        p and verb[:len(p)] == list(p) for p in relay)


def _lang(ctx: dict) -> str:
    return ctx["lang"] if ctx.get("lang") in i18n.LANGS else "en"


def answer_label(lang: str, ask: dict, value) -> str:
    """An answer in the words the human saw it in (Approve, an option's
    label, 4.2 USD), as plain text: what serve.py puts in a result line's
    `value`, since only it has the ask."""
    return _answer(_T(lang if lang in i18n.LANGS else "en"), ask, value)


def flash_text(lang: str, key) -> str | None:
    """The banner for a redirect that finished a reopen or a note (plain text),
    None for any other key."""
    if not isinstance(key, str) or key not in FLASH:
        return None
    return _T(lang if lang in i18n.LANGS else "en").raw(FLASH[key])


# --------------------------------------------------------------- the shell --

def _bar(t: _T, ctx: dict, tab, n_open: int, href: str) -> str:
    other = "zh" if t.lang == "en" else "en"

    def tab_link(name, url, key, count=0):
        cur = ' aria-current="page"' if tab == name else ""
        num = f' <span class="count">{count}</span>' if count else ""
        return f'<a href="{url}"{cur}>{t(key)}{num}</a>'
    return (f'<header class="bar"><div class="bar-in">'
            f'<a class="brand" href="./">{_e(ctx.get("title"))}</a>'
            f'<nav class="tabs" aria-label="{t("nav.label")}">'
            f'{tab_link("waiting", "./", "nav.waiting", n_open)}'
            f'{tab_link("history", "history", "nav.history")}</nav>'
            f'<a class="lang" href="{href}{other}" lang="{other}" hreflang="{other}">{t("lang.other")}</a>'
            f'</div></header>')


def _shell(ctx: dict, t: _T, *, title: str, body: str, tab=None, st=None,
           href: str = "?lang=", ro: bool = False, identity: bool = True) -> str:
    """The whole document. `st` set = a page that polls (`data-*` on body, with
    the words console.js shows). `identity` = the footer says who is answering."""
    n = len(core.open_asks(st)) if st is not None else 0
    live = ""
    if st is not None:
        live = (f' data-seq="{_e(st.get("seq", 0))}" data-open="{n}" data-poll="poll"'
                f' data-new="{t("banner.new")}" data-lost="{t("banner.lost")}"')
    flash = ""
    f = ctx.get("flash")
    if f and f.get("text"):
        kind = "ok" if f.get("kind") == "ok" else "bad"
        flash = f'<p class="flash {kind}" role="status">{_e(f["text"])}</p>'
    banner = f'<p class="banner">{t("ro.banner")}</p>' if ro and not ctx.get("user") else ""
    who = ""
    if identity:
        who = f'<p>{t("foot.user", user=ctx["user"]) if ctx.get("user") else t("foot.ro")}</p>'
    return (f'<!doctype html>\n<html lang="{t.lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f'<title>{title} · {_e(ctx.get("title"))}</title>'
            f'<link rel="stylesheet" href="static/console.css">'
            f'<script src="static/console.js" defer></script></head>\n'
            f'<body{live}>\n{_bar(t, ctx, tab, n, href)}\n'
            f'<main>{flash}{banner}{body}</main>\n'
            f'<footer class="foot"><p>{t("foot.line")}</p>{who}</footer>\n'
            f'</body></html>\n')


# ---------------------------------------------------------------- evidence --

def _source(source, url) -> str:
    u = _safe(url)
    if u:
        text = source or urlsplit(u).hostname or u
        return (f'<a class="src" href="{_e(u)}" target="_blank" '
                f'rel="noopener noreferrer">{_e(text)}</a>')
    return f'<span class="src">{_e(source)}</span>' if source else ""


def _is_number(cell) -> bool:
    s = str(cell).strip().replace(",", "").rstrip("%").lstrip("$¥€£")
    if not s or not (s[0].isdigit() or s[0] in ".-+"):
        return False
    try:
        float(s)
    except ValueError:
        return False
    return True


def _table(t: _T, tb: dict) -> str:
    cap = tb.get("caption") or ""
    cols, data = tb.get("columns", []), tb.get("rows", [])
    # a column of numbers lines up on the right, header included
    num = [bool(data) and all(_is_number(r[i]) for r in data) for i in range(len(cols))]

    def cell(tag, i, c, attrs=""):
        cls = ' class="num"' if num[i] else ""
        return f"<{tag}{attrs}{cls}>{_e(c)}</{tag}>"
    head = "".join(cell("th", i, c, ' scope="col"') for i, c in enumerate(cols))
    rows = "".join("<tr>" + "".join(cell("td", i, c) for i, c in enumerate(r)) + "</tr>" for r in data)
    label = _e(cap) if cap else t("evidence.table")
    caption = f"<figcaption>{_e(cap)}</figcaption>" if cap else ""
    return (f'<figure class="tablebox">{caption}'
            f'<div class="scroll" role="region" tabindex="0" aria-label="{label}">'
            f'<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></figure>')


def _evidence(t: _T, items, heading: bool = True) -> str:
    """Facts as chips (runs of them share one list), quotes with their source,
    tables in a box of their own that scrolls sideways, not the page."""
    out: list[str] = []
    facts: list[str] = []

    def flush():
        if facts:
            out.append('<ul class="facts">' + "".join(facts) + "</ul>")
            facts.clear()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if "table" in it:
            flush()
            out.append(_table(t, it["table"]))
        elif "quote" in it:
            flush()
            src = _source(it.get("source"), it.get("url"))
            foot = f"<footer>{src}</footer>" if src else ""
            out.append(f'<blockquote class="quote"><p class="txt">{_e(it["quote"])}</p>{foot}</blockquote>')
        else:
            facts.append(f'<li class="fact"><span class="k">{_e(it.get("label"))}</span> '
                         f'<span class="v">{_e(it.get("value"))}</span> '
                         f'{_source(it.get("source"), it.get("url"))}</li>')
    flush()
    h = f'<h3>{t("evidence.head")}</h3>' if heading else ""
    return f'<div class="ev">{h}{"".join(out)}</div>'


# --------------------------------------------------------- one waiting ask --

def _flat(x, path=""):
    """(path, scalar) pairs of a payload, for the folded 'what gets written'."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _flat(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _flat(v, f"{path}[{i}]")
    else:
        yield path, x


def _tech(t: _T, k: dict) -> str:
    """What the harness will be told, before the button. Technical, so folded.
    What the person typed is "your answer"; for a choice each option gets its
    own row with the value it would write, so the value sent is never hidden
    behind a label."""
    gate, rows = k["gate"], ""
    if k.get("step") == "choose":
        for o in k.get("options") or []:
            written = "; ".join(f"{path} = {JSON_WORDS.get(repr(v), v)}"
                                for path, v in _flat(core.expect_for(k, o.get("value"))))
            rows += f"<dt>{_e(o.get('label'))}</dt><dd>{_e(written)}</dd>"
    else:
        for path, v in _flat(gate.get("expect")):
            shown = t("gate.value") if v == "$value" else _e(JSON_WORDS.get(repr(v), v))
            rows += f"<dt>{_e(path)}</dt><dd>{shown}</dd>"
    verb = _e(" ".join(gate.get("verb") or []))
    return (f'<details class="tech"><summary>{t("gate.tech")}</summary>'
            f'<p><code>{verb}</code></p><dl>{rows}</dl></details>')


def _ifno(t: _T, k: dict) -> str:
    """What the answer "no" means; for a choice, which has no "no", what it
    means when none of the options fits."""
    lead = {"choose": "ifno.lead_choose", "approve": "ifno.lead_reject"}.get(k.get("step"), "ifno.lead")
    return f'<p class="ifno"><b class="lead">{t(lead)}</b> {_e(k.get("if_no"))}</p>'


def _radios(t: _T, k: dict, rec, only: str | None = None) -> str:
    """The choices. `only` narrows a yes/no to one answer (a refusal never needs
    the harness), which is then the checked one, and marked as suggested only
    if the agent did suggest it."""
    if k["step"] == "choose":
        opts = [(o["value"], o["label"], o.get("note")) for o in k["options"]]
    else:
        opts = [(v, _answer(t, k, v), None) for v in ("yes", "no") if only in (None, v)]
    rows = []
    for value, label, note in opts:
        suggested = bool(rec) and rec["value"] == value
        pick = suggested or only == value
        tag = f'<span class="chip ok">{t("chip.suggested")}</span>' if suggested else ""
        desc = f'<span class="desc">{_e(note)}</span>' if note else ""
        rows.append(f'<label class="opt"><input type="radio" name="value" value="{_e(value)}"'
                    f'{" checked" if pick else ""} required>'
                    f'<span class="lab">{_e(label)}{tag}{desc}</span></label>')
    return f'<fieldset class="opts"><legend class="sr">{t("form.answer")}</legend>{"".join(rows)}</fieldset>'


def _field(t: _T, a: dict, k: dict, rec) -> str:
    spec = k.get("input") or {}
    typ, lo, hi = spec.get("type", "text"), spec.get("min"), spec.get("max")
    fid = f'v-{_e(a["id"])}'
    attrs = f'type="{_e(typ)}" name="value" id="{fid}" required maxlength="{core.LIMITS["answer"]}"'
    hint = ""
    if typ == "number":
        attrs += ' step="any" inputmode="decimal"'
        attrs += f' min="{_e(lo)}"' if lo is not None else ""
        attrs += f' max="{_e(hi)}"' if hi is not None else ""
        if lo is not None and hi is not None:
            hint = t("hint.between", min=lo, max=hi)
        elif lo is not None:
            hint = t("hint.atleast", min=lo)
        elif hi is not None:
            hint = t("hint.atmost", max=hi)
    elif typ == "url":
        hint = t("hint.url")
    if rec:
        attrs += f' value="{_e(rec["value"])}"'
    unit = f'<span class="unit">{_e(spec["unit"])}</span>' if spec.get("unit") else ""
    hint = f'<p class="hint">{hint}</p>' if hint else ""
    return (f'<div class="field"><label for="{fid}">{t("form.answer")}</label>'
            f'<input {attrs}>{unit}{hint}</div>')


def _button(t: _T, k: dict) -> str:
    """One button per ask. For yes/no it says what the checked radio does
    (console.css switches the two words); with no CSS support it says the
    first, which is the suggestion's usual answer."""
    step = k["step"]
    if step in ("confirm", "approve"):
        yes = "btn.confirm" if step == "confirm" else "ans.approve"
        no = "btn.confirm_no" if step == "confirm" else "ans.reject"
        inner = f'<span class="yes">{t(yes)}</span><span class="no">{t(no)}</span>'
    else:
        inner = t("btn.send")
    return f'<button class="btn primary" type="submit">{inner}</button>'


def _form(t: _T, ctx: dict, a: dict, k: dict, rec) -> str:
    if not ctx.get("user"):
        return ""
    gate = k.get("gate")
    decline = bool(gate) and not _runs_here(ctx, gate)
    if decline and k["step"] not in ("confirm", "approve"):
        return f'<p class="banner">{t("gate.norelay")}</p>'
    hidden = "".join(f'<input type="hidden" name="{n}" value="{_e(v)}">'
                     for n, v in (("token", ctx.get("token")), ("id", a["id"]), ("hash", a["hash"])))
    controls = (_field(t, a, k, rec) if k["step"] == "provide"
                else _radios(t, k, rec, "no" if decline else None))
    more = (f'<details class="more"><summary>{t("comment.toggle")}</summary>'
            f'<textarea name="comment" rows="3" maxlength="{core.LIMITS["comment"]}" '
            f'aria-label="{t("comment.toggle")}"></textarea></details>')
    note = (f'<p class="banner">{t("gate.norelay")}</p>' if decline
            else f'<p class="hint">{t("gate.note")}</p>' if gate else "")
    return (f'<form class="answer" method="post" action="answer" autocomplete="off">'
            f'{hidden}{controls}{more}{note}{_button(t, k)}</form>')


def _ask(t: _T, ctx: dict, a: dict, is_open: bool) -> str:
    k, step = a["ask"], a["ask"].get("step")
    rec = k.get("recommend")
    chips = f'<span class="chip{" warn" if step == "approve" else ""}">{t("step." + step)}</span>'
    if rec:
        chips += f'<span class="chip ok">{t("suggested.chip", answer=_answer(t, k, rec["value"]))}</span>'
    chips += f'<span class="age">{_e(_age(t, a.get("opened_at"), ctx["now"]))}</span>'
    parts = [f'<p class="why txt">{_e(k.get("why"))}</p>', _evidence(t, k.get("evidence"))]
    if rec:
        parts.append(f'<p class="suggest"><b class="lead">{t("suggest.lead", answer=_answer(t, k, rec["value"]))}</b>'
                     f' — <span>{_e(rec.get("because"))}</span></p>')
    if step == "approve" and k.get("effect"):
        parts.append(f'<p class="effect"><b class="lead">{t("effect.lead")}</b> {_e(k["effect"])}</p>')
    parts.append(_ifno(t, k))
    if k.get("gate"):
        parts.append(_tech(t, k))
    parts.append(_form(t, ctx, a, k, rec))
    return (f'<details class="ask{" approve" if step == "approve" else ""}"{" open" if is_open else ""}>'
            f'<summary><span class="ttl">{_e(k.get("title"))}</span><span class="meta">{chips}</span></summary>'
            f'<div class="inner">{"".join(p for p in parts if p)}</div></details>')


# ------------------------------------------------------------- the inbox --

def _all_form(t: _T, ctx: dict, items: list[dict]) -> str:
    """Answer a whole group with its suggestions: only when there is a person
    to answer, at least two asks, every ask has a suggestion, no ask approves
    something (that is read one ask at a time) and none needs a gate the
    console cannot run."""
    asks = [a["ask"] for a in items]
    if not (ctx.get("user") and len(items) > 1 and all(k.get("recommend") for k in asks)
            and not any(k.get("step") == "approve" for k in asks)
            and all(_runs_here(ctx, k["gate"]) for k in asks if k.get("gate"))):
        return ""
    pairs = "".join(f'<input type="hidden" name="pair" value="{_e(a["id"])}:{_e(a["hash"])}">' for a in items)
    return (f'<form class="all" method="post" action="answer_all" autocomplete="off" data-busy="{t("all.busy")}">'
            f'<input type="hidden" name="token" value="{_e(ctx.get("token"))}">{pairs}'
            f'<button class="btn secondary" type="submit">{t("all.button", n=len(items))}</button>'
            f'<p class="gnote">{t("all.note")}</p></form>')


def _group(t: _T, ctx: dict, n: int, name: str, items: list[dict], titled: bool) -> str:
    rows = "".join(_ask(t, ctx, a, n == 1 and j == 0) for j, a in enumerate(items))
    head = (f'<h2>{_e(name) or t("group.none")} <span class="count">{len(items)}</span></h2>'
            if titled else "")
    return (f'<section class="group" id="g{n}">{head}<div class="list">{rows}</div>'
            f'{_all_form(t, ctx, items)}</section>')


def _messages(t: _T, st: dict, ctx: dict, top: dict | None = None) -> str:
    """The thread below the asks: every message except `top`, which the page
    already shows as the agent's latest word (the same text is never twice on it)."""
    msgs = [m for m in st.get("messages", []) if m is not top]
    lis = []
    for m in msgs[-SHOW_MESSAGES:]:
        human = m.get("type") == "note"
        sig = (f' <span class="chip warn">{t("sig.bad")}</span>'
               if human and core.verify(ctx.get("secret"), m) is False else "")
        lis.append(f'<li class="msg{" you" if human else ""}"><p class="txt">{_e(m.get("text"))}</p>'
                   f'<p class="who">{_e(_who(m.get("by")))} · {_e(_age(t, m.get("at"), ctx["now"]))}{sig}</p></li>')
    body = f'<ul>{"".join(lis)}</ul>' if lis else ""
    if len(msgs) > SHOW_MESSAGES:
        body += f'<p class="hint">{t("messages.more", n=SHOW_MESSAGES)}</p>'
    form = ""
    if ctx.get("user"):
        form = (f'<form class="noteform" method="post" action="note" autocomplete="off">'
                f'<input type="hidden" name="token" value="{_e(ctx.get("token"))}">'
                f'<label for="note-text">{t("note.label")}</label>'
                f'<textarea id="note-text" name="text" rows="3" maxlength="{core.LIMITS["note"]}" required></textarea>'
                f'<button class="btn secondary" type="submit">{t("note.send")}</button></form>')
    head = f'<h2>{t("messages.head")}</h2>' if body else ""
    return f'<section class="msgs">{head}{body}{form}</section>'


def render_inbox(st: dict, ctx: dict) -> str:
    t = _T(_lang(ctx))
    groups = core.groups(st)
    n = sum(len(items) for _, items in groups)
    over = n > core.MAX_OPEN
    line = (t("budget.over", open=n, over=n - core.MAX_OPEN, max=core.MAX_OPEN) if over
            else t("budget.line", open=n, max=core.MAX_OPEN))
    segs = "".join("<b></b>" if i < n else "<i></i>" for i in range(core.MAX_OPEN))
    parts = [f'<div class="head"><h1>{t("page.waiting")}</h1>'
             f'<span class="meter{" over" if over else ""}" aria-hidden="true">{segs}</span>'
             f'<p class="sub">{line}</p></div>']
    say = next((m for m in reversed(st.get("messages", [])) if m.get("type") == "say"), None)
    if say:
        parts.append(f'<aside class="say"><p class="txt">{_e(say.get("text"))}</p><p class="who">'
                     f'{t("say.from", age=_age(t, say.get("at"), ctx["now"]))}</p></aside>')
    if not groups:
        parts.append(f'<div class="empty clear"><p class="big">{t("empty.title")}</p>'
                     f'<p class="hint">{t("empty.hint")}</p>'
                     f'<p><a href="history">{t("empty.link")}</a></p></div>')
    if len(groups) > 1:
        links = "".join(f'<a href="#g{i}">{_e(name) or t("group.none")} <span class="count">{len(items)}</span></a>'
                        for i, (name, items) in enumerate(groups, 1))
        parts.append(f'<nav class="jump" aria-label="{t("jump.label")}">{links}</nav>')
    for i, (name, items) in enumerate(groups, 1):
        parts.append(_group(t, ctx, i, name, items, titled=len(groups) > 1 or bool(name)))
    parts.append(_messages(t, st, ctx, say))
    return _shell(ctx, t, title=t("page.waiting"), body="".join(parts), tab="waiting", st=st, ro=True)


# ------------------------------------------------------------- the history --

def _reopen_form(t: _T, ctx: dict, a: dict) -> str:
    hidden = "".join(f'<input type="hidden" name="{n}" value="{_e(v)}">'
                     for n, v in (("token", ctx.get("token")), ("id", a["id"]),
                                  ("answer_seq", a["answer"]["seq"])))
    return (f'<form class="reopen" method="post" action="reopen" autocomplete="off">{hidden}'
            f'<button class="btn secondary" type="submit">{t("reopen.button")}</button>'
            f'<p class="hint">{t("reopen.hint")}</p></form>')


def _done(t: _T, ctx: dict, a: dict) -> str:
    answered = a["status"] == "answered"
    ev = a["answer"] if answered else a["withdrawn"]
    shown = a["revs"].get(ev.get("subject"), a["ask"]) if answered else a["ask"]
    chips, rec = [], []
    if answered:
        chips.append(f'<span class="chip">{_e(_answer(t, shown, ev.get("value")))}</span>')
        chips.append(f'<span class="chip ok">{t("state.applied")}</span>' if a["applied"]
                     else f'<span class="chip warn">{t("state.waiting")}</span>')
        if core.verify(ctx.get("secret"), ev) is False:
            chips.append(f'<span class="chip warn">{t("sig.bad")}</span>')
        revised = f' <span class="chip warn">{t("history.revised")}</span>' if ev.get("revised") else ""
        rec.append((t("form.answer"), _e(_answer(t, shown, ev.get("value"))) + revised))
        if ev.get("comment"):
            rec.append((t("history.comment"), f'<span class="txt">{_e(ev["comment"])}</span>'))
        if shown.get("recommend"):
            rec.append((t("history.compare"),
                        f'<span class="chip ok">{t("chip.suggested")}</span>' if ev.get("suggested")
                        else f'<span class="chip">{t("history.changed")}</span>'))
        gate = ev.get("gate")
        if isinstance(gate, dict):
            rec.append((t("history.gate"), _e(gate.get("message")) or t("history.gate_done")))
        if a["applied"]:
            status = t("state.applied_where", where=a["applied"].get("where"))
        else:
            status = f'<span class="chip warn">{t("state.waiting")}</span>'
            if ctx.get("user"):     # what the system already took cannot be taken back here
                status += (f'<p class="hint">{t("reopen.written")}</p>' if isinstance(gate, dict) and gate.get("ok")
                           else _reopen_form(t, ctx, a))
    else:
        chips.append(f'<span class="chip mute">{t("state.withdrawn")}</span>')
        status = t("state.withdrawn_reason", reason=ev.get("reason"))
    rec.append((t("history.status"), status))
    dl = "".join(f"<dt>{dt}</dt><dd>{dd}</dd>" for dt, dd in rec)
    byline = f'{_e(_who(ev.get("by")))} · {_e(_age(t, ev.get("at"), ctx["now"]))}'
    state = "waiting" if answered and not a["applied"] else "applied" if answered else "withdrawn"
    return (f'<details class="ask done {state}"><summary><span class="ttl">{_e(shown.get("title"))}</span>'
            f'<span class="meta">{"".join(chips)}<span class="age">{byline}</span></span></summary>'
            f'<div class="inner"><h3>{t("history.shown")}</h3>'
            f'<p class="why txt">{_e(shown.get("why"))}</p>{_evidence(t, shown.get("evidence"), heading=False)}'
            f'{_ifno(t, shown)}'
            f'<dl class="rec">{dl}</dl></div></details>')


def render_history(st: dict, ctx: dict) -> str:
    t = _T(_lang(ctx))
    rows = [st["asks"][i] for i in st["order"] if st["asks"][i]["status"] in ("answered", "withdrawn")]
    rows.sort(key=lambda a: (a["answer"] or a["withdrawn"])["seq"], reverse=True)
    waiting = sum(1 for a in rows if a["status"] == "answered" and not a["applied"])
    parts = [f'<div class="head"><h1>{t("page.history")}</h1>'
             f'<p class="sub">{t("history.sub", n=len(rows), m=waiting)}</p></div>']
    if rows:
        parts.append('<div class="list">' + "".join(_done(t, ctx, a) for a in rows[:SHOW_HISTORY]) + "</div>")
        if len(rows) > SHOW_HISTORY:
            parts.append(f'<p class="hint">{t("history.more", n=SHOW_HISTORY)}</p>')
    else:
        parts.append(f'<div class="empty"><p class="big">{t("history.empty")}</p></div>')
    if st.get("problems"):
        def why(p):
            key = "problem." + str(p.get("code"))
            return t.raw(key if i18n.has(key) else "problem.other")
        lis = "".join(f'<li>{t("problems.line", seq=p.get("seq"), why=why(p))}</li>' for p in st["problems"])
        parts.append(f'<section class="problems"><h2>{t("problems.head")}</h2><ul>{lis}</ul></section>')
    return _shell(ctx, t, title=t("page.history"), body="".join(parts), tab="history", st=st, ro=True)


# --------------------------------------------------- after a POST, and errors --

def _res(t: _T, line: dict) -> str:
    ok = bool(line.get("ok"))
    ttl = f'<p class="ttl">{_e(line.get("title"))}</p>' if line.get("title") else ""
    msg = line.get("message") or ""
    if ok:
        v = line.get("value")
        body = f'<p>{t("result.line_ok", value=v) if v not in (None, "") else t("result.ok")}</p>'
        if msg:
            body += f'<p class="txt">{t("result.wrote", message=msg)}</p>'
    else:
        body = f'<p>{_err(t, line.get("code"), line.get("reason"))}</p>'
        if msg and line.get("code") in HARNESS_SAYS:     # the harness's own words
            body += f'<p class="txt">{t("result.refused", message=msg)}</p>'
        if line.get("comment"):                           # nothing typed is lost with a refusal
            body += f'<p class="txt">{t("result.comment", comment=line["comment"])}</p>'
    return f'<li class="res {"ok" if ok else "bad"}">{ttl}{body}</li>'


def _back(t: _T) -> str:
    return f'<p><a class="btn primary" href="./">{t("result.back")}</a></p>'


def render_result(ctx: dict, lines: list[dict]) -> str:
    t = _T(_lang(ctx))
    good = sum(1 for x in lines if x.get("ok"))
    unsure = any(x.get("code") in UNSURE for x in lines)
    head = (t("result.check") if unsure else t("result.ok") if good == len(lines)
            else t("result.none") if good == 0 else t("result.some"))
    body = (f'<div class="head"><h1>{head}</h1></div>'
            f'<ul>{"".join(_res(t, x) for x in lines)}</ul>{_back(t)}')
    return _shell(ctx, t, title=head, body=body, href="./?lang=")


def render_error(ctx: dict, code: str, message: str = "") -> str:
    t = _T(_lang(ctx))
    said = code in HARNESS_SAYS and message
    tech = (f'<details class="tech"><summary>{t("err.details")}</summary><p>{_e(message)}</p></details>'
            if message and not said else "")
    harness = f'<p class="txt">{t("result.refused", message=message)}</p>' if said else ""
    body = (f'<div class="head"><h1>{t("err.head")}</h1></div>'
            f'<div class="res bad"><p>{_err(t, code)}</p>{harness}</div>{tech}{_back(t)}')
    return _shell(ctx, t, title=t("err.head"), body=body, href="./?lang=", identity=False)
