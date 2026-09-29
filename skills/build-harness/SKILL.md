---
name: build-harness
description: Build a new agent harness for a client from the Agent Harness Playbook, from recorded client meetings to a tested release, following BUILD.md step by step (B0 to B10). Use when asked to start, scaffold, continue or resume a harness build, turn client meetings into stories and registries, or take a harness to its first release. Do NOT use to operate a finished harness day to day; that is the harness's own SKILL.md.
version: 0.1.0
type: workflow
---

# Build a harness

This skill follows [BUILD.md](../../BUILD.md). BUILD.md is the recipe; this file is what you must keep in mind while following it. When the two disagree, BUILD.md wins: fix this file.

## When to use

- A client meeting was recorded and a new harness should come out of it.
- A harness build stopped part way, and you pick it up.
- A running harness had a new meeting: go back to B0 for that meeting.

## The steps, in order

Read BUILD.md's table first. Then, one step at a time:

1. **B0 Intake.** Consent first. Organize each meeting with [templates/meeting-intake.md](../../templates/meeting-intake.md); check it with `build/check_intake.py`.
2. **B0.5 Prior-art scan** with [templates/prior-art-scan.md](../../templates/prior-art-scan.md), one agent per lens.
3. **B0.6 Asks and team review.** Name deciders and advisers ([templates/decision-rights.md](../../templates/decision-rights.md)); make each round's asks with `build/intake_to_asks.py` (the builder's round too: its items have audience `builder`); forward the digest ([templates/team-review.md](../../templates/team-review.md)).
4. **B1 Scaffold** with `scaffold/new_harness.py` (BUILD.md B1); push; CI green before the first feature.
5. **B2 Words and stories**: apply answers with `build/apply_answers.py`; you give the ids.
6. **B3 Registries**, one agent per registry; the owner approves each rule and number.
7. **B4 Data chain**, one agent per data source.
8. **B5 Human data**: the human declares the scope; stated numbers load pending and are confirmed through the gate.
9. **B6 Reports**, one agent per story, each with its check row.
10. **B7 Gate and money path**: writes stay off; the owner merges.
11. **B8 Agent docs and evals** from [templates/SKILL.md](../../templates/SKILL.md), [templates/README-operator.md](../../templates/README-operator.md) and [templates/workflows.md](../../templates/workflows.md).
12. **B9 Release**: one release owner; the client owner types "confirm vX".
13. **B10 Operate**: every new meeting goes back to B0.

Before each step, check its `after` steps are signed in `ssot/stages.agent.tsv`. After it, record who signed and the reference. Some paths above are still being built: BUILD.md lists them in one place.

## Never

- Never put a recording, a transcript, a client number or a console log in a code repository. They live in the client's data folder.
- Never write a number with no quote. Never round one, and never turn a range into one value.
- Never edit an owner file except to apply an answer the owner gave; write its `decided` reference in the same change.
- Never confirm, approve or restore for the owner, and never relay a code the owner did not send.
- Never ask on a page to widen what may be written: an allowlist, a cap, a write switch or paid calls. That is asked in chat.
- Never apply advice as an answer. Only the decider's answer counts; a disagreement becomes a new ask.
- Never let two agents edit one file in one step, and never let a worker make an id.
- Never edit the vendored `scripts/kit/` or `console/` inside a harness. Fix them in the playbook, then vendor again.
- Never call a step done without its test or its signed answer.

## Where questions go

Only to the console, only at a gate: at most 10 open asks per decider, each with evidence, a recommendation and what "no" means. Follow [console/AGENT.md](../../console/AGENT.md). The mechanical rest you do yourself, and say in chat what you did.

## Where the state is

`ssot/stages.agent.tsv` in the harness, and each decider's console cursor in `$DATA_DIR/build/state.json`. To resume: read both, run `ask.py answers --since SEQ` for each decider, then continue at the first step not signed. Resume from the files, never from memory.
