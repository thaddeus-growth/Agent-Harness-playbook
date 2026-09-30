# Build a harness

The step-by-step workflow for building the next harness from this playbook. Any agent can follow it from the files alone: each step says what it produces, whether it can fan out to parallel agents, and who signs its gate with which test. The owner signs only **meaning, stories, numbers, money and release**: in the console, or in chat for the contract and a release. A machine signs everything else.

An agent loads [skills/build-harness/SKILL.md](skills/build-harness/SKILL.md) to follow it. The stages it maps to are in [README.md](README.md); every template is listed in [templates/README.md](templates/README.md).

`$DATA_DIR` below is the client's data folder: recordings, transcripts, intakes, numbers, digests and the prior-art scan live there, never in a code repository. After B1 the harness calls it `<PREFIX>_DATA_DIR`. `<cli>` is the harness's CLI word.

## The steps

| Step | Produces | After | Fan out | Gate: who signs · which test |
| --- | --- | --- | --- | --- |
| B0 Intake | A consent note; transcripts in `$DATA_DIR`; one `intake.json` per meeting | — | yes: one agent per meeting or document | machine · `build/check_intake.py`: no quote, no item; a blank consent line is refused |
| B0.5 Prior-art scan | A dated verdict: what to borrow, what to avoid | B0 | yes: one agent per lens | builder owner reads the verdict · every claim carries its link and date |
| B0.6 Build-time asks | At most 10 open asks per owner; their answers; the round's digest | B0, B0.5 | no: the integrator | each owner, in their console · the console refuses an 11th open ask (console/tests/test_core.py) |
| B1 Scaffold | The harness tree: templates rendered, kit and console vendored; the first push | B0.6 | no | CI · the generated suite prints `RESULT: N passed` (tests/test_run_tests.py, tests/test_kit_drift.py) |
| B2 Words and stories | Glossary and story rows from the answers | B1 | no: the integrator gives ids | client owner (meaning, stories) · tests/test_ssot.py |
| B3 Registries | Rules, thresholds, fact keys, decision keys, message codes | B2 | yes: one agent per registry | client owner approves each rule and number · tests/test_ssot.py, tests/test_json_contract.py |
| B4 Data chain | Per source: puller, raw fixtures, ingest, cache tables, tests | B1 | yes: one agent per data source | CI · rows in = rows out, ingest twice = same rows; tests/test_layering.py |
| B5 Human data | The scope declared; stated numbers loaded pending, then confirmed | B3, B4 | no: the integrator | client owner confirms each number through the gate · tests/test_human_tables.py, tests/test_gate.py |
| B6 Reports | Per story: one read-only report and its check row | B4, B5 | yes: one agent per story | CI · tests/test_json_contract.py; the story's check green on fixtures |
| B7 Gate and money path | Queue, approve, execute as a dry run; an empty allowlist; writes off | B6 | no: the risky list | owner merges (CODEOWNERS) · tests/test_gate.py, tests/test_layering.py |
| B8 Agent docs and evals | SKILL.md, README.md, references/workflows.md, evals | B6 | yes: one agent per doc | builder owner reads the scope · each eval fails when its rule is removed |
| B9 Release | A tag, its archive, a host install and the host's report | B7, B8 | no: one release owner | client owner types "confirm vX" in chat · tests/test_release.py; the host checklist |
| B10 Operate | Scheduled pulls, nightly story checks, the owner queue, triaged issues | B9 | yes: one agent per issue | CI signs lane 3; the owner merges the risky list · the full suite, every merge |

The same rows, with their status and the reference that signed each, are the harness's `ssot/stages.agent.tsv` ([template](templates/ssot/stages.agent.tsv)).

## B0 · Intake

1. Ask for consent to record. Note who gave it and when.
2. Put recordings, transcripts and briefs in `$DATA_DIR/meetings/<date>/`.
3. One agent per meeting or document organizes it with [templates/meeting-intake.md](templates/meeting-intake.md) into `$DATA_DIR/intake/<date>.json`, in the shape of [templates/intake.schema.json](templates/intake.schema.json): every item has an `iid` that is never reused, an `audience` (`client` or `builder`) and a `source`.
4. `python3 build/check_intake.py $DATA_DIR/intake/*.json` checks all intakes together. It refuses an item with no source, a number with no quote or unit, a rounded or estimated number, a contradiction that is not asked as a question, and a meeting whose consent line is blank. A consent line that says consent was not given or not recorded passes the check: put it to the owner as a question (B0.6).
5. Numbers never enter a repository. After the owner answers, they wait as pending rows in `$DATA_DIR/intake/numbers.tsv` until B5.

## B0.5 · Prior-art scan

Follow [templates/prior-art-scan.md](templates/prior-art-scan.md): one agent per lens, each claim with its link and date. The first scan comes after the first intake, before any story is accepted. It is repeated at fixed points: before B3 and B4, before B7 or B9 when they add a write path or a paid call, and whenever the client names a tool. Until the repository exists the scan waits in `$DATA_DIR/build/prior-art/`; at B1 it moves to `docs/prior-art/`. A borrowed idea that changes meaning or a number becomes an ask in B0.6.

## B0.6 · Build-time asks

1. **Give each owner a console folder** of their own (`CONSOLE_DIR`), per kind of ask ([templates/decision-rights.md](templates/decision-rights.md)). Serve it with `serve.py`; behind a login proxy that names the person, `--user-header` ([console/README.md](console/README.md)).
2. **Pick at most 10 items per owner**, minus the asks still open. For the client owner, in this order: numbers a story needs, word clashes, stories whose human step is approve, other stories, questions that block a story, the remaining words. A goal is never asked: it is the why of its stories. An item that would widen writes, a cap, an allowlist or paid calls is never an ask: raise it in chat.
3. **The builder owner's round** asks the harness's shape, which B1 needs: v1 scope, the first scope (market), the host, the CI home and repository, the kit version, how data gets in, and where the team reads the digest. They are intake questions with audience `builder`, the playbook's default as the suggestion.
4. **Make the asks** with `python3 build/intake_to_asks.py --intake FILE… --pick IID,… --audience client --console-dir DIR` (`builder` for the builder's round). Each ask's id is `intake-<iid>`, and an item already answered or withdrawn is refused.
5. **Post and wait** with [console/ask.py](console/ask.py): `ask.py add`, then `ask.py wait --since SEQ` ([console/AGENT.md](console/AGENT.md)).
6. **Apply** with `python3 build/apply_answers.py --answers FILE --intake FILE… --ssot DIR --data-dir $DATA_DIR`. It acts only on verified answers the owner saw unchanged. A yes writes the owner row and its sibling with `decided` = `console:ID@SEQ`; a no marks the sibling `dropped`; a number goes to `$DATA_DIR/intake/numbers.tsv`, pending. It prints the `ask.py applied` commands; run them. Owner rows need the ssot folder, so answers given before B1 are applied at B2 and B3.
7. **Forward the round's digest** (`ask.py digest` prints one JSON document; its `text` field is the Markdown to forward) where the team reads it ([templates/decision-rights.md](templates/decision-rights.md), "The digest"). A console copied before the `digest` verb existed (its `--help` does not list `digest`) is vendored again, not copied over by hand: `python3 kit/tools/vendor.py --harness DIR --console` (`--dry-run` first lists what changes; B1's `--update-kit` does it too) replaces its files and rewrites its `MANIFEST.sha256` and `VERSION`, which the harness's drift test reads, and a hand copy leaves the old two and turns that test red. Its data folder is not part of the copy and stays as it is.

## B1 · Scaffold

```
python3 scaffold/new_harness.py --dir ../acme-harness \
  --name acme-harness --cli acme --prefix ACME --markets AA,BB \
  --langs en,zh --owner @handle --repo-home <where the repository lives>
```

- It renders every template with a rendered target (`RENDERED` in `scaffold/new_harness.py`; [templates/README.md](templates/README.md) says which templates are used, not copied, or written by a later step) and fills the placeholders from the builder owner's answers. `<<fill: …>>` stays only where a later step writes: SKILL.md, README.md, references/workflows.md and docs/decision-rights.md (and CLAUDE.md without `--repo-home`).
- It writes the skeleton the kit runs on: `scripts/<cli>.py` (the kit's dispatcher), `scripts/verbs.py` (every verb and its kind), the kit's facts, decisions, pending, story, queue and execute verbs, `scripts/_lib/schema.py` (the human tables only) and `scripts/_lib/writer.py` (an empty allowlist); every ssot file as its header row, plus `approval_ttl_hours` and the build steps; and exactly the ten tests of [templates/README.md](templates/README.md), each one call into the kit's `kit.testing.suites`.
- It vendors the kit (`kit/`) into `scripts/kit/` and the console into `console/`, each with its `VERSION` and `MANIFEST.sha256`, through `python3 kit/tools/vendor.py --harness ../acme-harness`, and renders `.gitattributes` from `harness.toml` and the ssot index.
- It refuses a folder that is not empty or sits inside this checkout (the scaffolder's own `out/` folder, ignored by git, is the one preview place), takes no data path, and leaves no `{{` behind. `--dry-run` prints the file list and writes nothing. `--update-kit` vendors the kit and console again and touches nothing else.
- The generated suite is green before the first feature (`python3 scripts/<cli>.py test`; scaffold/tests/test_new_harness.py proves it on every change here). Push the skeleton as the first commit: the first pipeline proves the push rights and the CI home at once.

## B2 · Words and stories

- The integrator applies the client owner's answers: glossary rows, story rows with ids it gives (S01, S02 …), and each sibling row with `status`, `ask` and `decided`.
- One name per idea. A clash is an ask, never a silent pick.
- Each story names its human step. Anything that can spend money is never "none".
- Fill the names in the harness's `docs/decision-rights.md`.

## B3 · Registries

- The integrator gives out ids and names first, then one agent per registry drafts: rules in `ssot/policies.agent.tsv` (owner rows only from answers), thresholds in `ssot/constants.tsv` with a default and its why, fact keys, decision keys (`confirm = human` for anything that can move money), message codes.
- Round two asks the client owner each rule and number. Once reports exist, a what-if run (`--assume threshold_<name>=<value>`) is the evidence.

## B4 · Data chain

One agent per data source, after the prior-art scan's second fixed point:

- `scripts/pull_<source>.py`: read-only; raw is write-once and only grows.
- `scripts/ingest_<source>.py` and its cache tables in `scripts/_lib/schema_<source>.py`; the integrator adds the fragment to `scripts/_lib/schema.py`.
- Fixtures copied from real responses, never invented, and the source's own tests: rows in = rows out, ingest twice = same rows, a shorter re-pull keeps the older days.

## B5 · Human data

- `<cli> facts init`: the human declares the scope; there is no default.
- The client owner signs which fact and decision keys exist (asked in B3).
- Each number in `$DATA_DIR/intake/numbers.tsv`: `<cli> facts set <key> <value> --source client_meeting_<date> --reason "<hh:mm:ss> <quote>"`. It stays pending.
- `<cli> pending --json` gives ready console asks with a `gate`. With the console started with the harness command and those verbs allowed ([console/README.md](console/README.md)), the owner's click passes the harness's own gate, bound to the value shown.

## B6 · Reports

One agent per story, with the story id and its message-code prefix given:

- One read-only `scripts/compute_<report>.py`. Its `--json` carries `meta`, and every message carries a code with the unit's prefix.
- Its row in `ssot/story_checks.tsv`: the read verb, what its JSON must hold, the tables it needs.
- `<cli> compute stories --json` passes on fixtures. On real data it runs at B9.

## B7 · Gate and money path

Only when a story's human step is approve. The integrator builds it; the owner merges it.

- `<cli> queue add` snapshots a report's proposals; `queue approve` passes the gate; an approval expires after `approval_ttl_hours`.
- `<cli> execute apply` is a dry run that prints the whole plan unless `--apply`.
- `scripts/_lib/writer.py` ships with an empty allowlist, and `<PREFIX>_ALLOW_WRITES` stays unset: writes are off.
- Writes turn on only when the client owner types it in chat, with a cap. The allowlist entry is its own merge request, merged by the owner.

## B8 · Agent docs and evals

- `SKILL.md` from [templates/SKILL.md](templates/SKILL.md): the description and the scope (read, pending writes, out of scope).
- `README.md` from [templates/README-operator.md](templates/README-operator.md), and `references/workflows.md` from [templates/workflows.md](templates/workflows.md), plus the domain's own recipes.
- Every `<<fill: …>>` is written.
- One eval per rule in SKILL.md. An eval counts only if it fails when its rule is removed. Evals make live model calls, so they run by hand; the result is recorded with the release.

## B9 · Release

- One release owner at a time, named in the B9 row of `ssot/stages.agent.tsv`.
- A harness installed on Zylos copies the files that [hosts/zylos/README.md](hosts/zylos/README.md) lists (not the adapter's `tests/` or its two templates) into itself as `zylos/`, writes the three things that README names (the manifest, the Zylos keys of `SKILL.md`, the root `ecosystem.config.cjs`) and adds `node zylos/lib.js check` to CI, and commits all of it before the tag is cut: the host installs the archive of the tag, so a copy made after the tag is not in it. None of it is in `[release].internal`, so it ships once it is tracked; naming `zylos/manifest.json`, `zylos/lib.js` and `ecosystem.config.cjs` in `[release].must_ship` makes `tests/test_release.py` fail while one of them is not. `hosts/zylos/` is the worked example for any other host.
- Tag `vX.Y.Z` on main (merges are never squashed). The release is `git archive` of the tag.
- The client owner types "confirm vX" in chat. The host agent installs and reports with [templates/install-checklist.md](templates/install-checklist.md). Every story check runs green on real data.

## B10 · Operate

- Scheduled pulls with freshness checks. Each night `<cli> compute stories --json` becomes one line for the owner: "37 green, 1 red: S0x".
- `<cli> pending --json` feeds the console, at most 10 open per owner.
- Issues are triaged into the three lanes of decision rights. Lane 3 merges on green CI; the risky list waits for the owner.
- A fix to the kit or the console goes to the playbook first, then `scaffold/new_harness.py --update-kit`.
- `--update-kit` does not rewrite `.gitlab-ci.yml`. A harness scaffolded before every job of [templates/gitlab-ci.yml](templates/gitlab-ci.yml) stated its `rules:` has jobs with none, and a job with no `rules:` never runs in a merge request pipeline: such a merge request can turn green with only its `story id` job run and no test. Add `rules:` with `- when: on_success` to the secret scan, the test job and any other job of that file by hand, as the template does.
- Each new meeting goes back to B0 while the build tools are at hand (`build/` lives in this playbook; a harness has no such folder). The operator's agent inside a running harness follows section 3 of its `references/workflows.md` ([templates/workflows.md](templates/workflows.md)): stated numbers enter as pending facts with `--source client_meeting_<date>`, everything else as `proposed` rows.

## Fan-out rules

- **Disjoint files only.** The units are a meeting (B0), a lens (B0.5), a registry (B3), a data source (B4), a story (B6), a doc (B8) and an issue (B10). Never two agents on one file in one step: message codes and the schema have one fragment file per unit, below. A merge conflict is the signal to stop: the integrator merges.
- **Integrator-only ids.** One integrator gives out every id and name before a fan-out: story (S..) and rule (P..) ids, threshold names, fact and decision keys, message-code prefixes. A worker never makes one. Two branches once reused story ids and a story was lost. *(tests/test_ssot.py: ids unique, never reused)*
- **Owner files and the risky list are the integrator's.** A worker's new word, story idea or rule goes in its merge request description; the integrator copies it into the `.agent.tsv` sibling as `proposed`.
- **Message codes: one fragment per unit.** The kit keeps its codes in fragments, `kit/message_codes.d/<module>.tsv`, one file per module, loaded after `kit/message_codes.tsv`. A harness does the same: each unit of a fan-out writes its codes to its own file, `ssot/message_codes.d/<unit>.tsv` (same columns as `ssot/message_codes.tsv`), loaded after `ssot/message_codes.tsv` in name order, so parallel workers never edit the same file. The integrator gives each unit its fragment name and its code prefix (`<unit>_`), and may fold fragments into `ssot/message_codes.tsv` later; the index row of `ssot/message_codes.tsv` covers the fragments. *(tests/test_json_contract.py: a code defined twice anywhere, in two fragments or in a fragment and the registry or the kit, is refused at load)*
- **Schema: one fragment per data source**, `scripts/_lib/schema_<source>.py`. Only the integrator edits `scripts/_lib/schema.py`.
- **One worktree and branch per unit.** The merge request title names its story (the `story id` CI job). Never squash.
- **Asks come from the integrator.** Only at a gate, only through the console, at most 10 open per owner. A worker hands the integrator what to ask.
- **One release owner at a time.**

## Resume from the files

The build's state is two things, so any agent can pick it up:

- `ssot/stages.agent.tsv`: each step's status and the reference that signed it (`console:ID@SEQ`, `ci:<pipeline>`, `merge:<sha>`, `chat:<date> <who>`, `check:<name> <date>`). Before B1 there is no such file: B0 to B0.6 show in what they left (intakes in `$DATA_DIR/intake/`, the scan in `$DATA_DIR/build/prior-art/`) and in the console logs; the first commit after B1 fills their rows. A step a solo owner-builder has no use for is signed `chat:<date> <who>` with `[waived]` in its notes, so the steps after it can be signed.
- Each owner's console log: what was asked, answered and applied. No cursor is kept: `ask.py answers` lists the answers still waiting to be applied.

To resume: read both, run `ask.py answers` for each owner, apply what is waiting, then continue at the first step that is not signed.

## Still being built

The one list of paths this workflow names that do not exist yet. [tests/test_docs_build.py](tests/test_docs_build.py) fails on any other path that is missing, and says when a path below has landed.

Nothing: every path the build names exists.
