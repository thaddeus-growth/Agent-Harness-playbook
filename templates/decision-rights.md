# Decision rights

Three lanes. Every change, value and action belongs to exactly one.

## 1. Always the owner

- **Meaning:** glossary words, user stories, business rules (owner files).
- **Numbers:** threshold values, costs, targets.
- **Money:** approving each action that spends or moves money.
- **Contract:** whether the harness may write to a client's account at all, and paid data calls (with a cap).
- **Risky merges:** any change that touches the risky list below.
- **Release:** installing a version on a client host ("confirm vX", typed by the owner).

## 2. AI proposes, owner confirms

- The AI writes a **pending** value with evidence and a recommendation.
- The owner confirms with one click, a chat code, or a terminal retype — bound to exactly the value shown, single use.
- Rules read confirmed values before pending ones. Anyone may lower trust; only a human raises it.

## 3. AI alone

- Bug fixes, missing warnings, additive report fields, tests, docs, version-bump releases of already-merged work.
- Flow: issue → triage against the house rules → fix + tests in its own worktree → CI green → auto-merge.

## The risky list (owner merges)

- The human gate (confirm / approve / restore, the code relay)
- The write path (the one writer module, its allowlist, execute/apply, the write switch)
- Removing or renaming keys in the `--json` contract (adding keys is fine)
- Owner files and threshold values
- Human-data table schema and its protections

## Channels that count

- Widening what may be written, or paid data calls: only when the owner **types it in chat**. A click on a page does not count.
- Everything else in lane 1: the owner's answer in the owner queue, or their merge.

## Deciders and advisers

"The owner" is a named person per kind of ask. Name them here on day one; a kind of ask with no decider is not asked until one is named.

| Kind of ask | Decider (answers) | Advisers (comment, never answer) | Channel |
| --- | --- | --- | --- |
| Meaning: words, stories, rules | <<fill: the client owner>> | <<fill: who was in the meeting>> | the decider's console |
| Numbers: costs, caps, thresholds | <<fill: the client owner, or who owns that number>> | <<fill: finance, the builder>> | the decider's console; a stated fact is confirmed through the harness's gate |
| Money: each action | <<fill: the client owner>> | <<fill: the builder>> | the decider's console, through the harness's gate |
| Contract: writes at all, paid calls, caps | <<fill: the client owner>> | <<fill: the builder owner>> | chat only, typed, with a cap |
| The harness's shape: scope, host, CI home, kit version | <<fill: the builder owner>> | <<fill: the build team>> | the builder owner's console |
| Risky merges | <<fill: the handle in CODEOWNERS>> | <<fill: reviewers>> | the merge request |
| Release | <<fill: the client owner>> | <<fill: the release owner>> | chat: "confirm vX" |

Each decider has a console folder of their own, so each keeps their own budget of 10 open asks. The console is started with `--deciders` naming them; everyone else logged in is an adviser. How advisers see and question the answers: [team-review.md](team-review.md).

## Dissent

- **Advice never answers.** An adviser's view is evidence for the decider. The agent never applies it, not even when the decider is silent. *(the console lets an adviser write only a view, and the agent applies only rows of `ask.py answers`)*
- **A disagreement becomes a new ask.** An adviser who disagrees says so, with a reason: in the console, or in chat when the console has no login. The agent posts one new ask to the decider, under a new id, with both views side by side as evidence. "No" means the first answer stands. *(the console refuses an answered id: `id_used`)*
- **An answer stands until its decider changes it.** A dissent does not pause it. A dissent about money or the contract is also said in chat, so the decider sees it before the next approval.
- **Record both.** The digest keeps the answer, each adviser's view and the answer to the dissent, each with its `ID@SEQ`; the `.agent.tsv` row's `decided` names the answer in force. *(tests/test_ssot.py: every owner row names its `decided` answer)*
