# Working in the playbook

This repository is the **parent of every growth-marketing harness** (KOL, Amazon Ads, Meta ads, SEO, ...). A harness adds only its channel: its pulls, reports, registries and the hooks the kit calls. Everything here exists because two or more harnesses need it. Why it is shaped this way: [RESTRUCTURE.md](RESTRUCTURE.md). Read [ROADMAP.md](ROADMAP.md) before you start.

## Keep it small

- **Channel code stays in the harness.** A platform's API, fields and vocabulary do not enter `kit/`, `console/`, `build/` or `scaffold/`. If only one harness needs it, it lives there.
- **A new kit module goes in the pack named for its concern, never in `base`.** It moves to `base` once two harnesses import it from code merged to their main branches. A module no harness imports for two minor versions is deleted ([kit/packs.tsv](kit/packs.tsv)).
- **Packs stay decoupled.** A pack imports only `base` and itself, and a test enforces it. The kit is stdlib only, and `from kit import X` paths do not move.
- **A lesson arrives as a small change with a test,** from a harness that paid for it. Say where it was seen; if once, say once. No new rule without a check that fails when the rule is broken: a rule nothing enforces is deleted, not kept.

## Keep each change focused

- **One pull request, one concern.** A kit change, a build-tool change and a scaffold change are three pull requests, each with its own test.
- **A changed byte the vendor tool copies needs a `kit/VERSION` bump and a regenerated `kit/MANIFEST.sha256`** ([kit/README.md](kit/README.md), "Upgrades and drift"). A move or rename breaks every harness that imports it: ask first.
- **Never edit a harness's vendored copy;** the fix goes here first.
- **Docs have one home.** The README is the overview and stays short; detail goes in `docs/`; a lesson goes in `docs/lessons.md` once. No new top-level file without a reason in the pull request.

## Before you push

`python3 run_all.py` (Python 3.11 or newer; macOS's own `python3` is 3.9) must end `RESULT: N passed`.
