#!/usr/bin/env python3
"""scaffold/new_client.py: one client's workspace for an existing harness.

  [1] --dry-run lists the files and writes nothing
  [2] the folder: bin/<cli>, bin/console, bin/ask executable; no `{{`
      left; one `<<fill: …>>` in CLAUDE.md; workspace/ and its intake/
      0700 and git-ignored; a clients/README.md index when there is none
  [3] the gate verbs come from the harness's own `verbs --json`: every
      `gated` verb is denied through the wrapper in .claude/settings.json,
      hand edits under workspace/ are denied, and the console relays the
      gated verbs ending in confirm / approve / answer (not restore)
  [4] bin/<cli> pins <PREFIX>_DATA_DIR to the workspace; the scripts parse
  [5] refusals, exit 2 with one line: a folder that is not empty, a
      clients folder inside the harness, a path with no harness.toml, a
      bad client id; a second client prints the index row instead
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PLAYBOOK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLAYBOOK))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from kit.testing.check import check, finish, tmp_dir  # noqa: E402

SCAFFOLD = PLAYBOOK / "scaffold" / "new_client.py"
VERBS = {"harness": "acme-harness", "cli": "acme", "verbs": [
    {"words": ["status"], "kind": "read"},
    {"words": ["facts", "set"], "kind": "human"},
    {"words": ["facts", "confirm"], "kind": "gated"},
    {"words": ["facts", "restore"], "kind": "gated"},
    {"words": ["plan", "answer"], "kind": "gated"},
    {"words": ["queue", "approve"], "kind": "gated"}]}
ENTRY = f"""import json, os, sys
if sys.argv[1:] == ["verbs", "--json"]:
    print(json.dumps({json.dumps(VERBS)}))
else:
    print(json.dumps({{"data_dir": os.environ.get("ACME_DATA_DIR"),
                      "argv": sys.argv[1:]}}))
"""


def fake_harness() -> Path:
    h = Path(tmp_dir("fake-harness-")) / "acme-harness"
    (h / "scripts").mkdir(parents=True)
    (h / "harness.toml").write_text(
        '[harness]\nname = "acme-harness"\ncli = "acme"\n'
        'env_prefix = "ACME"\nscripts_dir = "scripts"\n', encoding="utf-8")
    (h / "scripts" / "acme.py").write_text(ENTRY, encoding="utf-8")
    return h


def run(*argv: str) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACME_")}
    r = subprocess.run([sys.executable, str(SCAFFOLD), *argv],
                       capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    h = fake_harness()
    clients = Path(tmp_dir("clients-")) / "acme-clients"
    base = ["--harness", str(h), "--clients", str(clients)]

    print("[1] --dry-run")
    rc, out, err = run(*base, "--client", "northwind", "--title", "Northwind",
                       "--dry-run")
    check("lists the files, writes nothing", rc == 0
          and "would write" in out and "bin/acme" in out
          and not clients.exists(), out + err)

    print("[2] the folder")
    rc, out, err = run(*base, "--client", "northwind", "--title", "Northwind",
                       "--lang", "zh", "--language", "Traditional Chinese")
    f = clients / "northwind"
    check("written", rc == 0 and f.is_dir(), out + err)
    for b in ("bin/acme", "bin/console", "bin/ask"):
        check(f"{b} is executable", os.access(f / b, os.X_OK))
    texts = {p: p.read_text(encoding="utf-8") for p in f.rglob("*")
             if p.is_file()}
    check("no `{{` left anywhere", not any("{{" in t for t in texts.values()),
          [str(p) for p, t in texts.items() if "{{" in t])
    check("one `<<fill: …>>` in CLAUDE.md, the language named",
          texts[f / "CLAUDE.md"].count("<<fill:") == 1
          and "Traditional Chinese" in texts[f / "CLAUDE.md"])
    for d in (f / "workspace", f / "workspace" / "intake"):
        check(f"{d.relative_to(f)} is 0700",
              stat.S_IMODE(d.stat().st_mode) == 0o700)
    check("workspace/ is git-ignored",
          "workspace/" in texts[f / ".gitignore"].splitlines())
    check("an index is written when there is none",
          "[northwind](northwind/)" in (clients / "README.md").read_text(
              encoding="utf-8"))

    print("[3] the gate verbs come from the harness")
    deny = json.loads(texts[f / ".claude" / "settings.json"])[
        "permissions"]["deny"]
    gated = ["facts confirm", "facts restore", "plan answer", "queue approve"]
    check("every gated verb is denied through ./bin/acme and bin/acme",
          all(f"Bash({p}bin/acme {g}:*)" in deny for g in gated
              for p in ("./", "")), deny)
    check("a human (pending) verb is not denied",
          not any("facts set" in d for d in deny), deny)
    check("hand edits under workspace/ and reading its .env are denied",
          {"Edit(./workspace/**)", "Write(./workspace/**)",
           "Read(./workspace/.env)"} <= set(deny), deny)
    check("the console relays confirm / approve / answer, not restore",
          '--relay-verbs "facts confirm,plan answer,queue approve"'
          in texts[f / "bin" / "console"], texts[f / "bin" / "console"])
    launch = json.loads(texts[f / ".claude" / "launch.json"])
    check("the console is a preview on its port",
          launch["configurations"][0]["port"] == 8771
          and launch["configurations"][0]["runtimeArgs"][0].endswith(
              "bin/console"), launch)

    print("[4] the wrapper")
    r = subprocess.run([str(f / "bin" / "acme"), "status", "--json"],
                       capture_output=True, text=True, timeout=60,
                       env={k: v for k, v in os.environ.items()
                            if not k.startswith("ACME_")})
    doc = json.loads(r.stdout or "{}")
    check("bin/acme pins ACME_DATA_DIR to the workspace and passes argv",
          os.path.realpath(doc.get("data_dir") or "")
          == str((f / "workspace").resolve())
          and doc.get("argv") == ["status", "--json"], r.stdout + r.stderr)
    for b in ("bin/acme", "bin/console", "bin/ask"):
        r = subprocess.run(["sh", "-n", str(f / b)], capture_output=True,
                           text=True)
        check(f"{b} parses (sh -n)", r.returncode == 0, r.stderr)

    print("[5] refusals")
    for argv, what in (
            (base + ["--client", "northwind", "--title", "x"], "not empty"),
            (["--harness", str(h), "--clients", str(h / "clients"),
              "--client", "a", "--title", "x"], "inside the harness"),
            (["--harness", str(clients), "--clients", str(clients.parent
                                                          / "o"),
              "--client", "a", "--title", "x"], "no harness.toml"),
            (base + ["--client", "Bad Id", "--title", "x"], "lower-case")):
        rc, out, err = run(*argv)
        check(f"refused, exit 2: {what}", rc == 2 and what in err
              and len(err.strip().splitlines()) == 1, (rc, err))
    rc, out, err = run(*base, "--client", "second", "--title", "Second",
                       "--port", "8772")
    check("a second client prints the index row, the index is untouched",
          rc == 0 and "| [second](second/) | Second | :8772 |" in out
          and "second" not in (clients / "README.md").read_text(
              encoding="utf-8"), out + err)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
