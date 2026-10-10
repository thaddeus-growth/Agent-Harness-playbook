# Changelog

What a harness gets when it re-vendors the kit, and what else changed in the
playbook, newest first. Format: Keep a Changelog.
A release is a `kit/VERSION` (a patch for a fix, a minor for a new module or hook,
a major when a harness must change). `hosts/zylos` has its own `VERSION`, noted
where it moved. The short hash on a version line is the commit that set it; dates
are the merge day. Everything under *Other* is outside the kit: templates, build,
scaffold, console, docs, conformance.

## [Unreleased]

### Other
- Client template: the first call is `doctor` and `pending`, not a status verb (#55).
- `templates/CHANGELOG.md`, rendered into every new harness, and a `changelog` job in both CI
  templates: a merge request that changes the code, a registry, the skill or the console must
  add its line under Unreleased, or say `[no changelog]` in its title. Tested by running the
  job's own shell on sample file lists.

## [0.10.0] - 2026-10-10
### Added
- `kit/guards/changelog.py` and `kit.testing.suites.changelog`: `CHANGELOG.md` starts with
  Unreleased, its releases are in order (versions descending, dates not increasing, each with
  a `### ` section), `SKILL.md`'s version is the newest release (a harness not yet released is
  at 0.1.0), and every `vX.Y.Z` tag has a heading. A self-test plants each fault. Seen in
  amazon-ads-harness, which wrote the same rule for itself.
### Other
- A new harness is generated with `tests/test_changelog.py`: eleven day-one tests, not ten.
  `templates/AGENT_INSTRUCTIONS.md` gains the **Changelog** invariant, naming that test.

## [0.9.0] - 2026-10-08 · `7569fb2`
### Added
- `tools/skill_upload.py` writes the copy of SKILL.md that claude.ai and the Skills API
  accept (`name`, `description`, `metadata`…), leaving the repository's frontmatter, which
  Zylos reads, untouched (#54).
### Other
- Evals: one method; the pair-run lessons move into `templates/evals/README.md` (#46).
- `build`: `apply_answers` applies the owner's answer to a builder's own proposal.
- Scaffold: client wrappers run `<PREFIX>_PYTHON` and refuse a Python older than 3.11.
- `CLAUDE.md`: the parent's rules for working here — small, decoupled, one concern per
  change (#52). Lessons for paid generation jobs (#53). Geo-china fixes (#51).
- Conformance: the `ppc` adapter follows its current CLI; adapters may type values and
  answer a TTY.

## [0.8.1] - 2026-10-05 · `e8e14c9`
### Fixed
- An agent-only row `asked` (sent to the console, not answered) is a legal status in the
  ssot guard; `proposed_statuses` defaults to `["proposed", "asked"]`. Seen in geo-china-harness.

## [0.8.0] - 2026-10-05 · `5867752`
### Changed
- **`facts set` and rollback never replace a confirmed value.** An ungated write over a
  confirmed value is refused (`fact_set_confirmed`), naming the two ways forward: unconfirm
  (lower trust, recorded) or `confirm --value`. Found by conformance C1 in
  amazon-ads-harness. **A harness that relied on the old replace must change.**

## [0.7.0] - 2026-10-05 · `8e13e16`
### Added
- **Packs and selective vendoring** (phase 2 of `RESTRUCTURE.md`): every module belongs to
  one pack in `kit/packs.tsv` (`base`, `compliance`, `generation`, `data`, `testkit`); a
  harness names its packs under `[kit] packs`; `vendor.py` copies only those plus `base`
  and `testkit`, and refuses to leave out a module the harness imports. With no
  `[kit] packs` a harness receives every pack, so existing harnesses see no change.
- `tools/fleet.py`: where each harness's vendored copies stand (phase 1).
### Removed
- `kit/pull.py` (no consumer).
### Other
- `conformance/`: the invariants every harness keeps and a black-box check for two of them
  (C1 a confirmed value is never replaced by an agent, C2 doctor).
- README is the overview; the detail moves unchanged into `docs/` (phase 4).
- `hosts/zylos` 0.1.1: a zombie job is not alive; Node 22 is the minimum (#21).
- `templates/SKILL.md`: agent rules from a second harness's client rehearsal; the
  playbook's own `ROADMAP.md`.

## [0.6.2] - 2026-10-01 · `c9aa076`
### Added
- `scaffold/new_client.py`: one folder per client — wrappers pinned to `./workspace`, its
  console, a `CLAUDE.md` brief, deny rules for gated verbs. `templates/ROADMAP.md`, the
  `docs/ROADMAP.md` every harness keeps.
### Changed
- `kit.copylint`: 兩 reads as 2. Law lens added to the prior-art scan; doctor check for a
  vendor API version still alive. Landed from KOL's local 0.3.1 work.

## [0.6.1] - 2026-10-01 · `b3ebb74`
### Changed
- `--help` examples and JSON keys come from the verb table; `--for-client` on facts and
  decisions `confirm`; the console user comes from the OS account (#37).

## [0.6.0] - 2026-10-01 · `1eb183d`
### Added
- Agent evals, the offline half: `kit/guards/evals.py` holds a suite to what needs no
  model — each case names the SKILL.md rule it tests, carries the forbidden-verbs grader
  generated from the verb table, and has a row in the ablation table (an eval counts only
  if it fails when its rule is cut). Template `templates/evals/` and fixture (#17).

## [0.5.0] - 2026-09-30 · `4b49354`
### Added
- Claim scope and phrasebook (#11).
- **0.4.0, the price registry, landed with this version** (it missed #9): `kit/prices.py`
  reads an owner TSV of prices whose rows stay pending until a person confirms them; a
  blank price is unknown, never zero.
### Other
- Evals run as a pair, the rule's text deleted from the twin. README and templates: sources
  kept apart, cited rules capped and re-verified, one-client rules stay proposed.
- Slimmed: CI and `run_all.py`, the kit tier registry, duplicates removed.

## [0.3.0] - 2026-09-29 · `3af9c7f`
### Added
- Generated creative and real people: `takes` (paid generation planned, priced, capped,
  approved for exactly the plan, kept once), `preflight`, consent, `copylint` (#9).
### Changed
- `kit.human`: attribution by OS account, never `LOGNAME`/`USER`; a third party's own
  confirmation bound into the code's subject (`--for-client`) (#8).
### Removed
- Team review (adviser agree/disagree) from the console, the Zylos adapter and the docs (#5).

## [0.2.0] - 2026-09-29 · `090f788`
### Added
- `scaffold/new_harness.py`: a new harness, green before its first feature.
### Changed
- The kit absorbs `core/` (owner decision, 2026-09-29): raw guards, store and gate checks,
  the one-clock guard, the verb-case contract; `core/` stays as a pointer.
- `queue add` never supersedes an approved row, only pending ones; a dry run says when
  `--apply` cannot send (new optional `allowlist` hook).

## [0.1.0] - 2026-09-29 · `ab1871a`
First kit: the shared modules distilled from amazon-ads-harness. Stdlib only, Python 3.11+,
configured once by a harness's `harness.toml`.
### Added
- `kit/`: config, messages (closed registries), contract (one `--json` document, `meta`,
  coded failures), dates/atomic/raw, paths/env/auth/retry, db (protected human tables:
  triggers, shrink guard, backups, lossy-rebuild refusal, version stamp), human (TTY retype
  or a relayed HMAC code bound to the subject), market, registry, facts, decisions, queue,
  execute (dry run by default), write_guard (kill switch, opt-in, allowlist, no retries),
  pending, stories, guards (ssot, release, layering, JSON contract, boundary, drift), the verb
  table, dispatcher and doctor, and `kit/README.md` with `MANIFEST.sha256`.
- `hosts/zylos` 0.1.0: a manifest-driven Zylos host adapter.
### Other (the playbook before the kit, 2026-09-27 to 09-29)
- The playbook itself: client meetings, the ten stages, decision rights, trust ladder, data
  layers, human-in-the-loop rules.
- `console/`, the owner console, forms sent in place.
- `templates/`: data bug classes each with its fixture test, a full GitLab CI, a test kit,
  the doctor's checks, many-agent workflows, golden-diff engine; `BUILD.md`.
