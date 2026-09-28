# <Project> — agent instructions (CLAUDE.md)

Act as the owner of this project: judge whether a request makes sense before building it. Code stays well scoped, minimal, decoupled and focused.

## What lives here

Only the invariants that `--help` and the code cannot show. Every command and flag: `<tool> --help` — when it and this file disagree, `--help` wins; fix the drift here. The mechanics of each subsystem live in its module docstring. No issue-by-issue history: that is git's job.

Every merge request names the story (`S..`) or rule (`P..`) it serves; an idea with neither is first a `proposed` row in `ssot/user-stories.agent.tsv`.

## Invariants (each names the test that enforces it)

- **Boundary** — the harness owns whatever must come out the same no matter which agent or UI asks (data, fixed rules, the guarded write path, the human gate's proof). The agent owns presentation, wording and conversation. `--json` is the contract; changes only add keys. *(tests/test_boundary.py)*
- **Layering** — pull (read-only) → raw → ingest → database → compute (read-only). Pull never writes the database; ingest never calls the API; compute never writes. *(tests/test_layering.py)*
- **Write side** — one module is the only code that writes to the external system: allowlist, opt-in switch, kill switch, no retries. Execute is a dry run unless `--apply`. *(tests/test_write_path.py)*
- **Cache tables** — rebuilt by the ingest that fills them, never hand-migrated, never lossily; an older tool refuses a database stamped by a newer one. *(tests/test_rebuild.py)*
- **Human tables** — never dropped or auto-migrated; one write path each; backed up before every ingest. *(tests/test_human_tables_survive.py)*
- **Human gate** — confirm / approve need a human: a terminal retype, or a relayed one-time code bound to exactly the ids, values and scope shown. The agent sees the code it relays; the guard is the binding and the audit, not secrecy. No bypass flag. Agents may write pending values and lower trust, never raise it. *(tests/test_human_gate.py)*
- **Closed registries** — `ssot/index.tsv` lists every registry with its owner, reader and test. A value lives in one place; a threshold's number only in its registry. Ids never change or get reused. *(tests/test_ssot_index.py)*
- **Owner files** — agents never edit them; they propose in the `.agent.tsv` sibling and apply only an answer the owner gave. *(tests/test_ssot_index.py)*
- **Owner asks** — the agent asks the owner only through `console/ask.py`: at most 10 open, each with evidence, a recommendation and what "no" means. It never writes an answer or edits the log; the log folder is `CONSOLE_DIR`, declared, never guessed. *(console/tests/test_boundary.py, console/tests/test_core.py)*
- **Scope** — declared once by an init command; there is no default. *(tests/test_init.py)*
- **`--json`** — stdout is exactly one JSON document; every report carries `meta`; a failure prints one `{error, next, code, params}` document; every English message carries a registered code. *(tests/test_contract.py)*
- **Tests** — the runner fails if any test file exits non-zero or omits its `RESULT: N passed` line. Agent-behaviour evals make live model calls and stay outside it. *(tests/test_run_tests.py)*
- **Releases** — a release is `git archive` of its tag; internal files are `export-ignore`. *(tests/test_release_archive.py)*

## Output paths

- `DATA_DIR` — root of all client data; required; never guessed from the working directory. Data never belongs inside the code checkout.
