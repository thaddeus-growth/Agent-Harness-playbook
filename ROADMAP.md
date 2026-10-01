# Agent Harness Playbook roadmap
Updated: 2026-10-01
## Now
- [N1] 2026-10-01 try the Zylos adapter under Node 22 and state the minimum Node version; relax the template's Node 24 pin if it holds (#21)
## Waiting on owner
- [W1] 2026-10-01 allow-list www.anthropic.com in the console leak scan, or leave three README source links out (#23). unblocks: X3
- [W2] 2026-10-01 which console copy stays: the playbook's, the SEO harness's or the outreach one's; does any client use one (#24). unblocks: X3
- [W3] 2026-10-01 whether the SEO harness migrates onto the kit and whether the outreach scraper takes only the guide (#26). unblocks: X5
- [W4] 2026-10-01 go-ahead and a quiet week with the short-video ad harness before selective vendoring changes import paths (#19). unblocks: X2
## Next
- [X1] 2026-10-01 re-vendor the three harnesses onto one kit version: stale digest.py mention, CI jobs with no rules, KOL's forked copy upstreamed (#20)
- [X2] 2026-10-01 selective vendoring: a harness takes only the kit tiers it needs; breaking, so after X1 (#19)
- [X3] 2026-10-01 cut the README to about 2,000 words and split console, host adapter and scaffold from the guide (#24)
- [X4] 2026-10-01 the scaffold sets up Zylos itself: vendors the adapter, writes the manifest, passes node zylos/lib.js check (#25)
- [X5] 2026-10-01 quality evals in every harness, and the SEO and outreach adoption decisions (#26)
- [X6] 2026-10-01 kit modules with no or one user: delete, move or keep; recheck copylint after KOL merges (#22)
- [X7] 2026-10-01 consent check limits, kept template copies, golden engine run end to end (#27)
## Done
- [D1] 2026-10-01 CI, run_all.py, the kit tier registry with its guard test, dead and duplicate parts removed (PR #18)
## Parked
## Decisions
- [R1] 2026-10-01 "I have to keep the zylos(openmax) related" (the owner, in chat, when asking for the cleanup)
## Links
- repository: Agent-Harness-playbook on GitHub (open work is its issues #19 to #27)
- tests: python3 run_all.py runs every suite; CI runs it on every push and pull request
- kit tiers: kit-tiers.tsv, enforced by kit/tests/test_tiers.py
