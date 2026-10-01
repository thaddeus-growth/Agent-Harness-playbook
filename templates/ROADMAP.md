# {{name}}: roadmap

<!-- Copy to docs/ROADMAP.md in the harness. The session rewrites Status and Next at every session end (templates/session-handoff.md). Keep it to one screen per section; detail lives in issues. -->

## The loop

intake → words and stories → registries → data chain → reports → gate → release → operate → next intake. Each session closes one turn of it.

## Where things are

| What | Where | Notes |
| --- | --- | --- |
| Harness repo | `{{repo_home}}` | default branch, open MRs |
| Shared kit and console | the playbook, vendored at `scripts/kit/` and `console/` | vendored version: `scripts/kit/VERSION` |
| Client workspaces | `<<fill: one row per client: repo or folder, its data dir, its console port>>` | client data never in this repo |
| Owner console (build questions) | `<<fill: CONSOLE_DIR and port>>` | the only place the agent asks the owner |
| Saved work not yet on a default branch | `<<fill: branch or patch, and what lands it>>` | |

## Start on a new machine

1. Clone the harness and every repo in the table above. A folder with no remote cannot move: give it one first.
2. Install `uv` and Python ≥ 3.11. Run the harness tests: `python3 tests/run.py` must end `RESULT: N passed`.
3. Recreate what git does not carry: each data dir's `.env` (chmod 600, never committed), the console secrets, tokens from their owners. List them here: `<<fill>>`.
4. Run `{{cli}} doctor --strict` in each client workspace; it names every missing setting.
5. Start the consoles and read the open asks before asking anything new.

## Session start checklist

1. Read `SKILL.md`, this file and the memory notes.
2. Read new owner answers in the console; summarise them in about five lines.
3. Fetch every remote: other sessions may have merged since this file was written.
4. Do the one turn **Next** names. Anything else goes to an issue.

## Status (`<<fill: date>>`)

- **Built:** `<<fill: what works, with test counts>>`
- **Merged / open:** `<<fill: MRs and PRs, with state>>`
- **Waits for the owner:** `<<fill: each item, and what it unblocks>>`
- **Waits for the client:** `<<fill: each item, and where the request is written>>`

## Next sessions

One turn each. Each names what it needs before it can start.

| # | Turn | Needs first | Issue |
| --- | --- | --- | --- |
| 1 | `<<fill>>` | `<<fill: owner answer, token, data>>` | `#N` |

## Backlog

Issues not yet scheduled, one line each, linked. A proposed story waits for the owner; a rule seen on one client stays proposed until another client confirms it.

## Open decisions

The question, who decides, the recommendation and what "no" changes. Each one is also an open ask in the console or an issue.

## Session end checklist

1. Tests green; push; MRs opened.
2. Rewrite **Status** and **Next sessions** above, dated.
3. File an issue for every loose end; nothing lives only in a transcript.
4. Memory notes only for what the repo cannot hold.
5. Tell the owner in one screen: what to look at, what you need, what the next session does.
