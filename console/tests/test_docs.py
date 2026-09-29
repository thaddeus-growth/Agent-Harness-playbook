"""The docs say only what the code does.

The console's README and AGENT.md, the top README's console section and the two
templates that name the console are read as text and held against the tools:
every path, flag, verb, limit and diagram they show is real; the quick start runs
as written (a real `ask.py`, a real `serve.py`) and prints what the README says it
prints; each rule the docs give the agent or the operator is one the code keeps
(and each is stated in words a reader can find); the top README promises no more
than the console does; and nothing a commit here would publish, bytecode
included, is a path, key, address or link that must not be public. A doc that
drifts from the code fails here, and so does a code change the docs no longer
describe. Each check is tried on a broken input first, so it cannot pass by
looking at nothing. Word lists of private names are deliberately not here: this
file is public too.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import urllib.request

sys.dont_write_bytecode = True            # a docs test leaves no .pyc beside the files it reads
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))          # console/
REPO = os.path.abspath(os.path.join(ROOT, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _t  # noqa: E402
import ask  # noqa: E402
import core  # noqa: E402
import i18n  # noqa: E402
import pages  # noqa: E402
import relay  # noqa: E402
import run as runner  # noqa: E402
import serve  # noqa: E402

CONSOLE, AGENT, TOP = "console/README.md", "console/AGENT.md", "README.md"
QUEUE, INSTR = "templates/owner-queue-item.md", "templates/AGENT_INSTRUCTIONS.md"
DOCS = (CONSOLE, AGENT, TOP, QUEUE, INSTR)
HARNESS_FLAGS = {"--value", "--reason", "--json", "--code", "--relay-user", "--relay-at"}   # a harness's, not ours
SCHEMATIC = {"--name"}                                                                       # `--name=value`, the shape
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
PATH = re.compile(r"(?<![\w/.-])((?:console|templates)/[A-Za-z0-9_./-]*[A-Za-z0-9_/])")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
CALL = re.compile(r"ask\.py (?:--\S+ \S+ )*([a-z]+)\b")


def doc(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def source(name: str) -> str:
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def flags_of(parser) -> set[str]:
    out = set()
    for a in parser._actions:
        out.update(o for o in a.option_strings if o.startswith("--"))
        if isinstance(a, argparse._SubParsersAction):
            for sub in a.choices.values():
                out |= flags_of(sub)
    return out


def ask_verbs() -> set[str]:
    sub = next(a for a in ask._parser()._actions if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices)


def example(name: str) -> dict:
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as f:
        return json.load(f)


def child_env(extra: dict | None = None) -> dict:
    """Our environment without the console's own settings, so a run depends on its arguments alone."""
    env = {k: v for k, v in os.environ.items() if k not in ("CONSOLE_DIR", "CONSOLE_SECRET")}
    return {**env, "PYTHONDONTWRITEBYTECODE": "1", **(extra or {})}


def run_ask(*argv: str, env: dict | None = None) -> tuple[int, dict]:
    """One ask.py run: (its exit code, its one JSON document)."""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "ask.py"), *argv],
                       capture_output=True, text=True, env=child_env(env), timeout=30)
    return p.returncode, json.loads(p.stdout)


def cli(folder: str, *args: str, env: dict | None = None) -> dict:
    """One ask.py run on a folder: its document, the exit code being 0 or 2 by its own rule."""
    code, doc_ = run_ask("--dir", folder, *args, env=env)
    assert code == (0 if doc_.get("ok") is not False else 2), (code, doc_)
    return doc_


def serve_refuses(*argv: str, env: dict | None = None) -> str:
    """What a real serve.py says on stderr when it will not start (and that it exits 2, having listened on nothing)."""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "serve.py"), *argv], capture_output=True, text=True,
                       env=child_env(env), timeout=30, stdin=subprocess.DEVNULL)
    assert p.returncode == 2 and "Traceback" not in p.stderr, (p.returncode, p.stderr)
    return p.stderr


@contextlib.contextmanager
def stdin_is_a_pipe():
    """Our own stdin made something other than /dev/null, so a child that merely inherits it is seen."""
    r, w = os.pipe()
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (r, w, saved):
            os.close(fd)


def dies(argv: list[str]) -> str:
    """What serve.py says on stderr when it refuses to start (and that it exits 2)."""
    def boom(*a, **k):
        raise AssertionError("serve.py started")
    err, real = io.StringIO(), serve.Server
    serve.Server = boom                       # a refusal that went missing must fail here, never bind a port
    try:
        with contextlib.redirect_stderr(err):
            serve.main(argv)
    except SystemExit as e:
        assert e.code == 2, e.code
        return err.getvalue()
    finally:
        serve.Server = real
    raise AssertionError("serve.py did not refuse")


# ------------------------------------------------------------ what is named --

def missing(rel: str, text: str) -> list[str]:
    """Links and `console/…` or `templates/…` paths in `text` that are not there."""
    base = os.path.dirname(os.path.join(REPO, rel))
    out = [t for t in LINK.findall(text) if not re.match(r"[a-z]+:", t)
           and not os.path.exists(os.path.normpath(os.path.join(base, t)))]
    return out + [p for p in PATH.findall(text) if not os.path.exists(os.path.join(REPO, p))]


def test_the_two_console_docs_stay_short():
    assert len(doc(CONSOLE).splitlines()) < 220, "the README is a page to look at, not a manual"
    assert len(doc(AGENT).splitlines()) <= 60, "the agent's file is read on every run"


def test_every_link_and_path_the_docs_name_exists():
    assert missing(TOP, "see [a](nope/none.md) and console/nope.py and console/ask.py") == ["nope/none.md", "console/nope.py"]
    for rel in DOCS:
        assert not missing(rel, doc(rel)), (rel, missing(rel, doc(rel)))
    assert sum(len(PATH.findall(doc(r))) for r in DOCS) > 15               # the check saw something


def test_every_flag_the_docs_show_is_real_and_the_ones_that_matter_are_shown():
    real = flags_of(serve._parser()) | flags_of(ask._parser()) | {"--help"}
    assert {"--user-header", "--max-open", "--relay-verbs", "--since"} <= real
    for rel in (CONSOLE, AGENT, QUEUE):
        shown = set(FLAG.findall(doc(rel)))
        assert shown, rel
        assert not shown - real - HARNESS_FLAGS - SCHEMATIC, (rel, sorted(shown - real - HARNESS_FLAGS - SCHEMATIC))
    assert "--gone" in set(FLAG.findall("`--gone`")) - real                # the check would notice
    said = "".join(doc(r) for r in (CONSOLE, AGENT))
    for flag in ("--user", "--user-header", "--allow-host", "--host", "--port", "--relay-cmd", "--relay-verbs",
                 "--default-reason", "--max-open", "--since", "--where", "--dir", "--as"):
        assert flag in real and flag in said, flag


def test_every_verb_the_docs_call_is_real_and_every_verb_is_told():
    verbs = ask_verbs()
    assert {"add", "wait", "answers", "applied", "withdraw", "say", "schema", "verify", "list", "digest"} == verbs
    for rel in (CONSOLE, AGENT, QUEUE):
        called = set(CALL.findall(doc(rel)))
        assert called and called <= verbs, (rel, called - verbs)
    assert CALL.findall("ask.py --as x fly now") == ["fly"]                # the check would notice
    said = doc(CONSOLE) + doc(AGENT)
    for verb in verbs:
        assert re.search(rf"\b{verb}\b", said), verb


def test_the_owner_queue_template_maps_only_fields_that_exist():
    row = re.compile(r"^\| `\w+` \| (.+) \|$", re.M)
    fields = set(core.ASK_FIELDS) | set(core.STEPS) | {"value", "because", "comment", "suggested", "apply"}
    seen = set()
    for cell in row.findall(doc(QUEUE).split("## In the console")[1]):
        seen |= {w for w in re.findall(r"`(\w+)`", cell)}
    assert seen and seen <= fields, sorted(seen - fields)
    assert {"id", "kind", "step", "title", "why", "evidence", "recommend", "if_no", "suggested", "apply"} <= seen
    assert "ID@SEQ" in doc(QUEUE)


# ---------------------------------------------------------------- diagrams --

MERMAID = re.compile(r"```mermaid\n(.*?)\n```", re.S)


def sound(block: str) -> str | None:
    """Why a Mermaid block is broken (unclosed, unknown kind, a class or participant nobody declared), else None."""
    kind = block.split()[0]
    if kind not in ("flowchart", "stateDiagram-v2", "sequenceDiagram"):
        return f"unknown kind {kind}"
    if block.count('"') % 2 or block.count("[") != block.count("]") or block.count("(") != block.count(")"):
        return "unbalanced quote or bracket"
    lines = [ln.strip() for ln in block.splitlines()]
    if sum(ln.startswith("subgraph") for ln in lines) != lines.count("end"):
        return "subgraph and end do not pair"
    if set(re.findall(r":::(\w+)", block)) - set(re.findall(r"classDef (\w+)", block)):
        return "a class is used and never defined"
    if kind == "sequenceDiagram":
        who = set(re.findall(r"participant (\w+)", block))
        talk = set(re.findall(r"^\s*(\w+)-{1,2}>>", block, re.M)) | set(re.findall(r"-{1,2}>>(\w+):", block))
        if talk - who:
            return f"talks to {sorted(talk - who)}, never declared"
    return None


def test_every_diagram_is_sound_and_names_real_things():
    assert sound("gantt\n a") and sound('flowchart LR\n A["x') and sound("flowchart LR\n A:::x")
    assert sound("sequenceDiagram\n participant A as a\n A->>B: hi")
    assert sound("flowchart LR\n subgraph S\n A") and not sound("flowchart LR\n A --> B")
    for rel in (CONSOLE, TOP):
        text = doc(rel)
        assert text.count("```") % 2 == 0, rel
        for block in MERMAID.findall(text):
            assert sound(block) is None, (rel, sound(block), block[:60])
    blocks = MERMAID.findall(doc(CONSOLE))
    assert len(blocks) == 4
    for block in blocks:
        for name in re.findall(r"\b([a-z_]+\.py)\b", block):
            assert os.path.exists(os.path.join(ROOT, name)), name
    arch, loop, seq = diagram("ask.py<br/>"), diagram("wait --since"), diagram("sequenceDiagram")
    assert os.path.basename(core.Store("x").path) in arch
    label = re.search(r'ask\.py<br/>([^"]+)"', arch)
    assert label and set(label.group(1).replace("<br/>", " · ").split(" · ")) <= ask_verbs()
    chain = re.findall(r'\w+\["([^"]+)"\]', loop.splitlines()[1])         # the main line of the loop, in order
    assert [c.split("<br/>")[0].split()[0] for c in (chain[0], chain[1], chain[3], chain[4])] == ["add", "answers", "applied", "wait"]
    assert "the seq answers returned" in chain[4] and chain[3].startswith("applied ID@SEQ --where")
    told = [doc(AGENT).index(f"`ask.py {v}") for v in ("add", "answers", "applied", "wait")]
    assert told == sorted(told)                                                   # AGENT.md walks the same order
    assert re.findall(r": ([1-4]) ", seq) == ["1", "2", "3", "4"]                # the exchange relay.py runs, in its order


def diagram(needle: str) -> str:
    found = [b for b in MERMAID.findall(doc(CONSOLE)) if needle in b]
    assert len(found) == 1, (needle, len(found))
    return found[0]


def edges_of_the_fold() -> set[tuple[str, str]]:
    """Every (from, to) one event can make an ask do, found by trying each event on each state."""
    a, _ = core.validate_ask(example("confirm-word.json"))
    changed = {**a, "title": a["title"] + " again"}

    def ev(seq, type, **f):
        return {"seq": seq, "at": "2026-01-01T00:00:00Z", "by": "x", "type": type, "id": a["id"], **f}
    ans = dict(value="yes", subject=core.subject_hash(a))
    made = {"open": [ev(1, "ask", ask=a)], "answered": [ev(1, "ask", ask=a), ev(2, "answer", **ans)],
            "applied": [ev(1, "ask", ask=a), ev(2, "answer", **ans), ev(3, "applied", where="x")],
            "withdrawn": [ev(1, "ask", ask=a), ev(2, "withdraw", reason="x")]}
    edges = set()
    for src, path in made.items():
        n = len(path) + 1
        for move in (ev(n, "ask", ask=changed), ev(n, "answer", **ans), ev(n, "reopen", answer_seq=2),
                     ev(n, "applied", where="x"), ev(n, "withdraw", reason="x")):
            st = core.fold(path + [move])
            if not st["problems"]:
                cur = st["asks"][a["id"]]
                edges.add((src, "applied" if cur["applied"] else cur["status"]))
    return edges


def test_the_state_diagram_is_the_fold():
    states = diagram("stateDiagram-v2")
    drawn = set(re.findall(r"^\s*(\w+) --> (\w+)", states, re.M))
    assert drawn == edges_of_the_fold(), (drawn ^ edges_of_the_fold())
    assert "[*] --> open" in states
    assert set(re.findall(r"^\s*(\w+) --> \[\*\]", states, re.M)) == {"applied", "withdrawn"}
    assert not {e for e in edges_of_the_fold() if e[0] in ("applied", "withdrawn")}    # nothing leaves an end


# ------------------------------------------------ the quick start, end to end --

def test_the_quick_start_runs_as_written_and_prints_what_the_readme_prints():
    readme = doc(CONSOLE)
    since = re.search(r"\nuv run console/ask\.py answers\nuv run console/ask\.py wait --since (\d+)\n```", readme).group(1)
    block = re.search(r"ask\.py wait --since \d+\n```\n.*?```json\n(.*?)\n```", readme, re.S).group(1)
    shown = json.loads(block)
    sent_id, where = re.search(r'ask\.py applied (\S+) --where "([^"]+)"', readme).groups()
    row = shown["answers"][0]
    assert sent_id == row["apply"] and row["id"] == example("choose-priority.json")["id"]
    with _t.tmpdir() as d:
        posted = cli(d, "add", os.path.join(ROOT, "examples", "choose-priority.json"))
        looked = cli(d, "answers")
        assert looked["seq"] == posted["seq"] == int(since) and looked["answers"] == []     # "the seq that answers returned"
        store = core.Store(d)
        cur = store.state()["asks"][row["id"]]
        store.answer("alice", row["id"], row["value"], shown=cur["hash"])
        real = cli(d, "wait", "--since", since, "--timeout", "0")
        assert real["timed_out"] is False
        for k, v in shown.items():
            if k not in ("answers", "dir"):
                assert real[k] == v, k
        assert real["dir"] == d and os.path.isabs(shown["dir"])                              # every ok reply names its folder
        assert len(real["answers"]) == 1
        for k, v in row.items():
            assert real["answers"][0][k] == v, k
        stale = cli(d, "applied", f"{row['id']}@1", "--where", where)       # an answer the agent never read
        assert stale["ok"] is False and stale["code"] == "changed"
        done = cli(d, "applied", sent_id, "--where", where)
        assert done["ok"] is True and done["applied"] == [row["id"]]
        assert cli(d, "answers")["answers"] == []


def test_the_console_command_of_the_quick_start_prints_the_address_the_readme_gives():
    readme = doc(CONSOLE)
    args = shlex.split(re.search(r"\nuv run console/serve\.py ([^\n]+)\n```", readme).group(1))
    shown = re.search(r"prints `(Console: http://127\.0\.0\.1:)8770(/)`", readme)
    assert args and shown and serve._parser().parse_args([]).port == 8770
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with _t.tmpdir() as d:
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "serve.py"), *args, "--dir", d, "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, env=child_env())
        watchdog = threading.Timer(30, proc.kill)
        watchdog.start()
        try:
            assert proc.stdout.readline().strip() == f"{shown.group(1)}{port}{shown.group(2)}"
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
                assert "Northwind Tea Co." in r.read().decode("utf-8")               # --title, as the quick start gives it
        finally:
            watchdog.cancel()
            proc.kill()
            proc.wait()
            proc.stdout.close()
    with socket.socket() as busy:                                                     # "--port N if 8770 is taken"
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        with _t.tmpdir() as d:
            assert "--port" in serve_refuses("--dir", d, "--port", str(busy.getsockname()[1]))
    assert "`--port N` if 8770 is taken" in readme


def test_the_scripts_the_docs_run_with_uv_are_pep_723_scripts():
    for name in ("ask.py", "serve.py"):
        src = source(name)
        assert src.startswith("#!/usr/bin/env -S uv run --script\n# /// script\n"), name
        block = src.split("# /// script")[1].split("# ///")[0]
        assert 'requires-python = ">=3.11"' in block and "dependencies = []" in block, name
        assert os.access(os.path.join(ROOT, name), os.X_OK) and f"uv run console/{name}" in doc(CONSOLE), name


def test_wait_is_given_the_seq_answers_returned_so_an_answer_in_between_is_heard():
    with _t.tmpdir() as d:
        first = example("confirm-word.json")
        cli(d, "add", os.path.join(ROOT, "examples", "confirm-word.json"))
        looked = cli(d, "answers")                                          # the loop's step 2: nothing yet
        store = core.Store(d)
        store.answer("alice", first["id"], "yes", shown=store.state()["asks"][first["id"]]["hash"])   # lands while the next ask is written
        posted = cli(d, "add", os.path.join(ROOT, "examples", "choose-priority.json"))
        late = cli(d, "wait", "--since", str(posted["seq"]), "--timeout", "0")
        assert late["timed_out"] is True and [a["id"] for a in late["answers"]] == [first["id"]]   # add's seq: not heard, yet listed
        heard = cli(d, "wait", "--since", str(looked["seq"]), "--timeout", "0")
        assert heard["timed_out"] is False and [a["id"] for a in heard["answers"]] == [first["id"]]
    text = doc(AGENT)
    assert "with the `seq` that `answers` returned (not `add`'s" in text
    assert "`timed_out: true` means nobody has answered yet, not that the answer is no: read `answers[]` anyway" in text


def test_notes_and_reopens_come_after_since_only_and_a_first_look_sees_the_newest_20():
    with _t.tmpdir() as d:
        ask_id = example("confirm-word.json")["id"]
        cli(d, "add", os.path.join(ROOT, "examples", "confirm-word.json"))
        store = core.Store(d)
        cur = store.state()["asks"][ask_id]
        store.answer("alice", ask_id, "yes", shown=cur["hash"])
        store.reopen("alice", ask_id, store.state()["asks"][ask_id]["answer"]["seq"])
        for n in range(25):
            store.note("alice", f"note {n}")
        first = cli(d, "answers")
        assert [n["text"] for n in first["notes"]] == [f"note {n}" for n in range(5, 25)] and first["notes_truncated"] is True
        assert [r["id"] for r in first["reopened"]] == [ask_id]
        after = cli(d, "answers", "--since", str(first["seq"]))
        assert after["notes"] == [] and after["reopened"] == [] and after["notes_truncated"] is False
        assert cli(d, "answers")["notes"] == first["notes"]                # nothing marks a note read
    text = doc(AGENT)
    assert "the newest 20 notes" in text and "Nothing marks a note read" in text and "leave `--since` out only on a first run" in text


def test_dir_and_as_go_before_the_verb_as_the_docs_say():
    with _t.tmpdir() as d:
        code, refused = run_ask("list", "--dir", d)
        assert code == 2 and refused["code"] == "bad_request"
        code, fine = run_ask("--dir", d, "--as", "bob", "list")
        assert code == 0 and fine["ok"] is True
    assert "Both flags go before the verb (`ask.py --as bob list`); after it they are refused (`bad_request`)" in doc(AGENT)


def test_a_no_is_an_answer_and_is_applied_too_as_the_docs_say():
    with _t.tmpdir() as d:
        spend, path = example("approve-spend.json"), os.path.join(ROOT, "examples", "approve-spend.json")
        cli(d, "add", path)
        store = core.Store(d)
        store.answer("alice", spend["id"], "no", shown=store.state()["asks"][spend["id"]]["hash"])
        row = cli(d, "answers")["answers"][0]
        assert row["value"] == "no" and row["apply"].startswith(spend["id"] + "@")
        assert cli(d, "withdraw", spend["id"], "--reason", "x")["code"] == "not_open"
        assert cli(d, "add", path)["code"] == "id_used"
        said = re.search(r'ask\.py applied ID@SEQ --where "(declined, nothing changed)"', doc(AGENT)).group(1)
        assert cli(d, "applied", row["apply"], "--where", said)["ok"] is True
        assert cli(d, "answers")["answers"] == []
    assert "A `no` is an answer like any other" in doc(AGENT) and 'A "no" is an answer too, and is recorded with `applied`' in doc(CONSOLE)
    assert "Until then it stays in `answers`, and `withdraw` and a new `add` of its id are refused" in doc(AGENT)


def test_an_id_is_refused_once_it_is_answered_not_only_once_it_is_applied():
    with _t.tmpdir() as d:
        path = os.path.join(ROOT, "examples", "confirm-word.json")
        ask_id = example("confirm-word.json")["id"]
        cli(d, "add", path)
        store = core.Store(d)
        store.answer("alice", ask_id, "yes", shown=store.state()["asks"][ask_id]["hash"])
        again = cli(d, "add", path)
        assert again["code"] == "id_used" and again["params"]["status"] == "answered"
    assert "once an ask is answered or withdrawn, `add` refuses it (`id_used`)" in doc(CONSOLE)


def test_every_ok_reply_names_the_folder_and_verify_without_a_secret_is_refused():
    with _t.tmpdir() as d:
        path = os.path.join(ROOT, "examples", "confirm-word.json")
        for args in (("add", path, "--dry-run"), ("list",), ("answers",), ("say", "hello")):
            assert cli(d, *args)["dir"] == d, args
        code, refused = run_ask("--dir", d, "verify")
        assert code == 2 and refused["ok"] is False and refused["code"] == "no_secret"
        core.Store(d).ensure_secret()                                       # a console has started here
        assert cli(d, "verify")["ok"] is True
    code, schema = run_ask("schema")
    assert code == 0 and schema["dir"] is None                              # no folder declared: null, not a guess
    assert "every ok document names it as `dir`" in doc(AGENT) and "(`ask.py verify` is then refused, `no_secret`)" in doc(AGENT)
    assert "every ok reply also names the `dir` it used" in doc(CONSOLE) and "(refused, `no_secret`, where there is no secret" in doc(CONSOLE)


def test_a_console_secret_that_is_too_short_is_refused_as_the_docs_say():
    short = {"CONSOLE_SECRET": "too-short"}
    with _t.tmpdir() as d:
        assert "at least 16 characters" in serve_refuses("--dir", d, "--port", "0", env=short)
        assert not os.path.exists(os.path.join(d, "secret"))                # and no readable file was made in its place
        code, refused = run_ask("--dir", d, "answers", env=short)
        assert code == 2 and refused["code"] == "bad_request" and "16 characters" in refused["message"]
    assert "(16 or more characters; a shorter one is refused, and `serve.py` exits 2)" in doc(CONSOLE)


def test_a_source_is_asked_for_but_not_checked_and_no_doc_claims_otherwise():
    bare = {**example("confirm-word.json"), "evidence": [{"label": "l", "value": "v"}, {"quote": "q"},
                                                         {"table": {"columns": ["c"], "rows": [["r"]]}}]}
    assert core.validate_ask(bare)[1] == []
    for rel in (CONSOLE, AGENT, QUEUE):
        assert "with its source" not in doc(rel), rel
    assert "(not checked; a table names it in its caption)" in doc(CONSOLE)
    assert "(`add` does not check it; a table names it in its caption)" in doc(AGENT)
    assert "the source of each (not checked)" in doc(QUEUE)


def test_an_answer_the_gate_wrote_cannot_be_reopened_as_the_docs_say():
    with _t.tmpstore() as store:
        gated, plain = example("provide-number.json"), example("confirm-word.json")
        store.post("main", [gated, plain])
        shown = {i: store.state()["asks"][i]["hash"] for i in (gated["id"], plain["id"])}
        store.answer("alice", gated["id"], "4.20", shown=shown[gated["id"]],
                     gate={"ok": True, "verb": gated["gate"]["verb"], "message": "done"})
        store.answer("alice", plain["id"], "yes", shown=shown[plain["id"]])
        seq = lambda i: store.state()["asks"][i]["answer"]["seq"]           # noqa: E731
        with _t.assert_raises("already_applied"):
            store.reopen("alice", gated["id"], seq(gated["id"]))
        store.reopen("alice", plain["id"], seq(plain["id"]))                 # an answer nothing was written for can be taken back
    assert "not once a harness's gate has written the answer (`already_applied`" in doc(CONSOLE)
    assert "the owner cannot reopen it" in doc(AGENT)


def test_the_decision_record_is_told_as_the_code_does_it():
    readme, agent = doc(CONSOLE).replace("**", ""), doc(AGENT)
    for said in ("`ask.py digest` renders the record a team lead forwards", "Markdown in `text`, or data with `--format json`"):
        assert said in readme, said
    assert "`ask.py digest` renders the decision record for the team lead" in agent
    assert "advise" not in ask_verbs() and "--deciders" not in readme + agent
    with _t.tmpdir() as d:
        cli(d, "add", os.path.join(ROOT, "examples", "confirm-word.json"))
        md, js = cli(d, "digest"), cli(d, "digest", "--format", "json")
        assert md["format"] == "md" and md["text"] and "advice" not in js["asks"][0]


def test_the_top_readme_does_not_promise_what_the_console_does_not_do():
    top = doc(TOP)
    typed = {k: v for k, v in example("provide-number.json").items() if k not in ("recommend", "gate")}
    assert core.validate_ask(typed)[1] == []                                 # a typed value may go without a recommendation
    choose = {k: v for k, v in example("choose-priority.json").items() if k != "recommend"}
    assert [e["field"] for e in core.validate_ask(choose)[1]] == ["recommend"]   # every other step may not
    assert "never typed" not in top and "the one thing typed is a value the harness validates" in top
    assert "(a typed value may go without one)" in top
    assert "Widening what may be written counts only when typed in chat, not clicked on a page." in top
    assert "answered in chat (rule 8), not clicked" in top
    assert "**No widening.**" in doc(CONSOLE) and "Never ask the owner to widen what may be written" in doc(AGENT)


# ------------------------------------------- what the docs decide, and the code --

def test_the_limits_the_readme_gives_are_the_real_ones():
    readme, lim = doc(CONSOLE), core.LIMITS

    def cells(field: str) -> list[str]:
        row = next(ln for ln in readme.splitlines() if ln.startswith(f"| {field} |"))
        return [c.strip() for c in row.strip("|").split("|")]
    assert cells("`id`")[2].startswith(f"{lim['id']},") and cells("`title`")[2] == str(lim["title"])
    assert cells("`why`")[2] == str(lim["why"]) and cells("`if_no`")[2] == str(lim["if_no"])
    assert cells("`evidence`")[2] == f"1 to {lim['evidence']} items"
    assert cells("`recommend`")[2] == f"{lim['because']} for `because`"
    by_step = cells("by `step`")
    assert f"`options` (2 to {lim['options']})" in by_step[1] and by_step[2] == f"option label {lim['option_label']}, `effect` {lim['effect']}"
    assert f"At most {core.MAX_OPEN} are open" in readme and "at most 64 KB" in readme and serve.MAX_BODY == 64 * 1024
    assert "4 at a time" in readme and runner.WORKERS == 4


def test_every_key_of_a_reply_that_the_agent_file_names_is_in_a_real_reply():
    with _t.tmpdir() as d:
        first = example("confirm-word.json")
        cli(d, "add", os.path.join(ROOT, "examples", "confirm-word.json"))
        store = core.Store(d)
        store.answer("alice", first["id"], "yes", shown=store.state()["asks"][first["id"]]["hash"])
        store.note("alice", "hello")
        reply = cli(d, "wait", "--since", "0", "--timeout", "0")
    keys = set(reply) | set(reply["answers"][0]) | set(reply["notes"][0])
    section = doc(AGENT).split("## What comes back")[1].split("\n## ")[0]
    named = set(re.findall(r"\w+", " ".join(re.findall(r"`([^`]+)`", section))))          # every word set in code
    told = {"value", "suggested", "revised", "verified", "gate", "comment", "reopened", "notes", "notes_truncated", "text"}
    assert told <= named and told <= keys, sorted((told - named) | (told - keys))
    assert "apply" in keys and "`apply`" in doc(AGENT)
    assert {"timed_out", "dir"} <= keys and "timed_out" in doc(AGENT) and "`dir`" in doc(AGENT)


def test_max_open_can_only_lower_as_the_docs_say():
    with _t.tmpstore() as store:
        got = store.post("main", [example("confirm-word.json")], max_open=50, dry_run=True)
        assert got["budget"] == core.MAX_OPEN == 10
        assert store.post("main", [example("confirm-word.json")], max_open=3, dry_run=True)["budget"] == 3
    assert "can only lower" in doc(CONSOLE) and re.search(r"--max-open N` can only lower", doc(AGENT))
    subs = next(a for a in ask._parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert "only lower" in next(a.help for a in subs["add"]._actions if "--max-open" in a.option_strings)


def test_ask_runs_with_no_folder_only_for_schema_and_says_who_it_is():
    code, schema = run_ask("schema")
    assert code == 0 and schema["ok"] is True
    code, refused = run_ask("list")
    assert code == 2 and refused["code"] == "no_dir"
    assert "needs no folder" in doc(AGENT) and "needs no folder" in doc(CONSOLE)
    assert ask._parser().parse_args(["schema"]).name == "main"
    assert "`--as NAME` logs you as `agent:NAME` (default `main`)" in doc(AGENT)


def test_the_network_is_refused_without_a_login_header_as_the_docs_say():
    with _t.tmpdir() as d:
        assert "--user-header" in dies(["--dir", d, "--host", "0.0.0.0"])
        assert "--user-header" in dies(["--dir", d, "--host", "192.0.2.7", "--user", "alice"])
        assert "exclude each other" in dies(["--dir", d, "--user", "alice", "--user-header", "X-User"])
    readme = doc(CONSOLE)
    assert re.search(r"not loopback\W+is refused without `--user-header`", readme)
    assert "`--user` and `--user-header` exclude each other" in readme
    assert "binds to loopback" in doc(TOP)


def test_no_login_is_said_plainly_where_the_docs_and_the_help_speak():
    assert "By default there is no login: anyone who can reach the port answers as `--user`" in doc(CONSOLE).replace("**", "")
    assert "No login of its own" in doc(TOP) and "no login of its own" in doc(CONSOLE)
    said = next(a.help for a in serve._parser()._actions if "--user" in a.option_strings)
    assert "no login" in said and "anyone who can reach the port" in said


def test_a_proxy_must_pass_the_host_through_as_the_docs_say():
    assert not serve.same_origin({"Origin": "https://console.example.com", "Host": "127.0.0.1:8770"})
    assert serve.same_origin({"Origin": "https://console.example.com", "Host": "console.example.com"})
    text = doc(CONSOLE)
    assert "passes the public `Host` through unchanged" in text and "`Origin` is compared with the `Host`" in text
    assert "every POST refused (403)" in text
    assert serve.same_origin({"Origin": "null", "Sec-Fetch-Site": "same-origin", "Host": "console.example.com"})
    assert not serve.same_origin({"Origin": "null", "Host": "console.example.com"})      # `null` alone says nothing about where
    assert "counts only with `Sec-Fetch-Site: same-origin`" in text and "the owner opens the proxy's" in text
    assert "so it stays under the prefix" in text and "`Path`" in text


def test_a_harness_that_stalls_may_have_written_and_the_docs_and_words_say_so():
    assert "may have written" in relay.LATE and "ask the agent" in core.CODES["gate_timeout"]
    assert "may or may not have been saved" in i18n.t("err.gate_timeout", "en")
    assert "may or may not have been saved" in doc(AGENT)
    assert "**the harness may have written**" in doc(CONSOLE) and "the ask stays open" in doc(CONSOLE)
    assert int(serve.RELAY_TIMEOUT) == 60 and "Each call gets 60 seconds" in doc(CONSOLE)


def test_the_relay_protocol_in_the_readme_is_the_one_relay_speaks():
    readme = doc(CONSOLE)
    assert relay.CHALLENGE in readme and f"{relay.MAX_MESSAGE} characters" in readme
    gated_ask, _ = core.validate_ask(example("provide-number.json"))
    with _t.tmpdir() as d:                                                  # one real exchange, as the harness sees it
        log = os.path.join(d, "calls")
        env = {**os.environ, "FAKE_HARNESS_LOG": log, "FAKE_HARNESS_STATE": os.path.join(d, "state")}
        with stdin_is_a_pipe():
            got = relay.run(gated_ask, "4.20", cmd=[sys.executable, os.path.join(HERE, "fake_harness.py")],
                            verbs=[["facts", "confirm"]], user="alice", reason="why", timeout=20, env=env)
        assert got["ok"], got
        with open(log, encoding="utf-8") as f:
            calls = [json.loads(line) for line in f]
    first, second = calls[0]["argv"], calls[1]["argv"]
    assert first == ["facts", "confirm", "unit_cost", "--value=4.20", "--reason=why", "--json"]
    assert second[:len(first)] == first and len(second) == len(first) + 3
    code, who, at = second[len(first):]
    assert code == "--code=***" and who == "--relay-user=web:alice"           # each one argument, in the = form
    assert re.fullmatch(r"--relay-at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", at), at
    assert all(c["stdin_devnull"] and c["session_leader"] and not c["tty"] for c in calls)
    for flag in ("--value=", "--reason=", "--code=", "--relay-user=web:", "--relay-at="):
        assert flag in readme, flag
    assert not re.search(r"--(?:code|relay-user|relay-at|value) [A-Za-z<\[]", readme)      # never the two-argument form
    assert "in a session of its own with no terminal" in readme and "/dev/tty" in readme
    assert "is one `--name=value` argument" in readme


HARNESS = """import json, os, sys
if not any(a.startswith("--code=") for a in sys.argv):
    print(json.dumps({"code": "confirm_code_required", "params": {"confirm_code": "c-1"},
                      "subject": {"items": {"unit_cost": 4.2}}}))
    sys.exit(2)
if os.environ["ENDS"] == "says":
    print(json.dumps({"error": "outside the policy"}))
sys.exit(1)
"""


def test_a_second_call_that_ends_without_words_is_unsure_and_one_with_words_is_a_refusal():
    gated, _ = core.validate_ask(example("provide-number.json"))
    with _t.tmpdir() as d:
        script = os.path.join(d, "harness.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(HARNESS)
        got = {}
        for ends in ("crash", "says"):
            got[ends] = relay.run(gated, "4.20", cmd=[sys.executable, script], verbs=[["facts", "confirm"]], user="alice",
                                  reason="why", timeout=20, env=child_env({"ENDS": ends}))
    assert (got["crash"]["ok"], got["crash"]["code"]) == (False, "gate_unsure")
    assert (got["says"]["ok"], got["says"]["code"]) == (False, "gate_refused") and "outside the policy" in got["says"]["message"]
    assert "may or may not have been saved" in i18n.t("err.gate_unsure", "en") and "may or may not" in core.CODES["gate_unsure"]
    readme = doc(CONSOLE)
    assert "a non-zero exit with `error` or `message` in its document a refusal" in readme
    assert "(`gate_timeout`), or a step-4 call that ends with no document carrying `error` or `message` (a crash, garbage, silence: `gate_unsure`)" in readme
    assert "or in a way the console could not read" in doc(AGENT)


def test_the_code_is_sent_only_for_a_subject_that_matches_what_expect_names_exactly():
    gated, _ = core.validate_ask(example("provide-number.json"))
    with _t.tmpdir() as d:
        for mode, ok, code, calls in (("ok", True, None, 2), ("extra", False, "gate_changed", 1)):
            log = os.path.join(d, mode)
            env = child_env({"FAKE_HARNESS_MODE": mode, "FAKE_HARNESS_LOG": log, "FAKE_HARNESS_STATE": log + "-state"})
            got = relay.run(gated, "4.20", cmd=[sys.executable, os.path.join(HERE, "fake_harness.py")],
                            verbs=[["facts", "confirm"]], user="alice", reason="why", timeout=20, env=env)
            assert (got["ok"], got["code"]) == (ok, code), (mode, got)
            with open(log, encoding="utf-8") as f:
                assert len(f.readlines()) == calls, mode                   # after a mismatch the call with the code never happens
    assert core.matches({"items": {"unit_cost": 4.2}}, {"verb": "facts confirm", "items": {"unit_cost": 4.2}})
    assert not core.matches({"items": {"unit_cost": 4.2}}, {"items": {"unit_cost": 4.2, "more": 1}})
    _, errs = core.validate_ask({**gated, "gate": {**gated["gate"], "expect": {"items": {}}}})
    assert [e["field"] for e in errs] == ["gate.expect"]
    readme = doc(CONSOLE)
    for said in ("matches what `expect` names exactly", "so an extra item is refused",
                 "Only the top level of `subject` may say more", "`expect` holds no empty `{}` or `[]`"):
        assert said in readme, said


def test_the_relay_flags_go_together_as_the_docs_say():
    with _t.tmpdir() as d:
        assert "go together" in dies(["--dir", d, "--relay-cmd", "x"])
        assert "go together" in dies(["--dir", d, "--relay-verbs", "facts confirm"])
    assert serve._parser().parse_args([]).default_reason == "console"
    readme = doc(CONSOLE)
    assert "Both flags, or neither" in readme and "(default `console`)" in readme


def make(id: str, step: str = "confirm", verb: list | None = None, **extra) -> dict:
    """A valid ask of the example shop's shape, with a gate when it has a verb."""
    k = {"id": id, "step": step, "group": "G", "kind": "k", "title": id, "why": "w", "if_no": "n",
         "evidence": [{"label": "l", "value": "v"}], "recommend": {"value": "yes", "because": "b"}, **extra}
    if step == "approve":
        k["effect"] = "e"
    if verb:
        k["gate"] = {"verb": verb, "expect": {"items": {"x": "yes"}}}
    made, errors = core.validate_ask(k)
    assert not errors, errors
    return made


def inbox(asks, relay_verbs):
    events = [{"seq": n, "at": "2026-01-01T00:00:00Z", "by": "agent:main", "type": "ask", "ask": a}
              for n, a in enumerate(asks, 1)]
    ctx = {"lang": "en", "title": "T", "user": "alice", "token": "t", "relay": relay_verbs,
           "now": "2026-01-01T00:05:00Z", "secret": None}
    return pages.render_inbox(core.fold(events), ctx)


def test_the_group_button_and_the_gate_form_follow_the_docs():
    text = doc(CONSOLE).replace("**", "")
    plain = [make("a"), make("b")]
    assert 'action="answer_all"' in inbox(plain, None)
    assert 'action="answer_all"' not in inbox([make("a", "approve"), make("b", "approve")], None)
    allowed = [["facts", "confirm"]]
    one_yes = [make("a", verb=["facts", "confirm", "x"]), make("b", verb=["facts", "confirm", "y"])]
    assert 'action="answer_all"' in inbox(one_yes, allowed) and "confirmed another way" not in inbox(one_yes, allowed)
    one_no = [make("a", verb=["facts", "confirm", "x"]), make("b", verb=["queue", "approve", "y"])]
    page = inbox(one_no, allowed)
    assert 'action="answer_all"' not in page and page.count("confirmed another way") == 1
    assert page.count('action="answer"') == 2 and page.count('value="yes"') == 1     # the other can only be declined here
    assert "confirmed another way" in inbox(one_yes, None)
    assert "Answer all as suggested" in text and "never drawn for a group holding an `approve` ask" in text
    assert "it tells the owner to confirm another way, before a click can fail" in text
    assert "another way" in i18n.t("gate.norelay", "en") and "another way" in doc(AGENT)


def test_choose_and_provide_gates_must_carry_the_answer_as_the_docs_say():
    options = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
    for step, extra in (("choose", {"options": options, "recommend": {"value": "a", "because": "b"}}),
                        ("provide", {"recommend": {"value": "a", "because": "b"}})):
        base = make("a", step, **extra)
        expect = {"items": {"x": "$value"}}
        _, errs = core.validate_ask({**base, "gate": {"verb": ["facts", "confirm"], "expect": expect}})
        assert [e["field"] for e in errs] == ["gate.value_arg"], errs
        _, errs = core.validate_ask({**base, "gate": {"verb": ["facts", "confirm"], "value_arg": True, "expect": expect}})
        assert not errs, errs
    assert 'must set it and put `"$value"` in `expect`' in doc(CONSOLE)


def test_posix_only_is_said_and_is_true():
    assert "fcntl" in source("core.py") and "start_new_session" in source("relay.py") and "killpg" in source("relay.py")
    assert "POSIX only" in doc(CONSOLE) and "macOS or Linux" in doc(CONSOLE) and "macOS or Linux only" in doc(TOP)


def test_every_test_file_the_safety_table_names_exists():
    named = set(re.findall(r"`(test_\w+\.py)`", doc(CONSOLE)))
    assert {"test_boundary.py", "test_core.py", "test_serve.py", "test_relay.py", "test_pages.py", "test_ask_cli.py",
            "test_docs.py"} <= named
    for name in named:
        assert os.path.exists(os.path.join(HERE, name)), name
    assert re.findall(r"`(test_\w+\.py)`", "see `test_gone.py`") == ["test_gone.py"]      # the check would notice


def test_what_the_tests_cannot_see_is_said_with_a_six_line_check_that_matches_the_script():
    part = doc(CONSOLE).split("**Not covered by the tests: a real browser.**")[1].split("\n## ")[0]
    assert re.findall(r"^(\d)\. ", part, re.M) == list("123456")
    with open(os.path.join(ROOT, "static", "console.js"), encoding="utf-8") as f:
        js = f.read()
    every = int(re.search(r"setInterval\(\w+,\s*(\d+)\)", js).group(1)) // 1000
    misses = int(re.search(r"failed\s*>=\s*(\d+)", js).group(1))
    assert f"within {every} s" in part and f"for {every * misses} s" in part      # 4 s to notice a change, three missed polls to complain
    for key in ("banner.new", "banner.lost", "all.busy"):                          # the words the check tells a person to look for
        assert i18n.has(key), key
    assert "none runs `console.js`" in part


# -------------------------------------------------------- nothing private --

PRIVATE = (("a machine path", re.compile(r"/Us" r"ers/|/home/[a-z]|/private/" r"tmp|/tmp/" r"claude|/var/" r"folders/|[A-Z]:\\Us" r"ers")),
           ("a key or token", re.compile(r"sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-|AKIA[0-9A-Z]{12,}"
                                         r"|Bearer [A-Za-z0-9._-]{20,}|-----BEGIN [A-Z ]*KEY")))
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.|evil\.example)[A-Za-z0-9.-]+\.[a-z]{2,}")
LINKED = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*)")
LITTER = re.compile(r"(^|/)(__pycache__/|\.DS_Store$)|\.py[co]$")           # build leftovers: never published, whatever they hold


def leaks(rel: str, text: str, binary: bool = False) -> list[str]:
    """What in `text` must not be public. Tests may hold fake addresses; nothing else may.
    A binary file (bytecode, an image) is read as Latin-1: only machine paths and keys are looked for in it."""
    out = [f"{what} in {rel}" for what, rx in PRIVATE if rx.search(text)]
    if not binary and not rel.startswith("console/tests/"):
        out += [f"an address in {rel}: {m}" for m in ADDRESS.findall(text)]
        out += [f"a host in {rel}: {h}" for h in LINKED.findall(text)
                if h not in ("localhost", "127.0.0.1") and h != "example.com" and not h.endswith(".example.com")]
    return out


def published(root: str) -> list[str]:
    """Every file a commit in `root` could publish: what git tracks plus what it does not ignore (an ignored `.pyc`
    is not one). With no git to ask, every file there is: a rule that cannot be checked hides nothing."""
    try:
        p = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                           capture_output=True, timeout=30)
        if p.returncode == 0:
            return sorted(n for n in p.stdout.decode("utf-8", "surrogateescape").split("\0") if n)
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for top, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        out += [os.path.relpath(os.path.join(top, n), root).replace(os.sep, "/") for n in names]
    return sorted(out)


def test_a_file_that_git_ignores_is_not_a_file_a_commit_would_publish():
    with _t.tmpdir() as d:
        def git(*a):
            subprocess.run(["git", "-C", d, *a], capture_output=True, check=True, timeout=30)
        git("init", "-q")
        for rel in ("a/x.py", "a/__pycache__/x.cpython-312.pyc", "y.pyc"):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "wb") as f:
                f.write(b"x")
        assert LITTER.search("a/__pycache__/x.cpython-312.pyc") and LITTER.search("y.pyc") and not LITTER.search("a/x.py")
        assert [n for n in published(d) if LITTER.search(n)] == ["a/__pycache__/x.cpython-312.pyc", "y.pyc"]   # nothing hides them
        with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("__pycache__/\n*.pyc\n")
        assert published(d) == [".gitignore", "a/x.py"]


def test_the_repository_ignores_what_only_a_console_or_an_interpreter_leaves():
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        rules = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    assert {"__pycache__/", "*.pyc", ".DS_Store", "events.jsonl", "secret"} <= rules      # the log and its signing secret never go in


def test_nothing_that_must_not_be_public_is_in_the_repository():
    assert leaks("a.md", "at /Us" + "ers/x/y") and leaks("a.md", "key sk-" + "a" * 20) and leaks("a.md", "mail me@nowhere.invalid")
    assert leaks("a.md", "see https://nowhere.invalid/x") and not leaks("a.md", "https://console.example.com and http://127.0.0.1:1/")
    assert not leaks("console/tests/t.py", "a@b.co")
    assert leaks("x.pyc", "\x00\x00/private/" + "tmp/run/core.py\x00", binary=True)          # bytecode holds the path it was built from
    assert not leaks("x.png", "\x89PNG a@b.co https://nowhere.invalid", binary=True)
    files = published(REPO)
    assert len(files) > 30
    assert not [n for n in files if LITTER.search(n)], [n for n in files if LITTER.search(n)]
    for rel in files:
        if os.path.basename(rel) == os.path.basename(__file__):
            continue
        try:
            with open(os.path.join(REPO, rel), "rb") as f:
                raw = f.read()
        except OSError:
            continue                                                        # tracked, but gone from the tree
        try:
            found = leaks(rel, raw.decode("utf-8"))
        except UnicodeDecodeError:
            found = leaks(rel, raw.decode("latin-1"), binary=True)
        assert not found, found


if __name__ == "__main__":
    _t.main(globals())
