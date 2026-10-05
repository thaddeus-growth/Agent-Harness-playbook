# The kit, the build tools and the templates

Moved from the playbook's [README](../README.md) unchanged; the README keeps the overview.

## Build the next harness faster

The checklist above, already built: a new harness starts from these instead of from a blank folder. [BUILD.md](../BUILD.md) says which step uses which.

- [`kit/`](../kit/): the shared harness modules (the gate, human tables, the write guard, message codes, the `--json` contract, the verb table and dispatcher, doctor, the guards), vendored into each harness as `scripts/kit/`; each module has a tier ([the admission rule](#the-admission-rule), [`kit-tiers.tsv`](../kit-tiers.tsv)). *Test:* `python3 kit/tests/run.py`, with `kit/tests/test_e2e_shop.py` proving the gate together with the console.
- [`hosts/zylos/`](../hosts/zylos/): the worked example of a host adapter (write one per host), driven by one manifest: install, configure, scheduled tasks and the console as a service. *Test:* `python3 hosts/zylos/tests/run.py`.
- [`scaffold/`](../scaffold/): `scaffold/new_harness.py` renders the templates, writes the skeleton, vendors the kit and the console, and generates the ten day-one tests; the result is green before its first feature. *Test:* `python3 scaffold/tests/run.py` (`scaffold/tests/test_new_harness.py`).
- [`build/`](../build/): the build-time tools: check meeting intakes, turn items into console asks, apply the answers. *Test:* `python3 build/tests/run.py`.
- [BUILD.md](../BUILD.md): the step-by-step workflow, B0 to B10: what each step produces, what fans out, who signs which gate. *Test:* `python3 tests/run.py` (`tests/test_docs_build.py`).
- [`skills/build-harness/`](../skills/build-harness/SKILL.md): the skill an agent loads to follow BUILD.md from the files alone. *Test:* `tests/test_docs_build.py` holds it to the same steps.

**Check the whole playbook with one command.** [`run_all.py`](../run_all.py) runs every test suite in the repository (the ones above, the console's, the templates' self-test, the workflow templates' and the golden-diff tool's), one line each, and ends with one `RESULT:` line; it exits 1 if any suite fails. A suite that prints no `RESULT:` line, has gone missing or lacks its tool (`node` for the Zylos adapter, `git`) fails and is never skipped; every `run.py` in the tree is a suite or is listed as a template, so a new one cannot be left out. `python3 run_all.py --list` names the suites and `python3 run_all.py kit console` runs some. [CI](../.github/workflows/ci.yml) runs `python3 run_all.py` on every push and pull request. *Test:* `python3 tests/run.py` (`tests/test_run_all.py`).

### The admission rule

Every module in [`kit/`](../kit/) has a tier. **It is core only when at least two independent harnesses use it in production**, in code merged to their main branches. A new module is added as extra; when a second harness needs the same thing, its row in [`kit-tiers.tsv`](../kit-tiers.tsv) gets that second consumer and the module moves up. Nothing keeps an extra module out of `kit/`; what reaches a harness is decided by packs, not tiers (below). [`kit/tests/test_tiers.py`](../kit/tests/test_tiers.py) refuses a core row with fewer than two harnesses using the module in production, an extra row with more than one consumer, a consumer name or kind outside the lists below, evidence that names a file of this repository that does not use the module, a module with no row, a row for a module that is gone, and a core or stack module that imports a tier above its own (by the import graph, lazy imports included).

- **core**: two or more independent consumers, and imports only core. Here "core" is this tier alone: the "Core" of [the reuse map](channels.md#reusing-the-harness-for-other-channels) is everything that names no channel, which takes in the stack and extra modules and the two folders outside `kit/` (the console and the Zylos adapter).
- **stack**: the human-table stack: what only a harness with protected human tables and a write path needs (the database, the gate, facts, decisions, the queue, the write guard, the registry reader, the raw store). Defined by what it is, not by a count: several of them (for example `db`, `registry` and `raw`) meet the core count and stay stack. Imports core and stack only.
- **extra**: everything else, one consumer or none, and the test kit (`guards/`, `testing/`). May import anything.

**Only merged use counts** (decided 2026-10-05, [RESTRUCTURE.md](../RESTRUCTURE.md) decision 4). A consumer counts as `prod` only for code merged to its main branch. `copylint` was core on the strength of two KOL files on KOL's pushed, unmerged branch claims-and-workspace, which hold a locally changed copy of the kit; it is extra until that branch merges, and it is in the `compliance` pack either way.

Reading the registry: one row per module with its tier, its consumers as `who:kind`, the evidence (a path in the consumer; the video-ads harness's package folder is written `pkg/` whatever it is called there, and "(uncommitted)" marks a use that only one of its two checkouts holds and no commit does), the kit version it first appeared in, and a note. `who` is `video-ads`, `kol` or `rednote` (the three harnesses that vendor the kit), `skeleton` (`scaffold/skeleton/`), `seo`, `outreach` or `source` (other projects that wrote their own version of a job), or `zylos` or `playbook` (this repository). Only `prod` (the production code of `video-ads`, `kol` or `rednote`; no other `who` has it) counts toward core, which needs two of them. The `skeleton` (counted once however many harnesses inherit its files) counts toward the cap on extra, never toward core: the harnesses built from it are not independent of it. `test`, `tool` (a generator, an eval script, a runner) and `fork` (a need, not a use) are shown, never counted. A `skeleton`, `zylos` or `playbook` consumer (files in this repository) is held to its evidence: every file of this repository that the evidence names exists and imports the module (a `(via X)` says which module it imports to reach it; a twin is only a file that exists), and some such file sits under `scaffold/skeleton/` for the skeleton, under `hosts/zylos/` for `zylos`, and elsewhere for `playbook`. The counts come from each consumer's checked-out tree, so a row says when a use is uncommitted or on an unmerged branch, and when it is a use of a copy of the module that the consumer changed locally (the KOL harness's kit copy on its unmerged branch claims-and-workspace, labeled 0.3.1, is a version this playbook never had; its main holds 0.2.0). Extra does not mean safe to delete: several extra modules are reached by every generated harness or by the console integration test, and two are kept for the Zylos adapter. A row that says "no consumer" is a removal candidate for a later decision; nothing has been removed.

**Packs decide what a harness vendors.** Since kit 0.7.0 every module also belongs to one pack ([`kit/packs.tsv`](../kit/packs.tsv)): `base`, `compliance`, `generation`, `data` and `testkit`. A harness names its packs under `[kit] packs` in `harness.toml`, and `kit/tools/vendor.py` copies only those, plus `base` and `testkit` ([kit/README.md](../kit/README.md), "Packs"). A harness without the line still receives every pack, so nothing changed for the three that vendor the kit. Packs replace the tiers once every harness has moved ([RESTRUCTURE.md](../RESTRUCTURE.md), phase 3).

**Why the registry is outside `kit/`.** `kit/` is what harnesses vendor and `kit/MANIFEST.sha256` fingerprints: a change inside it is a new kit version and a re-vendor everywhere. The registry and its test are not vendored, so retiering costs no harness a re-vendor. The price: the module table in [kit/README.md](../kit/README.md) does not show the tier; read the registry.

## Templates

| File | Stage | What it is |
| --- | --- | --- |
| [`templates/meeting-intake.md`](../templates/meeting-intake.md) | 0 | The organizer prompt and its output shape |
| [`templates/decision-rights.md`](../templates/decision-rights.md) | 1 | The three lanes and the risky list |
| [`templates/CODEOWNERS`](../templates/CODEOWNERS) | 1, 10 | Protected paths for the risky list |
| [`templates/ssot/`](../templates/ssot/) | 1–2, 5–7 | Registry index, glossary, user stories and policies (owner file + agent sibling), thresholds, message codes, fact and decision keys, alert rules, story checks |
| [`templates/ci/gitlab-ci.yml`](../templates/ci/gitlab-ci.yml) | 2–3, 10 | The fuller CI to grow into: one pipeline per change, every job with its rules, story id (with a note for GitHub Actions), offline tests with a cache, a check of the Zylos adapter, adapter smoke, secret scan with a placeholder convention; `templates/gitlab-ci.yml` is its short form |
| [`templates/AGENT_INSTRUCTIONS.md`](../templates/AGENT_INSTRUCTIONS.md) | 3 | Invariants-only instructions for the coding agent (a `CLAUDE.md`) |
| [`kit/`](../kit/) | 3, 6, 8 | A module, not a template: the human gate, message codes with their contract test, the child-verb runner, one clock, and the raw and database guards, vendored into a harness by `scaffold/new_harness.py` *(the rules and their tests come from the source project, where the original code ran on real data; the kit has since run inside three harnesses, one of them as a locally changed copy)* |
| [`templates/evals/`](../templates/evals/) | 8 | Agent-behaviour evals: the case layout (`case.yaml`, `prompt.md`, graders), the ablation table, the offline rules `kit/guards/evals.py` holds (the forbidden-verbs pattern is generated from the verb table), and a fixture builder that refuses a checkout holding client data *(untried as copied here: seen on one harness, run only against the toy harness)* |
| [`templates/tests/`](../templates/tests/) | 3, 9 | The test kit: a runner with a temp-leak gate, a one-clock lint, an import-graph layering engine with rules in a table, and a release-archive test; `selftest/` breaks each gate on the case it exists to catch *(untried as copied here)* |
| [`templates/tests/golden/`](../templates/tests/golden/) | 3, 10 | A module to copy: the golden-diff engine, the cases hook your harness fills from its verb table, and the tests that prove it can say 1 *(untried as copied here)* |
| [`templates/workflows/`](../templates/workflows/) | 3–10 | Workflow scripts for building with many agents (build → review → fix → verify; sweep → skeptic → plan), the ref check to run before any push, and the prompt rules with what each one prevented |
| [`templates/bug-classes.md`](../templates/bug-classes.md) | 4, 7, 10 | Data bug classes, each with the fixture test that pins it; the ids for the triage line's `Class:` |
| [`templates/doctor-checks.md`](../templates/doctor-checks.md) | 4–5, 9 | What the doctor checks: when each warns, the fix it names, the bug it caught |
| [`templates/console/ui_rules.tsv`](../templates/console/ui_rules.tsv) | 8 | UI rules as an owner file, each checked by name |
| [`templates/gitattributes`](../templates/gitattributes) | 9 | Copied to the root as `.gitattributes`: the internal files every release leaves out |
| [`templates/install-checklist.md`](../templates/install-checklist.md) | 9 | The install message, the host report, and what a host agent can read |
| [`templates/owner-queue-item.md`](../templates/owner-queue-item.md) | 10 | The shape of one owner ask |
| [`console/`](../console/) | 10 | A module, not a template: the agent's ask CLI and the owner's one-page console |
| [`templates/triage-checklist.md`](../templates/triage-checklist.md) | 10 | Judging an agent-filed issue; the triage line |
| [`templates/verify-challenge.md`](../templates/verify-challenge.md) | 10 | Reader and skeptic prompts for an outside report |
| [`templates/intake.schema.json`](../templates/intake.schema.json) | 0 | The shape of one meeting's intake; `build/check_intake.py` reads it |
| [`templates/prior-art-scan.md`](../templates/prior-art-scan.md) | 0, 4, 8 | When and how to scan what already exists, each claim with its link and date |
| [`templates/vendor-api-discovery.md`](../templates/vendor-api-discovery.md) | 0, 4, 8 | Pinning down a paid vendor API cheaply, and preflighting every request against its published schema |
| [`templates/third-party-consent.md`](../templates/third-party-consent.md) | 5, 8 | A real person in a marketing output: the record, confirmed only through the gate, bound to its content |
| [`templates/claim-scope.md`](../templates/claim-scope.md) | 5, 7, 10 | Regulated copy names only what the approved document allows: the claims record at the gate, the lexicon, waivers, the phrase library, rejections as recall tests |
| [`templates/owner-review-loop.md`](../templates/owner-review-loop.md) | 7, 10 | One published page the owner decides on per item, read back by the next session |
| [`templates/outcome-learning.md`](../templates/outcome-learning.md) | 10 | Winners against the rest on one factor catalog; rules stay pending until a variant confirms them |
| [`templates/session-handoff.md`](../templates/session-handoff.md) | all | Each session closes one turn and leaves the next one ready |
| [`templates/harness.toml`](../templates/harness.toml) | 3 | The harness declares itself once: names, languages, scopes, layers, what a release ships |
| [`templates/gitlab-ci.yml`](../templates/gitlab-ci.yml) | 3, 9 | The short form of that CI, the one the scaffolder renders into every harness: secret scan, story id in the title, the RESULT-gated test job, each with its rules |
| [`templates/gitignore`](../templates/gitignore) | 3 | Secrets, the console's log and client data never enter the repository |
| [`templates/SKILL.md`](../templates/SKILL.md) | 9 | The agent's rules and the skill's scope: read, pending writes, out of scope |
| [`templates/README-operator.md`](../templates/README-operator.md) | 9 | The operator guide: install, configure, the daily loop, what needs a human |
| [`templates/workflows.md`](../templates/workflows.md) | 9, 10 | Recipes: the daily check, why a number moved, a client meeting |
| [`templates/README.md`](../templates/README.md) | all | Every template, the step that uses it and its target in a harness; the ten tests a new harness starts with |
