# Zylos host adapter

This folder runs a playbook harness as a Zylos component (zylos-core, the Zylos agent host): the lifecycle hooks, the linked CLI, detached long jobs, the scheduled tasks and the owner console as a pm2 service. It is generic. Everything that belongs to one harness (its names, env prefix, pinned runtime, endpoints, scope check, console settings, tasks and their prompts) comes from that harness's `zylos/manifest.json`, which `lib.js` loads and checks once.

The adapter targets **zylos-core ≥ 0.7.1** and uses only core's documented surface:

- the `SKILL.md` frontmatter;
- a pm2 `ecosystem.config.cjs` at the skill root;
- configure's stdin JSON and the `ZYLOS_*` env;
- the `components/<name>` data dir;
- the scheduler CLI's `add`, `update`, `list --json` and `remove <id>`.

The harness itself never refers to `zylos/`. Delete the folder, the Zylos keys of the frontmatter and the root `ecosystem.config.cjs`, and the harness still runs anywhere.

Version: `VERSION` (0.1.0). Tests: `python3 hosts/zylos/tests/run.py` (Python stdlib, needs `node`).

## What a harness copies, and what it writes

A harness **copies** these files into `<harness>/zylos/` and never edits them. Keep the executable bit on `bin/*.js`.

| From `hosts/zylos/` | To | What it is |
| --- | --- | --- |
| `lib.js` | `zylos/lib.js` | Everything: the manifest check, paths, env chain, uv/Python, scheduler reconcile, jobs, console argv |
| `hooks/*.js` | `zylos/hooks/` | `configure`, `post-install`, `post-upgrade`, `pre-uninstall`: thin wrappers over `lib.js` |
| `bin/cli.js`, `bin/detach.js`, `bin/console.js` | `zylos/bin/` | The linked CLI, detached long jobs, the console service |
| `Caddyfile.snippet` | `zylos/Caddyfile.snippet` | The console's login route; `node zylos/lib.js caddy` fills it in |
| `README.md`, `VERSION` | `zylos/` | This guide; the adapter version the harness vendored |

A harness **writes** only three things:

1. `zylos/manifest.json`, starting from [manifest.example.json](manifest.example.json) (a fictional `shop-harness`).
2. The Zylos keys of its `SKILL.md` frontmatter (below).
3. The root `ecosystem.config.cjs`, a copy of [ecosystem.config.cjs.template](ecosystem.config.cjs.template) (three lines). Core looks for it at the skill root only.

The owner console itself is the playbook's `console/`, vendored as usual (`python3 kit/tools/vendor.py --harness <dir> --console`); `console.entry` points at its `serve.py`.

Then add `node zylos/lib.js check` to the harness's CI. It exits 1 and names each problem when the manifest, the `SKILL.md` frontmatter, the root ecosystem file or a file they name disagree.

## The manifest

`zylos/manifest.json` is plain JSON. Every key is checked when `lib.js` loads. A missing key, an unknown key or a bad value stops every hook with one line that names it, for example `zylos/manifest.json: tasks[1].cron: missing`. A `_comment` key is allowed anywhere. Keys marked optional may be left out.

| Section | Key | What it is |
| --- | --- | --- |
| | `name` | The component name: equals `SKILL.md` `name`; the data dir is `$ZYLOS_DIR/components/<name>`, the pm2 service `zylos-<name>` |
| | `min_zylos` | The oldest zylos-core this harness supports; post-install and post-upgrade refuse an older one |
| `cli` | `bin` | The linked command, `<brand>-<cli>` (a generic name clashes, and core overwrites clashing links); equals the `SKILL.md` `bin` key |
| `cli` | `entry` | The harness CLI inside the skill dir, a PEP 723 script run with `uv run --no-project` |
| `cli` | `doctor` | The CLI arguments of its health check, run by post-install (`[]` skips it) |
| `env` | `prefix` | The harness's env prefix, e.g. `SHOP`; it also names the test knobs `<PREFIX>_ZYLOS_HOOK_*` and `<PREFIX>_AUTH_ENV_PATHS` |
| `env` | `data_dir` | The env var that holds the data dir, `<PREFIX>_DATA_DIR`; cli.js, detach.js and console.js set it |
| `env` | `files` | The env files between the process env and `<data dir>/.env` (`~/` or absolute), as the harness's own chain reads them |
| `runtime` | `python` | The Python version installed with `uv python install` when `uv python find` fails |
| `runtime` | `uv_version` | The uv version Astral's installer is pinned to |
| `runtime` | `uv_installer_url` | The address of Astral's standalone uv installer script for exactly `uv_version` (Astral's site, path `/uv/<uv_version>/install.sh`; uv's installation docs give it); it must name `uv_version` |
| `runtime` | `uv_installer_sha256` | The sha256 of that script; bump the version, the URL and the sha256 together |
| | `endpoints` | `https://` URLs post-install checks for reachability (a soft note) |
| `scope` | `list` | CLI arguments that print the declared scope as JSON, e.g. `["facts", "list", "--json"]` |
| `scope` | `field` | The JSON field holding it, e.g. `declared_markets` |
| `scope` | `declare` | The CLI verb the owner runs to declare one, printed as the next step when none is |
| `reply` | `channel` | The config key naming the scheduler's reply channel (e.g. `lark`) |
| `reply` | `endpoint` | The config key naming the endpoint on it (e.g. the owner's chat id) |
| `console` | `entry` | The vendored playbook console, `console/serve.py` |
| `console` | `port` | Its default port on 127.0.0.1 (1024-65535) |
| `console` | `port_key` | The config key that overrides the port |
| `console` | `title_key` | The config key for the page title (default: `name`) |
| `console` | `base_path` | The public path, e.g. `/shop`; used by the Caddy block and the warnings |
| `console` | `user_header` | The header Caddy sets to the logged-in user, e.g. `X-Remote-User` |
| `console` | `users_file` | The logins file in the data dir, e.g. `console.users` |
| `console` | `args` | More `serve.py` flags, e.g. `--relay-verbs`, `--lang`, `--default-reason`, a static `--allow-host`; console.js owns `--dir`, `--host`, `--port`, `--user`, `--user-header`, `--relay-cmd`, `--title` |
| `console` | `deciders_key` | Optional. The config key listing who decides (`--deciders`) |
| `console` | `allow_host_key` | Optional. The config key holding the public domain(s) the console answers to (`--allow-host`) |
| `detach` | `exit_marker` | The last log line of a detached job, `<MARKER> <status>`, e.g. `SHOP-EXIT` |
| `detach` | `alias` | The word that means the harness CLI at the start of a detached command, e.g. `shop` |
| `tasks[]` | `name` | The scheduler task name (names identify tasks; unique) |
| `tasks[]` | `cron` | A five-field cron, in the scheduler's time zone |
| `tasks[]` | `miss_threshold` | Seconds late a run still happens after the agent was offline |
| `tasks[]` | `requires` | Config keys that must resolve before the task is registered (without them it could only fail) |
| `tasks[]` | `prompt` | What the scheduler sends the agent; placeholders below |
| | `defaults` | Optional. `{KEY: {value, when}}`: configure writes `value` when a key named in `when` is in use and KEY is absent (core never applies a declared `default`) |

The two fields of a `defaults` entry are `value` (a string) and `when` (a list of keys).

**Prompt placeholders.** Resolved at install; paths are shell-quoted. A prompt with an unknown placeholder is refused when the manifest loads, and no rendered prompt keeps an unresolved one.

| Placeholder | Becomes |
| --- | --- |
| `{cli}` | The harness CLI as a shell calls it: the absolute, quoted `$ZYLOS_DIR/bin/<bin>` link, or `node '<skill>/zylos/bin/cli.js'` without the link |
| `{node}` | `node` |
| `{detach}` | `node '<skill>/zylos/bin/detach.js'` |
| `{data_dir}` | The quoted data dir |
| `{skill_dir}` | The quoted skill dir |
| `{logs}` | The quoted `<data dir>/logs` |
| `{log:NAME}` | The quoted `<data dir>/logs/NAME.log` |
| `{exit}` | `detach.exit_marker`, unquoted (it is a plain token) |

Write prompts as the example does: "run only these commands", the done/report contract, the error lines to the owner. They name harness verbs and JSON fields, so a harness test should render each one and check that the fields it names exist.

## SKILL.md frontmatter

The frontmatter serves any agent host (`name`, `description`, `version`, `type`) and Zylos (the rest; other hosts ignore it). For the example manifest:

```yaml
---
name: shop-harness
description: "… Use whenever … Do NOT use for …"
version: 0.1.0                      # plain X.Y.Z = the release tag vX.Y.Z
type: capability

# Zylos adapter (zylos/README.md); other hosts ignore the keys below.
next-steps: "Give post-install a 10-minute command timeout and relay its output to the owner. Then run `facts init` with the owner, and set SHOP_REPLY_CHANNEL / SHOP_REPLY_ENDPOINT through configure."
bin:
  northwind-shop: zylos/bin/cli.js
lifecycle:
  npm: false
  hooks:
    configure: zylos/hooks/configure.js
    post-install: zylos/hooks/post-install.js
    post-upgrade: zylos/hooks/post-upgrade.js
    pre-uninstall: zylos/hooks/pre-uninstall.js
  service:
    name: zylos-shop-harness
    entry: zylos/bin/console.js
    type: pm2
config:
  required:
    - name: SHOP_API_KEY
      description: the shop API key
      sensitive: true
  optional:
    - name: SHOP_REPLY_CHANNEL
      description: where the scheduled tasks report (a Zylos channel, e.g. lark); read by zylos/ only
    - name: SHOP_REPLY_ENDPOINT
      description: the endpoint on that channel (e.g. the owner's chat id); read by zylos/ only
    - name: SHOP_CONSOLE_PORT
      description: the console's port on 127.0.0.1 (default 8767); read by zylos/ only
    - name: SHOP_CONSOLE_TITLE
      description: the console's page title; read by zylos/ only
    - name: SHOP_CONSOLE_DECIDERS
      description: who decides in the console (comma-separated logins); read by zylos/ only
    - name: SHOP_CONSOLE_ALLOW_HOST
      description: the host's public domain, which the console answers to behind Caddy; read by zylos/ only
---
```

`node zylos/lib.js check` enforces:

- `name` equals the manifest's; `version` is plain semver; `type: capability`.
- `next-steps` mentions the 10-minute timeout, `scope.declare` and `reply.channel`.
- `bin` is exactly `{<cli.bin>: zylos/bin/cli.js}`.
- `lifecycle` holds only `npm: false`, the four hooks and the service `zylos-<name>` → `zylos/bin/console.js`, type pm2.
- There is no `http_routes` (core's routes carry no login) and no `upgrade` block.
- Every key the adapter reads is declared: the reply keys, the console keys, every task's `requires`, every `defaults` key.
- A key whose name says secret (`SECRET`, `TOKEN`, `PASSWORD`, `ACCESS_KEY`, `API_KEY`, `PRIVATE_KEY`) is `sensitive: true`.
- A declared `default` of a `defaults` key equals the manifest's value.

## Install

Zylos installs a component from a semver release tag (`vX.Y.Z`) and compares tags with `version` on upgrade, so every release bumps both. `zylos add <owner>/<repo>` downloads from GitHub only. For a private repository elsewhere, install from a local copy of the release:

    git clone --depth 1 --branch vX.Y.Z <repo url> /var/tmp/h-clone
    mkdir /var/tmp/h-vX.Y.Z
    git -C /var/tmp/h-clone archive --format=tar vX.Y.Z | tar -x -C /var/tmp/h-vX.Y.Z
    rm -rf /var/tmp/h-clone
    zylos add /var/tmp/h-vX.Y.Z

`git archive` leaves out whatever `.gitattributes` marks `export-ignore` (tests, CI, planning files). An upgrade from a local copy is uninstall without purge, then add; the data dir is never touched.

Supported hosts: Linux and macOS.

| Where | Path | What Zylos does to it |
| --- | --- | --- |
| code | `~/zylos/.claude/skills/<name>` | `zylos upgrade` backs it up and 3-way merges the new release. The adapter writes nothing here: `uv run --no-project`, and bytecode caches go under the data dir. |
| state | `$ZYLOS_DIR/components/<name>` (mode 0700) = `<PREFIX>_DATA_DIR` | Never touched by upgrade; deleted on uninstall only with purge. |
| the CLI | `$ZYLOS_DIR/bin/<cli.bin>` → `zylos/bin/cli.js` | Linked at install. **Not on the agent's PATH**: always call it by its absolute path. |

The data dir is resolved as core resolves it: `ZYLOS_DATA_DIR` (ignored when `ZYLOS_COMPONENT` names another component), then `$ZYLOS_DIR/components/<name>`, then `$HOME/zylos/components/<name>`.

| Event | Hooks |
| --- | --- |
| `zylos add` at a terminal | Zylos prompts for the required keys, pipes them to configure, then runs post-install. |
| `zylos add … --json` (chat install) | **None.** The agent pipes the collected keys to configure and runs post-install. |
| `zylos upgrade` | post-upgrade, holding the component lock; a failure is non-fatal to Zylos. |
| `zylos uninstall` | pre-uninstall. |

In a chat install, **give post-install a 10-minute command timeout** (a first install downloads uv and Python) and relay both hooks' output to the owner. By hand:

    cd ~/zylos/.claude/skills/<name>
    node zylos/hooks/configure.js <<'EOF'
    {"SHOP_API_KEY": "…", "SHOP_REPLY_CHANNEL": "lark", "SHOP_REPLY_ENDPOINT": "…"}
    EOF
    node zylos/hooks/post-install.js

Zylos treats a failing post-install or post-upgrade as a warning, so read the output. Every hook can be rerun by hand.

## What the hooks do

- **configure** merges the given keys into `<data dir>/.env` (below). No network, no uv: Zylos kills it after 30 s. It prints key names, never values.
- **post-install**:
  - Hard steps; on failure it exits non-zero and prints the fix to run by hand:
    - zylos-core ≥ `min_zylos`, read from `zylos --version` (a missing or unreadable version only warns);
    - the data dir, mode 0700;
    - `cli.entry` present;
    - `uv`: when missing, the installer at `uv_installer_url` is downloaded, checked against `uv_installer_sha256`, and only then run with `UV_NO_MODIFY_PATH=1` (lands in `~/.local/bin`, no sudo, no shell rc edits);
    - Python `runtime.python` (installed only if `uv python find` fails);
    - `uv run --no-project <entry> --help`, which builds the script's environment in uv's cache.
  - Soft checks, which print a note and continue: each endpoint's reachability; `<cli> <doctor>` (summary line printed, full report in `logs/doctor-install.log`); the declared scope, with the exact `scope.declare` command when none is; the console entry present; the Caddy block and the public Host (below).
  - It prints the absolute CLI command, then reconciles the scheduled tasks.
- **post-upgrade** is post-install in light mode: uv, Python, the CLI and the tasks; it skips the endpoint, doctor and scope checks and says so.
- **pre-uninstall** stops the detached jobs still running (a job counts only if its pid *and* start time match), removes every task this adapter manages, and never touches the data dir. It exits 1 with the reason when a job or a task can't be stopped or removed.

Each hook's runtime is capped at 9 minutes, under the agent's 10-minute command cap. A soft step the cap cuts is listed as skipped with the command that finishes it; a hard step the cap cuts fails with "rerun post-install". The hooks are idempotent, so a rerun picks up where it stopped.

**Test-only switches.** `<PREFIX>_ZYLOS_HOOK_TEST=1` enables `<PREFIX>_ZYLOS_HOOK_SKIP_UV`, `…_UV_INSTALLER`, `…_UV_INSTALLER_SHA256`, `…_ENDPOINTS` and `…_BUDGET_MS`. Without it they are ignored. Never set them on a host.

## Config

A terminal `zylos add` prompts only for the required keys; a chat install prompts for nothing, and the agent collects them. The configure hook stores every value in the component's own `<data dir>/.env` (mode 0600), which the harness reads as `$<PREFIX>_DATA_DIR/.env`; never the Zylos root `.env`. No Zylos command reruns configure: to add or change keys later, pipe just those keys again. The owner can run it at a terminal on the host (no heredoc; paste the JSON; Ctrl-D), which keeps secrets out of chat.

- **It only merges.** A key it isn't given is kept. An empty or null value keeps the stored value. It never deletes a key; delete the line by hand.
- **Values are strings, numbers or booleans**, stored as given (numbers and booleans as their JSON text). An object, an array or a multi-line value refuses the whole input, and nothing is written.
- **Values read back exactly.** A value with edge whitespace or surrounding quotes is written quoted, so the kit's `.env` parser (`kit/env.py`) returns it unchanged. `lib.js` reads `.env` files by the same rules.
- **Defaults.** A `defaults` entry is written when its group is in use and the key is absent.
- **Interrupted runs clean up**: a half-written `.env.tmp-*` is removed.

The adapter resolves a key as the harness does: process env, then `env.files`, then `<data dir>/.env` (later wins). `<PREFIX>_AUTH_ENV_PATHS` replaces the file chain (`none`: process env only).

## One client per host

A Zylos host runs one agent for one owner, and nothing isolates one client from another. So a host serves exactly one client: one data dir. A second client needs a second host. One client with several scopes (markets) fits on one host.

## Running the harness CLI

Call the link by its absolute path; post-install prints it. It runs `uv run --no-project <skill dir>/<cli.entry> …` in the caller's cwd, sets `<PREFIX>_DATA_DIR` to the data dir unless the caller set one, keeps bytecode caches under the data dir, finds `uv` in `~/.local/bin` when it isn't on PATH, and exits with the CLI's status.

    "$HOME/zylos/bin/northwind-shop" doctor

Without the link, the same command is `node ~/zylos/.claude/skills/<name>/zylos/bin/cli.js`.

## Scheduled tasks

post-install and post-upgrade keep exactly the manifest's `tasks` in the Zylos scheduler, each once its `requires` keys resolve:

- **Reconcile by name.** Task names are not unique, so the hooks list the tasks (`cli.js list --json`) and act by id. They keep one task per name (the one matching this release, else the oldest), `update` it when its prompt, cron, miss threshold or configured reply differs, and remove the duplicates. They judge `add`, `update` and `remove` by their output (`Task created: <id>`, `Task updated: <id>`, `Removed task: <id>`), never by the exit code: a remove of an unknown id exits 0.
- **A task dropped from the manifest is removed.** The names this adapter manages are recorded in `<data dir>/.zylos-tasks.json`. A name recorded there but no longer in the manifest has its tasks removed by the next post-install or post-upgrade; a removal that fails stays recorded and is retried. pre-uninstall removes the manifest's names and the recorded ones. A task with any other name is never touched.
- **Reply channel.** Without one, the scheduler dispatches the task with no reply path, so "send the owner …" reaches no one. Set the `reply.channel` / `reply.endpoint` keys through configure and rerun post-install, or `cli.js update <id> --reply-channel … --reply-endpoint …`. Until one is set, post-install prints this with the task id. A reply set by hand is kept while none is configured.
- **Scheduling.** The scheduler sends the prompt to the agent at each tick; the agent runs the commands. Cron runs in the scheduler's time zone (`TZ` in `~/zylos/.env`, else the daemon's, else UTC). The miss threshold only matters while the agent runtime is offline.

Inspect the tasks with `node ~/zylos/.claude/skills/scheduler/scripts/cli.js list`.

## Long jobs: run them detached

A foreground agent command is capped at 10 minutes, and an agent's background job dies when its session restarts. Start a long command with `detach.js`:

    node ~/zylos/.claude/skills/<name>/zylos/bin/detach.js pull-orders -- shop pull orders --days 30

- The command runs in its own session and process group, with cwd = the data dir and `<PREFIX>_DATA_DIR` set. A leading `detach.alias` means the harness CLI; a leading `uv` is resolved like the hooks resolve it.
- Output goes to `<data dir>/logs/<name>.log`, whose last line is `<exit_marker> <status>` when it ends. `<data dir>/logs/<name>.pid` holds `{pid, start, cmd}`.
- It refuses to start `<name>` again while that job runs (pid and start time both match; a pid owned by another user counts as alive). Concurrent starts are serialized by `<name>.lock`.

Poll with `tail -n 5 <data dir>/logs/<name>.log`. `<MARKER> 0` is done; another number failed (the lines above say why); no marker and no running job means it was killed, for example by a reboot.

## The console service

The owner console (the playbook's `console/serve.py`: the asks inbox and the relay to the harness's human gate) runs as a pm2 service. The public URL and the login are a Caddy block the host agent adds once.

**What runs.** `lifecycle.service` names the pm2 app `zylos-<name>` and its entry `zylos/bin/console.js`. The root `ecosystem.config.cjs` runs it with cwd = the data dir and its log at `<data dir>/logs/console.log`. console.js runs `uv run --no-project <console.entry>` with:

- `--dir <data dir>/console` (the event log and its secret);
- `--host 127.0.0.1 --port <port_key or port>`;
- `--user-header <user_header>` (believed from a loopback peer only; without it no one can answer);
- `--title <title_key or name>`;
- `--allow-host` for each domain in `allow_host_key`;
- `--deciders` from `deciders_key`, when set;
- `--relay-cmd '<node>' '<skill>/zylos/bin/cli.js'` when `console.args` carries `--relay-verbs` (serve.py wants both or neither);
- then `console.args`.

Who starts it: a terminal `zylos add` (`pm2 start ecosystem.config.cjs`, `pm2 save`); in a chat install the agent runs `pm2 start <skill dir>/ecosystem.config.cjs && pm2 save`; `zylos upgrade` restarts it if it ran; `zylos uninstall` deletes it; at boot core's own pm2 ecosystem loads it. A broken manifest makes the root file export no app (and log why) rather than break core's boot.

**Why not `http_routes`.** Core's declared routes support only `{path, type: reverse_proxy, target, strip_prefix}`: no `basic_auth`, no header rules. A declared route would publish the console with no login, and a visitor-sent user header would reach it from Caddy's 127.0.0.1. So the component declares no route, and the host agent pastes the block below.

### The Caddy block

Print it with this host's values, then paste it inside the primary site block of `~/zylos/http/Caddyfile`, just before its closing `}`, keeping the BEGIN/END lines (`zylos uninstall` removes the block by them):

    node ~/zylos/.claude/skills/<name>/zylos/lib.js caddy

For the example manifest it prints:

    # BEGIN zylos-component:shop-harness
    redir /shop /shop/ permanent
    handle_path /shop/* {
        basic_auth {
            import /home/<u>/zylos/components/shop-harness/console.users
        }
        request_header -X-Remote-User
        reverse_proxy 127.0.0.1:8767 {
            header_up X-Remote-User {http.auth.user.id}
        }
    }
    # END zylos-component:shop-harness

- `redir` sends `/shop` to `/shop/`, so the console's relative links resolve under the prefix.
- `handle_path` strips `/shop`: the console's links are all relative and it takes no base path.
- `basic_auth` answers 401 first. `request_header -X-Remote-User` drops any copy the visitor sent; `header_up X-Remote-User` then sets the logged-in user.
- **The public Host is passed through unchanged.** There is no `header_up Host`: the console compares a POST's `Origin` with the `Host` it sees, so a rewritten Host gets every answer refused (403). In exchange the console must accept the public Host: set `allow_host_key` (e.g. `SHOP_CONSOLE_ALLOW_HOST=agent.example.com`) through configure, then `pm2 restart zylos-<name>`. Without it, every page through Caddy is 421. post-install warns about both.

Validate, then reload: `~/zylos/bin/caddy validate --config ~/zylos/http/Caddyfile --adapter caddyfile && pm2 reload caddy`.

Core never rewrites the block on upgrade (no routes are declared). A reinstall (uninstall without purge, then add) or `zylos init` with a domain removes it: paste it again. post-install and post-upgrade warn when `users_file` has logins but the Caddyfile has no BEGIN marker; they never edit the Caddyfile.

**Adding a person.** The logins are `<data dir>/<users_file>` (mode 0600), one `<username> <bcrypt hash>` per line, never in the repository. Caddy fails to start on a missing or empty list, so add the first person before pasting the block. Hash at a terminal so the password never goes through chat:

    ~/zylos/bin/caddy hash-password
    printf '%s %s\n' alice '<hash>' >> ~/zylos/components/<name>/console.users
    chmod 600 ~/zylos/components/<name>/console.users
    pm2 reload caddy

The console records each answer under the header's user. With `deciders_key` set, only those logins answer; everyone else logged in advises.

**After a restart, check:**

    pm2 describe zylos-<name> | grep status                                   # online
    tail -n 3 ~/zylos/components/<name>/logs/console.log                      # "Console: http://127.0.0.1:<port>/"
    curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/<base>/          # 401
    curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Remote-User: x' https://<domain>/<base>/   # still 401

Then open the URL, log in, and check that the page names you.

## Known limits

- **Checked against a fake host only.** The hooks and bin are tested with a fake scheduler CLI, a fake `uv` and a fake `zylos --version`. The console is tested once for real (the playbook's `serve.py` behind a kept public Host). Real zylos-core has not run this adapter yet: its frontmatter parser, pm2 ecosystem and `linkBins`, the scheduler daemon dispatching a task to a live agent, a full `zylos upgrade`, and the Caddy block under a real Caddy (`caddy validate`). The reference harness's CI smoke against core tags has not been ported to the manifest yet.
- **`requires` is a plain list of keys.** A task whose keys depend on other keys (a configurable credential prefix, a token-command mode) can't say so; list the keys of the default layout, or register the task by hand.
- **A task renamed with the state file lost is orphaned.** `.zylos-tasks.json` lives in the data dir. If it is deleted, a task dropped from the manifest is no longer known to be this harness's; remove it with the scheduler CLI.
- **`{cli}` and `{detach}` need `node` on the agent's PATH** (the link's shebang, the fallback form, detach). Zylos hosts have it. The console service instead calls the absolute node that runs it.
- **The console's secret sits in `<data dir>/console/secret`**, which the agent on a single-user host can read: the signature is then an accident guard, not a wall. To keep it out of the agent's reach, give only the pm2 app a `CONSOLE_SECRET` (16+ characters) in its environment and keep that out of the data dir.
- **One client per host**, and one console per component: a second console for the same harness needs a second host.
- **Windows is not supported.** Detached jobs rely on POSIX sessions and `sh`.
- **The hooks never edit the Caddyfile**, and the scheduler's time zone is the host's; both are the host agent's job.
- **Vendoring is by hand for now.** `kit/tools/vendor.py` copies `kit/` and `console/`; copy this folder (without `tests/` and the two templates) yourself and keep `VERSION` with it.
