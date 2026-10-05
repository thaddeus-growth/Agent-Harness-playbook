act as the {{harness_name}} agent for {{title}} (client `{{client}}`), using the harness at {{harness_dir}}. read the harness's README.md and SKILL.md once per session; SKILL.md's rules for the agent apply here in full.

run every command through `./bin/{{cli}}` in this directory: it pins `{{env_prefix}}_DATA_DIR` to `./workspace` (the database, raw files, backups, the console log, the client's `.env`). credentials go into `workspace/.env` by a person at a terminal, never through chat; never print or copy them.

start every session with `./bin/{{cli}} status --json` (the harness's one first call), then read the newest `reports/handoff-*.md` for what the database can't hold.

## who and what

<<fill: the client, its markets, the contact, our side, the contract, the decision rule in the client's words>>

## what is where

| Path | What |
|---|---|
| `workspace/intake/` | the drop folder: whatever a person collected from the client (transcripts, briefs, exports). Read the compact facts file first, never the raw transcript |
| `reports/` | for people: dated reports, the client to-do list, and one `handoff-YYYY-MM-DD.md` per session (template: the playbook's `templates/client-handoff.md`) |
| `workspace/console/` | this client's owner console log (`./bin/console`, http://127.0.0.1:{{port}}/) |

## working with the owner

the owner answers in this client's console: `./bin/console` → http://127.0.0.1:{{port}}/. every round:

1. write what you need as harness rows (pending facts and decisions with `--source` and `--reason`); `./bin/{{cli}} pending --json` turns them into ready asks; post them with `./bin/ask add FILE` (at most 10 open, each with evidence, your recommendation and what "no" means). a click there runs the gate through `./bin/{{cli}}` with a relayed code.
2. tell the owner in one line how many asks wait, with the console link. don't restate them in chat.
3. next session, `./bin/ask answers` and `./bin/{{cli}} status --json` show what moved; apply the answers and carry on.

an answer the owner gives in chat instead: record it as a pending value with the chat as `--source`; never confirm it yourself.

## rules

- you propose, a person confirms: never run a gate verb ({{gate_verbs}}), and never answer your own relay code. `.claude/settings.json` denies them; two consecutive refusals mean stop and ask the owner.
- the CLI is the only door: never write into `workspace/` by hand, never touch the database with hand SQL, never call the vendor's API directly.
- talk to the client in {{language}}, from the `--json` message codes, not by pasting English.
- do not fix the harness from here: report blockers or wrong results as issues on the harness repository.
- every handoff ends with a **Distil** list: each insight goes to a harness issue or MR, a proposed story or policy (the harness's `ssot/*.agent.tsv`), a method question (`../_method`), or is marked client-only.
