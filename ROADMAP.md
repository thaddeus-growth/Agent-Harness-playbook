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

## Status (2026-10-05)

- **Built:** CI and `run_all.py` (9 suites); kit 0.7.0 with packs and selective vendoring (`kit/packs.tsv`); the kit tier registry (5 core, 11 stack, 33 extra) with its guard test; `tools/fleet.py`; Zylos adapter 0.1.1 on Node 22; the README cut to the overview, the detail in `docs/`; `kit/pull.py` removed.
- **Merged / open:** the restructure branch (RESTRUCTURE.md phases 2, 4 and 5, and the playbook side of phase 1).
- **Waits for the owner:** decisions 1 and 3 under **Open decisions**; the harness checkouts for turn 2.
- **Waits for a client:** nothing.

## Next sessions

| # | Turn | Needs first | Issue |
| --- | --- | --- | --- |
| 2 | Bring the three harnesses to one kit version: start with `python3 tools/fleet.py status` on the three checkouts; stale digest.py mention, CI jobs with no rules, KOL's forked copy upstreamed | the harness checkouts; each harness owner's review | #20 |
| 3 | Selective vendoring: a harness takes only the tiers it needs (breaking) | turn 2 done, owner go-ahead, a quiet week for the short-video harness | #19 |

Turns 2 to 4 follow [RESTRUCTURE.md](RESTRUCTURE.md) (accepted 2026-10-05): one kit version first, then packs and selective vendoring, then docs. Turn 1 (#21) is done: Zylos adapter 0.1.1, Node 22 minimum. Turn 4 (#24) is done: the README is the overview, the detail is in `docs/`.

## Backlog

- `amazon-ads-harness` passed conformance C1 and C2 on 2026-10-05 after its fix for thaddeus-growth-hacking/amazon-ads-harness#448 (`facts set` and `facts rollback` refuse to replace a confirmed value). The ppc adapter now follows its current CLI: numeric `values`, `init`'s profit-model prompts, `--reason`, no `--type`, and the confirm retyped on a terminal (`tty`).
- The scaffold sets up Zylos itself: vendors the adapter, writes the manifest, passes `node zylos/lib.js check` (#25)
- Quality evals in every harness; SEO and outreach adoption (#26)
- Two proposed kit modules from the KOL harness's client, `workbook` (a client's spreadsheet in and out, stdlib) and `browser-ops` (a guarded browser: read, draft, operate): design in [docs/workbook-and-browser-ops.md](docs/workbook-and-browser-ops.md), no code; the first slice is `workbook` read plus the map registry, once the KOL harness's X33 needs it (owner agreed 2026-10-10)
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
