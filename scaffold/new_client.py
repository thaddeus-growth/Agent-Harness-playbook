#!/usr/bin/env python3
"""Generate one client's workspace for an existing harness.

    python3 scaffold/new_client.py --harness ../acme-harness \\
        --clients ../acme-clients --client northwind --title "Northwind Tea" \\
        [--port 8771] [--lang en|zh] [--language "Traditional Chinese"] \\
        [--relay-verbs "facts confirm,decisions confirm"] [--dry-run]

A harness serves many clients; each client is one folder in a clients
repository (the SEO harness's seo-researches, the KOL harness's
kol-clients), never inside the harness checkout. What it writes, under
<clients>/<client>/:

  * bin/<cli>: the harness pinned to this client, <PREFIX>_DATA_DIR =
    ./workspace; <PREFIX>_HARNESS (env) points at another checkout;
  * bin/console: this client's owner console (the harness's vendored
    console/serve.py), log in workspace/console; the first run appends a
    random <PREFIX>_CONFIRM_CODE_SECRET to workspace/.env (0600, never
    printed) so a click can run a gate verb with a relayed code;
  * bin/ask: the agent's side of that console;
  * CLAUDE.md: the agent's brief (templates/client/CLAUDE.md; one
    `<<fill: …>>` left for who the client is);
  * .claude/settings.json: permissions.deny for every gate verb through
    the wrapper, and for hand edits under workspace/ (deny rules hold in
    every permission mode; the wrapper is not a second gate, the harness
    is: this only stops an agent asking for what it may not do);
  * .claude/launch.json: the console as a preview;
  * .gitignore (workspace/ is data and secrets), reports/, and
    workspace/intake/ (0700, the drop folder), none of it committed.

The gate verbs come from the harness itself (`<cli> verbs --json`, kind
`gated`); the console relays those ending in confirm, approve or answer
unless --relay-verbs says otherwise. It refuses a client folder that
exists and is not empty, a clients folder inside the harness, and a
harness with no harness.toml; `--dry-run` prints the files and writes
nothing. It leaves no `{{` behind. A clients/README.md index is written
only when there is none; otherwise the row to add is printed.

Test: scaffold/tests/test_new_client.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

PLAYBOOK = Path(__file__).resolve().parents[1]
TEMPLATE = PLAYBOOK / "templates" / "client" / "CLAUDE.md"
CLIENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
RELAYED = ("confirm", "approve", "answer")


class Refused(Exception):
    pass


def harness_names(harness: Path) -> dict:
    toml = harness / "harness.toml"
    if not toml.is_file():
        raise Refused(f"{harness} has no harness.toml: not a harness")
    h = tomllib.loads(toml.read_text(encoding="utf-8")).get("harness", {})
    for k in ("name", "cli", "env_prefix"):
        if not h.get(k):
            raise Refused(f"{toml}: [harness].{k} is missing")
    return {"name": h["name"], "cli": h["cli"], "prefix": h["env_prefix"],
            "scripts": h.get("scripts_dir", "scripts")}


def gate_verbs(harness: Path, n: dict) -> list[str]:
    """The words of every `gated` verb, from the harness's own table."""
    entry = harness / n["scripts"] / f"{n['cli']}.py"
    r = subprocess.run([sys.executable, str(entry), "verbs", "--json"],
                       capture_output=True, text=True, timeout=120,
                       env={k: v for k, v in os.environ.items()
                            if not k.startswith(n["prefix"] + "_")})
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise Refused(f"`{n['cli']} verbs --json` did not print one JSON "
                      f"document (exit {r.returncode}): {r.stderr[-300:]}")
    return [" ".join(v["words"]) for v in doc.get("verbs", [])
            if v.get("kind") == "gated"]


def render(text: str, values: dict) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        raise Refused(f"a placeholder is left: {re.findall(r'{{[^}]*}}', text)}")
    return text


def files(a, n: dict, gates: list[str], relay: list[str]) -> dict[str, tuple[str, int]]:
    """{relative path: (content, mode)} of the client folder."""
    cli, p = n["cli"], n["prefix"]
    home = f'"${{{p}_HARNESS:-{a.harness}}}"'
    wrapper = f"""#!/bin/sh
# {a.title}: {cli} pinned to this client's workspace ({p}_DATA_DIR = ./workspace).
# {p}_HARNESS points at another harness checkout.
export {p}_DATA_DIR="$(cd "$(dirname "$0")/../workspace" && pwd)"
exec python3 {home}/{n['scripts']}/{cli}.py "$@"
"""
    console = f"""#!/bin/sh
# {a.title}'s owner console: what `{cli} pending` lists, answered in the
# browser. A click on a gated ask runs the gate through ./bin/{cli} with a
# relayed one-time code. The first run switches relayed confirmation on: a
# random {p}_CONFIRM_CODE_SECRET appended to workspace/.env (0600), never printed.
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
envf="$root/workspace/.env"
if ! grep -qs '^{p}_CONFIRM_CODE_SECRET=' "$envf"; then
  (umask 077; printf '{p}_CONFIRM_CODE_SECRET=%s\\n' "$(openssl rand -hex 32)" >> "$envf")
fi
export {p}_DATA_DIR="$root/workspace" CONSOLE_DIR="$root/workspace/console"
mkdir -p "$CONSOLE_DIR" && chmod 700 "$CONSOLE_DIR"
exec python3 {home}/console/serve.py --dir "$CONSOLE_DIR" --title "{a.title}" \\
  --lang {a.lang} --port "${{{p}_CONSOLE_PORT:-{a.port}}}" \\
  --relay-cmd "$root/bin/{cli}" --relay-verbs "{','.join(relay)}" "$@"
"""
    ask = f"""#!/bin/sh
# The agent's side of {a.title}'s console (console/ask.py), pinned to workspace/console.
root="$(cd "$(dirname "$0")/.." && pwd)"
export CONSOLE_DIR="$root/workspace/console"
exec python3 {home}/console/ask.py "$@"
"""
    deny = [f"Bash({pre}bin/{cli} {g}:*)" for g in gates
            for pre in ("./", "")]
    deny += ["Edit(./workspace/**)", "Write(./workspace/**)",
             "Read(./workspace/.env)"]
    settings = json.dumps({"permissions": {"deny": deny}}, indent=2) + "\n"
    folder = Path(a.clients).resolve() / a.client
    launch = json.dumps({"version": "0.0.1", "configurations": [{
        "name": f"{a.client}-console", "runtimeExecutable": "/bin/sh",
        "runtimeArgs": [str(folder / "bin" / "console")],
        "port": a.port}]}, indent=2) + "\n"
    brief = render(TEMPLATE.read_text(encoding="utf-8"), {
        "harness_name": n["name"], "title": a.title, "client": a.client,
        "harness_dir": str(a.harness), "cli": cli, "env_prefix": p,
        "port": str(a.port), "language": a.language,
        "gate_verbs": ", ".join(f"`{g}`" for g in gates) or "none"})
    return {"bin/" + cli: (wrapper, 0o755), "bin/console": (console, 0o755),
            "bin/ask": (ask, 0o755), "CLAUDE.md": (brief, 0o644),
            ".claude/settings.json": (settings, 0o644),
            ".claude/launch.json": (launch, 0o644),
            ".gitignore": ("workspace/\n.DS_Store\n__pycache__/\n", 0o644),
            "reports/README.md": (
                "Reports for people, dated (`<what>-YYYY-MM-DD.md`), and "
                "one `handoff-YYYY-MM-DD.md` per session ending in a "
                "Distil list (the playbook's templates/client-handoff.md)."
                "\n", 0o644)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--clients", required=True, type=Path)
    ap.add_argument("--client", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--lang", choices=("en", "zh"), default="en",
                    help="the console's language")
    ap.add_argument("--language", default="the client's language",
                    help="the language the agent talks to the client in")
    ap.add_argument("--relay-verbs",
                    help="comma-separated gate verbs the console may run "
                         "(default: the gated verbs ending in "
                         + ", ".join(RELAYED) + ")")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        a.harness = a.harness.expanduser().resolve()
        if not CLIENT_RE.fullmatch(a.client):
            raise Refused(f"--client {a.client!r}: lower-case letters, "
                          f"digits, '.', '_' and '-' only")
        clients = a.clients.expanduser().resolve()
        if clients == a.harness or a.harness in clients.parents:
            raise Refused(f"{clients} is inside the harness: client data "
                          f"never lives in the harness checkout")
        folder = clients / a.client
        if folder.exists() and any(folder.iterdir()):
            raise Refused(f"{folder} exists and is not empty")
        n = harness_names(a.harness)
        gates = gate_verbs(a.harness, n)
        relay = ([v.strip() for v in a.relay_verbs.split(",") if v.strip()]
                 if a.relay_verbs else
                 [g for g in gates if g.split()[-1] in RELAYED])
        out = files(a, n, gates, relay)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    for rel in sorted(out):
        print(("would write " if a.dry_run else "write ") + str(folder / rel))
    index = clients / "README.md"
    row = f"| [{a.client}]({a.client}/) | {a.title} | :{a.port} |"
    if a.dry_run:
        return 0
    for rel, (text, mode) in out.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        p.chmod(mode)
    intake = folder / "workspace" / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    for d in (folder / "workspace", intake):
        d.chmod(stat.S_IRWXU)
    if not index.exists():
        index.write_text(
            f"# {n['name']} clients\n\nOne folder per client, each a "
            f"workspace for {n['name']}: `bin/{n['cli']}` runs the harness "
            f"against that client's `workspace/` (never committed), "
            f"`bin/console` is the client's owner console, `CLAUDE.md` the "
            f"agent's brief, `reports/` is for people.\n\n"
            f"| Folder | Client | Console |\n|---|---|---|\n{row}\n",
            encoding="utf-8")
    else:
        print(f"add to {index}: {row}")
    print(f"next: fill the `<<fill: …>>` in {folder / 'CLAUDE.md'}, drop the "
          f"client's materials into workspace/intake/, then "
          f"`{folder / 'bin' / n['cli']} status --json`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
