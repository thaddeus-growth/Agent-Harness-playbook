"""A fake Zylos host for the adapter's tests (not a test file: no test_ prefix).

    with Host(link=True) as host:
        host.configure({"SHOP_API_KEY": "k"})
        r = host.hook("post-install.js")

`Host` lays out a temp HOME whose path has a space, the way Zylos does:

  $HOME/zylos                               ZYLOS_DIR (exported)
  $HOME/zylos/.claude/skills/<name>         the skill dir: a harness with this
                                            adapter vendored as zylos/, its
                                            manifest.json, a SKILL.md whose
                                            frontmatter matches it, the root
                                            ecosystem.config.cjs, a stub CLI
                                            entry and a stub console entry
  $HOME/zylos/.claude/skills/scheduler/scripts/cli.js
                                            a fake scheduler with the real
                                            one's output contract
  $HOME/zylos/components/<name>             the data dir
  $HOME/zylos/bin/<cli.bin>                 core's bin link (link=True)
  $HOME/fakebin/{uv,zylos}                  first on PATH: a fake uv that logs
                                            each call, a fake `zylos --version`

Nothing is downloaded and no endpoint is hit: the hooks run with the
test-only <PREFIX>_ZYLOS_HOOK_* overrides (read only with
<PREFIX>_ZYLOS_HOOK_TEST=1). PATH never includes $ZYLOS_DIR/bin, as on a real
agent.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ADAPTER = os.path.dirname(HERE)                        # hosts/zylos
PLAYBOOK = os.path.dirname(os.path.dirname(ADAPTER))
EXAMPLE = os.path.join(ADAPTER, "manifest.example.json")
TEMPLATE = os.path.join(ADAPTER, "ecosystem.config.cjs.template")
# What a harness copies into <harness>/zylos/ (everything but tests/, the
# example and the root-file template).
VENDORED = ["lib.js", "hooks", "bin", "Caddyfile.snippet", "VERSION", "README.md"]
NODE = shutil.which("node")
CLOSED = "127.0.0.1:9"      # discard port: connection refused at once
PLACEHOLDER_LEFT = r"\{[a-z_]+(?::[^{}\s]*)?\}"


def example() -> dict:
    with open(EXAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


# zylos-core's scheduler cli.js where the hooks depend on it: `list --json`
# prints full task rows, names are not unique, `add` prints `Task created:
# <id>`, `update` prints `Task updated: <id>`, and a `remove` it can't do
# prints an error but still exits 0 (a seeded `sticky` task).
# FAKE_ADD_FAILS / FAKE_LIST_FAILS break add / list.
FAKE_SCHEDULER = r"""
const fs = require('fs'), path = require('path');
const db = path.join(__dirname, 'tasks.json');
let tasks = fs.existsSync(db) ? JSON.parse(fs.readFileSync(db, 'utf8')) : [];
const argv = process.argv.slice(2);
fs.appendFileSync(path.join(__dirname, 'calls.log'), JSON.stringify(argv) + '\n');
const opt = (k) => { const i = argv.indexOf(k); return i < 0 ? undefined : argv[i + 1]; };
const save = () => fs.writeFileSync(db, JSON.stringify(tasks));
if (argv[0] === 'list' && argv[1] === '--json') {
  if (process.env.FAKE_LIST_FAILS) { console.error('Error: database is locked'); process.exit(1); }
  console.log(JSON.stringify(tasks));
} else if (argv[0] === 'add') {
  if (process.env.FAKE_ADD_FAILS) { console.error('Error: database is locked'); process.exit(0); }
  const id = 'task-' + (tasks.length + 1) + '-' + Date.now().toString(16);
  tasks.push({ id, name: opt('--name'), prompt: argv[1], type: 'recurring',
               cron_expression: opt('--cron'), miss_threshold: Number(opt('--miss-threshold') || 300),
               reply_channel: opt('--reply-channel') ?? null, reply_endpoint: opt('--reply-endpoint') ?? null,
               status: 'pending', next_run_at: 0, created_at: Date.now() });
  save();
  console.log('Task created: ' + id);
} else if (argv[0] === 'update') {
  const t = tasks.find((x) => x.id === argv[1]);
  if (!t) { console.error('Error: Task not found: ' + argv[1]); process.exit(0); }
  if (opt('--prompt') !== undefined) t.prompt = opt('--prompt');
  if (opt('--cron') !== undefined) t.cron_expression = opt('--cron');
  if (opt('--miss-threshold') !== undefined) t.miss_threshold = Number(opt('--miss-threshold'));
  if (opt('--reply-channel') !== undefined) t.reply_channel = opt('--reply-channel');
  if (opt('--reply-endpoint') !== undefined) t.reply_endpoint = opt('--reply-endpoint');
  save();
  console.log('\nTask updated: ' + t.id);
} else if (['remove', 'rm', 'delete'].includes(argv[0])) {
  const hit = tasks.filter((t) => t.id.startsWith(argv[1] || '\0'));
  if (hit.length !== 1 || hit[0].sticky) {
    console.error('Error: Task not found: ' + argv[1]);
    process.exit(0);
  }
  tasks = tasks.filter((t) => t !== hit[0]);
  save();
  console.log('Removed task: ' + hit[0].id);
} else process.exit(2);
"""

# Logs "cwd|cache prefix|data dir|argv" per call. FAKE_UV_SLEEP_ON sleeps on
# a matching call; FAKE_UV_FAIL_ON makes one exit 1; FAKE_UV_NO_PYTHON makes
# `python find` fail; FAKE_UV_SCOPES (e.g. '"US", "CA"') is what the scope
# list prints.
FAKE_UV = """#!/bin/sh
printf '%s|%s|%s|%s\\n' "$PWD" "$PYTHONPYCACHEPREFIX" "$SHOP_DATA_DIR" "$*" >> "$FAKE_UV_LOG"
if [ -n "$FAKE_UV_SLEEP_ON" ]; then
  case "$*" in *"$FAKE_UV_SLEEP_ON"*) sleep 5 ;; esac
fi
if [ -n "$FAKE_UV_FAIL_ON" ]; then
  case "$*" in *"$FAKE_UV_FAIL_ON"*) exit 1 ;; esac
fi
case "$*" in
  "python find"*) [ -n "$FAKE_UV_NO_PYTHON" ] && exit 1 ;;
  *"facts list"*) printf '{"declared_markets": [%s]}\\n' "$FAKE_UV_SCOPES" ;;
  *doctor*) echo "0 warning(s)" ;;
esac
exit 0
"""

# Writes the env it got and each argument on its own line (overwriting).
FAKE_UV_ARGV = """#!/bin/sh
{ echo "DATA=$SHOP_DATA_DIR"; echo "CACHE=$PYTHONPYCACHEPREFIX"; for a in "$@"; do echo "ARG=$a"; done; } > "$FAKE_UV_LOG"
[ -n "$FAKE_UV_SIGNAL" ] && kill -"$FAKE_UV_SIGNAL" $$
exit "${FAKE_UV_EXIT:-0}"
"""

# `uv run --no-project X …` → this Python on X (runs the real console).
FAKE_UV_PYTHON = f"""#!/bin/sh
[ "$1" = run ] && [ "$2" = --no-project ] && {{ shift 2; exec '{sys.executable}' "$@"; }}
exit 3
"""


def skill_md(m: dict) -> str:
    """A SKILL.md whose frontmatter is what `node zylos/lib.js check` wants."""
    c = m["console"]
    required = sorted({k for t in m["tasks"] for k in t["requires"]})
    optional = [m["reply"]["channel"], m["reply"]["endpoint"], c["port_key"], c["title_key"]]
    optional += [c[k] for k in ("allow_host_key",) if k in c]
    defaults = {k: v for k, v in m.get("defaults", {}).items() if k != "_comment"}
    optional += [k for k in defaults if k not in optional and k not in required]

    def item(k):
        out = [f"    - name: {k}", "      description: read by zylos/ only"]
        if re.search(r"SECRET|TOKEN|PASSWORD|ACCESS_KEY|API_KEY|PRIVATE_KEY", k):
            out.append("      sensitive: true")
        if k in defaults:
            out.append(f"      default: {defaults[k]['value']}")
        return out

    hooks = "\n".join(f"    {h}: zylos/hooks/{h}.js"
                      for h in ("configure", "post-install", "post-upgrade", "pre-uninstall"))
    lines = [
        "---",
        f"name: {m['name']}",
        "description: A fictional harness for the adapter's tests. Use whenever testing. Do NOT use otherwise.",
        "version: 0.1.0",
        "type: capability",
        "",
        "# Zylos adapter (zylos/README.md); other hosts ignore the keys below.",
        f"next-steps: \"Give post-install a 10-minute command timeout. Then run `{m['scope']['declare']}` with the "
        f"owner, and set {m['reply']['channel']} / {m['reply']['endpoint']} through configure.\"",
        "bin:",
        f"  {m['cli']['bin']}: zylos/bin/cli.js",
        "lifecycle:",
        "  npm: false",
        "  hooks:",
        hooks,
        "  service:",
        f"    name: zylos-{m['name']}",
        "    entry: zylos/bin/console.js",
        "    type: pm2",
        "config:",
        "  required:",
        *[x for k in required for x in item(k)],
        "  optional:",
        *[x for k in optional for x in item(k)],
        "---",
        "",
        f"# {m['name']}",
        "",
    ]
    return "\n".join(lines)


class Host:
    """A temp HOME laid out like a Zylos host (its path contains a space)."""

    def __init__(self, manifest: dict | None = None, *, scheduler=True, uv: str | None = FAKE_UV,
                 link=False, zylos_version: str | None = "0.8.1", console=None):
        self.m = copy.deepcopy(manifest if manifest is not None else example())
        self.name = self.m["name"]
        self.prefix = self.m["env"]["prefix"]
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="zylos host "))
        self.zylos = os.path.join(self.home, "zylos")
        self.skill = os.path.join(self.zylos, ".claude", "skills", self.name)
        self.adapter = os.path.join(self.skill, "zylos")
        self.data = os.path.join(self.zylos, "components", self.name)
        self.logs = os.path.join(self.data, "logs")
        self.sched = os.path.join(self.zylos, ".claude", "skills", "scheduler", "scripts")
        self.bin = os.path.join(self.home, "fakebin")
        self.uv = os.path.join(self.bin, "uv")
        self.uv_log = os.path.join(self.home, "uv.log")
        self.link = os.path.join(self.zylos, "bin", self.m["cli"]["bin"])
        os.makedirs(self.adapter)
        os.makedirs(self.bin)
        for rel in VENDORED:
            src = os.path.join(ADAPTER, rel)
            dst = os.path.join(self.adapter, rel)
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        shutil.copy2(TEMPLATE, os.path.join(self.skill, "ecosystem.config.cjs"))
        self.write_manifest(self.m)
        self.write(os.path.join(self.skill, "SKILL.md"), skill_md(self.m))
        self.write(os.path.join(self.skill, self.m["cli"]["entry"]), "# the harness CLI (a stub)\n")
        if console == "playbook":        # the real playbook console, vendored
            shutil.copytree(os.path.join(PLAYBOOK, "console"), os.path.join(self.skill, "console"),
                            ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        else:
            self.write(os.path.join(self.skill, self.m["console"]["entry"]), "# the console (a stub)\n")
        if scheduler:
            self.write(os.path.join(self.sched, "cli.js"), FAKE_SCHEDULER)
        if uv is not None:
            self.set_uv(uv)
        if zylos_version is not None:
            self.write(os.path.join(self.bin, "zylos"),
                       f"#!/bin/sh\n[ \"$1\" = --version ] && printf '%s' '{zylos_version}'\n", mode=0o755)
        if link:                          # what core's linkBins does
            os.makedirs(os.path.dirname(self.link))
            os.symlink(os.path.join(self.adapter, "bin", "cli.js"), self.link)

    # --- files -------------------------------------------------------------
    @staticmethod
    def write(path, text, mode=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(path, mode)

    def write_manifest(self, m) -> None:
        text = m if isinstance(m, str) else json.dumps(m, indent=2)
        self.write(os.path.join(self.adapter, "manifest.json"), text)

    def set_uv(self, script: str) -> None:
        self.write(self.uv, script, mode=0o755)

    # --- running -------------------------------------------------------------
    def env(self, **extra) -> dict:
        p = f"{self.prefix}_ZYLOS_HOOK"
        env = {"HOME": self.home, "ZYLOS_DIR": self.zylos, "PATH": f"{self.bin}:/usr/bin:/bin",
               "FAKE_UV_LOG": self.uv_log, f"{p}_TEST": "1",
               f"{p}_ENDPOINTS": f"https://{CLOSED}",
               f"{p}_UV_INSTALLER": f"http://{CLOSED}/install.sh"}
        env.update({k: v for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            env.pop(k, None)
        return env

    def hook(self, name, stdin="", timeout=60, **extra):
        return subprocess.run([NODE, os.path.join(self.adapter, "hooks", name)], cwd=self.skill,
                              input=stdin, capture_output=True, text=True,
                              env=self.env(**extra), timeout=timeout)

    def node(self, *args, timeout=30, cwd=None, **extra):
        return subprocess.run([NODE, *args], cwd=cwd or self.skill, capture_output=True, text=True,
                              env=self.env(**extra), timeout=timeout)

    def lib(self, code: str, **extra):
        """Run JS with `h` = the vendored lib.js; returns the parsed stdout."""
        r = self.node("-e", f"const h=require(process.argv[1]);{code}",
                      os.path.join(self.adapter, "lib.js"), **extra)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def configure(self, values, **extra):
        return self.hook("configure.js", json.dumps(values), ZYLOS_COMPONENT=self.name,
                         ZYLOS_SKILL_DIR=self.skill, ZYLOS_DATA_DIR=self.data, **extra)

    def sh(self, command, **extra):
        """A command line as the agent's non-login shell runs it: PATH with
        node (Zylos hosts have it) but without $ZYLOS_DIR/bin."""
        path = f"{self.bin}:{os.path.dirname(NODE)}:/usr/bin:/bin"
        return subprocess.run(["/bin/sh", "-c", command], capture_output=True, cwd=self.home,
                              text=True, env=self.env(PATH=path, **extra), timeout=30)

    # --- what happened ---------------------------------------------------------
    def tasks(self) -> list:
        p = os.path.join(self.sched, "tasks.json")
        return json.load(open(p)) if os.path.exists(p) else []

    def named(self, name) -> list:
        return [t for t in self.tasks() if t.get("name") == name]

    def seed(self, tasks) -> None:
        with open(os.path.join(self.sched, "tasks.json"), "w") as fh:
            json.dump(tasks, fh)

    def calls(self, verb) -> list:
        p = os.path.join(self.sched, "calls.log")
        lines = open(p).read().splitlines() if os.path.exists(p) else []
        return [a for a in map(json.loads, lines) if a and a[0] == verb]

    def uv_calls(self) -> list:
        """[cwd, cache prefix, data dir, argv] per fake-uv call."""
        if not os.path.exists(self.uv_log):
            return []
        return [ln.split("|", 3) for ln in open(self.uv_log).read().splitlines()]

    def reset_uv_log(self) -> None:
        if os.path.exists(self.uv_log):
            os.remove(self.uv_log)

    def argv_seen(self) -> list:
        return open(self.uv_log).read().splitlines() if os.path.exists(self.uv_log) else []

    def done(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.done()


def wait_for(path, marker, tries=80):
    """The file's text once it holds `marker` (or whatever it holds after ~8 s)."""
    text = ""
    for _ in range(tries):
        text = open(path).read() if os.path.exists(path) else ""
        if marker in text:
            return text
        time.sleep(0.1)
    return text
