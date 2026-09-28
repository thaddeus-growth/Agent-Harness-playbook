# Workflows

Recipes for the recurring jobs of an agent using this skill. `{{cli}}` is `uv run scripts/{{cli}}.py`, or the host's link to it. Arguments after `--` go to the script as they are; `{{cli}} compute <report> -- --help` lists them. Every reply follows [SKILL.md](../SKILL.md): the client's numbers only from the JSON, the `meta` window and freshness always, the wording from the message codes.

## 1. Daily check (read only)

When: once a day after the scheduled pulls, or when the human asks whether anything needs attention.

1. `{{cli}} doctor --strict`. Exit 1: stop, and tell the human the warning and its fix.
2. `{{cli}} compute stories --json`. `stories[]`: each story `pass`, `fail` or `skip` with its `reason_code`. The owner's line is "N green, M red: S0x".
3. <<fill: the main report>> `--json`. What moved against the prior period, and by how much.
4. `{{cli}} pending --json`. `asks[]`: every value and action waiting for a human, ready for the console.

Tell the human: the window and freshness, the red stories, what moved, then what waits for them. Post the waiting items to the console (at most 10 open) and say where. Nothing changes until they answer.

## 2. Why did <<fill: a metric the owner watches>> move (read only)

When: the human asks why a number went up or down.

1. <<fill: the report>> `--json --by week`. Which period moved: the value, its `change`, whether the periods are comparable.
2. <<fill: the report by entity>> `--json`. Which entity drove it, with its share of the change.
3. <<fill: the detail report>> `--json`. Which rows inside that entity.
4. `{{cli}} facts history` and `{{cli}} decisions list --json`. Did a person change a value? `meta.thresholds_overridden` says which thresholds are the client's own.
5. <<fill: the report that proposes actions>> `--json`. The proposed fixes, each with its evidence.

Tell the human: each step's own window and freshness, the entity and rows behind the move with their numbers, then any proposals, as proposals.

## 3. A client meeting (pending writes)

When: after a meeting with the client, once its transcript is in the client's data folder (`${{env_prefix}}_DATA_DIR/meetings/<date>/`, recorded with consent), never in the checkout.

1. Read the whole transcript. List, each with its quote and timestamp: goals, the client's own words for things, what they want the harness to do, every number they stated, open questions.
2. A number with no quote is not written. A range stays a range: ask which end to use.
3. A number that matches a key in `ssot/fact_keys.tsv` with no value yet: `{{cli}} facts set <key> <value> --source client_meeting_<date> --reason "<hh:mm:ss> <quote>"`. It stays pending.
4. A number that differs from a confirmed fact: do not set it. Show the human the old value and the new quote; they change it.
5. A value for one entity: `{{cli}} decisions set <entity type> <id> <key> <value> --source client_meeting_<date> --reason "<hh:mm:ss> <quote>"`. A confirmed value stays in force until a human confirms yours.
6. Goals, words, story ideas and rules are proposals: `proposed` rows in the `.agent.tsv` files, then console asks. Never edits to the owner files.
7. `{{cli}} pending --json`. What you wrote, beside what is in force.

Tell the human, at most 10 items: each pending value with its quote, each number you did not write and why, the proposals, the open questions. They answer in the console; nothing you wrote drives a rule until then.
