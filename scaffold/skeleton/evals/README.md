# evals/: agent-behaviour evals

One eval per rule in SKILL.md: a prompt, the commands the agent may run, and what counts as keeping the rule. An eval counts only if it fails when its rule is removed. Evals make live model calls, so they run by hand, outside `tests/`; the result is recorded with the release (BUILD.md, B8). This folder never ships. Start from the playbook's `templates/evals/`: the case layout, the ablation table, and the fixture builder. `kit.guards.evals` checks on every test run that each case quotes the rule it tests, carries the forbidden-verbs grader generated from the verb table, and has its ablation row.

How to run a pair, and which pressure to write in: the playbook's `templates/evals.md`.
