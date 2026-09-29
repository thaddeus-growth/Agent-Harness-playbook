# <Project> — agent instructions (CLAUDE.md)

Act as the owner of this project: judge whether a request makes sense before building it. Code stays well scoped, minimal, decoupled and focused.

## What lives here

Only the invariants that `--help` and the code cannot show. Every command and flag: `<tool> --help` — when it and this file disagree, `--help` wins; fix the drift here. The mechanics of each subsystem live in its module docstring. No issue-by-issue history: that is git's job.

Every merge request names the story (`S..`) or rule (`P..`) it serves in its title (CI job `story id`); an idea with neither is first a `proposed` row in `ssot/user-stories.agent.tsv`. Refactors change no behaviour: the golden diff (below) shows 0 differences on fixtures and on a copy of real data; a behaviour change found on the way is its own named MR.

Nothing a later session needs lives only in a session: tools go in the repo, the owner's answers in the owner queue, state in a dated journal. One worktree per builder; only the orchestrator pushes, and it removes a worktree only when its commits are on main and nothing is uncommitted. Each session ends with a handoff: released vs installed, each branch in flight with its worktree and how to verify it, open owner items.

## Golden diff

`tests/golden/engine.py compare BASE HEAD [--strict] [--data DIR]` ([engine and cases hook](tests/golden/README.md)), committed before the first refactor. It is a tool, not a test, and never ships.

- **Cases:** every `--json` read verb, each failure document, each dry run, and every console page in every language.
- **Data:** the same data for both sides: a sandbox built from the fixtures, or one copy of a client's data folder (read-only, never printed).
- **Pinned:** every clock read goes through a seam patched to one instant; no credentials, writes off, no confirm secret. Only temp paths and the run's own timestamps are masked.
- **Fails on:** a removed case or key, a changed value, list length or exit code, a document that is not one JSON document, a page that raises, any HTML difference. `--strict` also fails an added key: refactors run strict, features may add.
- **Checked before trusted:** `compare HEAD HEAD --strict` twice gives 0 differences, and a case never gets `PYTHONHASHSEED`, so an order taken from a set shows as a diff; a throwaway change to one registry default shows a diff; `--today` another day moves every `today`, child verbs' included.
- **Run it this way:** one case at a time, never across a UTC midnight. With fixtures, both sides are seeded from BASE's fixtures. With `--data`, one copy is taken while nothing in the folder changes, then each side gets its own copy. Output goes outside any git work tree: a snapshot holds data.

## Invariants (each names the test that enforces it)

- **Boundary** — the harness owns whatever must come out the same no matter which agent or UI asks (data, fixed rules, the guarded write path, the human gate's proof). The agent owns presentation, wording and conversation. `--json` is the contract; changes only add keys. The console is one more consumer: it runs read verbs and, from a human's click, only the gate verbs with the relayed code, user and time; never the database file or a write verb. The core never names the console or the host adapter. *(tests/test_layering.py: its text rules)*
- **Layering** — pull (read-only) → raw → ingest → database → compute (read-only). Pull never writes the database; ingest never calls the API; compute never writes and spawns nothing; only the executor imports the one writer. The rules are a table checked on the import graph, lazy imports included, and a rule whose layer matches no module fails. *(tests/test_layering.py)*
- **One clock** — `dates.now()` (aware UTC) is the only calendar read; `today()` is its UTC day and `host_today()` the host's day, named apart. Code calls them through the module, never `from dates import now`, so one patch pins every read. The allowlist of reads not yet moved only shrinks. *(tests/test_clock.py)*
- **Write side** — one module is the only code that writes to the external system: allowlist, opt-in switch, kill switch, no retries. Execute is a dry run unless `--apply`. *(tests/test_write_path.py)*
- **Cache tables** — rebuilt by the ingest that fills them, never hand-migrated, never lossily; an older tool refuses a database stamped by a newer one. *(tests/test_rebuild.py)*
- **Human tables** — never dropped or auto-migrated; one write path each; backed up before every ingest. *(tests/test_human_tables_survive.py)*
- **Human gate** — confirm / approve need a human: a terminal retype, or a relayed one-time code bound to exactly the ids, values and scope shown. The agent sees the code it relays; the guard is the binding and the audit, not secrecy. No bypass flag. Agents may write pending values and lower trust, never raise it. *(tests/test_human_gate.py)*
- **Closed registries** — `ssot/index.tsv` lists every registry with its owner, reader and test. A value lives in one place; a threshold's number only in its registry. Ids never change or get reused. *(tests/test_ssot_index.py)*
- **Owner files** — agents never edit them; they propose in the `.agent.tsv` sibling and apply only an answer the owner gave. *(tests/test_ssot_index.py)*
- **Owner asks** — the agent asks the owner only through `console/ask.py`: at most 10 open, each with evidence, a recommendation and what "no" means. It never writes an answer or edits the log; the log folder is `CONSOLE_DIR`, declared, never guessed. *(console/tests/test_boundary.py, console/tests/test_core.py)*
- **Scope** — declared once by an init command; there is no default. *(tests/test_init.py)*
- **`--json`** — stdout is exactly one JSON document; every report carries `meta`; a failure prints one `{error, next, code, params}` document; every English message carries a registered code, in every verb, and every registered code is emitted somewhere. *(tests/test_contract.py)*
- **Stories** — every done story has one read-only check in `ssot/story_checks.tsv`; missing data skips, never passes. *(tests/test_compute_stories.py)*
- **Tests** — the runner fails if any test file exits non-zero, omits its `RESULT: N passed` line, reports a failure or `0 passed` on it, or leaves anything in the run's private temp dir. Each file, and every child process it starts (`_check.child_env()`), runs on an environment allowlist, never the operator's environment. A wall-clock check keeps at least 2x headroom and still fails when the work runs serially. An idempotency test pins two different instants. Checks that compare database files byte for byte hold one connection open. Test data uses synthetic ids only, and a guard test fails any id-shaped token outside the synthetic shape. Agent-behaviour evals make live model calls and stay outside the runner. *(tests/test_run_tests.py)*
- **Releases** — a release is `git archive` of its tag; internal files are `export-ignore` in the root `.gitattributes`, and every entry there names a tracked path. Every registry the index names a reader for, and every one runtime code opens by name, ships. *(tests/test_release_archive.py)*

## Output paths

- `DATA_DIR` — root of all client data; required; never guessed from the working directory. Data never belongs inside the code checkout.
