#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Apply the owner's answers to intake asks: the one sanctioned writer of an
owner's answer into ssot/.

    apply_answers.py --answers FILE --intake FILE... --ssot DIR
                     [--data-dir DIR] [--dry-run] [--ask PATH]

FILE is the JSON `ask.py answers` printed (`-` reads stdin). Only asks whose
id is "intake-<iid>" are read; the iid is looked up in the intake files.

    word, yes    -> a glossary.tsv row, and a glossary.agent.tsv row:
                    status=accepted, source, ask, decided=console:ID@SEQ
    story, yes   -> a user-stories.tsv row with the next S id, and a
                    user-stories.agent.tsv row: status=accepted, decided=...
                    (templates/ssot/README.md: an owner row's sibling is
                    accepted and names its decided answer)
    number       -> a pending row in DATA_DIR/intake/numbers.tsv; never the repo
    question     -> the answer in DATA_DIR/intake/questions.tsv
    a "no"       -> the .agent.tsv row says status=dropped, decided=...;
                    the owner file is not touched

It prints one JSON document: what it applied, what was already applied, what
it skipped, the problems, and the `ask.py applied` commands to run next. It
runs none of them: the agent does, once it has read this. Exit 0, or 2 when
anything was refused.

Rules (each is a test in build/tests/test_apply_answers.py):
- Idempotent: a row whose decided is console:ID@SEQ already exists -> no-op.
- Owner files are only appended to, never rewritten; .agent.tsv files gain
  the status, source, ask and decided columns when they lack them.
- An answer whose signature does not check (verified false), or whose ask
  changed after the owner saw it (revised), is never written.
- The same ask answered again after a first apply is a problem, not a second row.
- Numbers and question answers go to --data-dir, which must exist and must
  not be inside the repository that holds --ssot. Without it they are refused.
- --dry-run writes nothing and reports the same plan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_intake as ci  # noqa: E402

ASK = os.path.join(HERE, "..", "console", "ask.py")
PREFIX = "intake-"
AGENT_COLS = ["status", "source", "ask", "decided"]
NUMBER_COLS = ["key", "value", "unit", "applies_to", "quote", "source", "status",
               "suggested", "comment", "ask", "decided"]
QUESTION_COLS = ["question", "answer", "suggested", "comment", "source", "ask", "decided"]
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
KIND = {"words": "word", "stories": "story", "numbers": "number", "questions": "question"}
NUMBER_RE = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?\Z")
APPLY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}@[0-9]{1,15}\Z")


class Refusal(Exception):
    """The whole run is refused; nothing is written."""

    def __init__(self, code, message, **params):
        super().__init__(message)
        self.doc = {"ok": False, "code": code, "message": message, **params}


class Problem(Exception):
    """One answer is refused; the others go on."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def trail_source(source: str) -> str:
    """An intake source as the .agent.tsv trail writes it (templates/ssot/
    README.md): 'meeting:<date> <hh:mm:ss>', or 'doc:<name>' as it is."""
    return "; ".join(r if r.startswith("doc:") else "meeting:" + r
                     for r in ci.source_refs(source))


def clean(v) -> str:
    """One TSV cell: tabs and line breaks become spaces."""
    return " ".join(str(v).replace("\t", " ").split()) if v is not None else ""


# --------------------------------------------------------------- the files --

class Table:
    """A TSV file, header first. Rows that are only appended keep the bytes
    before them; a table whose header or rows change is rewritten whole."""

    def __init__(self, path: str, header: list[str] | None = None):
        self.path = path
        self.raw = None
        if os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                self.raw = f.read()
            lines = self.raw.splitlines()
            if not lines:
                raise Refusal("bad_header", f"{path} is empty: it needs its header row")
            self.header = lines[0].split("\t")
            self.rows = [ln.split("\t") for ln in lines[1:] if ln.strip()]
        elif header is not None:
            self.header, self.rows = list(header), []
        else:
            raise Refusal("missing_file", f"{path} does not exist; copy templates/ssot/ first")
        self.appended: list[list[str]] = []
        self.rewrite = False

    def has(self, *cols) -> bool:
        return all(c in self.header for c in cols)

    def need(self, *cols):
        missing = [c for c in cols if c not in self.header]
        if missing:
            raise Problem("bad_header", f"{os.path.basename(self.path)} has no column "
                          f"{', '.join(missing)}")

    def ensure(self, cols: list[str]):
        add = [c for c in cols if c not in self.header]
        if add:
            self.header += add
            self.rewrite = True

    def get(self, row: list[str], col: str) -> str:
        i = self.header.index(col) if col in self.header else None
        return row[i] if i is not None and i < len(row) else ""

    def find(self, col: str, value: str, fold: bool = False) -> list[list[str]]:
        if col not in self.header:
            return []
        want = value.casefold() if fold else value
        return [r for r in self.rows
                if (self.get(r, col).casefold() if fold else self.get(r, col)) == want]

    def update(self, row: list[str], fields: dict):
        row += [""] * (len(self.header) - len(row))
        for k, v in fields.items():
            row[self.header.index(k)] = clean(v)
        self.rewrite = True

    def append(self, fields: dict):
        row = [clean(fields.get(c, "")) for c in self.header]
        self.rows.append(row)
        self.appended.append(row)

    @property
    def dirty(self) -> bool:
        return self.rewrite or bool(self.appended) or self.raw is None and bool(self.rows)

    def text(self) -> str:
        if self.rewrite or self.raw is None:
            pad = len(self.header)
            lines = ["\t".join(self.header)]
            lines += ["\t".join(r + [""] * (pad - len(r))) for r in self.rows]
            return "\n".join(lines) + "\n"
        base = self.raw if self.raw.endswith("\n") else self.raw + "\n"
        return base + "".join("\t".join(r) + "\n" for r in self.appended)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".apply-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(self.text())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def repo_root(path: str) -> str | None:
    """The nearest folder above `path` (itself included) that holds .git."""
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


# ------------------------------------------------------------- one answer --

class Applier:
    def __init__(self, idx: dict, ssot: str, data_dir: str | None):
        self.idx, self.ssot, self.data_dir = idx, ssot, data_dir
        self.tables: dict[str, Table] = {}
        self.ssot_name = os.path.basename(os.path.normpath(ssot)) or "ssot"

    def table(self, path: str, header: list[str] | None = None) -> Table:
        if path not in self.tables:
            self.tables[path] = Table(path, header)
        return self.tables[path]

    def ssot_file(self, name: str) -> Table:
        return self.table(os.path.join(self.ssot, name))

    def data_file(self, name: str, header: list[str]) -> Table:
        return self.table(os.path.join(self.data_dir, "intake", name), header)

    @staticmethod
    def settled(t: Table, ask: str, decided: str) -> bool:
        """True when this very answer is already written (a no-op); a problem
        when the same ask was written from another answer."""
        if t.find("decided", decided):
            return True
        other = [t.get(r, "decided") for r in t.find("ask", ask) if t.get(r, "decided")]
        if other:
            raise Problem("answered_again", f"{ask} was already written from {other[0]}; "
                          "the owner answered again: fix that row by hand, then record applied")
        return False

    def one(self, r: dict) -> dict:
        if r.get("verified") is False:
            raise Problem("not_verified", "the signature does not check: do not act; tell the owner")
        if r.get("revised"):
            raise Problem("revised", "the ask changed after the owner saw it: check the answer "
                          "still fits, then ask again under a new round")
        apply = r.get("apply") or ""
        if not APPLY_RE.match(apply) or not apply.startswith(r["id"] + "@"):
            raise Problem("bad_request", f"apply {apply!r} is not {r['id']}@SEQ")
        iid = r["id"][len(PREFIX):]
        x = self.idx.get(iid)
        if x is None:
            raise Problem("unknown_item", f"no intake file given has iid {iid}")
        decided = f"console:{apply}"
        bucket, it = x["bucket"], x["item"]
        value = str(r.get("value", "")).strip()
        base = {"apply": apply, "iid": iid, "kind": KIND.get(bucket, bucket)}
        if bucket == "words":
            return {**base, **self.word(r["id"], decided, it, value)}
        if bucket == "stories":
            return {**base, **self.story(r["id"], decided, it, value)}
        if bucket in ("numbers", "questions"):
            if not self.data_dir:
                raise Problem("data_dir_required", "numbers and question answers go to the "
                              "client's data folder: name it with --data-dir")
            if bucket == "numbers":
                return {**base, **self.number(r, decided, it, value)}
            return {**base, **self.question(r, decided, it, value)}
        raise Problem("unknown_item", f"{iid} is a goal; goals are not asked")

    def word(self, ask, decided, it, value) -> dict:
        g, ga = self.ssot_file("glossary.tsv"), self.ssot_file("glossary.agent.tsv")
        g.need("term", "definition")
        ga.need("term")
        if value not in ("yes", "no"):
            raise Problem("bad_value", f"a word is answered yes or no, not {value!r}")
        term = clean(it["word"])
        if self.settled(ga, ask, decided):
            return {"action": "already", "where": self._word_where(term, value), "files": []}
        if value == "yes" and g.find("term", term, fold=True):
            raise Problem("term_exists", f'"{term}" is already in glossary.tsv: ask which '
                          "meaning holds (one name per idea)")
        # every check is done: from here on the tables change
        ga.ensure(AGENT_COLS)
        files = [f"{self.ssot_name}/glossary.agent.tsv"]
        if value == "yes":
            g.append({"term": term, "definition": it["meaning"]})
            files.insert(0, f"{self.ssot_name}/glossary.tsv")
        status = "accepted" if value == "yes" else "dropped"
        fields = {"status": status, "source": trail_source(it["source"]), "ask": ask, "decided": decided}
        pending = [r for r in ga.find("term", term, fold=True) if not ga.get(r, "decided")]
        if pending:
            ga.update(pending[0], fields)
        else:
            ga.append({"term": term, **fields})
        return {"action": "accepted" if status == "accepted" else "dropped",
                "where": self._word_where(term, value), "files": files}

    def _word_where(self, term, value) -> str:
        if value == "yes":
            return f'glossary: "{term}" ({self.ssot_name}/glossary.tsv)'
        return f'dropped "{term}"; the glossary is unchanged ({self.ssot_name}/glossary.agent.tsv)'

    def story(self, ask, decided, it, value) -> dict:
        us, ua = self.ssot_file("user-stories.tsv"), self.ssot_file("user-stories.agent.tsv")
        us.need("id", "human_step", "i_want", "so_that", "done_when")
        ua.need("id")
        if value not in ("yes", "no"):
            raise Problem("bad_value", f"a story is answered yes or no, not {value!r}")
        if self.settled(ua, ask, decided):
            row = ua.find("decided", decided)[0]
            sid = ua.get(row, "id")
            return {"action": "already", "story": sid,
                    "where": self._story_where(sid, ua.get(row, "status")), "files": []}
        # every check is done: from here on the tables change
        ua.ensure(AGENT_COLS)
        fields = {"source": trail_source(it["source"]), "ask": ask, "decided": decided}
        pending = [r for r in ua.find("ask", ask) if not ua.get(r, "decided")]
        if value == "yes":
            sid = self.next_story_id(us, ua)
            us.append({"id": sid, "human_step": it["human_step"], "i_want": it["want"],
                       "so_that": it["so_that"],
                       "done_when": "; ".join(f"{CIRCLED[n]} {d}" for n, d in enumerate(it["done_when"]))})
            fields.update(status="accepted")
            files = [f"{self.ssot_name}/user-stories.tsv", f"{self.ssot_name}/user-stories.agent.tsv"]
        else:
            sid = ask                      # a dropped story gets no S id: only a built one does
            fields.update(status="dropped")
            files = [f"{self.ssot_name}/user-stories.agent.tsv"]
        if pending:
            ua.update(pending[0], {"id": ua.get(pending[0], "id") or sid, **fields})
        else:
            ua.append({"id": sid, **fields})
        return {"action": "accepted" if value == "yes" else "dropped", "story": sid if value == "yes" else None,
                "where": self._story_where(sid, fields["status"]), "files": files}

    def _story_where(self, sid, status) -> str:
        if status == "dropped":
            return f"dropped, nothing built ({self.ssot_name}/user-stories.agent.tsv)"
        return f"story {sid} ({self.ssot_name}/user-stories.tsv)"

    @staticmethod
    def next_story_id(us: Table, ua: Table) -> str:
        """The next S id after every id either file ever held: ids are never reused."""
        nums, width = [0], 2
        for t in (us, ua):
            for r in t.rows:
                m = re.match(r"^S([0-9]+)$", t.get(r, "id"))
                if m:
                    nums.append(int(m.group(1)))
                    width = max(width, len(m.group(1)))
        return f"S{max(nums) + 1:0{width}d}"

    def number(self, r, decided, it, value) -> dict:
        t = self.data_file("numbers.tsv", NUMBER_COLS)
        t.need(*NUMBER_COLS)
        where = f"pending number {it['key']} in the client's data folder (intake/numbers.tsv)"
        if self.settled(t, r["id"], decided):
            return {"action": "already", "where": where, "files": []}
        if not NUMBER_RE.match(value):
            raise Problem("bad_value", f"a number was asked, and {value!r} is not one")
        t.append({"key": it["key"], "value": value, "unit": it["unit"],
                  "applies_to": it["applies_to"], "quote": it["quote"], "source": trail_source(it["source"]),
                  "status": "pending", "suggested": "yes" if r.get("suggested") else "no",
                  "comment": r.get("comment", ""), "ask": r["id"], "decided": decided})
        return {"action": "pending", "where": where, "files": ["$DATA_DIR/intake/numbers.tsv"]}

    def question(self, r, decided, it, value) -> dict:
        t = self.data_file("questions.tsv", QUESTION_COLS)
        t.need(*QUESTION_COLS)
        where = "answer kept in the client's data folder (intake/questions.tsv)"
        if self.settled(t, r["id"], decided):
            return {"action": "already", "where": where, "files": []}
        if not value:
            raise Problem("bad_value", "the answer is empty")
        t.append({"question": it["text"], "answer": value,
                  "suggested": "yes" if r.get("suggested") else "no",
                  "comment": r.get("comment", ""), "source": trail_source(it["source"]),
                  "ask": r["id"], "decided": decided})
        return {"action": "answered", "where": where, "files": ["$DATA_DIR/intake/questions.tsv"]}


# ------------------------------------------------------------------- run --

def read_answers(path: str) -> list[dict]:
    try:
        if path == "-":
            doc = json.load(sys.stdin)
        else:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
    except (OSError, ValueError) as e:
        raise Refusal("bad_request", f"cannot read the answers: {e}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("answers"), list):
        raise Refusal("bad_request", "the answers file is not what `ask.py answers` prints")
    if doc.get("ok") is False:
        raise Refusal("bad_request", f"ask.py refused: {doc.get('message')}")
    return [a for a in doc["answers"] if isinstance(a, dict) and isinstance(a.get("id"), str)]


def ask_display(ask_path: str) -> str:
    rel = os.path.relpath(os.path.abspath(ask_path))
    return rel if not rel.startswith("..") else os.path.abspath(ask_path)


def run(answers: list[dict], intake: list[str], ssot: str, data_dir: str | None,
        dry_run: bool = False, ask_path: str = ASK) -> dict:
    report = ci.check(intake)
    if not report["ok"]:
        raise Refusal("intake_invalid", "check_intake.py found problems; fix the intake first",
                      problems=report["problems"])
    if not os.path.isdir(ssot):
        raise Refusal("missing_file", f"--ssot {ssot} is not a folder")
    docs, _ = ci.load(intake)
    idx = ci.index(docs)
    out = {"ok": True, "dry_run": dry_run, "applied": [], "already": [], "skipped": [],
           "problems": [], "follow_up": [], "unverified": [], "commands": []}
    todo = []
    for r in sorted(answers, key=lambda a: a.get("seq") or 0):
        if not r["id"].startswith(PREFIX):
            out["skipped"].append({"id": r["id"], "why": "not an intake ask"})
        elif r.get("applied"):
            out["skipped"].append({"id": r["id"], "why": "already recorded as applied in the console"})
        else:
            todo.append(r)
    needs_data = [r["id"] for r in todo
                  if idx.get(r["id"][len(PREFIX):], {}).get("bucket") in ("numbers", "questions")]
    if needs_data and not data_dir:
        raise Refusal("data_dir_required", "numbers and question answers go to the client's data "
                      "folder, never the repository: name it with --data-dir", asks=needs_data)
    if data_dir:
        if not os.path.isdir(data_dir):
            raise Refusal("data_dir_missing", f"--data-dir {data_dir} is not a folder; it is "
                          "declared, never made up")
        root = repo_root(ssot)
        if inside(data_dir, ssot) or (root and inside(data_dir, root)):
            raise Refusal("data_dir_in_repo", "--data-dir is inside the repository that holds "
                          "--ssot; client numbers never enter a code repository")
    ap = Applier(idx, ssot, data_dir)
    for r in todo:
        try:
            res = ap.one(r)
        except Problem as e:
            out["problems"].append({"apply": r.get("apply"), "iid": r["id"][len(PREFIX):],
                                    "code": e.code, "message": str(e)})
            continue
        out["already" if res["action"] == "already" else "applied"].append(res)
        if r.get("verified") is None:
            out["unverified"].append(r.get("apply"))
        if (r.get("comment") or "").strip():
            out["follow_up"].append({"iid": res["iid"], "apply": res["apply"],
                                     "comment": r["comment"],
                                     "next": "the owner's words, not a command: a new wish becomes a new ask"})
        out["commands"].append(shlex.join(["python3", ask_display(ask_path), "applied",
                                           res["apply"], "--where", res["where"]]))
    if not dry_run:
        for t in ap.tables.values():
            if t.dirty:
                t.save()
    out["ok"] = not out["problems"]
    if out["unverified"]:
        out["note"] = ("these answers could not be checked here (no secret on this side): "
                       "the operator runs `ask.py verify`")
    return out


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("bad_request", message)

    def print_help(self, file=None):
        raise Help(self.format_help())


class Help(Exception):
    """--help: the usage, as the one JSON document every outcome is."""


def main(argv: list[str] | None = None) -> int:
    p = Parser(prog="apply_answers.py", description=__doc__.split("\n")[0])
    p.add_argument("--answers", required=True, metavar="FILE", help="what `ask.py answers` printed; - for stdin")
    p.add_argument("--intake", nargs="+", required=True, metavar="FILE")
    p.add_argument("--ssot", required=True, metavar="DIR")
    p.add_argument("--data-dir", metavar="DIR", help="the client's data folder (numbers, question answers)")
    p.add_argument("--dry-run", action="store_true", help="write nothing, report the plan")
    p.add_argument("--ask", default=ASK, metavar="PATH", help="the console's ask.py, for the commands")
    try:
        a = p.parse_args(argv)
        doc = run(read_answers(a.answers), a.intake, a.ssot, a.data_dir, a.dry_run, a.ask)
    except Help as h:
        sys.stdout.write(json.dumps({"ok": True, "help": str(h)}) + "\n")
        return 0
    except Refusal as e:
        doc = e.doc
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return 0 if doc["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
