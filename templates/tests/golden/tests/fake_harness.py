"""The smallest harness the example cases.py fits, as a git repository.

build(root) writes it and commits it once, with this module's engine.py and
cases.py copied to tests/golden/ as a harness adopts them. It has a verb
table, a clock seam, a runner seam, a registry default, fixtures, a report
with a variant and a failure document, a verb whose child verb must be pinned
too, a dry run, and a console of two pages in two languages. Test-only
switches: FAKE_ALLOW_WRITES and FAKE_API_KEY show up in the documents when a
case can see them, and a `slow` file in the data folder makes the summary
wait a minute, after writing `started`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
TREES = ("alder", "birch", "cedar", "dogwood", "elm", "fir", "ginkgo", "hazel",
         "ivy", "juniper", "larch", "maple", "oak", "pine", "rowan", "spruce")
ROWS = [{"entity": e, "day": f"2031-01-0{d}", "clicks": (i * 7 + d * 3) % 19,
         "cost": round(0.4 * i + d, 2)} for i, e in enumerate(TREES) for d in (1, 2)]

FILES = {
    "tool.py": '''\
"""Dispatcher: `tool.py WORD... [-- ARG...]` runs the script VERBS names."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import runner  # noqa: E402

VERBS = {
    ("ingest",): "data.py",
    ("init",): "data.py",
    ("facts", "list"): "data.py",
    ("report", "summary"): "report.py",
    ("report", "entity"): "report.py",
    ("report", "rollup"): "report.py",
    ("execute",): "execute.py",
}


def main(argv):
    cut = argv.index("--") if "--" in argv else len(argv)
    verb, rest = tuple(argv[:cut]), argv[cut + 1:]
    if verb not in VERBS:
        print(json.dumps({"error": "unknown verb", "next": [], "code": "unknown_verb",
                          "params": {"verb": " ".join(verb)}}))
        return 2
    cmd = runner.script_cmd(os.path.join(HERE, VERBS[verb]), [*verb, *rest])
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
''',
    "lib/__init__.py": "",
    "lib/clock.py": '''\
"""The only clock reads."""
import datetime as dt


def now():
    return dt.datetime.now(dt.timezone.utc)


def today():
    return now().date()
''',
    "lib/runner.py": '''\
"""The only way one script starts another."""
import json
import subprocess
import sys


def script_cmd(script, args):
    return [sys.executable, str(script), *args]


def run_json(script, args):
    p = subprocess.run(script_cmd(script, args), capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)
''',
    "lib/store.py": '''\
"""The data folder (DATA_DIR, never the cwd), the registry, meta, failures."""
import json
import os
import sys
from pathlib import Path

from lib import clock

ROOT = Path(__file__).resolve().parents[1]


def fail(error, code, **params):
    print(json.dumps({"error": error, "next": ["tool.py report summary -- --json"],
                      "code": code, "params": params}))
    sys.exit(2)


def data_dir():
    if not os.environ.get("DATA_DIR"):
        fail("DATA_DIR is not set", "no_data_dir")
    return Path(os.environ["DATA_DIR"])


def load(name, default):
    p = data_dir() / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    (data_dir() / name).write_text(json.dumps(obj, indent=1))


def threshold(name):
    for line in (ROOT / "registry.tsv").read_text().splitlines()[1:]:
        key, value = line.split("\\t")
        if key == name:
            return float(value)
    raise KeyError(name)


def meta():
    return {"today": clock.today().isoformat(),
            "generated_at": clock.now().isoformat(timespec="seconds"),
            "data_dir": str(data_dir()),
            "writes_enabled": os.environ.get("FAKE_ALLOW_WRITES") == "1"}
''',
    "registry.tsv": "name\tvalue\nmin_clicks\t10\n",
    "data.py": '''\
"""ingest, init and facts list."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import clock, store  # noqa: E402


def main(argv):
    if argv[0] == "ingest":
        src = argv[argv.index("--from") + 1]
        with open(os.path.join(src, "rows.json")) as f:
            rows = json.load(f)
        store.save("store.json", {"rows": rows, "ingested_at": clock.now().isoformat()})
        print(json.dumps({"ingested": len(rows)}))
    elif argv[0] == "init":
        scope = sys.stdin.readline().strip()
        store.save("facts.json", {"scope": scope, "updated_at": clock.now().isoformat()})
        print(json.dumps({"scope": scope}))
    else:
        print(json.dumps({"meta": store.meta(), "facts": store.load("facts.json", {})}))


if __name__ == "__main__":
    main(sys.argv[1:])
''',
    "report.py": '''\
"""report summary [--by entity|day], report entity --id ID, report rollup."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import clock, runner, store  # noqa: E402


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def summary(argv):
    if (store.data_dir() / "slow").exists():
        (store.data_dir() / "started").write_text("1")
        time.sleep(60)
    rows = store.load("store.json", {"rows": []})["rows"]
    by = opt(argv, "--by", "entity")
    if by not in ("entity", "day"):
        store.fail("--by takes entity or day", "bad_by", by=by)
    floor = store.threshold("min_clicks")
    out = []
    for key in sorted({r[by] for r in rows}):  # totals per key
        mine = [r for r in rows if r[by] == key]
        clicks = sum(r["clicks"] for r in mine)
        out.append({by: key, "clicks": clicks, "judged": clicks >= floor,
                    "cost": round(sum(r["cost"] for r in mine), 2)})
    return {"meta": store.meta(), "rows": out}


def entity(argv):
    ident = opt(argv, "--id")
    if not ident:
        store.fail("--id is required", "id_required")
    rows = [r for r in store.load("store.json", {"rows": []})["rows"] if r["entity"] == ident]
    if not rows:
        store.fail(f"no entity {ident}", "unknown_entity", id=ident)
    return {"meta": store.meta(), "rows": rows}


def rollup(argv):
    rc, doc = runner.run_json(os.path.join(HERE, "report.py"), ["report", "summary", "--json"])
    return {"meta": store.meta(), "child_rc": rc, "child_today": doc["meta"]["today"],
            "clicks": sum(r["clicks"] for r in doc["rows"])}


if __name__ == "__main__":
    verb = {"summary": summary, "entity": entity, "rollup": rollup}[sys.argv[2]]
    print(json.dumps(verb(sys.argv[3:])))
''',
    "execute.py": '''\
"""execute --dry-run --json: what would be written. --apply writes."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import runner, store  # noqa: E402

if __name__ == "__main__":
    _, doc = runner.run_json(os.path.join(HERE, "report.py"), ["report", "summary", "--json"])
    plan = [{"entity": r["entity"], "action": "pause"} for r in doc["rows"] if not r["judged"]]
    if "--apply" in sys.argv:
        store.save("APPLIED", plan)
    print(json.dumps({"meta": store.meta(), "dry_run": "--apply" not in sys.argv,
                      "key_loaded": bool(os.environ.get("FAKE_API_KEY")),
                      "would_write": plan, "child_today": doc["meta"]["today"]}))
''',
    "console.py": '''\
"""Two pages in two languages, drawn from the --json of the read verbs."""
import html

from lib import clock

PAGES = ("home", "entity")
LANGS = ("en", "zh")
WORDS = {"en": {"home": "Entities", "entity": "One entity", "clicks": "clicks", "as_of": "as of"},
         "zh": {"home": "条目", "entity": "单个条目", "clicks": "点击", "as_of": "截至"}}


def render(page, lang, read, entity=None):
    w = WORDS[lang]
    if page == "home":
        doc = read(["report", "summary", "--", "--json"])
    else:
        doc = read(["report", "entity", "--", "--json", "--id", entity or ""])
    items = "".join(f"<li>{html.escape(r['entity'])}: {r['clicks']} {w['clicks']}</li>"
                    for r in doc.get("rows", []))
    return (f'<!doctype html><html lang="{lang}"><head><title>{w[page]}</title></head>'
            f"<body><h1>{w[page]}</h1><p>{w['as_of']} {doc['meta']['generated_at']}</p>"
            f"<p>{clock.today().isoformat()}</p>"
            f"<ul>{items}</ul></body></html>")
''',
    "fixtures/rows.json": json.dumps(ROWS, indent=1) + "\n",
    ".gitignore": "__pycache__/\n",
}

# A throwaway change in a comment only: the documents stay byte for byte.
COMMENT_ONLY = {"report.py": ("# totals per key", "# one row per key, in key order")}
# A changed registry default: the golden diff must show it.
NEW_DEFAULT = {"registry.tsv": ("min_clicks\t10", "min_clicks\t25")}
# A page that raises in one language.
PAGE_RAISES = {"console.py": ("    w = WORDS[lang]\n", "    w = WORDS[lang]\n    assert lang == \"en\"\n")}
# The bug found on real data: order taken from a set, so from the hash seed.
SET_ORDER = {"report.py": ("sorted({r[by] for r in rows})", "list({r[by] for r in rows})")}


def env(**extra: str) -> dict:
    """A clean env for git and the engine: no GIT_* from a caller's hook, no
    git config of the machine's, and a PYTHONHASHSEED the engine must not
    pass on."""
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")}
    return {**keep, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, **extra}


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=fake", "-c",
                        "user.email=fake@example.com", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=env(), timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def build(root: Path) -> Path:
    """The fake harness at root/repo, one commit on main; returns the repo."""
    repo = root / "repo"
    for rel, text in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    (repo / "tests" / "golden").mkdir(parents=True)
    for name in ("engine.py", "cases.py"):
        shutil.copy(MODULE / name, repo / "tests" / "golden" / name)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fake harness")
    return repo


def commit(repo: Path, edits: dict, message: str) -> str:
    """Apply {path: (old, new)} (each old must be there), commit, return the rev."""
    for rel, (old, new) in edits.items():
        text = (repo / rel).read_text(encoding="utf-8")
        assert old in text, (rel, old)
        (repo / rel).write_text(text.replace(old, new), encoding="utf-8")
    git(repo, "commit", "-q", "-am", message)
    return git(repo, "rev-parse", "HEAD")


def make_data(dest: Path) -> Path:
    """A data folder as `ingest` and `init` would leave it, stamped long ago."""
    dest.mkdir(parents=True)
    (dest / "store.json").write_text(json.dumps(
        {"rows": ROWS, "ingested_at": "2030-12-31T08:00:00+00:00"}), encoding="utf-8")
    (dest / "facts.json").write_text(json.dumps(
        {"scope": "demo", "updated_at": "2030-12-31T08:00:00+00:00"}), encoding="utf-8")
    return dest


def engine(repo: Path, *args: str, tmp: Path, timeout: float = 100,
           **extra: str) -> subprocess.CompletedProcess:
    """repo's own tests/golden/engine.py, run as an operator would, with
    TMPDIR = tmp (so a test can see what it leaves) and `extra` in its env."""
    return subprocess.run([sys.executable, str(repo / "tests" / "golden" / "engine.py"), *args],
                          capture_output=True, text=True, cwd=repo, timeout=timeout,
                          env=env(TMPDIR=str(tmp), **extra))


def worktrees(repo: Path) -> list[str]:
    return [ln for ln in git(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")]
