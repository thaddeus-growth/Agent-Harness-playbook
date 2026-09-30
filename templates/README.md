# Templates

The files a new harness starts from. The build workflow, [BUILD.md](../BUILD.md), says when each one is used; `scaffold/new_harness.py` renders the ones with a target path into a new harness, together with the skeleton the vendored kit runs on (BUILD.md, B1).

## Every template

| Template | Step | Becomes, in the harness | What it is |
| --- | --- | --- | --- |
| [meeting-intake.md](meeting-intake.md) | B0 | (used, not copied) | The organizer prompt |
| [intake.schema.json](intake.schema.json) | B0 | (used, not copied) | The shape of one `intake.json`: every item's `iid`, `audience` and `source`; `build/check_intake.py` reads it |
| [prior-art-scan.md](prior-art-scan.md) | B0.5 | `docs/prior-art/` | When and how to scan what already exists |
| [vendor-api-discovery.md](vendor-api-discovery.md) | B0.5, B4, B7 | `docs/vendor-api-discovery.md` | Pinning down a paid vendor API cheaply: product pages and their network requests, free calls first, one smallest paid call, the published schema recorded and every body preflighted against it |
| [owner-queue-item.md](owner-queue-item.md) | B0.6, B10 | (used, not copied) | The shape of one owner ask, and its console fields |
| [decision-rights.md](decision-rights.md) | B0.6, B2 | `docs/decision-rights.md` | The three lanes, the risky list, the channels that count, the digest |
| [harness.toml](harness.toml) | B1 | `harness.toml` | The harness's names, languages, scopes, layers and release lists |
| [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) | B1 | `CLAUDE.md` | Invariants only, each naming its test |
| [CODEOWNERS](CODEOWNERS) | B1 | `.gitlab/CODEOWNERS` | Protected paths for the risky list |
| [gitlab-ci.yml](gitlab-ci.yml) | B1 | `.gitlab-ci.yml` | Secret scan, story id in the title, the RESULT-gated test job |
| [ci/story-id.yml](ci/story-id.yml) | B1 | a job in a CI file of your own | The story-id job alone, with why it is a job and not only a rule; gitlab-ci.yml already holds it |
| [ci/gitlab-ci.yml](ci/gitlab-ci.yml) | B1 | (used, not copied) | The fuller CI to grow into: the secret-scan pattern file with a channel slot, a dependency cache, and an adapter smoke job for each host version. gitlab-ci.yml is its short form |
| [evals/](evals/README.md), [evals/fixture-build.sh](evals/fixture-build.sh) | B8 | `evals/`, `evals/fixture/build.sh` | Agent-behaviour evals: the case layout, the ablation table, the offline rules `kit.guards.evals` holds, and the fixture builder that refuses a checkout holding client data |
| [gitignore](gitignore) | B1 | `.gitignore` | Secrets, the console's log, client data |
| [gitattributes](gitattributes) | B1, B9 | `.gitattributes` | The internal files every release leaves out. The scaffolder renders this file from `harness.toml` and the ssot index instead (`kit.guards.release.render_gitattributes`); copy this one into a repository that is not scaffolded |
| [ssot/README.md](ssot/README.md) | B1 | `ssot/README.md` | Owner files, agent files, registries, the trail columns |
| [ssot/index.tsv](ssot/index.tsv) | B1 | `ssot/index.tsv` | One row per ssot file: owner, reader, test, id column |
| [ssot/glossary.tsv](ssot/glossary.tsv), [ssot/glossary.agent.tsv](ssot/glossary.agent.tsv) | B2 | `ssot/` | One name per idea, in the client's words |
| [ssot/user-stories.tsv](ssot/user-stories.tsv), [ssot/user-stories.agent.tsv](ssot/user-stories.agent.tsv) | B2 | `ssot/` | I want, so that, done when, the human step |
| [ssot/policies.tsv](ssot/policies.tsv), [ssot/policies.agent.tsv](ssot/policies.agent.tsv) | B3 | `ssot/` | Business rules that name thresholds |
| [ssot/constants.tsv](ssot/constants.tsv) | B3 | `ssot/` | Threshold defaults |
| [ssot/fact_keys.tsv](ssot/fact_keys.tsv), [ssot/decision_keys.tsv](ssot/decision_keys.tsv) | B3, B5 | `ssot/` | Which facts and decisions exist, and which need a human |
| [ssot/message_codes.tsv](ssot/message_codes.tsv) | B3, B4, B6 | `ssot/` | The harness's own message codes |
| [ssot/story_checks.tsv](ssot/story_checks.tsv) | B6 | `ssot/` | One runnable check per done story |
| [ssot/alert_rules.tsv](ssot/alert_rules.tsv) | B6 | `ssot/` | Alert rules as rows, read by the harness's own alert compute |
| [console/ui_rules.tsv](console/ui_rules.tsv) | B5, B7 | `webconsole/ui_rules.tsv` | The client console's UI rules, an owner file; each rule checked by name |
| [ssot/stages.agent.tsv](ssot/stages.agent.tsv) | every step | `ssot/` | Which build step the harness is at, and who signed each |
| [bug-classes.md](bug-classes.md) | B4, B6, B10 | `docs/bug-classes.md` | The data bug classes, each with the fixture test that pins it; the ids of the triage line's `Class:` |
| [doctor-checks.md](doctor-checks.md) | B1, B9 | `docs/doctor-checks.md` | What the doctor checks: when each row warns, the fix it names, the bug it caught. The kit's `doctor` implements most rows |
| [SKILL.md](SKILL.md) | B8 | `SKILL.md` | The agent's rules and the skill's scope: read, pending writes, out of scope |
| [third-party-consent.md](third-party-consent.md) | B3, B7 | a consent record per person + the letter | A real person in a marketing output (AI twin, voice, testimonial, creator clip): the record, confirmed only through the gate and bound to its content; revoke is free |
| [claim-scope.md](claim-scope.md) | B3, B6, B10 | a claims record per product + `ssot/claim_terms.tsv` + a phrase library per product | Regulated copy says only what the approved document allows: the claims record confirmed at the gate, the claims lexicon, the checks at script, asset and render, waivers, the phrase library, and every rejection as a recall test beside a false-alarm test |
| [owner-review-loop.md](owner-review-loop.md) | B6, B10 | `docs/owner-review-loop.md` | One published page the owner decides on per item; the next session reads the decisions back and logs each change against its note |
| [outcome-learning.md](outcome-learning.md) | B10 | `docs/winning-factors.md` | Learning from what performed: one factor catalog measured on winners and the rest; the owner defines "winner"; rules stay pending until a variant confirms them |
| [session-handoff.md](session-handoff.md) | all | `docs/session-handoff.md` (the harness keeps its own `docs/ROADMAP.md`) | Each session closes one turn and leaves the next ready: roadmap status and next turn, changelog, dated feedback copies, memory only for what the repo can't hold |
| [README-operator.md](README-operator.md) | B8 | `README.md` | Install, configure, daily loop, what needs a human, which command answers which question |
| [workflows.md](workflows.md) | B8 | `references/workflows.md` | Recipes: the daily check, why a number moved, a client meeting |
| [install-checklist.md](install-checklist.md) | B9 | `docs/install-checklist.md` | The install message, what the host agent reports after every install, scheduled tasks, and what a host agent can reach |
| [triage-checklist.md](triage-checklist.md) | B10 | `docs/triage-checklist.md` | Judging an agent-filed issue; the triage line that routes it |
| [verify-challenge.md](verify-challenge.md) | B10 | (used, not copied) | Reader and skeptic prompts for a report from outside |
| [tests/golden/](tests/golden/README.md) (below) | B1, B10 | `tests/golden/` | The golden-diff engine, committed before the first refactor |
| [workflows/](workflows/README.md) (below) | B4 to B10 | `.claude/workflows/` | Workflow scripts for building with many agents, and the ref check before any push |
| [tests/](tests/README.md) (below) | B1 | (used, not copied) | The standalone test kit: a copy-as-is option, not what the scaffolder generates |

## Placeholders

| Placeholder | Filled with | From |
| --- | --- | --- |
| `{{name}}` | The harness's name, for example `acme-harness` | `harness.toml` `name` |
| `{{cli}}` | The CLI word | `harness.toml` `cli` |
| `{{env_prefix}}` | The environment prefix, for example `ACME` | `harness.toml` `env_prefix` |
| `{{owner}}` | The owner's handle, for example `@owner` | the builder owner's answer (B0.6) |
| `{{repo_home}}` | Where the repository lives | the builder owner's answer (B0.6) |

`<<fill: …>>` marks what only the build can write: the skill's description, the domain's verbs and recipes, names in decision rights. The release step refuses a shipped file that still has one.

The example rows in `ssot/` show each file's shape. A new harness starts from the header rows, plus the `constants.tsv` row the kit's write path reads.

## The tests a new harness starts with

The scaffolder generates exactly these files, each one call into the vendored kit's `kit.testing.suites`, plus `tests/run.py`; CLAUDE.md names no other harness test. Each is green before the first feature.

| Test | What it checks |
| --- | --- |
| `tests/test_run_tests.py` | The runner fails a file that exits non-zero, prints no `RESULT: N passed` line, ran no test, or left files in its temp folder |
| `tests/test_ssot.py` | The index lists every ssot file; owner files hold no engineering words; siblings share ids; ids are unique and never reused; every owner row names its `decided` answer; no stage is signed before its `after` |
| `tests/test_layering.py` | The `[layers]` rules of `harness.toml`: pull, ingest, compute and the one writer |
| `tests/test_clock.py` | The one clock: no code reads the calendar but through `scripts/kit/dates.py` (`datetime.now`, `today`, `utcnow`, a `time.strftime` with no time, a name bound by `from kit.dates import now`), and the clock itself reads it once |
| `tests/test_boundary.py` | The core never names the console, an adapter or a chat app; an adapter runs only read verbs, and gated verbs with relay flags |
| `tests/test_json_contract.py` | Every read verb under `--json` on fixtures prints one document with `meta`; every message is coded; a failure is one `{error, next, code, params}` |
| `tests/test_human_tables.py` | Human tables survive every rebuild; a lossy rebuild is refused; an older tool refuses a newer database |
| `tests/test_gate.py` | Confirm and approve need a human; a code is bound to what was shown; a replayed or swapped code is refused; no bypass flag; the scope enters only through init |
| `tests/test_release.py` | `git archive` leaves out the internal files, ships the kit and the console, and no shipped file holds `<<fill:` |
| `tests/test_kit_drift.py` | `scripts/kit/` and `console/` match their `MANIFEST.sha256` and `VERSION` |

The playbook's own check of this page, BUILD.md and the build skill: [tests/test_docs_build.py](../tests/test_docs_build.py).

## Standalone kits (copy as is)

Three folders are not rendered by the scaffolder. They are self-contained modules a harness copies whole, and each has its own tests, kept out of the generated set above.

**`tests/`: the test kit for a repository that does not vendor the kit.** It holds the same rules as the ten generated tests, for the layout of the source project (`src/` beside a `common/` package at the root), with the rules as constants at the top of each file. A scaffolded harness does not copy it: the generated tests call the vendored kit, and `scripts/kit/` and its manifest keep every harness on one copy of each rule. The overlap, rule by rule:

| Rule | Generated test, from the vendored kit | Standalone copy |
| --- | --- | --- |
| The runner fails a file that exits non-zero, prints no `RESULT` line, ran nothing, or leaves temp files | `tests/test_run_tests.py` | [tests/run.py](tests/run.py), [tests/_check.py](tests/_check.py), [tests/test_run_tests.py](tests/test_run_tests.py) |
| One clock | `tests/test_clock.py` (`kit.guards.clock`) | [tests/test_clock.py](tests/test_clock.py) |
| Layering on the import graph | `tests/test_layering.py` (`kit.guards.layering`) | [tests/archtest.py](tests/archtest.py), [tests/test_layering.py](tests/test_layering.py) |
| The release archive | `tests/test_release.py` (`kit.guards.release`) | [test_release_archive.py](./tests/test_release_archive.py) |
| The kit tests itself | `kit/tests/` in this playbook | [tests/selftest/run.py](tests/selftest/run.py), [tests/selftest/_sample.py](tests/selftest/_sample.py), the sample's `common/` package ([dates.py](tests/selftest/sample/dates.py), [raw.py](tests/selftest/sample/raw.py), [runner.py](tests/selftest/sample/runner.py), [store.py](tests/selftest/sample/store.py)), [tests/selftest/test_archtest.py](tests/selftest/test_archtest.py), [tests/selftest/test_installed.py](tests/selftest/test_installed.py) |

What the kit adds to the generated ten is [tests/README.md](tests/README.md): the rules no test can check, each with the incident that paid for it. Where a rule differs (the standalone runner also gives every child an environment allowlist), the generated test is the one the harness's CLAUDE.md names.

**`tests/golden/`: the golden-diff engine** (B10, before the first refactor). It is a tool, never shipped: [README](tests/golden/README.md), the engine [engine.py](tests/golden/engine.py) and the cases hook [cases.py](tests/golden/cases.py) the harness fills from its verb table. Its own tests: [_t.py](tests/golden/tests/_t.py), [fake_harness.py](tests/golden/tests/fake_harness.py), [run.py](tests/golden/tests/run.py), [test_cleanup.py](tests/golden/tests/test_cleanup.py), [test_compare.py](tests/golden/tests/test_compare.py) and [test_engine.py](tests/golden/tests/test_engine.py).

**`workflows/`: building with many agents, one branch each.** [README](workflows/README.md); the scripts [build-review-fix-verify.js](workflows/build-review-fix-verify.js) and [sweep-skeptic-plan.js](workflows/sweep-skeptic-plan.js); the ref check [refcheck.py](workflows/refcheck.py) to run before any push. Its own tests, run under a simulator: [_t.py](workflows/tests/_t.py), [run.py](workflows/tests/run.py), [sim.js](workflows/tests/sim.js), [test_flow.py](workflows/tests/test_flow.py), [test_refcheck.py](workflows/tests/test_refcheck.py) and [test_scripts.py](workflows/tests/test_scripts.py).
