# Agent Harness Playbook: roadmap

The playbook's own roadmap, in the shape of [templates/ROADMAP.md](templates/ROADMAP.md). Detail lives in the GitHub issues (#19 to #27).

## The loop

Harnesses adopt the kit, learn what is shared and what is not, and the lesson comes back here as a small change with a test. Each session closes one turn of it.

## Where things are

| What | Where | Notes |
| --- | --- | --- |
| Playbook | this repository, default branch `main` | CI runs `python3 run_all.py` on every push and pull request |
| Kit | `kit/`, version in `kit/VERSION` | tiers in `kit-tiers.tsv`, enforced by `kit/tests/test_tiers.py` |
| Zylos adapter and console | `hosts/zylos/`, `console/` | the host every harness is adapted to; always stays |
| Harnesses that vendor the kit | short-video ad harness (two worktrees of one repo), KOL harness, RedNote harness | on three different kit versions |
| Harnesses that do not | SEO harness (forked its own gate), outreach scraper, the ads harness the kit came from | see issue #26 |

## Start on a new machine

1. Clone this repository. Install Python 3.11 or newer and Node 22 or newer.
2. `python3 run_all.py` must end `RESULT: N passed` (about 3,400 checks, 2 minutes).
3. Clone each harness you will touch; the kit is copied into them, never imported from here.
4. Read the open issues, then the **Next sessions** table.

## Session start checklist

1. Read this file and `kit-tiers.tsv`.
2. Fetch every remote: other sessions may have merged since this file was written.
3. Do the one turn **Next sessions** names. Anything else goes to an issue.
4. Before changing `kit/`: a changed byte the vendor tool copies needs a `VERSION` bump and a regenerated `MANIFEST.sha256` (kit/README, "Upgrades and drift"); a move or rename breaks the harnesses that import it.

## Status (2026-10-01)

- **Built:** CI and `run_all.py` (9 suites); the kit tier registry (6 core, 11 stack, 32 extra) with its guard test; dead and duplicate parts removed; the Zylos adapter passes on Linux in CI.
- **Merged / open:** nothing open besides the issues below.
- **Waits for the owner:** the four decisions under **Open decisions**.
- **Waits for a client:** nothing.

## Next sessions

| # | Turn | Needs first | Issue |
| --- | --- | --- | --- |
| 2 | Bring the three harnesses to one kit version: start with `python3 tools/fleet.py status` on the three checkouts; stale digest.py mention, CI jobs with no rules, KOL's forked copy upstreamed | the harness checkouts; each harness owner's review | #20 |
| 3 | Selective vendoring: a harness takes only the tiers it needs (breaking) | turn 2 done, owner go-ahead, a quiet week for the short-video harness | #19 |

Turns 2 to 4 follow [RESTRUCTURE.md](RESTRUCTURE.md) (accepted 2026-10-05): one kit version first, then packs and selective vendoring, then docs. Turn 1 (#21) is done: Zylos adapter 0.1.1, Node 22 minimum. Turn 4 (#24) is done: the README is the overview, the detail is in `docs/`.

## Backlog

- The scaffold sets up Zylos itself: vendors the adapter, writes the manifest, passes `node zylos/lib.js check` (#25)
- Quality evals in every harness; SEO and outreach adoption (#26)
- Kit modules with no or one user: delete, move or keep; recheck `copylint` after KOL merges (#22)
- Consent check limits, kept template copies, golden engine run end to end (#27)

## Open decisions

1. **Anthropic source links in the README** (#23). Owner. Recommend: allow-list `www.anthropic.com` in the console leak scan and restore the three links. "No" leaves sources named by title only.
2. ~~**Which console copy stays** (#24).~~ Decided 2026-10-05: the playbook's.
3. **Does the SEO harness migrate onto the kit; does the outreach scraper take only the guide** (#26). Owner. Recommend: migrate SEO after selective vendoring; outreach takes the guide only.
4. ~~**Selective vendoring go-ahead and date** (#19).~~ Decided 2026-10-05: yes, after turn 2; built in kit 0.7.0 (RESTRUCTURE.md, phase 2).

## Session end checklist

1. Tests green; push; pull requests opened.
2. Rewrite **Status** and **Next sessions** above, dated.
3. File an issue for every loose end; nothing lives only in a transcript.
4. Memory notes only for what the repo cannot hold.
5. Tell the owner in one screen: what to look at, what you need, what the next session does.
