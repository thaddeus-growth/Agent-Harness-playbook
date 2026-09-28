# One owner ask

The owner queue holds at most 10 open asks. Each one has exactly these fields.

| Field | What goes in it |
| --- | --- |
| `id` | Stable id, never reused (`next-41`, `prs-86`) |
| `kind` | `decision`, `merge`, `value`, `confirm-release` |
| `title` | The question in one line, in the owner's language |
| `why` | Plain facts: what happened, the numbers, where they came from |
| `rec` | The recommendation, and what a "no" would mean |
| `answer` | The owner's words, verbatim |
| `done` | true once the answer has been applied (and where) |

Rules:

- Pre-filter: only asks that change meaning, carry a number, or move money. Apply the mechanical rest yourself and say so.
- Log `rec` next to `answer`. A kind of ask the owner never overrides is a candidate for delegation; one the owner often overrides needs a better recommendation.
- Never ask again what an earlier `answer` already settled.

## In the console

[`console/`](../console/) holds the same ask. Post it with `console/ask.py add`; the owner answers on a page, and `ask.py answers` brings the answer back.

| Template field | Console ask field |
| --- | --- |
| `id` | `id`, unchanged; an answered or withdrawn id is never reused |
| `kind` | `kind`, free text; `step` says what the owner does: `confirm`, `approve`, `choose` or `provide` |
| `title` | `title`, at most 120 characters |
| `why` | `why` (at most 400) and `evidence`: facts, quotes or a table, and the source of each (not checked) |
| `rec` | `recommend` (`value` and `because`) and `if_no`, what a "no" would mean |
| `answer` | the owner's answer: `value`, `comment`, and `suggested`, true when they took your recommendation |
| `done` | `ask.py applied ID@SEQ --where "…"` (the answer's `apply` key): the answer was applied, and where; a "no" is recorded the same way |
