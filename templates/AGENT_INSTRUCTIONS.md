# {{name}} — agent instructions (CLAUDE.md)

Act as the owner of this project: judge whether a request makes sense before building it. Code stays well scoped, minimal, decoupled and focused.

## What lives here

Only the invariants that `--help` and the code cannot show. Every command and flag: `{{cli}} --help`. When it and this file disagree, `--help` wins; fix the drift here. The mechanics of each subsystem live in its module docstring. No issue-by-issue history: that is git's job.

Every merge request names the story (`S..`) or rule (`P..`) it serves; a release is titled `release X.Y.Z`. An idea with neither is first a `proposed` row in `ssot/user-stories.agent.tsv`. The repository lives at {{repo_home}}: merge requests (never squashed), issues and CI.

## Layout

| Path | What it holds |
| --- | --- |
| `scripts/{{cli}}.py` | The one entry point: `{{cli}} <verb>`. Arguments after `--` go to the script as they are |
| `scripts/` | One script per verb family: `pull_*`, `ingest_*`, `compute_*`, the human verbs, `queue_actions.py`, `execute_actions.py` |
| `scripts/_lib/` | This harness's own library: `scripts/_lib/schema.py` (its tables, with the kit's human tables), `scripts/_lib/writer.py` (the one writer), the API clients |
| `scripts/kit/` | The shared kit, vendored with its `VERSION` and `MANIFEST.sha256`. Never edited here |
| `console/` | The owner console, vendored the same way. Never edited here |
| `ssot/` | Owner files, agent files and registries; `ssot/index.tsv` lists them |
| `tests/` | One file per guard; each prints `RESULT: N passed` |
| `harness.toml` | The harness's names: CLI word, env prefix, languages, scopes, layers, what a release ships |

## Invariants (each names the test that enforces it)

- **Boundary** — the harness owns whatever must come out the same no matter which agent or UI asks: data, fixed rules, the guarded write path, the human gate's proof. The agent owns presentation, wording and conversation. The core never names the console, an adapter or a chat app. *(tests/test_boundary.py)*
- **Layering** — pull (read-only) → raw → ingest → database → compute (read-only). Pull never writes the database; ingest never calls the API; compute never writes; only `scripts/execute_actions.py` imports the writer. *(tests/test_layering.py)*
- **Write side** — `scripts/_lib/writer.py` is the only code that writes to the external system: an allowlist (empty until the owner agrees in writing), an opt-in switch `{{env_prefix}}_ALLOW_WRITES`, a kill switch `{{env_prefix}}_KILL`, no retries. Execute is a dry run unless `--apply`. *(tests/test_layering.py; the guard is kit code, pinned by tests/test_kit_drift.py)*
- **Human tables** — never dropped or auto-migrated; one write path each; backed up before every rebuild; a write that would lose rows is rolled back. Cache tables are rebuilt by the ingest that fills them, never lossily; an older tool refuses a database stamped by a newer one. *(tests/test_human_tables.py)*
- **Human gate** — confirm and approve need a human: a terminal retype, or a relayed one-time code bound to exactly the ids, values and scope shown. The agent sees the code it relays; the guard is the binding and the audit, not secrecy. No bypass flag. Agents may write pending values and lower trust, never raise it. *(tests/test_gate.py)*
- **Scope** — declared once by `{{cli}} facts init`, confirmed through the gate; there is no default. *(tests/test_gate.py)*
- **Closed registries** — `ssot/index.tsv` lists every registry with its owner, reader and test. A value lives in one place; a threshold's number only in its registry. Ids never change or get reused. *(tests/test_ssot.py)*
- **Owner files** — agents never edit them; they propose in the `.agent.tsv` sibling and apply only an answer the owner gave, recorded in its `decided` column. *(tests/test_ssot.py)*
- **Owner asks** — the agent asks the owner only through `console/ask.py`: at most 10 open, each with evidence, a recommendation and what "no" means. It never writes an answer or edits the log; the log folder is `CONSOLE_DIR`, declared, never guessed. *(upstream: console/tests/test_boundary.py, console/tests/test_core.py; here the vendored copy is pinned by tests/test_kit_drift.py)*
- **Vendored code** — `scripts/kit/` and `console/` match their manifests. A fix goes to the playbook first and is vendored again. *(tests/test_kit_drift.py)*
- **`--json`** — stdout is exactly one JSON document; every report carries `meta`; a failure prints one `{error, next, code, params}` document; every English message carries a registered code. *(tests/test_json_contract.py)*
- **Tests** — the runner fails if any test file exits non-zero, omits its `RESULT: N passed` line, or leaves files in its private temp folder. Agent-behaviour evals make live model calls and stay outside it. *(tests/test_run_tests.py)*
- **Releases** — a release is `git archive` of its tag: internal files are `export-ignore`, the kit and the console ship. *(tests/test_release.py)*

## Output paths

- `{{env_prefix}}_DATA_DIR` — root of all client data; required; never guessed from the working directory. Data never belongs inside the code checkout.
- `CONSOLE_DIR` — the owner console's log folder; one per person who decides.
