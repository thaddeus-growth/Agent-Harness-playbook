"""conformance/check.py passes a harness that keeps the rule and fails one that breaks it.

A fake harness (one script, a json file for state) runs in two modes. Each check
is tried on the broken mode first, so it cannot pass by looking at nothing; a
missing adapter section is a SKIP, never a pass; nothing passing is a failure.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import _t  # noqa: E402

spec = importlib.util.spec_from_file_location("check", os.path.join(REPO, "conformance/check.py"))
ck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ck)

FAKE = '''
import json, os, sys
mode, verb, *rest = sys.argv[1:]
state = os.path.join(os.environ["FAKE_DIR"], "s.json")
db = json.load(open(state)) if os.path.exists(state) else {}
if verb == "set":
    value, by = rest
    if db.get("by") == "human" and by != "human" and mode == "good":
        sys.exit("confirmed: an agent may not change it")
    db.update(value=value, by=by)
    json.dump(db, open(state, "w"))
elif verb == "get":
    print(db.get("value"))
elif verb == "doctor":
    print("  WARNING thing is off")
    if mode == "good":
        print("          -> fix it with: fake set")
'''


def adapter(mode: str, facts: bool = True, doctor: bool = True) -> str:
    base = f'["python3", "{{root}}/fake.py", "{mode}"'
    t = '[harness]\nname = "fake"\n[env]\nFAKE_DIR = "{tmp}"\n'
    if facts:
        t += (f'[facts]\nhuman = [{base}, "set", "{{value}}", "human"]]\nagent = [{base}, "set", "{{value}}", "agent"]]\n'
              f'read = {base}, "get"]\n')
    if doctor:
        t += f'[doctor]\nargv = {base}, "doctor"]\n'
    return t


def run(mode: str, **kw) -> tuple[int, str]:
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as root:
        open(f"{root}/fake.py", "w").write(FAKE)
        open(f"{root}/a.toml", "w").write(adapter(mode, **kw))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ck.main([f"{root}/a.toml", "--root", root])
        return rc, buf.getvalue()


def test_a_harness_that_breaks_the_rules_fails_both_checks():
    rc, out = run("bad")
    assert rc == 1 and "FAIL  C1" in out and "FAIL  C2" in out, out


def test_a_harness_that_keeps_the_rules_passes():
    rc, out = run("good")
    assert rc == 0 and "RESULT: 2 passed" in out and "FAIL" not in out, out


def test_a_missing_section_is_a_skip_not_a_pass():
    rc, out = run("good", doctor=False)
    assert "SKIP  C2" in out and "RESULT: 1 passed" in out, out
    rc, out = run("good", facts=False, doctor=False)
    assert rc == 1 and "RESULT: 0 passed" in out, "an adapter that checks nothing must not exit 0"


def test_a_summary_line_is_not_a_warning():
    assert not ck.WARN.match("3 warning(s). Fix before running pull/ingest.")
    assert ck.WARN.match("  WARNING fit scores rest on unconfirmed facts")
    assert ck.WARN.match("  ⚠ WARNING: no market declared")


def test_the_shipped_adapters_parse_and_name_only_known_sections():
    import tomllib
    d = os.path.join(REPO, "conformance/adapters")
    names = sorted(f for f in os.listdir(d) if f.endswith(".toml"))
    assert names, "no adapter shipped"
    known = {"harness", "env", "facts", "doctor"}
    for n in names:
        cfg = tomllib.load(open(os.path.join(d, n), "rb"))
        assert set(cfg) <= known, (n, set(cfg) - known)
        assert "harness" in cfg and "name" in cfg["harness"], n


TTY_FAKE = '''
import json, os, sys
verb, *rest = sys.argv[1:]
state = os.path.join(os.environ["FAKE_DIR"], "s.json")
db = json.load(open(state)) if os.path.exists(state) else {}
if verb == "set":
    float(rest[0])                            # a typed fact: a number or nothing
    if db.get("confirmed") and rest[0] != db["value"]:
        sys.exit("confirmed: an agent may not change it")
    db.update(value=rest[0], confirmed=False)
elif verb == "confirm":
    with open("/dev/tty") as t:              # the human, never stdin
        print("retype the value: ", end="", flush=True)
        if t.readline().strip() != db["value"]:
            sys.exit("mistyped")
    db["confirmed"] = True
elif verb == "get":
    print(db.get("value"))
json.dump(db, open(state, "w"))
'''


def test_typed_values_and_a_tty_confirm():
    """A harness with numeric facts whose confirm reads /dev/tty passes through `values` and a `tty` step."""
    base = '["python3", "{root}/fake.py"'
    t = ('[harness]\nname = "fake"\n[env]\nFAKE_DIR = "{tmp}"\n[facts]\nvalues = ["0.31", "0.77"]\n'
         f'human = [{base}, "set", "{{value}}"], {{argv = {base}, "confirm"], tty = "{{value}}"}}]\n'
         f'agent = [{base}, "set", "{{value}}"]]\nread = {base}, "get"]\n')
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as root:
        open(f"{root}/fake.py", "w").write(TTY_FAKE)
        open(f"{root}/a.toml", "w").write(t)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ck.main([f"{root}/a.toml", "--root", root])
    out = buf.getvalue()
    assert rc == 0 and "PASS  C1" in out, out


if __name__ == "__main__":
    _t.main(globals())
