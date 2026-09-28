# {{name}} — operator guide

This guide is for the harness's real user: a client's AI agent operating it for one client. It answers how to install it, point it at a client, run it daily, and which command answers which question. How the harness is built is in the source repository's CLAUDE.md, which a release does not carry. An agent loading this as a skill starts at [SKILL.md](SKILL.md), which points back here.

Everything below is read-only until the queue step: `pull`, `ingest` and `compute` never change the client's systems and never ask for a confirmation. `--json` on any command is the contract: every field is named, and `meta` gives each number's source and window. Every English sentence in it carries a stable code beside it (`warning_codes`, `reason_code`, a failure's `code` and `params`). Tell the client in their own language from the code; `ssot/message_codes.tsv` has a ready wording per language.

## Install

Python 3.11 or newer and `uv`. Each script is a single file that declares its own dependencies; `uv run` fetches them on first use. There is no install step and no shared virtualenv.

    git clone <this repository>
    cd {{name}}
    uv run scripts/{{cli}}.py --help

`{{cli}}` below means `uv run scripts/{{cli}}.py`, or the host's link to it. Arguments after `--` go to the script as they are, so a script's own `--help` is always current:

    {{cli}} compute <report> -- --help

## Configure: environment only

Every command that touches client data needs exactly one variable:

    export {{env_prefix}}_DATA_DIR=/path/outside/this/checkout/client

It is never guessed from the current folder, and a `./.env` next to the checkout is never read: a client's data lives outside the harness, always. Credentials load from a chain: an optional file in your home folder (`home_env_file` in `harness.toml`), then `${{env_prefix}}_DATA_DIR/.env` (template: [.env.example](.env.example)); process environment always wins. `{{env_prefix}}_AUTH_ENV_PATHS=none` uses process environment only. Run this first, and whenever something looks wrong:

    {{cli}} doctor             # every path and credential, and where each came from
    {{cli}} doctor --strict    # the same, exit 1 on any warning

Trust `doctor` over this guide if they disagree.

## First run

1. `{{cli}} doctor`: the data folder and the credentials resolve.
2. `{{cli}} facts init`: the human declares the scope (<<fill: the scope noun, for example the market>>) and answers the required facts. It is the only door a scope enters through; there is no default. Every value it writes is pending until a human confirms it.
3. <<fill: the first pulls and ingests, one line each, with how long a long pull takes>>
4. `{{cli}} compute stories --json`: each story's check, green, red or skipped for missing data.

## Daily loop

    {{cli}} doctor --strict               # stop here if it fails
    <<fill: the pulls and ingests>>
    {{cli}} compute stories --json        # one line for the owner: "N green, M red"
    <<fill: the reports the owner reads daily, one line each>>
    {{cli}} pending --json                # what waits for a human, ready to post in the console

`pull`, `ingest` and `compute` are read-only; running the loop twice is always safe. Nothing changes the client's systems until a human approves items in the queue.

## What needs a human

The harness never raises trust on its own. An agent may write a pending value or propose an action; only a human makes it effective, by a retype at a real terminal, a click in the console, or a one-time code sent back from chat (`{{env_prefix}}_CONFIRM_CODE_SECRET`).

| Command | What it needs a human for |
| --- | --- |
| `{{cli}} facts init` | declares the scope; confirms it only at a terminal |
| `{{cli}} facts confirm <key>` | a stated fact stops being an assumption |
| `{{cli}} decisions confirm …` | a pending decision comes into force (`ssot/decision_keys.tsv` says which keys need it) |
| `{{cli}} queue approve <id>` | a proposed action is approved; the approval expires after `approval_ttl_hours` |
| `{{cli}} execute apply --apply` | the one write to the client's systems; without `--apply` it is a dry run |
| `{{cli}} facts restore` | a backup replayed over live facts; terminal only |

Writes are off until the owner agrees in writing, in chat, with a cap: the writer's allowlist is empty and `{{env_prefix}}_ALLOW_WRITES` is unset. `{{env_prefix}}_KILL=1`, or a `STOP` file in the data folder, stops every write at once.

## Which command answers which question

`{{cli}} --help` lists every command and the question it answers. A question none of them answers is not built yet: do not guess a command, and do not fall back to hand SQL; open an issue.

| Question | Command |
| --- | --- |
| Is the setup sound and the data fresh? | `{{cli}} doctor --strict` |
| Is each story still true on real data? | `{{cli}} compute stories --json` |
| What waits for a human? | `{{cli}} pending --json` |
| Which value is in force, and who set it? | `{{cli}} facts list --json`, `{{cli}} facts history <key>` |
| What was decided for one entity? | `{{cli}} decisions list --json` |
| Which actions are proposed, approved or done? | `{{cli}} queue list --json` |
| <<fill: one row per story: the owner's question>> | <<fill: the report that answers it>> |
