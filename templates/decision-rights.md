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

## The digest

`ask.py digest` renders a round's decision record from the console's log: every ask, what the owner was shown, the answer, who gave it and when, whether it took the suggestion, and where it was applied or why it was withdrawn. The agent never types it.

- Keep each round's digest in the client's data folder, `$DATA_DIR/build/digest/<date>-round<N>.md`. It may hold client numbers, so never in a code repository.
- Where the team reads it is asked once, of the builder owner. A teammate who missed the meeting reads the digest, not the chat.
- A published digest is never edited. The next round's digest shows what changed.
- The `.agent.tsv` row's `decided` names the answer in force, as `console:ID@SEQ`. *(tests/test_ssot.py: every owner row names its `decided` answer)*
