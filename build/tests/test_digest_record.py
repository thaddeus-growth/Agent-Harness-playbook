"""The decision record the build docs send a team lead to: `console/ask.py digest`.

build/ had its own digest script until the console's verb covered the same
record (the console's test of the verb is console/tests/test_ask_cli.py). What
only that script's tests held is kept here, against the real verb: text that
someone else wrote into the log (an answer, a comment) shows as those words and
can never become markup, a link, a heading or a table row in a file that is
forwarded. Each test names the rule it guards and would fail if that rule were
removed.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

EVIL_ANSWER = "<script>x</script> | [click](https://phish.example.com) `code`"
EVIL_COMMENT = "line one\n# not a heading\n| a | b |"
SPECIALS = "a\\b`c*d_e[f]g<h>i|j#k"                     # every character the escape covers, with a letter between


def record_of_an_answer(value: str, comment: str) -> str:
    """The Markdown `ask.py digest` renders for one ask answered with these words."""
    with _t.tmpdir() as d:
        code, asks = _t.tool("intake_to_asks.py", "--intake", _t.FIX1, _t.FIX2, "--pick", "q3")
        assert code == 0, asks
        assert _t.ask(d, "add", "-", stdin=json.dumps(asks))[0] == 0
        _t.owner_answers(d, {"intake-q3": (value, comment)})
        code, doc = _t.ask(d, "digest")
        assert code == 0 and doc["ok"] is True and doc["format"] == "md", doc
        return doc["text"]


def test_the_markdown_is_the_text_field_of_one_json_document():
    text = record_of_an_answer("EUR", "as said on the call")
    assert text.startswith("# Decision record\n") and "**Answer:** EUR, by mara" in text, text
    assert "comment: “as said on the call”" in text


def test_text_from_the_log_cannot_break_the_page():
    md = record_of_an_answer(EVIL_ANSWER, EVIL_COMMENT)
    assert "<script>" not in md and "[click](" not in md, md               # no markup, no link it did not have
    assert "\\<script\\>" in md and "\\[click\\]" in md                    # the words stay, escaped
    assert "\\`code\\`" in md                                              # a code span is shown, not made
    assert "\n# not a heading" not in md and "\n| a" not in md, md         # a newline in a comment starts no heading or table row
    assert "\\# not a heading" in md and "\\| a \\| b" in md, md
    answer = [ln for ln in md.splitlines() if ln.startswith("**Answer:**")]
    assert len(answer) == 1 and re.search(r"comment: “line one \\# not a heading", answer[0]), answer


def test_each_character_that_can_start_markup_is_escaped_and_a_backslash_cannot_undo_an_escape():
    md = record_of_an_answer(SPECIALS, "x")
    assert r"a\\b\`c\*d\_e\[f\]g\<h\>i\|j\#k" in md, md                 # all ten, in one line: each alone fails if its escape goes
    tag = record_of_an_answer("\\<b>hi</b>", "x")                                 # a backslash typed before a tag
    assert r"\\\<b\>hi\</b\>" in tag and "<b>" not in tag.replace(r"\<b\>", ""), tag   # is escaped itself: the tag stays escaped


if __name__ == "__main__":
    _t.main(globals())
