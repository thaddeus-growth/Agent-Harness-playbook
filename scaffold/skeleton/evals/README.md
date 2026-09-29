# evals/: agent-behaviour evals

One eval per rule in SKILL.md: a prompt, the commands the agent may run, and what counts as keeping the rule. An eval counts only if it fails when its rule is removed. Evals make live model calls, so they run by hand, outside `tests/`; the result is recorded with the release (BUILD.md, B8). This folder never ships.
