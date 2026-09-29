# kit/: the shared harness kit

The generic half of an agent harness, distilled from a harness that ran in production: the human gate, protected human tables, the write guard and its dry-run executor, closed message registries, the `--json` contract, the verb table and its dispatcher, doctor, story checks, and the guards and test runner every harness holds itself to. A harness adds only its domain: its pulls, ingests, reports, registries and the hooks the kit calls.

Stdlib only, Python 3.11 or newer. Version: [VERSION](VERSION). Fingerprint: [MANIFEST.sha256](MANIFEST.sha256).

## How a harness uses it

A harness never imports the kit from the playbook. It vendors a plain copy:

```
python3 kit/tools/vendor.py --harness ../acme-harness            # kit and console
python3 kit/tools/vendor.py --harness ../acme-harness --kit      # the kit only
python3 kit/tools/vendor.py --harness ../acme-harness --dry-run  # what would change
```

- The kit lands in `<harness>/scripts/kit/` (`[harness].scripts_dir`, default `scripts`), so `scripts/` on `sys.path` makes `import kit` work and `git archive` ships it. The console lands in `<harness>/console/`, with a `VERSION` file naming the kit version it was vendored with.
- `tests/` stays behind; every copy gets its own `MANIFEST.sha256`. A second run with nothing new changes nothing and says so.
- `scaffold/new_harness.py` does this for a new harness, together with the templates, and generates the harness's first tests.

The harness declares itself once, in `harness.toml` at its root ([templates/harness.toml](../templates/harness.toml)); every kit module reads names from it through `kit.config` and hard-codes none:

| Section | What the kit reads |
| --- | --- |
| `[harness]` | `name`, `cli` (the word in every `next` command), `env_prefix` (`<P>_DATA_DIR`, `<P>_DB`, `<P>_CONFIRM_CODE_SECRET`, `<P>_ALLOW_WRITES`, `<P>_KILL`, `<P>_AUTH_ENV_PATHS`, `<P>_TOKEN_SOURCE`), `db_file`, `scripts_dir`, `languages` (one `meaning_<lang>` per message row), `markets` (the closed set; `[]` = no partition), `home_env_file` |
| `[ssot]` | `dir`, and the registries: `message_codes` (plus `<dir>/message_codes.d/*.tsv`), `fact_keys`, `decision_keys`, `constants`, `story_checks` |
| `[release]` | `internal` (export-ignore), `must_ship`, `runtime` |
| `[layers]` | `pull`, `ingest`, `compute`, `writer`, `writer_importers`, `clients`, `spawn_allowed`, `write_verbs`, `exempt` |
| `[contract]`, `[auth]`, `[env]`, `[guards.*]` | optional: `no_db_next`, prose keys and data labels; token-command fields; default credential prefixes; the guards' parameters (`[guards.json_contract] args`: sample arguments per read verb) |

The harness lists its verbs in `scripts/verbs.py` (`VERBS = [Verb(words, script, kind, …)]`, kinds `read`, `ingest`, `human`, `gated`, `external`, `dev`), and its CLI is three lines over `kit.cli.Dispatcher`. A script named after its whole verb (`compute_sales.py` for `compute sales`) is that verb; any other script is a family, and the words after the first are its sub-verb (`facts confirm X` runs `facts.py confirm X`). A read verb that needs an argument gets a sample one for the `--json` contract test: `[guards.json_contract] args = {"facts get" = ["unit_cost"]}`. The fake harness in [tests/fake_harness/](tests/fake_harness/) is a whole small example: a verb table, a CLI, facts, decisions, pending, a queue fed by a compute, execute with an empty allowlist.

## The modules

Each module's docstring says what it guards and names its test; this is the map.

| Module | What it guards | Test |
| --- | --- | --- |
| `config.py` | A harness declares itself once (`harness.toml`); a bad declaration fails at load; which harness is bound; `use()` clears every cache | `test_config.py` |
| `messages.py` | Every English message is a `Msg` with a code from a closed registry (kit base + fragments + the harness's registry and fragments); a code defined twice anywhere is refused; one placement rule for codes | `test_messages.py` |
| `contract.py` | One stdout document under `--json`, failures included; `HarnessError`; coded usage errors (`Parser`); read verbs never create the DB; one `meta` shape | `test_contract.py` |
| `dates.py` | One clock every layer reads, patchable in one place | `test_dates.py`, `test_clock.py` |
| `atomic.py`, `single_instance.py` | A half-written file is never seen (the temp file is fsynced before the rename); a second scheduled run skips | `test_atomic.py` |
| `runner.py` | How one script runs another and reads its one document; a child's failure keeps its code | `test_runner.py` |
| `raw.py` | Raw only grows: write-once files, content-hash imports | `test_raw.py` |
| `pull.py` | A pull is merged into raw by natural key (newer wins per key, per-row `pulledAt`, raw never shrinks, duplicate keys refused); an unreadable raw file is moved aside; a long pull is planned in chunks, saved after each step, a chunk is requested again if a newer pull landed; every gap goes in an append-only ledger with its reason | `test_pull.py` |
| `paths.py` | The data dir is required and absolute, never the cwd; one resolver for the DB | `test_paths.py` |
| `env.py` | One env chain (process env → home file → `<DATA_DIR>/.env`), never the cwd; parsed, never sourced | `test_env.py` |
| `auth.py` | Where a token comes from (`env` or a host `command`); expiry; a token never printed | `test_auth.py` |
| `retry.py` | Reads only are retried; `Retry-After` honoured; `call` waits at least the quota's refill period, one budget per endpoint, and `GaveUp` carries the last reason | `test_retry.py` |
| `db.py`, `schema_base.py` | Human tables are never dropped, never migrated, never shrink; triggers in the file; backups before a rebuild; a lossy rebuild and a newer file are refused | `test_db.py` |
| `human.py` | The gate: a retype at `/dev/tty`, or a relayed one-time code bound to exactly what was shown; relay audit; no bypass flag | `test_human.py` |
| `market.py` | A market is always named, never assumed; the declaration is the one door | `test_market.py` |
| `registry.py` | Fact keys, decision keys and thresholds are closed; values checked where they are typed | `test_registry.py` |
| `facts.py`, `decisions.py` | The one write path for each human table: pending until a person confirms, history for every change | `test_facts.py`, `test_decisions.py` |
| `queue.py`, `execute.py`, `write_guard.py` | Nothing goes out unapproved; an approval goes stale when its basis moves; a dry run by default; kill switch, opt-in and an exact-shape allowlist; an unknown outcome blocks its target | `test_queue.py`, `test_execute.py`, `test_write_guard.py` |
| `pending.py` | Everything waiting on a person, as ready console asks whose gate is the subject the harness binds | `test_pending.py` |
| `stories.py` | Each story's check runs read verbs only and reports pass, fail or skip with a coded reason | `test_stories.py` |
| `verbs.py`, `cli.py` | Every verb is declared with its kind; an unlisted verb is refused; the dispatcher's env chain, data-dir guard, `--` rule and exit code | `test_cli.py` |
| `doctor.py` | An install says where every value came from and what to fix; `writes: off` is healthy; `--strict` makes a warning fatal | `test_doctor.py` |
| `guards/` | The structural rules a harness's own tests call: the ssot index, the release archive, layering, the `--json` contract (every verb of every kind has a case: `check_verbs`), adapter boundaries, vendored-copy drift, one clock | `test_guards.py`, `test_verb_cases.py`, `test_clock.py` |
| `testing/` | The test convention: `check()`/`finish()`, the RESULT-gated runner, the sandbox env | `test_check.py`, `test_run_tests.py` |
| `testing/suites.py` | The ten day-one tests every new harness is generated with, as library calls (runner, ssot, layering, boundary, `--json` contract, human tables, gate, release, drift, clock) | `test_suites.py` |
| `tools/` | `manifest.py` (the fingerprint), `vendor.py` (the plain copy) | `test_manifest.py`, `test_vendor.py` |

## What each guard paid for

Real incidents from the source project, kept with the module that now prevents them. The rows above say what a module guards; these say why it does.

| Module | Paid for |
| --- | --- |
| `db.py` | Client facts were wiped once. A rolling window moved between a schema change and the ingest, so raw held as many days as the table, but later ones: a count check passed and the rebuild dropped the oldest days (so the check compares the set of days). |
| `human.py` | Two rounds. The first guard only checked that stdin was a terminal, so an agent wrapped the call in a pseudo-terminal, confirmed live values and signed them as the owner. Then a relayed code approved a different pair of items than the one shown, confirmed another entity's value, and worked twice inside its window. |
| `messages.py` | Codes were added to a live harness in one change without removing a key: about 70 at once. |
| `guards/` (`check_verbs`) | The gate and write verbs sat outside the contract test, so about 40 of their refusals reached the owner's page as "unclassified". |
| `runner.py` | Three write verbs reported "no output" because they read stderr, while the reason was on stdout. |
| `pull.py` | Each pull replaced raw and the platform keeps only a few months; a late resumed report put older numbers back; an unreadable raw file was overwritten by one pull's rows; a long pull killed halfway started over and the quota ran out again; a run that cleared the last run's gaps lost the list a gaps-only pull needed. |
| `retry.py` | Days were lost as rate-limit gaps: the exponential guess retried before the quota refilled and the tries ran out; a throttled day became a day with no data instead of a recorded gap. |
| `atomic.py`, `single_instance.py` | A crash mid-write left a truncated raw file that the next ingest read as data; an hourly run outlasted the hour and two drains raced. |

The whole gate protocol, the kit and the console together, is proved end to end by `test_e2e_shop.py`: the fake harness's own CLI, the real `console/ask.py` and `console/serve.py`, a relayed code that works once, a queue approval and a refused `--apply`.

## The rules

Each rule has a test that fails when it breaks.

- **Stdlib only.** Vendoring stays a plain copy, and a harness installs nothing for the kit. *(tests/test_kit_rules.py)*
- **No domain words.** The kit names no platform, client, market or product; everything domain-specific is a parameter in `harness.toml` or a hook. *(tests/test_kit_rules.py)*
- **Never imports the console, and the console never imports the kit.** They meet only in JSON: the gate challenge (`confirm_code_required`, `subject`) and the console's ask format. *(tests/test_kit_rules.py; tests/test_e2e_shop.py proves they still meet)*
- **Every message coded.** A module owns one fragment, `message_codes.d/<module>.tsv`, en and zh, and emits every code in it. *(each module's test, `check_registry_closed`)*
- **Every test file** runs alone (`python3 kit/tests/test_x.py`), uses `kit.testing.check`, and prints `RESULT: N passed`; `python3 kit/tests/run.py [filter]` fails a file without that line.

## Upgrades and drift

- **A fix goes upstream first.** Change the kit here, with its test; bump [VERSION](VERSION) (a patch for a fix, a minor for a new module or hook, a major when a harness must change); regenerate the fingerprint with `python3 kit/tools/manifest.py kit`. `tests/test_manifest.py` fails while `MANIFEST.sha256` is stale, so the fingerprint always names what this version is.
- **Then re-vendor** each harness: `python3 kit/tools/vendor.py --harness PATH` (or `scaffold/new_harness.py --update-kit`), run the harness's suite, and merge it as its own change. The copy's `MANIFEST.sha256` equals this one.
- **Never edit a vendored copy.** A harness's `tests/test_kit_drift.py` (`kit.guards.drift`) fails on any changed, added or removed file under `scripts/kit/` or `console/`, and when the two copies' versions differ.
