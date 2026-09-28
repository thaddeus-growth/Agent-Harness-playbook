# ssot/ — single source of truth

Rows are examples from a paid-ads harness: keep the headers and the rules, replace every row.

- **Owner files** (`user-stories.tsv`, `policies.tsv`, `glossary.tsv`): plain business meaning in the owner's language. Agents never edit them; they propose changes in the `.agent.tsv` sibling's `proposed` column and apply only an answer the owner gave. A test fails on engineering words in them (issue refs, commands, flags, file and table names, a number next to a threshold name); cells waiting on an open owner question sit on an exemption list that fails when an entry no longer needs it, so it only shrinks.
- **Agent files** (`*.agent.tsv`): progress, code locations, proposals.
- **Registries** (`constants.tsv`, `message_codes.tsv`, `fact_keys.tsv`, `decision_keys.tsv`, `alert_rules.tsv`, `story_checks.tsv`): read by code, guarded by tests. A number lives only in its registry; everywhere else uses its name.
- **`index.tsv`** lists every file here with its owner, reader and test. Create it first, while it is still empty, with its lint test.
- The console's UI rules are an owner file too, but live with the console (`console/ui_rules.tsv`), guarded by the console's own lint test.
- Ids never change and are never reused; a retired row is marked, not deleted.

## Stories and policies

- A story says what outcome counts as done; a policy says "when X, do Y", names thresholds only by name and lists the stories it serves. An acceptance item cites a policy id instead of restating the rule.
- Story status is progress, so it lives in the agent file: `proposed` (an agent-only row, a legal home for work before the owner adopts it), `partial` (names an open blocker and the items left), `done` (has a row in `story_checks.tsv`), `merged` (points at its home story, whose check covers it). Test that each status points at something live: a closed blocker behind a `partial` story is drift.
- Policy status is the owner's approval, so it stays in the owner file: `proposed`, `approved`, `retired` (keeps its id, names no threshold).
- Show the owner the number of drafts waiting in `proposed` columns as one ask; unseen, they pile up across reviews.

## Thresholds

A threshold enters only when code reads it or an approved policy names it. A rename or merge goes through an old → new map in code; the old name is never read again, and a client value left under it is reported by the doctor, never moved silently. A client's own value is a confirmed fact, never an edit here.

## Facts and decisions

- A fact (`fact_keys.tsv`) is one business value per client scope. It always needs a human confirm to count; an agent may only write it as pending.
- A decision (`decision_keys.tsv`) is per entity, and its `confirm` column says whether a human must confirm it. A key that can move money never skips the gate.
- When the agent and a human judge the same thing, store two keys and let the human's win; never one value whose trust flips.
- A confirm that depends on a short platform history freezes the evidence it relied on (the `stage_snapshot` row in `decision_keys.tsv`). Confirms made before that show "unknown", never back-filled.

## Message codes

Every sentence in `--json` is a code plus params. The test fails on a code emitted but not registered, registered but never emitted, a call with the wrong params, or prose with no code beside it, in every verb. Add one `meaning_<lang>` column per language your clients read.

## Alert rules

Each rule compares an entity's latest settled day with its own recent settled days; `threshold` is the multiple (`ge`) or fraction (`le`) that fires it. A new grain, metric or comparison still needs code; the row makes the rule visible, named and tunable. The send verb pushes from the report's output and writes only its own cooldown table.

## Story checks

One row per done story. The runner allows only read verbs and refuses the whole run on any other; it exits 0 whatever the result, because a failed story is a finding. `needs` lists tables that must hold rows for the scope, or the story is skipped with a coded reason, never passed. `—` in every column means the story is proven by CI tests only.

The runner fills `{scope}` in a verb with the scope as an SQL string literal, so an SQL check counts that scope's rows only; a test fails an SQL row without it.

`expect` terms, space-separated, all must hold. A path is dot-separated keys; `[]` takes every element of a list, `*` every value of an object.

| Term | Holds when |
| --- | --- |
| `path` | some value at path is not null |
| `path>0` | some value is a number > 0, or a non-empty list, object or string |
| `path=V` | some value is V (compared as JSON text) |
| `!term` | the term does not hold |

To prove "every row", count the rows that break the rule and expect `[].n=0`: a check like that also holds when nothing is waiting.
