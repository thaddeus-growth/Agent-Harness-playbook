# Triage an agent-filed issue

Issues arrive in the reporter's words: an agent operating the harness, the client's own agent, the owner clicking in the console, a demo run, a golden diff. The triager adds the first line, and that line routes the issue.

## The triage line

```
**Story:** S.. ①  · **Policy:** P.. (if a rule is involved)
**Lane:** owner-independent | needs the owner's word (on what) | owner merge (which risky path)
**Found by:** nightly check | recheck | demo run | client's agent | owner in console | building #N | golden diff
**Class:** a row of bug-classes.md (e.g. zero-as-null) | new: <name> | none (not a data bug)
```

Then: `## Problem` (reproduced on a fixture or a sandbox copy of real data), `## Smallest fix`, `## Acceptance`.

## Judge it before building

1. **Does it hold?** Reproduce it on a fixture or a sandbox copy; never on the live host.
2. **Which class?** bug · misleading output · presentation · by design. "By design" gets a reply saying why, and no change. A data bug also names its row of [bug-classes](bug-classes.md) on the `Class:` line; when a class comes back, the fix is one guard test over the whole class, not one more fix for the instance.
3. **Does it break a written invariant?** Check the agent instructions. A batch of issues that conflicts with them becomes one owner question, not N fixes.
4. **Which story item does it serve?** None: write a `proposed` story row first.
5. **Does it change meaning?** Ship now the part that changes none (honest labels, coded reasons, evidence, a named step 1). Put the rule change in the owner queue as one question with options. Neither waits for the other.
6. **What does it do to the contract?** none · additive · breaking. Breaking goes on the risky list.
7. **Smallest change on existing tables and paths.** No new subsystem to close a gap.
8. **Lane.** Anything on or next to the risky list (the gate, the write path, human-table schema, owner files, thresholds, contract removals) waits for the owner's merge, even when the fix looks owner-independent.
