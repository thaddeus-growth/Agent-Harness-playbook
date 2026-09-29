# Templates

The files a new harness starts from. The build workflow, [BUILD.md](../BUILD.md), says when each one is used; `scaffold/new_harness.py` renders the ones with a target path into a new harness, together with the skeleton the vendored kit runs on (BUILD.md, B1).

## Every template

| Template | Step | Becomes, in the harness | What it is |
| --- | --- | --- | --- |
| [meeting-intake.md](meeting-intake.md) | B0 | (used, not copied) | The organizer prompt |
| [intake.schema.json](intake.schema.json) | B0 | (used, not copied) | The shape of one `intake.json`: every item's `iid`, `audience` and `source`; `build/check_intake.py` reads it |
| [prior-art-scan.md](prior-art-scan.md) | B0.5 | `docs/prior-art/` | When and how to scan what already exists |
| [owner-queue-item.md](owner-queue-item.md) | B0.6, B10 | (used, not copied) | The shape of one owner ask, and its console fields |
| [team-review.md](team-review.md) | B0.6 | `docs/team-review.md` | Deciders, advisers, the digest, and how a dissent becomes a new ask |
| [decision-rights.md](decision-rights.md) | B0.6, B2 | `docs/decision-rights.md` | The three lanes, the risky list, deciders and advisers, dissent |
| [harness.toml](harness.toml) | B1 | `harness.toml` | The harness's names, languages, scopes, layers and release lists |
| [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) | B1 | `CLAUDE.md` | Invariants only, each naming its test |
| [CODEOWNERS](CODEOWNERS) | B1 | `.gitlab/CODEOWNERS` | Protected paths for the risky list |
| [gitlab-ci.yml](gitlab-ci.yml) | B1 | `.gitlab-ci.yml` | Secret scan, story id in the title, the RESULT-gated test job |
| [ci/story-id.yml](ci/story-id.yml) | B1 | a job in a CI file of your own | The story-id job alone, with why it is a job and not only a rule; gitlab-ci.yml already holds it |
| [gitignore](gitignore) | B1 | `.gitignore` | Secrets, the console's log, client data |
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
| [SKILL.md](SKILL.md) | B8 | `SKILL.md` | The agent's rules and the skill's scope: read, pending writes, out of scope |
| [README-operator.md](README-operator.md) | B8 | `README.md` | Install, configure, daily loop, what needs a human, which command answers which question |
| [workflows.md](workflows.md) | B8 | `references/workflows.md` | Recipes: the daily check, why a number moved, a client meeting |
| [install-checklist.md](install-checklist.md) | B9 | `docs/install-checklist.md` | The install message, what the host agent reports after every install, scheduled tasks, and what a host agent can reach |
| [triage-checklist.md](triage-checklist.md) | B10 | `docs/triage-checklist.md` | Judging an agent-filed issue; the triage line that routes it |
| [verify-challenge.md](verify-challenge.md) | B10 | (used, not copied) | Reader and skeptic prompts for a report from outside |

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
| `tests/test_boundary.py` | The core never names the console, an adapter or a chat app; an adapter runs only read verbs, and gated verbs with relay flags |
| `tests/test_json_contract.py` | Every read verb under `--json` on fixtures prints one document with `meta`; every message is coded; a failure is one `{error, next, code, params}` |
| `tests/test_human_tables.py` | Human tables survive every rebuild; a lossy rebuild is refused; an older tool refuses a newer database |
| `tests/test_gate.py` | Confirm and approve need a human; a code is bound to what was shown; a replayed or swapped code is refused; no bypass flag; the scope enters only through init |
| `tests/test_release.py` | `git archive` leaves out the internal files, ships the kit and the console, and no shipped file holds `<<fill:` |
| `tests/test_kit_drift.py` | `scripts/kit/` and `console/` match their `MANIFEST.sha256` and `VERSION` |

The playbook's own check of this page, BUILD.md and the build skill: [tests/test_docs_build.py](../tests/test_docs_build.py).
