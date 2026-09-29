"""build/README.md and the scripts' own words name only real things. Each
test names the rule it guards and would fail if that rule were removed."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

README = os.path.join(_t.BUILD, "README.md")
SCRIPTS = ("check_intake.py", "intake_to_asks.py", "apply_answers.py", "digest.py")


def test_the_readme_is_short():
    lines = [ln for ln in _t.read(README).splitlines() if ln.strip()]
    assert 8 <= len(lines) <= 20, len(lines)


def test_every_link_and_path_the_readme_names_exists():
    text = _t.read(README)
    for link in re.findall(r"\]\(([^)#]+)\)", text):
        assert os.path.exists(os.path.normpath(os.path.join(_t.BUILD, link))), link
    for path in re.findall(r"`python3 ((?:build|console)/[\w/]+\.py)", text):
        assert os.path.exists(os.path.join(_t.ROOT, path)), path
    for path in re.findall(r"\((tests/test_\w+\.py)\)", text):
        assert os.path.exists(os.path.join(_t.BUILD, path)), path
    named = set(re.findall(r"build/(\w+\.py)", text))
    assert set(SCRIPTS) <= named, set(SCRIPTS) - named                     # every tool is in the chain


def test_every_flag_the_readme_shows_is_a_flag_of_the_script_it_follows():
    text = _t.read(README)
    for script, args in re.findall(r"`python3 build/(\w+\.py)([^`]*)`", text):
        src = _t.read(os.path.join(_t.BUILD, script))
        for flag in re.findall(r"(--[a-z-]+)", args):
            assert f'"{flag}"' in src, (script, flag)


def test_every_script_names_the_test_that_holds_its_rules():
    for s in SCRIPTS:
        doc = _t.read(os.path.join(_t.BUILD, s))
        named = re.findall(r"build/tests/(test_\w+\.py)", doc)
        assert named and all(os.path.exists(os.path.join(_t.HERE, n)) for n in named), (s, named)
        assert doc.startswith("#!/usr/bin/env -S uv run --script\n# /// script\n"), s   # PEP 723, like console/


def test_every_tool_says_how_to_use_it_as_one_json_document():
    for s in SCRIPTS:
        code, doc = _t.tool(s, "--help")
        assert code == 0 and doc["ok"] is True and (doc.get("help") or doc.get("usage")), (s, doc)


def test_nothing_in_build_holds_a_machine_path_an_address_or_an_outside_link():
    private = re.compile(r"/Us" r"ers/|/home/[a-z]|/private/" r"tmp|/tmp/" r"claude|/var/" r"folders/")
    address = re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9.-]+\.[a-z]{2,}")
    link = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*)")
    files = [os.path.join(b, n) for b, _d, ns in os.walk(_t.BUILD) if "__pycache__" not in b for n in ns]
    files.append(os.path.join(_t.ROOT, "templates", "intake.schema.json"))
    for p in files:
        if p == os.path.abspath(__file__):
            continue
        text = _t.read(p)
        assert not private.search(text), p
        assert not address.findall(text), (p, address.findall(text))
        assert all(h in ("localhost", "127.0.0.1", "example.com") or h.endswith(".example.com")
                   for h in link.findall(text)), (p, link.findall(text))      # the console's rule, console/tests/test_docs.py


if __name__ == "__main__":
    _t.main(globals())
