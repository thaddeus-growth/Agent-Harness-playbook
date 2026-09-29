#!/usr/bin/env python3
"""kit.runner: one script runs another and reads its one JSON document.

  * script_cmd: this interpreter, or `uv run` only when KIT_RUNNER=uv and
    uv is on PATH;
  * run_json: a child's document on exit 0; a child's failure document ->
    ChildFailed carrying its error, next and code (and fail() reports the
    child's code); a crash -> its stderr's last line, unclassified; no
    document -> ChildFailed; any_exit keeps a finding document; a hung
    child times out; a failed child's reason, fix commands and code come
    from its stdout document even with noise on stderr.
"""

import os
import stat
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import contract, runner  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.messages import failure  # noqa: E402
from kit.testing.check import (capture, check, finish, one_doc, raises,  # noqa: E402
                               tmp_dir)


def child(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text("import json, sys\n" + body, encoding="utf-8")
    return p


def main() -> int:
    _shop.use()
    d = Path(tmp_dir("children-"))

    print("[1] script_cmd")
    os.environ.pop("KIT_RUNNER", None)
    check("default: this interpreter",
          runner.script_cmd(d / "x.py", ["--json"])
          == [sys.executable, str(d / "x.py"), "--json"])
    fake_bin = Path(tmp_dir("bin-"))
    uv = fake_bin / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    saved_path = os.environ["PATH"]
    os.environ["PATH"] = str(fake_bin)
    try:
        check("uv on PATH but KIT_RUNNER unset: still this interpreter",
              runner.script_cmd("x.py", [])[0] == sys.executable)
        os.environ["KIT_RUNNER"] = "uv"
        check("KIT_RUNNER=uv: uv run <script>",
              runner.script_cmd("x.py", ["a"]) == [str(uv), "run", "x.py", "a"])
        os.environ["PATH"] = str(d)
        check("KIT_RUNNER=uv without uv: this interpreter",
              runner.script_cmd("x.py", [])[0] == sys.executable)
    finally:
        os.environ["PATH"] = saved_path
        os.environ.pop("KIT_RUNNER", None)

    print("\n[2] run_json")
    ok = child(d, "ok.py", "print(json.dumps({'args': sys.argv[1:]}))\n")
    check("exit 0: the child's document",
          runner.run_json(ok, ["--json"]) == {"args": ["--json"]})
    failing = child(d, "failing.py", (
        "print(json.dumps({'error': 'No price on file for A', 'next': "
        "['shop prices set A'], 'code': 'shop_price_missing', 'params': "
        "{'sku': 'A'}}))\nsys.exit(2)\n"))
    e = raises(lambda: runner.run_json(failing, []), runner.ChildFailed)
    check("a failure document -> ChildFailed(error, next, code)",
          e is not None and str(e) == "No price on file for A"
          and e.next == ["shop prices set A"]
          and e.code == {"code": "shop_price_missing", "params": {"sku": "A"}})
    check("ChildFailed is a HarnessError carrying the child's code",
          isinstance(e, HarnessError) and failure(e) == e.code)
    rc, out, _ = capture(lambda a: contract.fail(e, cmd=["shop", "q"],
                                                 as_json=True), [])
    check("fail() relays the child's error, next and code",
          rc == 2 and one_doc(out) == {"error": "No price on file for A",
                                       "next": ["shop prices set A"],
                                       **e.code}, out)
    noisy = child(d, "noisy.py", (
        "print('Installed 3 packages in 4ms', file=sys.stderr)\n"
        "print(json.dumps({'error': 'approve needs a person', 'next': "
        "['shop queue approve 1'], 'code': 'confirm_needs_human', 'params': "
        "{'what': 'approve'}}))\n"
        "print('uv: done', file=sys.stderr)\nsys.exit(1)\n"))
    e = raises(lambda: runner.run_json(noisy, []), runner.ChildFailed)
    check("a failed child is read from its document on stdout, whatever "
          "stderr holds (its reason, next and code, not stderr's last line)",
          e is not None and str(e) == "approve needs a person"
          and e.next == ["shop queue approve 1"]
          and e.code == {"code": "confirm_needs_human",
                         "params": {"what": "approve"}}, e)
    quiet_fail = child(d, "quiet_fail.py", (
        "print(json.dumps({'error': 'no store', 'next': []}))\n"
        "sys.exit(1)\n"))
    e = raises(lambda: runner.run_json(quiet_fail, [], any_exit=True),
               runner.ChildFailed)
    check("a failure document with no code: its error, code None "
          "(relayed: unclassified_error)",
          e is not None and str(e) == "no store" and e.code is None
          and failure(e)["code"] == "unclassified_error", e)
    crash = child(d, "crash.py", "raise RuntimeError('kaboom')\n")
    e = raises(lambda: runner.run_json(crash, []), runner.ChildFailed)
    check("a crash: stderr's last line, no next, unclassified",
          str(e) == "RuntimeError: kaboom" and e.next == [] and e.code is None
          and failure(e)["code"] == "unclassified_error", e)
    silent = child(d, "silent.py", "print('not json')\n")
    e = raises(lambda: runner.run_json(silent, []), runner.ChildFailed)
    check("exit 0 without a JSON document: ChildFailed",
          e is not None and "printed no JSON document" in str(e), e)
    finding = child(d, "finding.py",
                    "print(json.dumps({'stories': []}))\nsys.exit(1)\n")
    check("any_exit: a document without error is the output, whatever the "
          "exit", runner.run_json(finding, [], any_exit=True) == {"stories": []})
    check("… without any_exit it is a failure",
          raises(lambda: runner.run_json(finding, []), runner.ChildFailed)
          is not None)
    check("any_exit: an error document still raises",
          raises(lambda: runner.run_json(failing, [], any_exit=True),
                 runner.ChildFailed) is not None)
    hung = child(d, "hung.py", "import time; time.sleep(30)\n")
    e = raises(lambda: runner.run_json(hung, [], timeout=0.5),
               runner.ChildFailed)
    check("a hung child times out as ChildFailed",
          e is not None and "timed out" in str(e), e)
    os.environ["SHOP_SEEN"] = "yes"
    env_child = child(d, "env.py", "import os\n"
                      "print(json.dumps(os.environ.get('SHOP_SEEN')))\n")
    check("the child inherits this process's env",
          runner.run_json(env_child, []) == "yes")
    del os.environ["SHOP_SEEN"]
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
