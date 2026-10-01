# Client handoff: what the next session on this client needs, and what the harness learns

One file per session in the client workspace, `reports/handoff-YYYY-MM-DD.md`. The harness's database holds the client's state (facts, decisions, asks, raw data); this file holds what it can't: answers the owner gave in chat, verified findings with their sources, what is blocked on whom, and the **Distil** list, which is how client work improves the harness.

*Seen twice:* the SEO harness's client workspaces (three sites) and the KOL harness's first client. Both found harness bugs and missing rules only through client work.

## Sections

```markdown
# Handoff YYYY-MM-DD: <client>

## State
- harness checkout and branch used; kit version
- what the database holds now (one line per source: rows, as-of)
- what changed this session (reports written, rows proposed)

## Waits for the owner
1. <the gate step only a person can do, with the exact command or console ask id>

## Verified findings
- <finding> (source URL or `transcript hh:mm:ss`, date)

## Distil
| Insight | Goes to |
|---|---|
| <what this client taught> | harness: <MR / issue> · proposed story or policy <S.. / P..> · method question <ask id> · client-only |
```

## Rules

1. **Every insight is tagged.** An untagged row means the handoff is not done. "client-only" is a valid tag: a fact, a decision or the client's own brief.
2. **A rule learned on one client stays `proposed`** (the harness's `.agent.tsv`) until a second client with a different business confirms it. Until then it is a draft the harness may show but never decides on.
3. **Method questions go to the harness owner's `_method` workspace** (a console with no client data), not to a client's console. The answer is applied in the harness repository, and the MR names the ask.
4. **Replay after each milestone.** A fresh agent with only the harness README, SKILL, the client's `CLAUDE.md` and a frozen copy of the workspace writes the deliverable again. Every difference from the expert's version is distilled or marked client-only.
5. **Sources, not summaries.** A finding cites the transcript timestamp or the official page and its date. Another agent's summary is a pointer, never evidence.
