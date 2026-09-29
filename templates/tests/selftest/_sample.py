"""A sample harness with the test kit installed the way templates/tests/README.md
says: the kit's tests/*.py in tests/, the playbook's core/ at the root,
templates/gitattributes as .gitattributes, templates/ssot/ as ssot/, plus a
few lines of code in the example layout. `build()` writes it, applies the
changes a test asks for and stages it all in a fresh git repository.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # templates/tests
TEMPLATES = os.path.dirname(KIT)
REPO = os.path.dirname(TEMPLATES)

CODE = {
    "README.md": "# Sample harness\n",
    "CLAUDE.md": "# Agent instructions\n",
    ".claude/settings.json": "{}\n",
    ".github/workflows/test.yml": "on: push\n",
    ".gitlab-ci.yml": "test:\n  script: python3 tests/run.py\n",
    "evals/README.md": "# Agent-behaviour evals, run by hand\n",
    "src/harness.py": '"""The CLI: one verb per script."""\n\n\ndef main(verb):\n    return verb\n',
    "src/lib/__init__.py": "",
    "src/lib/db.py": "import sqlite3\n\n\ndef connect(path):\n    return sqlite3.connect(path)\n",
    "src/lib/constants.py": ("from pathlib import Path\n\n\ndef load():\n"
                             '    return (Path(__file__).parents[2] / "ssot" / "constants.tsv").read_text()\n'),
    "src/lib/rules.py": "def over_cap(value, cap):\n    return value > cap\n",
    "src/api/__init__.py": "from .client import Client\n",
    "src/api/client.py": ("import json\n\n\nclass Client:\n    def get(self, path):\n"
                          "        return json.dumps({'path': path})\n"),
    "src/api/writer.py": "from .client import Client\n\n\ndef write(change):\n    return Client().get(change)\n",
    "src/pull_ads.py": ("from api import Client\nfrom core import dates\n\n\ndef main():\n"
                        "    return Client().get('report'), dates.today()\n"),
    "src/ingest_ads.py": ("from core import dates\nfrom lib import db\n\n\ndef main(path):\n"
                          "    return db.connect(path), dates.utc_stamp()\n"),
    "src/compute_kpi.py": ("from core import dates\nfrom lib import db, rules\n\n\ndef main(path):\n"
                           "    return db.connect(path), dates.today(), rules.over_cap(1, 2)\n"),
    "src/compute_stories.py": ("import subprocess\nimport sys\n\n\ndef run(verb):\n"
                               "    return subprocess.run([sys.executable, 'src/harness.py', *verb])\n"),
    "src/facts.py": ("from core import dates\nfrom lib import db\n\n\ndef set_pending(path, key, value):\n"
                     "    return db.connect(path), key, value, dates.now()\n"),
    "src/execute.py": ("from api import writer\nfrom lib import db, rules\n\n\ndef main(path, apply=False):\n"
                       "    return writer.write('x') if apply and rules.over_cap(2, 1) else db.connect(path)\n"),
    "webconsole/serve.py": ('import subprocess\n\nTOOL = "harness"\n\n\ndef facts():\n'
                            '    return subprocess.run([TOOL, "facts", "list", "--json"], capture_output=True)\n'),
}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build(root: str, changes: dict[str, str | None] | None = None) -> str:
    """Write the sample under `root`, apply `changes` ({path: text, or None to
    delete}), `git init` and `git add` it all. Returns `root`."""
    files = dict(CODE)
    for p in glob.glob(os.path.join(KIT, "*.py")):
        files[f"tests/{os.path.basename(p)}"] = read(p)
    for p in glob.glob(os.path.join(TEMPLATES, "ssot", "*")):
        files[f"ssot/{os.path.basename(p)}"] = read(p)
    for p in glob.glob(os.path.join(REPO, "core", "**", "*"), recursive=True):
        if os.path.isfile(p) and "__pycache__" not in p:
            files[os.path.relpath(p, REPO)] = read(p)
    files[".gitattributes"] = read(os.path.join(TEMPLATES, "gitattributes"))
    for path, text in (changes or {}).items():
        if text is None:
            files.pop(path)
        else:
            files[path] = text
    for path, text in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-c", "init.defaultBranch=main", "-C", root, *args],
                       check=True, capture_output=True)
    return root


def run(root: str, env: dict, script: str, *args: str) -> tuple[int, str]:
    """Run one of the sample's scripts (`tests/test_clock.py`) from its root."""
    p = subprocess.run([sys.executable, os.path.join(root, script), *args], cwd=root,
                       capture_output=True, text=True, timeout=110, env=env)
    return p.returncode, p.stdout + p.stderr
