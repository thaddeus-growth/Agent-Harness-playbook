# kit/: the shared skill pack kit

The generic half of an agent skill pack, distilled from a skill pack that ran in production: the human gate, protected human tables, the write guard and its dry-run executor, closed message registries, the `--json` contract, the verb table and its dispatcher, doctor, story checks, and the guards and test runner every skill pack holds itself to. A skill pack adds only its domain: its pulls, ingests, reports, registries and the hooks the kit calls.

Stdlib only, Python 3.11 or newer. Version: [VERSION](VERSION). Fingerprint: [MANIFEST.sha256](MANIFEST.sha256).

## How a skill pack uses it

A skill pack never imports the kit from the playbook. It vendors a plain copy:

```
python3 kit/tools/vendor.py --skill-pack ../acme-skill-pack            # kit and console
python3 kit/tools/vendor.py --skill-pack ../acme-skill-pack --kit      # the kit only
python3 kit/tools/vendor.py --skill-pack ../acme-skill-pack --dry-run  # what would change
```

- The kit lands in `<skill-pack>/scripts/kit/` (`[skill_pack].scripts_dir`, default `scripts`), so `scripts/` on `sys.path` makes `import kit` work and `git archive` ships it. The console lands in `<skill-pack>/console/`, with a `VERSION` file naming the kit version it was vendored with.
- `tests/` stays behind; every copy gets its own `MANIFEST.sha256`. A second run with nothing new changes nothing and says so.
- `scaffold/new_skill_pack.py` does this for a new skill pack, together with the templates, and generates the skill pack's first tests.

### Packs

Every module belongs to one pack in [packs.tsv](packs.tsv), and a skill pack vendors only the packs it names in `skill-pack.toml`:

```toml
[kit]
packs = ["compliance"]      # base and testkit always come; no [kit] packs = every pack
```

| Pack | What it holds | Who takes it |
| --- | --- | --- |
| `base` | the contract, messages, clock, paths; the database and the human tables, the gate, facts, decisions, pending, the queue, execute and the write guard; the CLI, verbs, doctor, env, auth, runner, stories | every skill pack |
| `compliance` | `copylint`, `claimscope`, `phrasebook` | a skill pack that writes ad copy |
| `generation` | `takes`, `prices`, `preflight`, `consent` | a skill pack that pays for AI generation or uses a real person's likeness |
| `data` | `retry` | a skill pack that pulls rate-limited APIs |
| `workbook` | `workbook`, `workbook_map`, `workbook_store`, `file_gate`, `workbook_patch` | a skill pack that runs beside a client's own spreadsheet (reads it in, writes its results into a copy) |
| `learning` | `patternstore`, `pathweights`, `questions` | a skill pack that learns from its outputs' outcomes, per client ([docs/pattern-store.md](../docs/pattern-store.md), [docs/path-weights.md](../docs/path-weights.md)) |
| `pages` | `pagestyle` | a skill pack that writes pages a person reads to decide what to make next, and why ([docs/page-style.md](../docs/page-style.md)) |
| `autonomy` | `autonomy` | a skill pack that writes into someone else's system with less than a code per write: a scoped approval or an owner-switched mode ([docs/autonomy.md](../docs/autonomy.md)) |
| `testkit` | `guards/`, `testing/`, `tools/` | every skill pack's own tests (keep it in `[release].internal`) |

A module imports only `base` and its own pack, so any choice of packs imports cleanly (`tests/test_packs.py`). A module's message codes (`message_codes.d/<module>.tsv`) travel with it. `vendor.py` refuses to leave out a module the skill pack's own code imports: it names the file and the pack to add. `scaffold/new_skill_pack.py --packs data,compliance` writes the line for a new skill pack.

The skill pack declares itself once, in `skill-pack.toml` at its root ([templates/skill-pack.toml](../templates/skill-pack.toml)); every kit module reads names from it through `kit.config` and hard-codes none:

| Section | What the kit reads |
| --- | --- |
| `[skill_pack]` | `name`, `cli` (the word in every `next` command), `env_prefix` (`<P>_DATA_DIR`, `<P>_DB`, `<P>_CONFIRM_CODE_SECRET`, `<P>_ALLOW_WRITES`, `<P>_KILL`, `<P>_AUTH_ENV_PATHS`, `<P>_TOKEN_SOURCE`), `db_file`, `scripts_dir`, `languages` (one `meaning_<lang>` per message row), `markets` (the closed set; `[]` = no partition), `home_env_file` |
| `[ssot]` | `dir`, and the registries: `message_codes` (plus `<dir>/message_codes.d/*.tsv`), `fact_keys`, `decision_keys`, `constants`, `story_checks` |
| `[release]` | `internal` (export-ignore), `must_ship`, `runtime` |
| `[layers]` | `pull`, `ingest`, `compute`, `writer`, `writer_importers`, `clients`, `spawn_allowed`, `write_verbs`, `exempt` |
| `[contract]`, `[auth]`, `[env]`, `[guards.*]` | optional: `no_db_next`, prose keys and data labels; token-command fields; default credential prefixes and `never_from_file` (the skill pack's own names an env file may not set, e.g. a test seam); the guards' parameters (`[guards.json_contract] args`: sample arguments per read verb) |

The skill pack lists its verbs in `scripts/verbs.py` (`VERBS = [Verb(words, script, kind, …)]`, kinds `read`, `ingest`, `human`, `gated`, `external`, `dev`), and its CLI is three lines over `kit.cli.Dispatcher`. A verb may add `examples=("facts list --market US --json", …)` (command lines without the cli name, starting with the verb's words) and `keys=("facts", …)` (the top-level keys of its `--json` document): its own `--help` ends with them, `verbs --json` lists them, and doctor names the non-dev verbs without examples at level info. A script named after its whole verb (`compute_sales.py` for `compute sales`) is that verb; any other script is a family, and the words after the first are its sub-verb (`facts confirm X` runs `facts.py confirm X`). A read verb that needs an argument gets a sample one for the `--json` contract test: `[guards.json_contract] args = {"facts get" = ["unit_cost"]}`. The fake skill pack in [tests/fake_skill_pack/](tests/fake_skill_pack/) is a whole small example: a verb table, a CLI, facts, decisions, pending, a queue fed by a compute, execute with an empty allowlist.

## The modules

Each module's docstring says what it guards and names its test; this is the map.

| Module | What it guards | Test |
| --- | --- | --- |
| `config.py` | A skill pack declares itself once (`skill-pack.toml`); a bad declaration fails at load; which skill pack is bound; `use()` clears every cache | `test_config.py` |
| `messages.py` | Every English message is a `Msg` with a code from a closed registry (kit base + fragments + the skill pack's registry and fragments); a code defined twice anywhere is refused; one placement rule for codes | `test_messages.py` |
| `contract.py` | One stdout document under `--json`, failures included; `SkillPackError`; coded usage errors (`Parser`); read verbs never create the DB; one `meta` shape | `test_contract.py` |
| `dates.py` | One clock every layer reads, patchable in one place | `test_dates.py`, `test_clock.py` |
| `atomic.py`, `single_instance.py` | A half-written file is never seen (the temp file is fsynced before the rename); a second scheduled run skips | `test_atomic.py` |
| `runner.py` | How one script runs another and reads its one document; a child's failure keeps its code | `test_runner.py` |
| `raw.py` | Raw only grows: write-once files, content-hash imports | `test_raw.py` |
| `paths.py` | The data dir is required and absolute, never the cwd; one resolver for the DB | `test_paths.py` |
| `env.py` | One env chain (process env → home file → `<DATA_DIR>/.env`), never the cwd; parsed, never sourced; a gate secret, the gate's test seam, the binding and loader variables are never loaded from a file (doctor names such a line) | `test_env.py` |
| `auth.py` | Where a token comes from (`env` or a host `command`); expiry; a token never printed | `test_auth.py` |
| `retry.py` | Reads only are retried; `Retry-After` honoured; `call` waits at least the quota's refill period, one budget per endpoint, and `GaveUp` carries the last reason | `test_retry.py` |
| `db.py`, `schema_base.py` | Human tables are never dropped, never migrated, never shrink; triggers in the file; backups before a rebuild; a lossy rebuild and a newer file are refused | `test_db.py` |
| `human.py` | The gate: a retype at `/dev/tty`, or a relayed one-time code bound to exactly what was shown; relay audit (who, when, and for a write that leaves the skill pack on a chat yes, the person's own words); no bypass flag | `test_human.py` |
| `market.py` | A market is always named, never assumed; the declaration is the one door | `test_market.py` |
| `registry.py` | Fact keys, decision keys and thresholds are closed; values checked where they are typed | `test_registry.py` |
| `facts.py`, `decisions.py` | The one write path for each human table: pending until a person confirms, history for every change; a client file's values are confirmed with that file's gate (`plan_file`, `apply_file`) | `test_facts.py`, `test_decisions.py` |
| `queue.py`, `execute.py`, `write_guard.py` | Nothing goes out unapproved; an approval goes stale when its basis moves; a dry run by default; kill switch, opt-in and an exact-shape allowlist; an unknown outcome blocks its target | `test_queue.py`, `test_execute.py`, `test_write_guard.py` |
| `takes.py` | Paid generation: every AI call planned, priced (unpriced = refused), capped, approved at the gate for exactly the plan, then kept once under the sha of its request (a re-roll is a new request, never an overwrite); a broken result is never cached; spend is an append-only ledger with who approved; an optional owner-declared standing allowance | `test_takes.py` |
| `prices.py` | What a paid vendor call costs, from an owner TSV: a blank price is unknown (never zero), a retired row is not found, a price counts as confirmed only with the gate's trace for that very value (a hand-typed status or a later edit reads as pending), and an estimate says so; prices read off a vendor's catalog become pending proposals that never overwrite a confirmed row; confirm is a retype of the value at the gate, lowering trust needs none. `takes.py` keeps the standing allowance off any plan with an unconfirmed price | `test_prices.py`, `test_takes.py` |
| `preflight.py` | A paid request is checked offline against the vendor's own published input schema (types, enum options, ranges, lengths, accepted media types) before it is sent; an unknown schema type is itself a problem | `test_preflight.py` |
| `consent.py` | A real person's face, voice, name, words or footage is used only under a record a person confirmed through the gate, bound to the record's content hash; any later edit needs a new confirmation; revoke is free and final for earlier confirmations | `test_consent.py` |
| `copylint.py` | Copy compliance before spend: banned terms per category (literal or regex) and product facts said consistently (CJK numerals normalised); a finding from an unconfirmed rule says so; a broken rule is refused, never skipped | `test_copylint.py` |
| `claimscope.py` | Copy names only what an approved document allows: a claims record (the document's wording, the claims it allows) counts only when a person confirmed it at the gate for its exact content; a lexicon of claims (must be inside the scope) and `always` phrases (a finding whatever the scope), longest match first; a draft rule or record says so; a waiver is a person retyping the finding's words, bound to rule, place and words; every quoted rejection is a recall test, every approved creative a false-alarm test | `test_claimscope.py` |
| `phrasebook.py` | Every phrase a subject's copy has said or may say, approved, pending or rejected: checked on the way in, one row whatever the punctuation (with the skill pack's speech fix), a rejected phrase refused inside any later line, an approval only at the gate, only for its exact text and only with no open error | `test_phrasebook.py` |
| `workbook.py` | A client's .xlsx read as it is, stdlib only: cells with kind, text, number, format, ISO date (1900 or 1904), formula text beside its saved value (never recalculated), links, comment marks, hidden rows and sheets, merges, validations, core properties and every part's sha256; a DOCTYPE, an ENTITY or an oversize part is refused before it is parsed | `test_workbook.py` |
| `workbook_map.py` | The client's map (`tables.tsv`, `columns.tsv`, `values.tsv` for the values read from single cells, and `cells.tsv` for the cells a skill pack writes outside a table's rows, kept with the client's data): where each table is, what each column means; an outgoing column has one owner, every field and fact key is the skill pack's, only confirmed rows are read; a map without `values.tsv` keeps its version | `test_workbook_map.py` |
| `file_gate.py` | One human gate per client file: the map rows and the values a staged bundle confirms pass one retype or one relayed code together, bound to both bundles' versions and every value; one console ask per file (`pending`'s `staged=` hook) built from the same plan as the gate; every value checked like `facts set` before the gate | `test_file_gate.py` |
| `workbook_patch.py` | Cells set in a copy of the client's xlsx: every part it does not set byte for byte, a sheet edited as text, full recalculation on open, the same bytes for the same inputs; a formula, a value the client entered (not the skill pack's last copy) or a merged cell is never set, listed by ref; places a skill pack's results by the map (`table_sets`: a table's `out` columns by key, new columns after the header block; `cell_sets`: the map's `cells.tsv`) | `test_workbook_patch.py` |
| `workbook_store.py` | The generic store: eight cache tables naming no business field; tables found by anchor and stop, never by coordinates; rows keyed by the map; the change record between imports (the write-back conflict list); typed records with their source cell for a projection; personal columns and e-mail addresses masked in every output | `test_workbook_store.py` |
| `patternstore.py` | A per-client pattern store: evidence rows of closed-vocabulary ids and counts (`validate()` never raises, refuses free text, unknown keys, wrong types, a trailing newline, an id that does not recompute); sources never pooled, presence-only sources carry no outcome; a coded field counts only when two coders agree at the floor (0.70); beta-binomial shrinkage toward the store's own mean (prior worth 5-10 tests) with intervals and tied ranks, a rate never without its counts; append-only tables for kit.db, an export superseding what it no longer makes | `test_patternstore.py` |
| `pathweights.py` | Path weights for variants that come back with spend and conversions: each leaf's Gamma-Poisson posterior borrowed down the partition leave-one-out (fractional conversions; an optional prior factor, clipped); five states with their reasons, only settled numbers judging; a close only ever proposed (a person confirms, the skill pack records); the next batch by coverage when cold, by top-two Thompson when warm, with an explore share, half a batch per group, exclusive keys and a seed from the data; same-tag twins as a clue; past results (units the plan did not make) read as a gap signal against their own pool's rate and never as a weight; every threshold in `DEFAULTS`, a bad one refused | `test_pathweights.py` |
| `questions.py` | Open questions that never hold the plan: each unanswered one names the data that can answer it (`by_data`, from the skill pack's closed list, or `ask_only`) and what the plan does meanwhile (`meanwhile`, an action, never a wait); `problems()` is the check a skill pack's tests run, `groups()` sorts them for a page, `readback()` lists the ones whose data the skill pack now holds and has not been read for (a new version of the data prompts again); `self_test()` plants each break | `test_questions.py` |
| `pagestyle.py` | One look for the pages that guide making: a state as colour, word and icon (five states, colour-blind safe, its text 4.5:1 on its fill in light and dark, nothing dimming it); a status bar and number tiles first; one decision with one copy button and its hint, commands, flags and ids only there and in folds (`data-zone`); at most three warnings on a page, the rest one line that names the owner's fold; a node map with one cell per item, none past its edge; cards that unfold row by row, with if it works / if it does not; the owner's fold, folded and last; one document (doctype, lang, a CSP that allows no network at all, images only inline, the page data strict JSON and escaped, filled in one pass, a script that holds no word); every word the skill pack's (`Style(words, lang)`, English and Simplified Chinese ready); `problems(page)`, the check a skill pack's tests run on every page it writes, one line for each rule a page can break | `test_pagestyle.py` |
| `autonomy.py` | The autonomy levels' shared mechanics, pure over the data a skill pack passes ([docs/autonomy.md](../docs/autonomy.md)): the tier registry read under the skill pack's own column names and checked (a tier-B or auto row names its undo, an auto row's undo is a registered code-free undo); the owner's switch, one fact `YYYY-MM-DD:ID,…`, on only while its latest history row is a person's confirm, clamped to `max_days`, its ids frozen (a registry edit narrows, never widens), an agent's write only ever off; a scoped approval's verdict in a fixed order; the mode's verdict, a cap or a trip a stop with no code issued; caps counted from the archive's manifests, a client's value only lower; the manifest's `gate` and the reason's tail per proof (`approval`, `auto`, `undo`); a code-free undo of an auto write while the live object is as it left it; the digest; the lock a covered write holds | `test_autonomy.py` |
| `pending.py` | Everything waiting on a person, as ready console asks whose gate is the subject the skill pack binds; with several markets the gate names its market (`--market=M`), and a skill pack's staged client files are one ask each | `test_pending.py` |
| `stories.py` | Each story's check runs read verbs only and reports pass, fail or skip with a coded reason | `test_stories.py` |
| `verbs.py`, `cli.py` | Every verb is declared with its kind; an unlisted verb is refused; the dispatcher's env chain, data-dir guard, `--` rule and exit code; a verb's `--help` ends with its declared examples and JSON keys | `test_cli.py` |
| `doctor.py` | An install says where every value came from and what to fix; `writes: off` is healthy; `--strict` makes a warning fatal; the cwd in another checkout of the same skill pack than the one that runs warns | `test_doctor.py` |
| `guards/` | The structural rules a skill pack's own tests call: the ssot index, `ssot/` written by the builder only (no skill pack code writes under it; a client's value goes to its database), the release archive, the changelog (whole, and SKILL.md's version is its newest release), layering, the `--json` contract (every verb of every kind has a case: `check_verbs`), adapter boundaries, vendored-copy drift, one clock, the ROADMAP.md format (seven sections, dated rows with ids, what each wait unblocks), agent-eval cases (each names the SKILL.md rule it tests, carries the forbidden-verbs grader generated from the verb table, has an ablation row) | `test_guards.py`, `test_verb_cases.py`, `test_clock.py`, `test_evals.py`, `test_roadmap.py` |
| `testing/` | The test convention: `check()`/`finish()`, the RESULT-gated runner, the sandbox env; `xlsx.py`, a small deterministic xlsx writer so a test builds the workbook it reads | `test_check.py`, `test_run_tests.py` |
| `testing/suites.py` | The thirteen day-one tests every new skill pack is generated with, as library calls (runner, ssot, layering, boundary, `--json` contract, human tables, gate, release, changelog, roadmap, drift, clock, Never table) | `test_suites.py` |
| `tools/` | `manifest.py` (the fingerprint), `vendor.py` (the plain copy, only the packs a skill pack takes), `packs.py` (the pack table and the import reader), `skill_upload.py` (the copy of SKILL.md claude.ai and the Skills API accept: `name`, `description`, `version` and `type` under `metadata`, the same body and the files it links to, as a folder and a zip; a name or description the upload refuses is refused, never cut), `leak_guard.py` with `pre-push.sample` (a push whose added lines, paths or messages hold a client term is refused; the term lists stay outside the repository, and none = refused; [BUILD.md](../BUILD.md), B1) | `test_manifest.py`, `test_vendor.py`, `test_packs.py`, `test_skill_upload.py`, `test_leak_guard.py` |

## What each guard paid for

Real incidents from the source project, kept with the module that now prevents them. The rows above say what a module guards; these say why it does.

| Module | Paid for |
| --- | --- |
| `db.py` | Client facts were wiped once. A rolling window moved between a schema change and the ingest, so raw held as many days as the table, but later ones: a count check passed and the rebuild dropped the oldest days (so the check compares the set of days). |
| `human.py` | Two rounds. The first guard only checked that stdin was a terminal, so an agent wrapped the call in a pseudo-terminal, confirmed live values and signed them as the owner. Then a relayed code approved a different pair of items than the one shown, confirmed another entity's value, and worked twice inside its window. |
| `messages.py` | Codes were added to a live skill pack in one change without removing a key: about 70 at once. |
| `guards/` (`check_verbs`) | The gate and write verbs sat outside the contract test, so about 40 of their refusals reached the owner's page as "unclassified". |
| `runner.py` | Three write verbs reported "no output" because they read stderr, while the reason was on stdout. |
| `retry.py` | Days were lost as rate-limit gaps: the exponential guess retried before the quota refilled and the tries ran out; a throttled day became a day with no data instead of a recorded gap. |
| `takes.py` | A text-to-speech voice that was not installed wrote a 0.01 s file and exited 0; the empty take was cached and reused by every build. Re-rolls had to be new requests so an approved take could never be lost. |
| `prices.py` | A price read off a vendor's page was its member price (0.45), not the list price (0.50), and about 30 rows proposed from the vendor's catalog waited on the owner, while the skill pack read every row as a price and approved spend against prices nobody had confirmed. |
| `preflight.py` | Two refusals on a generative vendor's first live day, both in its published schema: a wav sent as `audio/x-wav` (it lists `audio/wav`), and a number sent for an enum whose one option is the string `"0"`. |
| `consent.py` | An agent set a real person's consent to `confirmed` by editing the record on the owner's word in chat, and the check accepted it. |
| `copylint.py` | The client's own reference ad gave a dosage two ways (three times a day, and once a day); a banned-word list alone could not see it. |
| `claimscope.py` | One OTC product's creatives were refused 20 times in one afternoon for naming symptoms its approved indication does not: ordinary words a banned list cannot catch. The checker then caught 10 of 10 of the reviewer's quoted phrases while the approved wording passed. And an agent once hand-set a record's status (`consent.py`), so a record counts only through the gate. |
| `phrasebook.py` | 26 rejected creatives turned out to be 5 scripts rearranged: one bad sentence was rejected again and again. |
| `guards/roadmap.py` | One roadmap was prose in its own layout, another a memory note that did not travel to the next machine; neither could be searched for what waited on the owner. The video-ads skill pack's format, held by its own test since 2026-10-01 and adopted by a second skill pack, is the one kept. |
| `guards/evals.py` | An agent-behaviour suite kept its forbidden-command pattern by hand, a long regular expression that began to miss a verb the moment one was added; and 4 of its 9 first cases passed with their rule cut out of the skill, because the model already behaved that way, so they measured nothing. |
| `atomic.py`, `single_instance.py` | A crash mid-write left a truncated raw file that the next ingest read as data; an hourly run outlasted the hour and two drains raced. |

The whole gate protocol, the kit and the console together, is proved end to end by `test_e2e_shop.py`: the fake skill pack's own CLI, the real `console/ask.py` and `console/serve.py`, a relayed code that works once, a queue approval and a refused `--apply`.

## The rules

Each rule has a test that fails when it breaks.

- **Stdlib only.** Vendoring stays a plain copy, and a skill pack installs nothing for the kit. *(tests/test_kit_rules.py)*
- **No domain words.** The kit names no platform, client or product (it still names `market` as its partition, a known leftover of the source project); everything domain-specific is a parameter in `skill-pack.toml` or a hook. *(tests/test_kit_rules.py)*
- **Never imports the console, and the console never imports the kit.** They meet only in JSON: the gate challenge (`confirm_code_required`, `subject`) and the console's ask format. *(tests/test_kit_rules.py; tests/test_e2e_shop.py proves they still meet)*
- **Every message coded.** A module owns one fragment, `message_codes.d/<module>.tsv`, en, zh (Simplified) and zh-Hant ([docs/languages.md](../docs/languages.md)), and emits every code in it. *(each module's test, `check_registry_closed`)*
- **Every test file** runs alone (`python3 kit/tests/test_x.py`), uses `kit.testing.check`, and prints `RESULT: N passed`; `python3 kit/tests/run.py [filter]` fails a file without that line.

## Upgrades and drift

- **A fix goes upstream first.** Change the kit here, with its test; bump [VERSION](VERSION) (a patch for a fix, a minor for a new module or hook, a major when a skill pack must change); regenerate the fingerprint with `python3 kit/tools/manifest.py kit`. `tests/test_manifest.py` fails while `MANIFEST.sha256` is stale, so the fingerprint always names what this version is.
- **Then re-vendor** each skill pack: `python3 kit/tools/vendor.py --skill-pack PATH` (or `scaffold/new_skill_pack.py --update-kit`), run the skill pack's suite, and merge it as its own change. The copy's `MANIFEST.sha256` equals this one.
- **Never edit a vendored copy.** A skill pack's `tests/test_kit_drift.py` (`kit.guards.drift`) fails on any changed, added or removed file under `scripts/kit/` or `console/`, and when the two copies' versions differ.
