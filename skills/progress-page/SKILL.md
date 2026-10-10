---
name: progress-page
description: Make or refresh the owner's one-glance progress page for a harness project, as one private Artifact built from the project's docs/ROADMAP.md and stage checks. Use when asked to "update the progress page", at the end of a session handoff, or when the owner wants to see how the project is going without reading the docs. Do NOT use for a design document, a client-facing report, or when the host cannot reach claude.ai (write the dated HTML file named under "No claude.ai" instead).
metadata:
  version: "0.1.0"
  type: workflow
---

# The progress page

The owner does not read the repository's docs; they are for agents. The owner reads one page that says how far the project is, what is at risk and what waits on them. This skill makes that page and keeps it fresh. First built for kol-harness (the layout below is that page's).

**The page is a view; the roadmap is the source.** Every fact on it comes from `docs/ROADMAP.md`, the stage checks (`<cli> compute stories`, `ssot/stages.tsv`), git and a command run this session. If the page and the roadmap disagree, fix the roadmap first, then the page. Never copy a number from the previous version of the page.

## When

At the end of every session, in the handoff ([templates/session-handoff.md](../../templates/session-handoff.md)), and whenever the owner asks. One artifact per project; an update publishes to the same URL and keeps the title.

## Steps

1. **Read the source.** `docs/ROADMAP.md` (Now, Waiting on owner, Next, Done, Parked, Decisions), `ssot/stages.tsv` and the stage checks, `git log` since the page's last version. Read the page's last version with the Artifact tool (`action: "read"`) and build on it; a first page starts from [page-template.html](page-template.html).
2. **Take the numbers from commands, this session:** the test runner's total, the main commit, the kit version, line counts, the verb count. A number you did not just run is not on the page.
3. **Judge each stage on three gates** and put them in the completion track and table: built; checked on synthetic data; run on the client's real data. **Real data is 0 until a real run exists.** Say what blocks each (a roadmap id, never a guess).
4. **Fill the sections in this order**, dropping one only when it has no content. What the owner must do comes first; what rarely changes goes last, folded.
   1. Header: one sentence of verdict (what is built, whether anything ran on real data, what blocks it), a meta line (`vN · date · main sha · tests · kit`), and **three** tiles: where the project is now (its phase), how many stages ran on real data, and what is blocked and by whom.
   2. **Waiting on you**, a table: id, what to do, **who owes it** (for example "client → you"), **days waited** (from the roadmap's date to today; a pill from three days on), what it unblocks. Sort by what blocks most. This is the part the owner acts on.
   3. Since the last version: what the owner decided, what closed, what opened.
   4. Completion track. When the project has phases (a ladder of what the harness may do), draw the track by phase; otherwise by stage. Three rows: built, checked on synthetic data, run on real data. Then the table by stage, if the stages are a separate view.
   5. Scope against a plan or contract, only when one exists: one row per promised item against what is built today. List what is already decided; do not repeat the blocking inputs, they are in "Waiting on you".
   6. Architecture check: four verdict cards (decoupling, focus, minimalism, scope), one line of evidence each.
   7. Cleanup: done with ids, later and what it waits for.
   8. Open roadmap items.
   9. **Appendix, folded in one `<details>`:** the flow figure (hand-drawn SVG with the template's `.dg` classes: green border built, amber partial, red dashed not built, grey dashed outside the harness; blue tags a person, purple the agent, grey a tool; redraw only when the stages change), the table of how fast each layer can be read, what one unit is made of, deployment. These rarely change; they must not push the owner's actions down the page.
5. **Publish.** Load the `artifact-design` skill first. Use the template's tokens (light and dark, phone width, a real fallback stack). The template names no web font host, because nothing published here may name one: when you publish, add the Artifact-allowed Google Fonts stylesheet link for IBM Plex Sans, IBM Plex Mono and Noto Sans SC to the page's head; without it the fallback stack renders. Publish with the Artifact tool to the project's existing URL; the first time, ask the owner to keep the link.
6. **Record the URL** in the project's handoff, not in the repository's checked-in docs when the repository is public.

## Rules

- **Write in the owner's language** (the project's CLAUDE.md or the owner's memory says which); ids, commands and file names stay as they are.
- **No invented facts.** A decision from a meeting the page's author was not told about is not on the page; write "the meeting's outcome is not recorded yet". The next free decision id comes from the roadmap, not from memory.
- **No secrets or client data beyond what the owner already sees.** Tokens, keys and raw rows never appear; the artifact is private, but it is still a copy.
- **Days waited are facts from the roadmap's dates**, not a feeling; if a row has no date, say so.
- **Red is for risk, not for effort.** A stage waiting on the owner is red only when the wait blocks the first deliverable.
- **Keep the title.** It names the project; the explanation goes in the publish description.
- **One page.** Do not add a second progress page per audience; the client gets a report, not this page.

## No claude.ai

When the host cannot reach claude.ai (a Zylos host, an owner who says so), the harness must not depend on it. Then render the same sections as one self-contained HTML file from the template into the client workspace, named `reports/progress-<date>.html`, and push it with the client workspace. Only the publish step changes.
