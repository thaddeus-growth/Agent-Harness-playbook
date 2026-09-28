# ssot/ — single source of truth

`index.tsv` lists every file here with its owner, reader and test. Create it first, while it is still empty, with its lint test. The example rows show each file's shape; a new harness starts from the header rows, plus the `constants.tsv` row the kit's write path reads.

## Three kinds of file

- **Owner files** (`glossary.tsv`, `user-stories.tsv`, `policies.tsv`): plain business meaning in the owner's language. No commands, flags, file or table names, issue numbers, or a number after a threshold name. Agents never edit them: they propose in the `.agent.tsv` sibling and write an owner row only to apply an answer the owner gave. *(tests/test_ssot.py)*
- **Agent files** (`*.agent.tsv`): the engineering side of the same ids, and the trail of each decision. *(tests/test_ssot.py)*
- **Registries** (`constants.tsv`, `fact_keys.tsv`, `decision_keys.tsv`, `message_codes.tsv`, `story_checks.tsv`): read by code, guarded by tests, changed in the same merge as the code that reads them. A number lives only in its registry; everywhere else uses its name.

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
- Ids never change and are never reused. A retired row stays, marked.

## The registries and their columns

The vendored kit reads these by column name, so the headers are fixed.

| File | Columns | Read by |
| --- | --- | --- |
| `constants.tsv` | `name, default, unit, min, max, group, label_en, label_zh, explain, why` | the kit's thresholds. A client's own value is a confirmed `threshold_<name>` fact, never an edit here |
| `fact_keys.tsv` | `key, group, type, unit, min, max, label_en, label_zh, why`; `type` is `number`, `text`, `date` or `choice:a\|b` | the kit's facts verbs; any other key is refused |
| `decision_keys.tsv` | `entity_type, key, domain, confirm, label_en, label_zh, story, why`; `confirm` is `human` or `none` | the kit's decisions verbs. A key that can move money is never `none` |
| `message_codes.tsv` | `code, params, meaning_en, meaning_zh`, one `meaning_<lang>` per language in `harness.toml` | the kit's messages. The kit's own codes live in the vendored kit; a code defined in both is refused |
| `story_checks.tsv` | `story, verb, expect, needs, note` | the kit's story runner: `verb` is a read verb, `expect` what its JSON must hold, `needs` the tables without which the check is skipped, not passed |

During a fan-out, each worker adds only codes with its own prefix (`<unit>_`), which the integrator gives out. See BUILD.md, "Fan-out rules".

## stages.agent.tsv

One row per build step of BUILD.md: who must sign it (`signer`), the steps that must be signed first (`after`), and the answer, merge or pipeline that signed it (`decided`). A step is never `signed` before every step in its `after`. It is how any agent picks a build up from the files, not from memory. *(tests/test_ssot.py)*
