#!/usr/bin/env python3
"""The gate protocol end to end: the fake "shop" harness, driven through
its own CLI (kit/tests/fake_harness/scripts/shop.py), and the playbook's
real console (console/ask.py + console/serve.py) relaying a person's
clicks. Nothing is imported from console/: both halves meet only in
their JSON and in the harness command, as in production.

  [1] onboarding: `shop facts init` (piped) leaves the market declaration
      and the answer pending; a write before the declaration is confirmed
      is refused (the scope enters only through init)
  [2] `shop pending --json` gives ready console asks; the declaration's
      ask carries a gate, the fact's waits for the declaration; ask.py
      refuses them until the agent writes its `because`, then posts them
  [3] serve.py runs with --relay-cmd = the shop CLI and --relay-verbs
      "facts confirm,queue approve" on a free loopback port; the person
      answers the declaration by posting the form the page renders: the
      console relays the one-time code, the harness writes it confirmed
      (changed_by @relay, the relay audit in its history)
  [4] the fact: its gate appears once the market is confirmed; the
      person types the value; confirmed through the relayed code. The
      same code replayed writes nothing; once the fact is pending again
      the replay is refused, and so is the code for another value
  [5] a queue proposal (`queue add` snapshots `compute steer`) approved
      the same way; `execute apply` (dry run) shows it going out; with
      --apply it is refused because writes are off, and nothing is
      written
  [5b] every read verb of the table keeps the --json contract on the data
      this run left (history rows, a queue item)
  [6] the console's own record: every answer signed, each with the
      harness's word
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import _shop  # noqa: E402
from kit.testing.check import check, finish, one_doc, tmp_dir  # noqa: E402
from kit.testing.sandbox import run as sandbox_run  # noqa: E402
from kit.testing.sandbox import sandbox_env  # noqa: E402

CFG = _shop.use()
CLI = _shop.SHOP / "scripts" / "shop.py"
CONSOLE = _shop.PLAYBOOK / "console"
DATA = Path(tmp_dir("e2e-data-"))
BOOK = Path(tmp_dir("e2e-console-")) / "log"
SECRET = CFG.env("CONFIRM_CODE_SECRET")
ENV = sandbox_env(DATA, PYTHONPATH=str(_shop.PLAYBOOK),
                  PYTHONDONTWRITEBYTECODE="1",
                  **{SECRET: "e2e-shop-gate-secret", "CONSOLE_DIR": None,
                     "CONSOLE_SECRET": None})
FORM = "application/x-www-form-urlencoded"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def shop(*argv: str, stdin: str | None = None) -> tuple[int, dict | None, str]:
    rc, out, err = sandbox_run([sys.executable, str(CLI), *argv], ENV,
                               stdin=stdin, timeout=120)
    return rc, one_doc(out), err


def ask_py(*argv: str, stdin: str | None = None) -> dict:
    rc, out, err = sandbox_run([sys.executable, str(CONSOLE / "ask.py"),
                                "--dir", str(BOOK), *argv], ENV, stdin=stdin,
                               timeout=60)
    return one_doc(out) or {"stdout": out, "stderr": err, "rc": rc}


def rows(sql: str, *args) -> list[tuple]:
    with closing(sqlite3.connect(DATA / CFG.db_file)) as c:
        return c.execute(sql, args).fetchall()


def fact(key: str) -> tuple | None:
    got = rows("SELECT value, is_assumption, changed_by FROM client_facts "
               "WHERE market = 'US' AND key = ?", key)
    return got[0] if got else None


def history(key: str) -> list[tuple]:
    return rows("SELECT action, new_value, changed_by, reason FROM "
                "client_facts_history WHERE market = 'US' AND key = ? "
                "ORDER BY id", key)


def filled(asks: list[dict]) -> list[dict]:
    """What the agent writes before it posts: its own argument."""
    return [{**a, "recommend": {**a["recommend"],
                                "because": "the owner said so at kickoff"}}
            for a in asks]


# ------------------------------------------------------------ the browser --

class Forms(HTMLParser):
    """Every <form>: its action and its named controls, as a browser sees."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.cur = {"action": a.get("action"), "fields": []}
            self.forms.append(self.cur)
        elif self.cur is not None and tag in ("input", "textarea") \
                and a.get("name"):
            self.cur["fields"].append((a["name"], a.get("type", tag),
                                       a.get("value", ""), "checked" in a))

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None


class Console:
    """serve.py as a subprocess on a free loopback port."""

    def __init__(self):
        cmd = [sys.executable, str(CONSOLE / "serve.py"), "--dir", str(BOOK),
               "--port", "0", "--user", "alice",
               "--relay-cmd", f"{sys.executable} {CLI}",
               "--relay-verbs", "facts confirm,queue approve",
               "--default-reason", "answered in the console"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=ENV,
                                     text=True)
        dog = threading.Timer(30, self.proc.kill)
        dog.start()
        try:
            first = self.proc.stdout.readline()
            self.relay = self.proc.stdout.readline().strip()
        finally:
            dog.cancel()
        m = re.fullmatch(r"Console: (\S+)\n", first)
        where = urllib.parse.urlsplit(m.group(1)) if m else None
        if where is None or where.hostname != "127.0.0.1" or not where.port:
            self.stop()
            raise AssertionError(f"serve.py did not start on loopback: "
                                 f"{first!r}")
        self.url, self.port = m.group(1), where.port

    def stop(self) -> str:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        err = self.proc.stderr.read()
        self.proc.stdout.close()
        self.proc.stderr.close()
        return err

    def fetch(self, path: str = "", data: bytes | None = None
              ) -> tuple[int, str]:
        headers = {"Content-Type": FORM, "Origin": self.url.rstrip("/"),
                   "Sec-Fetch-Site": "same-origin"} if data else {}
        req = urllib.request.Request(self.url + path, data=data,
                                     method="POST" if data else "GET",
                                     headers=headers)
        try:
            with OPENER.open(req, timeout=180) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def answer(self, ask_id: str, value: str, comment: str = ""
               ) -> tuple[int, str]:
        """Post the answer form the page renders for `ask_id`, as a
        browser would: its hidden fields as given, the person's value."""
        status, page = self.fetch()
        assert status == 200, status
        p = Forms()
        p.feed(page)
        form = next((f for f in p.forms if f["action"] == "answer"
                     and ("id", "hidden", ask_id, False) in f["fields"]), None)
        assert form is not None, f"no answer form for {ask_id}"
        fields = [(n, v) for n, kind, v, _ in form["fields"]
                  if kind == "hidden"]
        kinds = {n: kind for n, kind, _, _ in form["fields"]}
        assert "value" in kinds, form
        fields += [("value", value), ("comment", comment)]
        return self.fetch("answer", urllib.parse.urlencode(fields).encode())


def flat(page: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


# ------------------------------------------------------------------ steps --

def test_onboarding() -> None:
    print("[1] onboarding through the CLI")
    rc, doc, err = shop("facts", "init", "--json", stdin="US\n4.2\n\n")
    check("facts init (piped): the declaration and the answer, both pending",
          rc == 0 and doc["declaration"] == "pending"
          and fact("market_declared")[:2] == ("US", 1)
          and fact("unit_cost")[:2] == ("4.2", 1), (doc, err[-300:]))
    rc, doc, _ = shop("facts", "set", "channel", "web", "--source", "agent",
                      "--reason", "guess", "--json")
    check("a write before the declaration is confirmed: market_not_onboarded",
          rc == 2 and doc["code"] == "market_not_onboarded"
          and fact("channel") is None, doc)


def pending() -> dict:
    rc, doc, err = shop("pending", "--json")
    assert rc == 0 and doc is not None, (rc, err[-400:])
    return doc


def ask_for(doc: dict, table: str, **ref) -> dict:
    for a, b in zip(doc["asks"], doc["about"]):
        if b["table"] == table and all(b["ref"].get(k) == v
                                       for k, v in ref.items()):
            return a
    raise AssertionError(f"no {table} ask for {ref}: {doc['about']}")


def post(asks: list[dict]) -> dict:
    f = Path(tmp_dir("e2e-asks-")) / "asks.json"
    f.write_text(json.dumps(asks, ensure_ascii=False), encoding="utf-8")
    return ask_py("add", str(f))


def test_pending(srv: Console) -> tuple[dict, dict]:
    print("\n[2] pending -> console asks")
    doc = pending()
    decl = ask_for(doc, "client_facts", key="market_declared")
    cost = ask_for(doc, "client_facts", key="unit_cost")
    check("the declaration's ask carries the gate the harness binds",
          decl["step"] == "confirm" and decl["gate"] == {
              "verb": ["facts", "confirm", "market_declared"],
              "value_arg": False,
              "expect": {"market": "US", "items": {"market_declared": "US"}}},
          decl.get("gate"))
    check("the fact's ask has no gate until the market is confirmed",
          "gate" not in cost and "pending_gate_not_onboarded"
          in [w["code"] for w in doc["warning_codes"]], doc["warning_codes"])
    got = post([decl])
    check("ask.py refuses an ask whose `because` the agent left empty",
          got.get("ok") is False, got)
    got = post(filled([decl]))
    check("…and posts it once the agent wrote it", got.get("ok") is True, got)
    status, page = srv.fetch()
    check("the page shows the ask and what the gate will run",
          status == 200 and "Do we work on market US for you?" in flat(page)
          and "facts confirm market_declared" in flat(page))
    return decl, cost


def test_declaration(srv: Console, decl: dict) -> None:
    print("\n[3] the person confirms the market in the console")
    status, page = srv.answer(decl["id"], "yes", "we sell in the US")
    check("the answer is saved with the harness's word",
          status == 200 and "res ok" in page, flat(page)[:400])
    row = fact("market_declared")
    check("the harness wrote the declaration confirmed, through the relay",
          row[:2] == ("US", 0) and row[2].endswith("@relay"), row)
    last = history("market_declared")[-1]
    check("its history: a confirm with the person's reason and the relay "
          "audit", last[0] == "confirm" and last[1] == "US"
          and "we sell in the US" in last[3]
          and re.search(r"\[relay user=web:alice at=\d{4}-\d\d-\d\dT", last[3]),
          last)


def challenge(value: str) -> tuple[str, dict]:
    """What a caller without a code gets: the one-time code and its subject
    (issuing it writes nothing)."""
    rc, doc, _ = shop("facts", "confirm", "unit_cost", f"--value={value}",
                      "--reason=peek", "--json")
    assert rc == 2 and doc["code"] == "confirm_code_required", doc
    return doc["params"]["confirm_code"], doc["subject"]


def replay(code: str, value: str = "4.2") -> tuple[int, dict]:
    rc, doc, _ = shop("facts", "confirm", "unit_cost", f"--value={value}",
                      "--reason=replayed", "--json", f"--code={code}",
                      "--relay-user=web:mallory",
                      "--relay-at=2026-09-29T00:00:00Z")
    return rc, doc


def test_fact(srv: Console) -> None:
    print("\n[4] the person confirms a number; the code works once")
    cost = ask_for(pending(), "client_facts", key="unit_cost")
    check("once the market is confirmed the fact's ask carries its gate",
          cost["step"] == "provide" and cost["gate"] == {
              "verb": ["facts", "confirm", "unit_cost"], "value_arg": True,
              "expect": {"market": "US", "items": {"unit_cost": "$value"}}},
          cost.get("gate"))
    code, subject = challenge("4.2")
    check("the challenge is bound to the value and the market",
          subject["items"] == {"unit_cost": "4.2"} and subject["market"]
          == "US" and fact("unit_cost")[1] == 1)
    check("posted", post(filled([cost])).get("ok") is True)
    before = len(history("unit_cost"))
    status, page = srv.answer(cost["id"], "4.2")
    check("the person types 4.2: saved", status == 200 and "res ok" in page,
          flat(page)[:400])
    row = fact("unit_cost")
    check("unit_cost is confirmed through the relayed code",
          row[:2] == ("4.2", 0) and row[2].endswith("@relay")
          and len(history("unit_cost")) == before + 1, row)
    n = len(history("unit_cost"))
    rc, doc = replay(code)
    check("the same code replayed writes nothing (already confirmed)",
          rc == 0 and doc["message_code"]["code"] == "fact_already_confirmed"
          and len(history("unit_cost")) == n, doc)
    rc, doc, _ = shop("facts", "unconfirm", "unit_cost", "--reason",
                      "the invoice was wrong", "--json")
    check("the fact is doubted: pending again", rc == 0
          and fact("unit_cost")[1] == 1, doc)
    n = len(history("unit_cost"))
    rc, doc = replay(code)
    check("…and now the replayed code is refused (it was bound to a version "
          "the confirm moved); nothing written",
          rc == 2 and doc["code"] == "confirm_code_mismatch"
          and fact("unit_cost")[1] == 1 and len(history("unit_cost")) == n,
          doc)
    fresh, _ = challenge("4.2")
    rc, doc = replay(fresh, "5")
    check("a code shown for 4.2 does not confirm 5",
          rc == 2 and doc["code"] == "confirm_code_mismatch"
          and fact("unit_cost")[:2] == ("4.2", 1), doc)


def test_queue(srv: Console) -> None:
    print("\n[5] a proposal, approved in the console; execute")
    rc, doc, err = shop("facts", "confirm", "unit_cost", "--reason", "x",
                        "--json")
    code = doc["params"]["confirm_code"]
    rc, doc, _ = shop("facts", "confirm", "unit_cost", "--reason", "x",
                      "--json", "--code", code, "--relay-user", "web:alice",
                      "--relay-at", "2026-09-29T00:00:00Z")
    check("(the owner confirmed the cost again)", rc == 0
          and fact("unit_cost")[1] == 0, doc)
    rc, doc, err = shop("queue", "add", "--json")
    check("queue add snapshots compute steer through the CLI: one proposal",
          rc == 0 and doc["message_code"]["params"]["queued"] == 1,
          (doc, err[-400:]))
    q = rows("SELECT id, status, target_ref FROM action_queue")
    check("pending in the queue", q == [(1, "pending", "SKU-A1")], q)
    approve = ask_for(pending(), "action_queue", id=1)
    check("its ask: step approve, the gate `queue approve 1`, the effect",
          approve["step"] == "approve"
          and approve["gate"]["verb"] == ["queue", "approve", "1"]
          and approve["effect"] == "restock on SKU-A1: SKU-A1 gets 12 more "
          "units", approve)
    check("posted", post(filled([approve])).get("ok") is True)
    status, page = srv.answer(approve["id"], "yes", "go ahead")
    check("the person approves: saved", status == 200 and "res ok" in page,
          flat(page)[:400])
    q = rows("SELECT status, decided_by, reason FROM action_queue WHERE id=1")
    check("approved through the relayed code, with the audit",
          q[0][0] == "approved" and q[0][1].endswith("@relay")
          and "go ahead" in q[0][2] and "relay user=web:alice" in q[0][2], q)
    rc, doc, err = shop("execute", "apply", "--json")
    items = doc.get("items", []) if doc else []
    check("execute apply (a dry run) shows the approved item passing, and that --apply would send nothing while writes are off",
          rc == 0 and len(items) == 1 and items[0]["id"] == 1
          and items[0]["verdict_code"]["code"] == "execute_go"
          and doc["message_code"]["code"] == "execute_dry_run_by_hand"
          and doc["apply_possible"] is False
          and doc["apply_blocked_code"]["code"] == "write_off",
          (doc, err[-300:]))
    rc, doc, err = shop("execute", "apply", "--apply", "--json")
    check("--apply is refused: writes are off (SHOP_ALLOW_WRITES unset)",
          rc == 2 and doc["code"] == "write_off", (doc, err[-300:]))
    check("nothing was written: no effect, the item still approved",
          rows("SELECT COUNT(*) FROM action_effects") == [(0,)]
          and rows("SELECT status FROM action_queue") == [("approved",)])


def test_contract() -> None:
    print("\n[5b] every read verb keeps the --json contract on this data")
    from kit.guards import json_contract
    problems = json_contract.check_read_verbs(
        DATA, run=lambda argv: sandbox_run([sys.executable, str(CLI), *argv],
                                           ENV, timeout=120))
    check("one document each, every message coded, the compute's meta "
          "(history rows and queue items included)", problems == [], problems)


def test_record() -> None:
    print("\n[6] the console's record")
    got = ask_py("answers", "--all")
    answers = got.get("answers", [])
    check("three answers, each verified, each with the harness's word",
          len(answers) == 3 and all(a.get("verified") for a in answers)
          and all(isinstance(a.get("gate"), dict) and a["gate"]["ok"]
                  for a in answers), got)
    check("the gate verbs the console ran",
          [a["gate"]["verb"] for a in answers]
          == [["facts", "confirm", "market_declared"],
              ["facts", "confirm", "unit_cost"], ["queue", "approve", "1"]],
          [a.get("gate") for a in answers])


def main() -> int:
    test_onboarding()
    srv = Console()
    try:
        check("serve.py relays exactly the two gate verbs",
              srv.relay == "Relay verbs: facts confirm, queue approve",
              srv.relay)
        decl, _ = test_pending(srv)
        test_declaration(srv, decl)
        test_fact(srv)
        test_queue(srv)
    finally:
        log = srv.stop()
    check("the console logged no traceback", "Traceback" not in log, log[-500:])
    test_contract()
    test_record()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
