# ssot/ — single source of truth

`index.tsv` lists every file here with its owner, reader and test. Create it first, while it is still empty, with its lint test. Rows are examples from a paid-ads harness: keep the headers and the rules, replace every row. A new harness starts from the header rows, plus the `constants.tsv` row the kit's write path reads (`approval_ttl_hours`).

## Three kinds of file

- **Owner files** (`glossary.tsv`, `user-stories.tsv`, `policies.tsv`): plain business meaning in the owner's language. No commands, flags, file or table names, issue numbers, or a number after a threshold name. Agents never edit them: they propose in the `.agent.tsv` sibling's `proposed` column and write an owner row only to apply an answer the owner gave. Cells waiting on an open owner question sit on an exemption list (`held`, under `[guards.ssot]` in `harness.toml`) that fails when an entry no longer needs it, so it only shrinks. *(tests/test_ssot.py)*
- **Agent files** (`*.agent.tsv`): the engineering side of the same ids (progress, code locations, proposals), and the trail of each decision. *(tests/test_ssot.py)*
- **Registries** (`constants.tsv`, `fact_keys.tsv`, `decision_keys.tsv`, `message_codes.tsv`, `alert_rules.tsv`, `story_checks.tsv`): read by code, guarded by tests, changed in the same merge as the code that reads them. A number lives only in its registry; everywhere else uses its name.
- The client console's UI rules are an owner file too, but live with that console (`webconsole/ui_rules.tsv`), guarded by its own lint test. The vendored owner console carries its own UI rules, in its own folder, changed only in the playbook.

## Every agent file carries the trail

Four columns, in every `.agent.tsv`:

| Column | What goes in it |
| --- | --- |
| `status` | `proposed` (the agent's idea, not asked yet), `asked`, `accepted`, `dropped`, `retired`. Stages use `todo`, `doing`, `signed`, `blocked` |
| `source` | Where the item came from: `meeting:<date> <hh:mm:ss>`, `doc:<name>`, `prior-art:<date>`, `issue:#N`, `agent:<date>`; several joined by `; ` |
| `ask` | The console ask that carried it to the owner, once asked |
| `decided` | The answer that settled it: `console:ID@SEQ`, `chat:<date> <who>`, `merge:<sha>`; for a stage also `ci:<pipeline>` or `check:<name> <date>` |

Rules *(tests/test_ssot.py)*:

- Every owner row has a sibling row with the same id, `status` `accepted` or `retired`, and a `decided` reference. A row with no answer behind it is not the owner's.
- `asked` needs `ask`; `accepted`, `dropped`, `retired` and `signed` need `decided`.
- A change the owner asks for is a new proposal under a new ask id, never an edit of the answered one.
- Ids never change and are never reused. A retired row stays, marked, never deleted.

## Stories and policies

- A story says what outcome counts as done; a policy says "when X, do Y", names thresholds only by name and lists the stories it serves. An acceptance item cites a policy id instead of restating the rule.
- A story's progress is the agent file's `progress` column: `partial` (names an open blocker in `blocking_issue` and the items left), `done` (has a row in `story_checks.tsv`), `merged` (points at its home story, whose check covers it). Work the owner has not adopted yet is an agent-only row with `status` `proposed`: a legal home for it before the owner adopts it. Test that each progress points at something live: a closed blocker behind a `partial` story is drift.
- A policy's approval is the owner's answer: its sibling's `status` `accepted` with the `decided` that approved it, or `retired` (keeps its id, names no threshold). A rule the owner has not approved yet is an agent-only row with `status` `proposed` and its wording in `proposed` (P01 in `policies.agent.tsv`): code may propose from it, never execute.
- Show the owner the number of drafts waiting in `proposed` columns as one ask; unseen, they pile up across reviews.

## The registries and their columns

The vendored kit reads these by column name, so the headers are fixed.

| File | Columns | Read by |
| --- | --- | --- |
| `constants.tsv` | `name, default, unit, min, max, group, label_en, label_zh, explain, why` | the kit's thresholds |
| `fact_keys.tsv` | `key, group, type, unit, min, max, label_en, label_zh, why`; `type` is `number`, `text`, `date` or `choice:a\|b`; `group` sorts the keys (the examples: `required`, `optional`) | the kit's facts verbs; any other key is refused |
| `decision_keys.tsv` | `entity_type, key, domain, confirm, label_en, label_zh, story, why`; `confirm` is `human`, `none` or `harness` | the kit's decisions verbs |
| `message_codes.tsv` | `code, params, meaning_en, meaning_zh`, one `meaning_<lang>` per language in `harness.toml` | the kit's messages |
| `story_checks.tsv` | `story, verb, expect, needs, note` | the kit's story runner: `verb` is a read verb, `expect` what its JSON must hold, `needs` the tables without which the check is skipped, not passed |

`alert_rules.tsv` is read by the harness's own alert compute, not by the kit; its columns are the harness's (see "Alert rules").

## Thresholds

A threshold enters only when code reads it or an approved policy names it. A rename or merge goes through an old → new map in code; the old name is never read again, and a client value left under it is reported by the doctor, never moved silently. A client's own value is a confirmed `threshold_<name>` fact, never an edit here.

## Facts and decisions

- A fact (`fact_keys.tsv`) is one business value per client scope. It always needs a human confirm to count; an agent may only write it as pending.
- A decision (`decision_keys.tsv`) is per entity, and its `confirm` column says who puts it in force: `human` (a human confirms it through the gate), `none` (in force at once), `harness` (written only by the harness inside another human confirm). A key that can move money is never `none`: it never skips the gate.
- When the agent and a human judge the same thing, store two keys and let the human's win; never one value whose trust flips.
- A confirm that depends on a short platform history freezes the evidence it relied on (the `stage_snapshot` row in `decision_keys.tsv`). Confirms made before that show "unknown", never back-filled.

## Message codes

Every sentence in `--json` is a code plus params. The test fails on a code emitted but not registered, registered but never emitted, a call with the wrong params, or prose with no code beside it, in every verb. Add one `meaning_<lang>` column per language your clients read, and list the language in `harness.toml`. The kit's own codes (`unclassified_error` for a failure with no finer code, the gate's, the facts verbs' …) live in the vendored kit; a code defined in both is refused.

During a fan-out, each worker writes its codes to its own fragment, `message_codes.d/<unit>.tsv` (the same columns), with its own prefix (`<unit>_`); the integrator gives out both. The kit loads `message_codes.tsv` and then every fragment in name order, and refuses a code defined twice anywhere. Fragments need no row of their own in `index.tsv`: the `message_codes.tsv` row covers them. See BUILD.md, "Fan-out rules".

The kit's own codes (the gate's refusals and `unclassified_error`) live in [`kit/message_codes.tsv`](../../kit/message_codes.tsv) and [`kit/message_codes.d/`](../../kit/message_codes.d/); this file holds the harness's. A code in both is refused. The reader and the registry checks are [`kit/messages.py`](../../kit/messages.py) (`lint_registry`, `check_registry_closed`); the every-verb check is `check_verbs` in [`kit/guards/json_contract.py`](../../kit/guards/json_contract.py), which a harness's contract test calls on its own verb table.

## Alert rules

Each rule compares an entity's latest settled day with its own recent settled days; `threshold` is the multiple (`ge`) or fraction (`le`) that fires it. A new grain, metric or comparison still needs code; the row makes the rule visible, named and tunable. The send verb pushes from the report's output and writes only its own cooldown table.

## Story checks

One row per done story. The runner allows only read verbs and refuses the whole run on any other; it exits 0 whatever the result, because a failed story is a finding. `needs` lists tables (`;` between them) that must hold rows for the scope, or the story is skipped with a coded reason, never passed. `—` in every column means the story is proven by CI tests only.

The runner fills `{market}` in a verb with the scope (a market of `harness.toml`) as an SQL string literal, so an SQL check counts that scope's rows only; a test fails an SQL row without it.

`expect` terms, space-separated, all must hold. A path is dot-separated keys; `[]` takes every element of a list (a leading `[]`: the document is a list), `*` every value of an object.

| Term | Holds when |
| --- | --- |
| `path` | some value at path is not null |
| `path>0` | some value is a number > 0, or a non-empty list, object or string |
| `path=V` | some value is V (compared as JSON text) |
| `!term` | the term does not hold |

To prove "every row", count the rows that break the rule and expect `[].n=0`: a check like that also holds when nothing is waiting.

## stages.agent.tsv

One row per build step of BUILD.md: who must sign it (`signer`), the steps that must be signed first (`after`), and the answer, merge or pipeline that signed it (`decided`). A step is never `signed` before every step in its `after`. It is how any agent picks a build up from the files, not from memory. *(tests/test_ssot.py)*
