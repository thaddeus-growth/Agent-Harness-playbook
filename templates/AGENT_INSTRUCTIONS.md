# {{name}} — agent instructions (CLAUDE.md)

Act as the owner of this project: judge whether a request makes sense before building it. Code stays well scoped, minimal, decoupled and focused.

## What lives here

Only the invariants that `--help` and the code cannot show. Every command and flag: `{{cli}} --help`. When it and this file disagree, `--help` wins; fix the drift here. The mechanics of each subsystem live in its module docstring. No issue-by-issue history: that is git's job.

Every merge request names the story (`S..`) or rule (`P..`) it serves in its title (CI job `story id`); a release is titled `release X.Y.Z`. An idea with neither is first a `proposed` row in `ssot/user-stories.agent.tsv`. Refactors change no behaviour: the golden diff (below) shows 0 differences on fixtures and on a copy of real data; a behaviour change found on the way is its own named MR. The repository lives at {{repo_home}}: merge requests (never squashed), issues and CI.

Nothing a later session needs lives only in a session: tools go in the repo, the owner's answers in the owner queue, a client's state (facts, decisions, the work plan, spend) in the harness's own rows with a write path and a reader, and build state in a dated journal. Markdown about a client is presentation only. One worktree per builder; only the orchestrator pushes, and it removes a worktree only when its commits are on main and nothing is uncommitted. Each session ends with a handoff: released vs installed, each branch in flight with its worktree and how to verify it, open owner items.

## Layout

| Path | What it holds |
| --- | --- |
| `scripts/{{cli}}.py` | The one entry point: `{{cli}} <verb>`. Arguments after `--` go to the script as they are |
| `scripts/` | One script per verb family: `pull_*`, `ingest_*`, `compute_*`, the human verbs, `queue_actions.py`, `execute_actions.py` |
| `scripts/_lib/` | This harness's own library: `scripts/_lib/schema.py` (its tables, with the kit's human tables), `scripts/_lib/writer.py` (the one writer), the API clients |
| `scripts/kit/` | The shared kit, vendored with its `VERSION` and `MANIFEST.sha256`. Never edited here |
| `console/` | The owner console, vendored the same way. Never edited here |
| `webconsole/` | The client console, once the harness has one: its pages, and `webconsole/ui_rules.tsv`, an owner file |
| `ssot/` | Owner files, agent files and registries; `ssot/index.tsv` lists them |
| `tests/` | One file per guard; each prints `RESULT: N passed`. Also `tests/golden/`, the golden diff (below) |
| `harness.toml` | The harness's names: CLI word, env prefix, languages, scopes, layers, what a release ships |

## Golden diff

`tests/golden/engine.py compare BASE HEAD [--strict] [--data DIR]` ([engine and cases hook](tests/golden/README.md)), committed before the first refactor. It is a tool, not a test, and never ships.

- **Cases:** every `--json` read verb, each failure document, each dry run, and every console page in every language.
- **Data:** the same data for both sides: a sandbox built from the fixtures, or one copy of a client's data folder (read-only, never printed).
- **Pinned:** every clock read goes through a seam patched to one instant; no credentials, writes off, no confirm secret. Only temp paths and the run's own timestamps are masked.
- **Fails on:** a removed case or key, a changed value, list length or exit code, a document that is not one JSON document, a page that raises, any HTML difference. `--strict` also fails an added key: refactors run strict, features may add.
- **Checked before trusted:** `compare HEAD HEAD --strict` twice gives 0 differences, and a case never gets `PYTHONHASHSEED`, so an order taken from a set shows as a diff; a throwaway change to one registry default shows a diff; `--today` another day moves every `today`, child verbs' included.
- **Run it this way:** one case at a time, never across a UTC midnight. With fixtures, both sides are seeded from BASE's fixtures. With `--data`, one copy is taken while nothing in the folder changes, then each side gets its own copy. Output goes outside any git work tree: a snapshot holds data.

## Invariants (each names the test that enforces it)

- **Boundary** — the harness owns whatever must come out the same no matter which agent or UI asks: data, fixed rules, the guarded write path, the human gate's proof. The agent owns presentation, wording and conversation. `--json` is the contract; changes only add keys. A console is one more consumer: it runs read verbs and, from a human's click, only the gate verbs with the relayed code, user and time; never the database file or a write verb. The core never names the console, an adapter or a chat app. *(tests/test_boundary.py)*
- **Layering** — pull (read-only) → raw → ingest → database → compute (read-only). Pull never writes the database; ingest never calls the API; compute never writes; only `scripts/execute_actions.py` imports the writer. *(tests/test_layering.py)*
- **One clock** — `dates.now()` (aware UTC) is the only calendar read; `today()` is its UTC day, `host_today()` the host's day and `market_day(tz)` a market's, named apart. Code calls them through the module, never `from kit.dates import now`, so one patch pins every read. Reads not yet moved are rows in `[guards.clock.allowed]` of `harness.toml`, which only shrinks; a new harness has none. *(tests/test_clock.py)*
- **Write side** — `scripts/_lib/writer.py` is the only code that writes to the external system: an allowlist (empty until the owner agrees in writing), an opt-in switch `{{env_prefix}}_ALLOW_WRITES`, a kill switch `{{env_prefix}}_KILL`, no retries. Execute is a dry run unless `--apply`. *(tests/test_layering.py; the guard is kit code, pinned by tests/test_kit_drift.py)*
- **Human tables** — never dropped or auto-migrated; one write path each; backed up before every rebuild; a write that would lose rows is rolled back. Cache tables are rebuilt by the ingest that fills them, never hand-migrated, never lossily; an older tool refuses a database stamped by a newer one. *(tests/test_human_tables.py)*
- **Human gate** — confirm and approve need a human: a terminal retype, or a relayed one-time code bound to exactly the ids, values and scope shown. The agent sees the code it relays; the guard is the binding and the audit, not secrecy. No bypass flag. Agents may write pending values and lower trust, never raise it. *(tests/test_gate.py)*
- **Scope** — declared once by `{{cli}} facts init`, confirmed through the gate; there is no default. *(tests/test_gate.py)*
- **Closed registries** — `ssot/index.tsv` lists every registry with its owner, reader and test. A value lives in one place; a threshold's number only in its registry. Ids never change or get reused. *(tests/test_ssot.py)*
- **Owner files** — agents never edit them; they propose in the `.agent.tsv` sibling and apply only an answer the owner gave, recorded in its `decided` column. *(tests/test_ssot.py)*
- **Owner asks** — the agent asks the owner only through `console/ask.py`: at most 10 open, each with evidence, a recommendation and what "no" means. It never writes an answer or edits the log; the log folder is `CONSOLE_DIR`, declared, never guessed. *(upstream: console/tests/test_boundary.py, console/tests/test_core.py; here the vendored copy is pinned by tests/test_kit_drift.py)*
- **Vendored code** — `scripts/kit/` and `console/` match their manifests. A fix goes to the playbook first and is vendored again. *(tests/test_kit_drift.py)*
- **`--json`** — stdout is exactly one JSON document; every report carries `meta`; a failure prints one `{error, next, code, params}` document; every English message carries a registered code, in every verb, and every code the harness registers is emitted somewhere. *(tests/test_json_contract.py)*
- **Stories** — every done story has one read-only check in `ssot/story_checks.tsv`; missing data skips, never passes. *(tests/test_ssot.py; the runner is kit code, pinned by tests/test_kit_drift.py)*
- **Tests** — the runner fails if any test file exits non-zero, omits its `RESULT: N passed` line, or leaves files in its private temp folder. Agent-behaviour evals make live model calls and stay outside it. By practice, which no runner can check, are the playbook's rules the test kit cannot check (`templates/tests/README.md`): a child process gets a sandboxed environment, never the operator's; a wall-clock check keeps 2x headroom; an idempotency test pins two instants; test data uses synthetic ids only. *(tests/test_run_tests.py)*
- **Changelog** — `CHANGELOG.md` is shipped and is the one history a host reads. It starts with `## [Unreleased]`, its releases are in order, and `SKILL.md`'s version is the newest of them; a merge request that changes anything a host or an agent can see adds a line under Unreleased (CI job `changelog`; `[no changelog]` in the title says nothing visible changed), and a removal or rename of a `--json` key or a command is named as breaking. *(tests/test_changelog.py)*
- **Releases** — a release is `git archive` of its tag: internal files are `export-ignore`, the kit and the console ship. *(tests/test_release.py)*

## Output paths

- `{{env_prefix}}_DATA_DIR` — root of all client data; required; never guessed from the working directory. Data never belongs inside the code checkout.
- `CONSOLE_DIR` — the owner console's log folder.
