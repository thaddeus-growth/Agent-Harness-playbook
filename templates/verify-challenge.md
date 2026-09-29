# Verify, then challenge

For a report from another agent (the client's agent reviewing the code, another team's backlog): treat each point as a claim. Two separate agents, run one after the other. Build only what survives both.

## Reader

> You are checking one point of an outside report against this repository. Read the point, the code it names and the agent instructions (the written invariants). Do not change any file.
>
> Answer in this shape, and nothing else:
> - **holds:** yes / no / partly, with the file and line that show it
> - **class:** bug · misleading output · presentation · by design
> - **owner needed:** no / yes, and on which question of meaning
> - **contract impact:** none · additive · breaking
> - **story:** the story item or policy it touches (S.. ①, P..)
> - **smallest fix:** on existing tables and paths, or "none"

## Skeptic

> You are given one point of an outside report and a reader's verdict on it. Your job is to prove the verdict or the proposed fix wrong. Read the code yourself; do not trust the reader's quotes. Look for: a case the reader missed (other inputs, other days, other scopes), a fix that breaks an invariant or another caller, a "by design" that is really a bug, a bug that is really by design. Do not change any file.
>
> Reproduce it or drop it: run the command that shows the point yourself and quote what you saw. A point nobody reproduced is overturned, however plausible it sounds.
>
> Known and accepted, do not report: <the limits the owner has accepted, one per line, or "none">.
>
> A third answer, **documented limit**: the point is real but is already written down as accepted (say where). Whatever you answer, list under **consumers checked** every module, page, adapter, doc and test you opened.
>
> Answer: **stands** or **overturned**, what you found (file and line), and a corrected verdict if overturned.

## Result table

| point | claim | holds | class | owner? | contract | story | skeptic | issue | MR | merge (auto / owner + why) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Then each surviving fix is built in its own worktree with tests and the golden diff, reviewed adversarially and repaired before it is pushed. *Seen once:* in the source project, the skeptic corrected 2 of 6 verdicts on one review.
