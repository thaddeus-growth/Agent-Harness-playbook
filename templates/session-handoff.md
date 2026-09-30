# Session handoff: the next session starts where this one stopped

Context windows end mid-project. Each session closes one turn of the build loop and leaves a written starting point, so a fresh session (or another agent) can pick up without the transcript.

## What lives where

| File | Holds | Written by |
| --- | --- | --- |
| `docs/ROADMAP.md` in the harness | The loop diagram, where things are, the **session start checklist**, **status** (dated), **next sessions** (one turn each, with what is needed from the owner), backlog, when to use sub-agents | the session, at its end |
| `<project>/CHANGELOG.md` | Each plan revision: what changed and which owner note or decision asked for it | the session, as it applies feedback |
| `<project>/feedback/<date>.json` | A dated copy of the owner's decisions read from the review page | the session, at its start |
| The agent's memory notes | Only what isn't in the repo: the owner's preferences, account facts (which token is which group), links (review page URL) | the session, when learned |
| `SKILL.md` | How an agent operates the harness: the loop, the verbs, the rules | when a verb or rule changes |

## Session start checklist

1. Read `SKILL.md`, `docs/ROADMAP.md` and the memory notes.
2. Read the owner's feedback from the review page, save a dated copy, and summarise it back in about five lines.
3. Check the spend ledger for entries you didn't make (another session or the owner may have spent). Report them.
4. Do the one turn the roadmap names. Anything outside it goes to the backlog.

## Session end checklist

1. Run tests green, then republish the review page to the same URL.
2. Update ROADMAP **Status** (what is done, dated) and **Next sessions** (the next turn and what it needs from the owner).
3. Update memory notes only for facts the repo can't hold.
4. Tell the owner, in one screen: what they can look at now, what you need from them, what the next session will do.
5. When the session served a client, end with a **Distil** list: each insight goes to a harness issue or MR, a proposed story or policy, or is marked client-only. A rule seen on one client stays proposed until a client with another business model confirms it.

## Rules

- **One turn per session.** "Wire the paid route and make a 4-scene sample" is one turn. "Do the whole ad" is not.
- **The roadmap names what the owner must provide** (a token, a decision, footage) before each turn, so no session starts blocked.
- **Delegate research and review to sub-agents with a written brief.** Examples: prompt craft, vendor docs, frame QA. Keep spending, owner contact and plan edits in the main session.
- **When the context runs low, stop starting things.** Finish the current step, write the handoff, and say what is next.
