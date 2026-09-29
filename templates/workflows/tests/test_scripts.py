"""The workflow scripts read as text, without running them (no node needed).

The runtime needs `export const meta = {...}` first and as a pure literal, matches phases by
title, and refuses the clock and randomness (they would break resume). The rules table in the
README and the RULES of each script name the same ids. Nothing in this public folder names a
private path. Each check is shown failing on a broken copy of the text as well as passing.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t  # noqa: E402

SCRIPTS = [os.path.join(_t.WORKFLOWS, n) for n in ("build-review-fix-verify.js", "sweep-skeptic-plan.js")]
README = os.path.join(_t.WORKFLOWS, "README.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def code_only(src):
    """The script without its // and /* */ comments (strings are kept)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;,{}()\s])//[^'`\n]*$", "", src)


def meta_block(src):
    """The text of the `export const meta = {...}` literal, or None when it is not first."""
    m = re.match(r"export const meta = (\{.*?\n\})\n", src, re.S)
    return m.group(1) if m else None


def meta_problem(src):
    """Why the meta block is not a pure literal, or None."""
    block = meta_block(src)
    if block is None:
        return "the script does not start with export const meta = {...}"
    bare = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", block)          # every string is now ''
    if "`" in bare or "${" in block:
        return "a template string in meta"
    bare = re.sub(r"\b[A-Za-z_]\w*\s*:", "", bare)             # keys
    bare = re.sub(r"''", "", bare)
    if re.search(r"[^\s{}\[\],]", bare):
        return f"meta holds something other than keys and strings: {bare.strip()[:60]!r}"
    return None


def phases(src):
    """(titles meta declares, titles the code uses)."""
    declared = re.findall(r"title: '([^']+)'", meta_block(src) or "")
    body = code_only(src.split("\n}\n", 1)[-1])
    used = set(re.findall(r"phase: '([^']+)'", body)) | set(re.findall(r"\bphase\('([^']+)'\)", body))
    return declared, used


def forbidden(src):
    """Calls the runtime refuses: Date.now(), Math.random(), an argless new Date()."""
    return re.findall(r"Date\.now\s*\(|Math\.random\s*\(|new Date\s*\(\s*\)", code_only(src))


def rule_ids(src):
    m = re.search(r"^const RULES = \{\n(.*?)^\}", src, re.S | re.M)
    return set(re.findall(r"^\s*(R\d+):", m.group(1), re.M)) if m else set()


def table_ids(md):
    return set(re.findall(r"^\| (R\d+) \|", md, re.M))


def test_meta_is_first_and_a_pure_literal():
    for path in SCRIPTS:
        assert meta_problem(read(path)) is None, (path, meta_problem(read(path)))
    good = read(SCRIPTS[0])
    broken = {"a variable": good.replace("name: 'build-review-fix-verify'", "name: NAME", 1),
              "a call": good.replace("name: 'build-review-fix-verify'", "name: pick('x')", 1),
              "a template": good.replace("name: 'build-review-fix-verify'", "name: `b${1}`", 1),
              "a spread": good.replace("  phases: [", "  ...EXTRA,\n  phases: [", 1),
              "not first": "const X = 1\n" + good}
    for why, src in broken.items():
        assert meta_problem(src), why


def test_every_phase_used_is_declared_and_every_declared_one_used():
    for path in SCRIPTS:
        declared, used = phases(read(path))
        assert declared and set(declared) == used, (path, declared, used)
    src = read(SCRIPTS[0]).replace("phase: 'Verify'", "phase: 'Check'", 1)
    declared, used = phases(src)
    assert set(declared) != used


def test_no_clock_and_no_randomness():
    for path in SCRIPTS:
        assert forbidden(read(path)) == [], path
    src = read(SCRIPTS[1])
    for bad in ("const t = Date.now()", "const r = Math.random()", "const d = new Date()"):
        assert forbidden(src + "\n" + bad + "\n"), bad
    assert not forbidden(src + "\n// Date.now() would break resume\n")


def test_the_rules_table_and_the_scripts_name_the_same_ids():
    table = table_ids(read(README))
    used = set().union(*(rule_ids(read(p)) for p in SCRIPTS))
    assert table and table == used, (sorted(table - used), sorted(used - table))
    for path in SCRIPTS:
        src = read(path)
        for rid in rule_ids(src):
            assert re.search(rf"'{rid}'", src), f"{path}: {rid} is never given to a prompt"
    extra = read(SCRIPTS[0]).replace("const RULES = {\n", "const RULES = {\n  R99: () => 'new',\n", 1)
    assert rule_ids(extra) - table == {"R99"}


PRIVATE = re.compile(r"/Users/|/home/[a-z]|/private/tmp|[A-Z]:\\\\Users|\bB0[0-9A-Z]{8}\b")


def test_no_file_names_a_private_path_or_a_product_id():
    for root, _, files in os.walk(_t.WORKFLOWS):
        if "__pycache__" in root:
            continue
        for name in files:
            path = os.path.join(root, name)
            if os.path.samefile(path, __file__):        # this file spells the patterns out
                continue
            text = read(path)
            assert not PRIVATE.search(text), (name, PRIVATE.search(text).group(0))
    assert PRIVATE.search("see /Users/someone/x") and PRIVATE.search("item B0ABCDEFGH")


if __name__ == "__main__":
    _t.main(globals())
