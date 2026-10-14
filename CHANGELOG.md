# Changelog

What a harness gets when it re-vendors the kit, and what else changed in the
playbook, newest first. Format: Keep a Changelog.
A release is a `kit/VERSION` (a patch for a fix, a minor for a new module or hook,
a major when a harness must change). `hosts/zylos` has its own `VERSION`, noted
where it moved. The short hash on a version line is the commit that set it; dates
are the merge day. Everything under *Other* is outside the kit: templates, build,
scaffold, console, docs, conformance.

## [Unreleased]
### Other
- `docs/consoles.md`, client console item 8: a page behind a gateway answers within the gateway's limit (about 15 s)
  whatever the read costs. Profile the read first; keep what is left per state of the data, made in a background
  thread started with the console, with a bounded wait, then the previous result and a note, or a self-reloading page;
  never cache a failure; the keep-alive probe's timeout exceeds the slowest page. Seen once (the SEO harness's
  `/insights`, S36), tested in `tests/test_docs_build.py`.
- `docs/roadmap.md` (new): planning a harness that already runs. The vision cut into four to six pillars plus one for
  operations; a read-only inventory per pillar (state, evidence, smallest task, size, a do-not-build-now list); the
  roadmap kept in the story registry (an owner row with its milestone, an agent row with proof commands as DONE and
  `owner pending: question? (recommended: answer)` notes), a generated view with `--check` and registry tests; three
  open milestones plus later; one story per session on a cheaper model from a fixed builder prompt, merged by the
  coordinator, released per milestone; issue templates for production testers; who works where, the directory layout
  and the git rules. The SEO harness's roadmap of 2026-10-14 is the worked example. Seen once. Linked from the README;
  `tests/test_docs_build.py` holds its sections, its method items and the builder prompt.
- Lessons from the SEO harness, closing the loop on writes into someone else's system, each seen once there and
  tested in `tests/test_docs_build.py`. `docs/rules.md`, stories item 7: "done" for a written object is a fixed
  checklist recomputed from fresh data; a check older than the last write is `unknown`; a slow outside system gets a
  due list after a set number of days, never an assumed delay; a work item closes with its object (S43, S48).
  `docs/operating.md`, "Code filters; the agent judges": the other way round, a mechanical check that lives only in a
  prompt belongs in a read-only verb, and the prompt keeps the judgement. `docs/consoles.md`, one client, one
  workspace: a copied template drifts from its release, and the doctor compares each copy with the harness's template
  (S45, S48; not yet in the kit).
- `docs/lessons.md`, "A past winner the harness cannot say is studied for its mechanism, never copied": a client's
  strongest past ad that nothing in the plan can say is not a template and not a move; the harness proposes the
  mechanism (who it reached, how it opened, its timing, why people converted) for a person to confirm. A reference the
  client hands over to be copied is the other case. Seen once, in the video-ads harness (owner decision 2026-10-13).
  A judgement, not code: `tests/test_docs_build.py` pins the lesson's wording and its link from `docs/path-weights.md`.
- `docs/rules.md`, data layers: a page the harness delivers is drawn by the harness (code and test: `kit.guards.pages`,
  kit 0.30.0 below; tested in `tests/test_docs_build.py`).
- `docs/rules.md`, human-in-the-loop rule 13: an open question never holds the plan (code and test: `kit.questions`,
  kit 0.28.0 below; tested in `tests/test_docs_build.py`).
- `docs/autonomy.md` (new): autonomy levels L0 to L5, like SAE's driving levels, by what proof a write into
  someone else's system carries: L0 hand-carried, L1 per-change consent (`tty`, `relay`, or the approve of a known
  payload), L2 scoped approval (rule 12), L3 an owner-switched mode over a closed registry of revertible kinds,
  L4 rule-started work and L5 full autonomy (neither built; what L4 would need and why no harness is at L5). Per
  level: who decides, the proof recorded, the checks, what is revertible, the default caps, how it switches down
  and the write kinds allowed; eight rules at every level (the harness classes each write; a level is a ceiling
  per kind; raising takes a person, lowering anyone; expiry and caps counted from the archive; only revertible
  kinds above L1 and a code-free undo at L3; a stop issues no code; the digest; the approve still required through
  L3); the judgement that a retype and a relayed code are one level and the item's approve is the destination,
  so the owner's S46/S47 sit at L2/L3, not L3/L4; the SEO harness as the worked example; what the Amazon Ads
  harness must add. `docs/rules.md`: rule 8 admits an owner's mode switch retyped on a page; rule 12 points at the
  levels; the write door's rule 3 lets an L3 mode draft and publish in one step where nobody can preview in place.
  Seen once, in the SEO harness (S46, S47; owner decisions 2026-10-13). Tested in `tests/test_docs_build.py`,
  which holds the doc to `kit/autonomy.py`'s levels and proof labels.
- `docs/rules.md`, Registries item 9: a client's vocabulary is its value too. The options a harness mines or proposes for
  one client (a creative dimension's values, words taken from its own or its competitors' ads) are rows in that client's
  database, confirmed or retired by a person there; `ssot/` keeps the schema (the axes and their code letters) and the
  generic seeds a builder writes for every client. Seen once, in the video-ads harness (owner decision 2026-10-13).
  Tested in `tests/test_docs_build.py`.
- `docs/rules.md`, human-in-the-loop rule 4: one chat yes is quoted for one code only, and a
  wave that needs one yes gets one scoped approval (not yet proven: no harness refuses a reused
  quote). New rule 12: a gate the human clicks through proves nothing, so the harness tiers changes
  by the live diff (routine reversible kinds get one capped, expiring, scoped approval per wave;
  public or irreversible ones keep per-change proof; the approve checkboxes start unticked).
  Workflows rule R14 and `docs/operating.md` item 9: the owner's threat model (`config.threat`) is
  in every reviewer, finder and skeptic prompt, and review stops when only out-of-model findings
  remain; the build reviewer now also gets R11 (`config.known`). Seen once, in the SEO harness
  (S46). Tested in `tests/test_docs_build.py` and `templates/workflows/tests/test_scripts.py`.
- `docs/rules.md`, "Writing into someone else's system": six rules for a write door whose content
  is made after the approval (only approved work of one kind; the gate binds the file's sha256,
  or the live object as read just before a change of state; stage hidden, preview in place,
  then publish; archive every write under raw; read back and say so; revert is the inverse
  write, never a delete). Approve rule 5 points at it. Seen once, the SEO harness's store write
  (`scripts/apply_shopify.py`). Tested in `tests/test_docs_build.py`.
- `docs/rules.md`, "What an agent judges": a judgement quotes the page and the harness checks
  each quote word for word on the kept copy (markup, entities, spacing, dash and quote marks
  normalised; inline tags dropped without a space); an outside party is classified by its
  place in the value chain apart from the analysis switch, and a "no" stays a row on a
  read-only page; a large group competes on a segment, per market. In the data layers: a
  partial (filtered) paid pull is filed apart and flagged; an overview runs each report's own
  build and adds nothing to it. `docs/consoles.md`, client console item 7: a form the client
  fills offline is exported from the key registry, its answers come back as pending values
  sourced to the file. Each seen once, in the SEO harness. Tested in `tests/test_docs_build.py`.
- `templates/design-first.md` (new; used, not copied): before a feature an owner has to understand
  and steer, research it (the prior-art scan), write one design page in the owner's language (what
  it is, how it decides, the few asks each with a recommended answer and one reply that agrees to
  all, the tables it will store, append-only columns proven on a scratch copy of real data), show
  a clickable demo drawn by the harness's own renderer from a synthetic world, wait for the owner's
  yes, then write the code, after which the harness redraws the demo from a synthetic fixture to the
  same private link. The demo's guard is one Python block a harness copies (`demo_problems()`: the
  world marked `"synthetic": true` and nothing else, `DEMO` ids, `kit.pagestyle`'s synthetic badge
  with its words in the page's first header before the decision, `problems()` with the harness's
  own style and command patterns, never in any client's data folder, no client term by
  `kit/tools/leak_guard.py` in any form the page reaches a reader (as written, unescaped, as shown
  with its tags gone, and the page data's JSON escapes read back), failing closed); and a
  checklist. `docs/lessons.md` keeps what it paid for; a new harness's `CLAUDE.md`
  (`templates/AGENT_INSTRUCTIONS.md`), the prior-art scan's "When to scan" and BUILD.md's B0.5
  point at it. Seen once, in adcut-harness (its ROADMAP X45). Tested in
  `tests/test_design_first.py`, which runs the template's guard on a synthetic page drawn with
  `kit.pagestyle` and tries each way a demo can break the rule (a term in the page data or split by
  a tag, a badge folded, hidden or commented out, a demo in a client's data folder outside its review
  folder among them).
- Lessons from the SEO harness on data and platforms, each seen once there and tested in
  `tests/test_docs_build.py`. `docs/rules.md`, data layers: a datum a later decision needs is built from all of raw,
  never the newest snapshot alone, and an object's lifecycle (proposed, decided, written, verified or broken) is
  derived and names no platform (S52). The write door, rule 7: the right to write binds to a host id, never to a
  switch that travels with a copied database; an agent narrows it, only the owner moves it (`write_host`, S34, S47).
  Rule 8: one gate for every platform and an adapter per platform row, the gate extracted when the second platform
  arrives (`apply_gate.py`, S51). `docs/lessons.md`: an export branch never merges back into main.

## [0.30.1] - 2026-10-13
### Fixed
- `kit.guards.pages` read a vendored kit that sits at `kit/` (`scripts_dir = "."`): the skip path was `./kit`, which matched nothing, so the guard flagged its own host line. The path is normalised. Seen in the video-ads harness, which allowed the line meanwhile.

## [0.30.0] - 2026-10-13
### Added
- `kit.guards.pages` (pack `testkit`) and `suites.page_links(root)`: a page the harness delivers is drawn by the harness
  (`docs/rules.md`, data layers), so no file the harness ships links to a hosted artifact. `check_links(root)` reads
  what `git archive` ships (untracked files are not read; nothing tracked is a problem; with no repository the whole
  tree less .git, virtualenvs, caches and node_modules, never a skip) as text unless it holds a NUL byte (UTF-16 with a
  byte order mark is decoded) or is over 5 MB, follows no symlink, and
  names each file and line that holds a hosted-artifact link, never the link. A line is read after HTML-entity,
  percent (twice), `\uXXXX` and backslash decoding, with a trailing dot or a port on the host and a link wrapped across
  a line break; a link built from parts is not read. Not read: the vendored kit and console (when they hold a
  MANIFEST.sha256) and harness.toml's `[guards.pages]` tables. A link in an export-ignore file is fine.
  `[guards.pages]` takes `hosts` (more hosts) and `allow` ({path: reason} or {path: {reason, max}}; a row with no
  reason, a gone path, a file that does not ship or holds no such link now, or one over its `max` is a problem; a bad
  config is a problem). It errs toward a false hit (any text that spells a host, prose included), which an `allow` row
  answers. `self_test()` plants each form. Seen once, in the video-ads harness (owner decision 2026-10-13). A harness
  opts in with `run(suites.page_links(ROOT))` in a test; `templates/harness.toml` documents the table.

## [0.29.0] - 2026-10-13
### Added
- `kit.pathweights.past_signal(units, groups, cfg)` and two thresholds, `past_min_expected_conv` (3) and `past_warn_units`
  (3): past results (a client's past ads, another period and another hand) as a gap signal, never a weight
  (`docs/path-weights.md`, "Past results"). A group of past units is judged against the past pool's own rate, never the
  account's (P(better) under Gamma-Poisson, the prior worth `prior_conv`), speaks once its spend buys
  `past_min_expected_conv` at that rate, and reads strong or weak at `promising_p` and `downweight_p`. `gaps` are strong
  groups with none of the plan's own units, `warnings` weak groups with `past_warn_units` or more. Nothing from it is an
  input of `posteriors()`, `judge()` or `pick()`: the test holds that their results do not change when the past is read
  and that none of their sources names it (no module state is left behind), and it measures how often an equally good group
  reads strong or weak (about 1 in 6 and 1 in 5 with Poisson noise only, a third and 2 in 5 with spread between units).
  The pool includes the group, so a group that is most of it reads conservatively; `p_better` and `expected` are
  rounded and the read is decided on the rounded numbers. Additive: `settings()` gains two keys, and nothing else changes. Seen once, in the video-ads harness
  (its ROADMAP D64), which keeps its own copy for now.

## [0.28.0] - 2026-10-13
### Added
- `kit.questions` (pack `learning`; rule: `docs/rules.md`, human-in-the-loop rule 13): open questions that never hold the
  plan. A question names `by_data` (the data that can answer it, from the harness's closed list of kinds, or `ask_only`)
  and `meanwhile` (what the plan does until then: an action, never a wait). `problems(questions, sources)` is the check a
  harness's tests run (a missing or repeated id, a `by_data` outside the kinds, a blank, placeholder or waiting
  `meanwhile`; an answered or dropped question needs neither). The wait test is a floor: it catches the obvious waits
  (English, a few Chinese, `extra_waits` for a harness's own words); a word that also opens ordinary actions (hold,
  pause, stop) counts only in a short text or with an "until" cue, and a short one may be a false hit, which a reworded
  `meanwhile` answers. `groups()` sorts the questions for a page; `readback(questions, held)` lists the unanswered ones
  whose data the harness now holds and has no read record for (the record is the fingerprint the agent read, so a new
  version of the data prompts again; `ask_only` never does); `self_test()` plants each break and the words its problem
  must say. Pure, stdlib only, no message codes. Seen once, in the video-ads harness (its ROADMAP D65), which keeps
  its own copy for now.

## [0.27.0] - 2026-10-13
### Added
- `kit.autonomy` (new pack `autonomy`; imports nothing from the kit): the autonomy levels' shared mechanics, pure over
  data the harness passes. The tier registry under the harness's own column names (`canon`) and its check
  (`check_tiers`: a tier-B or auto row names its undo, an auto row's undo is a registered code-free undo, no orphan
  undo row); `tier_of`, `eligible`, `ids_of`. The owner's switch, one fact `YYYY-MM-DD:ID,…` (`switch_value`,
  `parse_switch`), on only while its latest history row is a person's confirm, clamped to `max_days`, its ids frozen
  so a registry edit narrows and never widens (`mode`), an agent's write only ever off (`may_set`). The verdicts:
  `approval_why` (L2, a fixed order) and `auto_verdict` (L3: a cap, an unverified last write or a mixed run is a
  stop, no code issued). Caps from the archive (`used`, `tighten` only lowers, `lock`). The record (`gate`,
  `suffix`, `proof_level`), code-free undo (`undo_free`, `undone_by`) and the digest. No harness imports it yet: the
  SEO harness keeps its own copy (`scripts/apply_shopify.py`), so it is extra with no consumer (`kit-tiers.tsv`).
  Test: `kit/tests/test_autonomy.py`.

## [0.26.2] - 2026-10-13
### Fixed
- `tools/leak_guard.py` failed open when a `re:` term sat beside other terms: every term was
  joined into one pattern and a line it missed was passed over. Joined, a later term's
  backreference pointed at another term's group (`re:(a)\1` then `re:(b)\1`: an added `bb`
  passed, though `re:(b)\1` alone refused it); and on Python 3.9 (macOS's `/usr/bin/python3`,
  which a pre-push hook may run) an inline global flag not at the start, such as `(?a)`, only
  warned and reached every term (`re:X\wY` beside `re:(?a)zzz`: an added `X示Y` passed under
  3.9, refused under 3.12). Now only plain terms are joined for the quick pass; every `re:`
  term is always searched on its own. Seen once, in the video-ads harness, whose own guard
  fixed it first. (Carried from PR 83, written against 0.21.0.)

## [0.26.1] - 2026-10-13
### Fixed
- `kit.guards.ssot.check_writes` missed writes 0.25.0 caught. 0.26.0's refinements are dropped where they cost a hit:
  a name is a path when any binding of it in its scope is (0.26.0 kept only the last one in straight code); `self.p`
  is matched by its text in any class (0.26.0 keyed it by the inheritance family); a name imported from any scanned
  module is matched by the name alone, so a star re-export or an import path with a package prefix is followed again
  (0.26.0 looked the name up in the one module the import path named); an attribute that holds an ssot path is a path
  whatever it is called (`self.name`), and only `.name`, `.stem`, `.suffix` and `.suffixes` of a path are its words
  (`.parts` rebuilds the path); a call returns data only when its name's first word reads (read, reads, reader,
  readlines, load, loads, parse) or it ends in rows, so `ensure_ready(SSOT)` or `upload_dir(SSOT)` is a path again.
  0.26.0's additions stay: `m.NAME` and `from pkg import m` are followed, os.walk's file names are not paths, a class
  attribute is followed into `self.p`. The rule prefers a false hit, which a writers row answers, to a miss
  (`docs/lessons.md`, "A guard's refinement never costs the recall it had"); each missed write is a must-hit case of
  `writes_self_test()`, and a test holds the list. Run case by case against 0.25.0, the only hits 0.26.1 no longer
  reports are three false ones: copying out of ssot under a file's own `.name` or `.stem`, and writing a row's own file
  with what a read returned from ssot.
- `facts confirm` without `--value`: a test that it confirms the stored value as stored (0.26.0's behaviour).

## [0.26.0] - 2026-10-13
### Added
- `kit.testing.suites.ssot_writes(root)`: the ssot write rule alone (its self-test and `check_writes`), reading no
  ssot index; `suites.ssot` ends with it. A harness that uses `suites.ssot` already runs it; one with its own
  `tests/test_ssot.py` adds `suites.run(suites.ssot_writes(ROOT))` (0.25.0 said every ssot test ran it on re-vendoring:
  only the generated one did).
### Changed
- `[guards.ssot.write_skip]` is a {folder: reason} table: each row needs its reason, and a row whose folder is gone is a
  problem (a list gives no reasons, so each entry is reported). The vendored kit is always left out, with no row.
### Fixed
- `check_writes` read some paths wrong. Now a path's words (`.name`, `.stem`, `.suffix`) are not a path, so
  `shutil.copy(CONST, DATA / CONST.name)` passes; a name the word `ssot` is in is a path only when it is not bound to
  data (a call that builds no path: `ssot_rows = read_tsv(...)` is rows, `ssot = Path(sys.argv[1])` is still a path); a
  name bound only by plain assignments in its scope's own statements holds what the last one above the line put in it
  (under a branch, loop, try, augmented assignment or comprehension any binding still counts); `self.p` is keyed by its
  class and followed into subclasses in the module (any other attribute by its text, as before), and a class attribute
  (`p = Path(...)` in the class body, then `self.p`) is followed; another
  module's ssot name is followed as `m.NAME` and `from pkg import m`, through a re-export, and only from the module
  that defines it (a same-named data path elsewhere passes); os.open writes unless its flags only read; os.walk and
  os.scandir keep a path. Each entry of `write_code` must find code (a misspelled one beside a good one was silent); a
  file is parsed as bytes (a UTF-8 BOM parses; a NUL byte is reported, never a crash); an expression too deep to read
  is reported, never a crash; a virtualenv or `site-packages` inside the harness is not its code; a writers file row
  under a folder row is used, not stale. `writes_self_test()` tries each form.
- `Thresholds.confirmed()` (and `load`, `get`, `overridden`) drops a confirmed `threshold_<name>` fact outside the
  TSV's current min..max, so the default stands; `Thresholds.out_of_bounds(con)` lists those facts for a harness's
  doctor (the client is asked again) and `facts list --thresholds` shows `default (outside min..max)` for them;
  `facts confirm` without `--value` checks the pending threshold value against today's bounds
  (`fact_value_out_of_range`) and confirms the value as stored. Both are what `docs/rules.md` "Registries" item 9 says: a value
  the bounds refuse is refused when written and again when read, since a bound may have moved.

## [0.25.0] - 2026-10-13
### Added
- `kit.guards.ssot.check_writes` (run by `kit.testing.suites.ssot`; a harness with its own `tests/test_ssot.py` adds
  it, see 0.26.0): `ssot/` is the builder's, and what a client sets or decides through its agent lives in that
  client's own database (`docs/rules.md`, "Registries", item 9). The check fails on harness code that writes under the
  ssot dir, by AST, as `path:line` and never what is written: Path `write_text` / `write_bytes` / `touch` / `unlink` /
  `mkdir` / `rename` / `replace`, `open` (builtin, Path, io, codecs, gzip, bz2, lzma, os) with a write mode (a mode that
  is not a literal counts), `os.replace` / `rename` / `remove` / `makedirs` …, `shutil.copy*` into it, `move`,
  `rmtree`, `kit.atomic`'s writers, and the harness's own helpers named in `[guards.ssot] write_calls`. A path is under
  ssot when its expression holds the dir as path segments, an identifier with the word `ssot` (`SSOT`, `cfg.ssot`,
  `ssot_dir`), or a name bound to such a path in the same or an enclosing scope, or at module level in another module
  it imports; a value read from an ssot file is data, not a path, so reading ssot, copying from it and writing a row's
  own file elsewhere pass. It reads every .py of the harness (`write_code`, default the whole tree; `write_skip`,
  default the vendored kit), never a test, a hidden folder or `node_modules`. `[guards.ssot.writers]` = {path or folder:
  reason} names the builder's own tools that may write there: a row with no reason, or one that writes nothing any
  more, fails. A file that does not parse, or a code path that finds nothing, is not a pass. `writes_self_test()` is
  its planted self-test, also in the suite. A harness whose code writes ssot fails the check: move the client's value to its database, or name a builder's tool in `writers` with its reason. Seen
  once, in the video-ads harness (owner decision 2026-10-13: a client's space thresholds go through a `set` verb into
  an append-only table of its database, over the ssot default).

## [0.24.0] - 2026-10-13
### Added
- `kit.pagestyle` (new pack `pages`, `[kit] packs = ["pages"]`; `docs/page-style.md`): one look for the pages a harness
  writes to guide making and show the reasoning (a plan's map, the next batch, a make page, an insight page). A state is
  colour, word and icon (`chip()`, `vchip()`; the five states of `kit.pathweights`, Okabe-Ito blue and orange, grey for
  closed; each state's text 4.5:1 on its fill in the light theme and both dark ones, and nothing dims it: no opacity,
  the map's hover only as dark as keeps 4.5:1); a status bar and number tiles first (`bar()`, `legend()`, `tiles()`; a
  count is a whole number of items, anything else refused); one decision with one copy button and its hint
  (`decision()`; a command without its hint refused), commands, flags, ids and hashes (either case, after Chinese text
  too) only there and in folds (`data-zone="decision"` / `"tech"`: `details()`, `owner()`); at most three warnings on
  the page, all its boxes together, the rest one line that names the owner's fold (`warnings()`; a limit above three
  refused); a node map with one cell per item, each a link with its title, as many to a line as the waffle column's
  width holds so none is past the map's edge, a stated node's icon never cut, and an empty leaf that says why
  (`node_map()`; a cell without a title or a link and a node with nothing under it and no why refused, a text left None
  empty); cards that unfold row by row with ten dots and if it works / if it does not (`fold_row()`, `card()`,
  `story()`, `dots()`, `expand_all()`); the owner's fold, folded and last, which the more line names by its title up to
  the colon (`fold_name()`), so `owner` and `more` are renamed together. `page()` is the whole document: doctype, lang,
  a CSP that allows no network (`default-src 'none'`, never `'self'`, an image only inline as `data:`), light and dark
  tokens on `:root`, the page data as strict JSON with no `</` (NaN and Infinity refused), filled in one pass (a text
  holding `__DATA__` stays text), a script that holds no word (a button's words, its label too, are its own attributes;
  a copy button comes back to its label after quick clicks and after the fallback). Every visible word is the
  harness's: `Style(words, lang)` lays them over `EN` and refuses an unknown key, an empty word, a template without
  `{n}` or a `more` that does not name the owner's fold; `ZH` is a ready Simplified Chinese set; the module-level parts
  are English; a Style carries every name the module exports; the currency is the harness's (`money`: `"¥{n}"`).
  `problems(page)` is the check a harness's tests run on every page, one line for each rule a page can break (the
  skeleton with the style's own stylesheet and script, nothing loaded from outside the page, the harness's CSS dimming
  or recolouring no state, a status bar or tiles before the one decision zone, one copy button with its hint and only
  there, no command, flag, hash or code outside the zones plus the harness's own patterns, three warnings on the page
  and a more line that names the owner's fold, the owner's fold folded and last, every card in a row, a state's icon and
  word on every chip, node and cell, a title and a link on every cell and none past the map's edge, every in-page link
  landing, a node with nothing under it saying why, no grid wider than a 320px phone's column, its fixed tracks added
  up); `zones(page)` is the visible text by zone. Its test runs the copy button's script under node, so the `kit` suite
  of `run_all.py` names node beside git. From the video-ads harness's page style (seen once, 2026-10-13; the owner made
  it the model for such pages), which keeps its own copy for now.

## [0.23.0] - 2026-10-13
### Added
- `kit.pathweights` (pack `learning`; `docs/path-weights.md`): path weights for variants that come back with spend and
  conversions. Exact Gamma statistics, stdlib only (`gamma_cdf`, `p_above`, `quantile`, run to convergence at any
  shape, ValueError rather than a truncated value; `tenths` rounded toward a half). `posteriors()`: each leaf of a
  partition (its ancestors' keys in order, any depth; a set refused) gets a Gamma-Poisson posterior on its settled
  numbers, the prior borrowed leave-one-out from its parent and worth `prior_conv` conversions, an optional per-leaf
  prior factor clipped to [1 / `prior_shift_max`, `prior_shift_max`]; conversions may be fractional; a node's numbers
  are its leaves' sums and the account covers them all. `judge()`: untested, testing, promising, down, closed, each
  with its reason (the account's warmth decided under the thresholds it is given), and a close proposal (down, enough
  settled spend and units, unlikely to reach `close_ratio` of the account rate) that a person confirms and the harness
  records: never decided here. `pick()`: the next batch over the harness's moves, coverage order when cold, batched
  top-two Thompson when warm, at least `explore_share` from moves not known yet, half a batch per group, `exclusive`
  and `shared` keys (a list or a set, kept sorted), a move within the batch before one up to two over (taken only when
  none fits, the explore share too), the seed from the data, the same in any process. `twins()`: same-tag units whose
  rates differ by `twin_ratio` (above 1) with probability `twin_p`, a clue with what is known to differ. Every
  threshold in `DEFAULTS`; `settings()` refuses one that is not a value, naming the key, and a `downweight_p` not
  below `promising_p`. The test includes the calibration (every leaf equally good, five rounds, spend pushed to early
  leaders: at most 5 in 100 made leaves ever proposed for closing). From the video-ads harness's creative space model
  (seen once, 2026-10-13), which keeps its own copy for now.

## [0.22.0] - 2026-10-12
### Added
- `human.py`: `relay_audit(..., relay_quote, need_quote=True)` and `add_gate_args(quote=True)`
  (`--relay-quote`). A verb whose write leaves the harness on a person's chat yes asks for the
  confirming person's own words, as sent, beside `--relay-user` and `--relay-at`; a relay
  without them is refused (`confirm_relay_quote_missing`), and the words are kept on the
  history reason as ` [quote="…"]`, one JSON string that cannot close the suffix. The code
  proves the person saw the summary; the words show what they said to it. Off by default:
  existing callers are unchanged, and the console (which relays a click, not words) never
  confirms such a verb. From the SEO harness (seen once, an owner decision on its store write,
  2026-10-12).

## [0.21.0] - 2026-10-12
### Added
- `kit.workbook_patch` (pack `workbook`, slice 4 of `docs/workbook-and-browser-ops.md`): cells set
  in a copy of a client's xlsx, stdlib only. Every part it does not set stays byte for byte (a
  sheet with cells to set is edited as text, cell by cell; texts inline, styles kept); the workbook
  part gains full recalculation on open; the same inputs give the same bytes. A formula, a value
  the client entered (not the harness's previous copy, `ours`), a merged cell or a missing sheet is
  never set, listed by ref. `table_sets` places a harness's results by the map (a table's `out` and
  `both` columns by key, new columns in the first column after the header block that is empty on the header row and every row of the table, never under the client's own data, found again by header on the next copy); `cell_sets` fills
  the map's new `cells.tsv`. First consumer: the KOL harness (meta-ads-retail-harness X39), which
  tried it on the client's real workbook: two parts changed, every other part identical.
- `kit.workbook_map`: an optional `cells.tsv` (`field, sheet, cell, owner, status, note`), where a
  harness writes outside a table's rows (a month's figure across a row); a cell written by two rows
  is refused (`workbook_map_cell_twice`); `confirmed()`, `pending()` and `confirmed_rows()` carry
  it, so the file gate binds it; a map without it keeps its version.
### Other
- `templates/claim-scope.md`: "What stops, what warns". A harness picks per channel whether an
  open compliance finding stops the step or warns a person (one switch, `warn` or `block`); where
  the platform's own review stops a bad ad at no cost to the ad, the default is `warn`. Unconfirmed
  spend, the spend gate, consent and technical QC always stop. Seen once, an owner decision in the
  video-ads harness. Tested in `tests/test_docs_build.py`.
- `docs/operating.md`, "Keeping a harness small as it grows": slim before features (an inventory
  by loop stage, one home per concept, a `_parked/` folder with no verbs, docs or tests and a way
  back, no new features while slimming), code filters and the agent judges (candidates listed
  unranked, a skill picks with a `why` the code checks), and a newcomer-agent acceptance test
  (`templates/client-handoff.md` rule 4 points at it). `docs/lessons.md`: a detached job that
  vanished without its end gets one notice on the next listing; `templates/install-checklist.md`:
  keep the harness, data and virtualenv on the internal disk. Each seen once, in the video-ads
  harness. Tested in `tests/test_docs_build.py`.
- Scaffold: the next steps `scaffold/new_harness.py` prints end with the leak guard, in order:
  create the client-term list outside the repository (seeded with the client's name from the
  intake), name it in `[guards.leak] terms`, install `scripts/kit/tools/pre-push.sample` as the
  pre-push hook, push. The hook is installed only once a list is named, since it refuses a push
  without one.

## [0.20.0] - 2026-10-12
### Fixed
- `messages.lint_registry()` (and so `check_registry_closed()`) refuses a registry param named
  after one of `msg()`'s own arguments (`code`, `text`): `msg(code, text, **params)` cannot be
  called with it, a TypeError at the one moment the message is needed. The names are read from
  `msg()`'s signature (`messages.MSG_ARGS`), so they cannot drift. Seen once: in the video-ads
  harness a param named `text` crashed `msg()`, and that harness has held the rule in its own
  contract test since.

## [0.19.0] - 2026-10-12
### Added
- `doctor.py`: `checkout` warns (`doctor_checkout_differs`) when the cwd is in another git
  checkout of the same harness (a `harness.toml` of the same name at or above it) than the
  bound one whose code runs, and the two differ: other HEADs, or docs or code (`*.md`, `*.py`,
  `*.toml`, `*.tsv`, `*.js`, `*.sh`) changed and not committed in either. Several sessions
  work in worktrees; an agent read SKILL.md in one and ran another's harness. Silent outside
  git, without git, in the bound checkout and in another harness's checkout. `git_head()`
  (HEAD plus the uncommitted docs and code; git's optional locks never taken; inherited
  `GIT_*` variables dropped, so a hook's `GIT_DIR` cannot point both at one repository).
  From the video-ads harness (seen once, a newcomer-agent audit).

## [0.18.0] - 2026-10-12
### Added
- A "Never" table of the verbs only a person runs, first in a harness's SKILL.md, held to the
  verb table (`kit/guards/evals.py`; seen once: a newcomer agent found the video-ads harness's
  person-only rules scattered over five places, and that harness gathered them in one table at
  the top of its SKILL.md). `never_problems()` and `check_never_table()` fail when a verb of
  kind `gated`, or one `[guards.evals].extra` names, has no row; when a row names a verb an agent
  may run, or no verb of the table, or does not say why; and when `## Never` is not the first
  `##` section. A cell names verbs in code font, with or without the cli word; a code span with
  a flag is a flag-level gate (`claims --confirm`, an agent's verb with a person's flag, or
  `any --code`, a flag on every verb): its verb must exist (or be `any`) and need not be gated.
  YAML front matter is skipped. The kind `external` is not person-only by itself (an external
  read, a pull, is the agent's own; an eval forbids it only because the run is offline): an
  external write only a person runs (`execute apply`) goes in `extra`. `[guards.evals].never`
  renames the heading. `never_self_test()` plants each fault, `check_never()` reports, and
  `kit.testing.suites.never_table(root)` is the suite a harness's test calls.

### Other
- Template `SKILL.md`: a `## Never` section first, the table of the verbs only a person runs
  (`facts confirm`, `decisions confirm`, `facts restore`, `facts init`, `queue approve`,
  `execute apply`), each with why; "Out of scope" points to it instead of listing them again.
  `templates/harness.toml` sets `[guards.evals] extra = ["facts init", "execute apply"]`.
- Scaffold: a thirteenth generated test, `tests/test_never_table.py` (`suites.never_table`), so a
  new harness's Never table stays the verb table's. A harness scaffolded before adds the table
  and the file (any generated test, calling `suites.never_table`) by hand; until then nothing
  changes for it.

## [0.17.0] - 2026-10-12
### Added
- `tools/leak_guard.py` and `tools/pre-push.sample` (testkit): a push whose added lines, file paths
  or commit messages hold a client term is refused; `--range A..B` and `--tree` check by hand. A
  term matches case- and whitespace-insensitively (`re:` for a regular expression), and a hit names
  the term's line and list, never the term. The lists are client data and stay outside the
  repository: `[guards.leak] terms` in harness.toml (paths or globs, one list per client workspace)
  and `<PREFIX>_CLIENT_TERMS`. Fail closed: no list, a named list missing, an empty or unreadable
  list, a bad pattern, one matching the empty string, or a git error refuses the push. Generalised
  from the video-ads harness's own guard and its local hook, which read every client workspace's
  list (seen once). Fixed here, and still open in that harness's copy: a non-ASCII path was read
  as git's octal escape, so a CJK term in a file name passed; a path hit printed the path, which
  holds the term; full-width and zero-width spellings passed (terms and text are now NFKC-normalised
  without format characters); a binary or empty file's path and a merge's own resolution lines
  were not read; an added line starting `++ ` was taken for a file header. Two `git log` runs read
  every commit, and all terms are tried as one pattern first: 125 commits and 200 terms take about
  0.1 s, not seconds.
### Other
- BUILD.md B1: install the hook before the first push; `templates/harness.toml` shows
  `[guards.leak]`. `kit-tiers.tsv`: `tools.leak_guard`, extra, no consumer yet.

## [0.16.0] - 2026-10-12
### Added
- `kit.testing.run_tests`: a file's own budget, a `# kit-timeout: S` comment (a comment may follow
  it), and `--timeout S` for
  one run's default (still 300 s), so a slow end-to-end file need not make every hang wait as long
  as it does (#56; the video-ads harness's files that build whole outputs take 3-5 minutes, every
  other file under 40 s, and it raised its one timeout to 600 s).
- `KIT_TEST_RUN=1` (`kit.config.TEST_RUN_ENV`) marks a process as a test: the runner exports it to
  every file, importing `kit.testing.check` sets it, `sandbox_env()` hands it to every child. A
  harness that honours the `KIT_TTY` seam only in a test checks it, not pytest's variable (#56; the
  video-ads harness's seam was refused under the runner and passed under pytest). It tells a test
  from a run; any process can export it, so it is no security boundary.
### Fixed
- `run_functions()` fails a file with a top-level `test_*` it never collected, naming it (defined
  below the call, or hidden by a decorator without functools.wraps): such a test never ran, and the
  file still printed `RESULT: N passed` (#56; the video-ads harness, 38 tests in 8 files).
- `sandbox_env()` drops every credential-looking variable (a name ending in TOKEN, _KEY, _KEY_ID,
  PASSWORD or _API, or holding SECRET), as the video-ads harness's own test setup does; a test
  passes the one it needs as an extra.
### Other
- Console: `tests/_t.py`'s `main(globals())` fails a file the same way (#56).
### Changed
- `kit/env.py`: `KIT_TEST_RUN` joins the names never loaded from an env file (with 0.15.0's list).

## [0.15.0] - 2026-10-12
### Changed
- `env.py`: the env chain never loads a gate secret or a test seam from a file, only from the
  process env: any `*CONFIRM_CODE_SECRET` (kit.human's relayed-code key, any harness's prefix),
  `CONSOLE_SECRET`, `KIT_TTY`, `PYTEST_CURRENT_TEST`, `KIT_HARNESS_ROOT`, and `DYLD_*`, `LD_*`,
  `PYTHON*`, plus a harness's own `[env].never_from_file` names. Such a line (plain, `export `
  or spaced) is skipped by `load`, `resolve`, `get` and `load_into_environ`;
  `is_gate_secret()`, `never_from_file()`, `EnvChain.refused()` (file and name, never the
  value). An agent can read or write a .env; with the secret or the seam it could mint its own
  approval. From the video-ads harness (seen once). No harness code changes; a deployment that
  kept one of these in a .env sets it in the process env of what runs the gate instead (the
  steps for a client console and a Zylos host are under Other).
### Added
- `doctor.py`: `env_files` warns for each such line, naming the file and the name, never the
  value (`doctor_env_gate_secret_in_file`, `doctor_env_file_unsafe_name`); its `next` says to
  delete the line and, for the confirm-code secret, the `bin/console` edit below.
  `console_secret` warns when `CONSOLE_SECRET`, which the console withholds from every harness
  call, is in the harness's own process env (`doctor_console_secret_in_env`).
  `<P>_CONFIRM_CODE_SECRET` there is not warned: the harness process issues and checks the code.

### Other
- Scaffold: a new client's `bin/console` exports a fresh random `<P>_CONFIRM_CODE_SECRET` into
  its own process env at each start instead of appending it to `workspace/.env`. **Deployment
  step for an existing client** (its generated `bin/console` is not regenerated): in
  `bin/console`, replace these 4 lines

      envf="$root/workspace/.env"
      if ! grep -qs '^<P>_CONFIRM_CODE_SECRET=' "$envf"; then
        (umask 077; printf '<P>_CONFIRM_CODE_SECRET=%s\n' "$(openssl rand -hex 32)" >> "$envf")
      fi

  with these 2, delete the `<P>_CONFIRM_CODE_SECRET=` line from `workspace/.env`, and restart
  the console; until then a console click ends in the no-terminal refusal.

      <P>_CONFIRM_CODE_SECRET="$(openssl rand -hex 32)"
      export <P>_CONFIRM_CODE_SECRET

  `.env.example`, `templates/harness.toml` and the operator README say where the secret goes.
- `hosts/zylos` 0.2.0: `bin/console.js` exports a fresh random `<PREFIX>_CONFIRM_CODE_SECRET`
  into the console's process env at each start (its relay read it from `<data dir>/.env`,
  which the kit no longer loads); the configure hook refuses a gate secret
  (`*CONFIRM_CODE_SECRET`, `CONSOLE_SECRET`) and writes nothing; the manifest and `SKILL.md`
  config may not name one. Re-vendor `zylos/` and delete the line from `<data dir>/.env`.

## [0.14.0] - 2026-10-12
### Added
- `kit/guards/roadmap.py` and `kit.testing.suites.roadmap`: one ROADMAP.md format. The title,
  `Updated: YYYY-MM-DD`, then exactly Now, Waiting on owner, Next, Done, Parked, Decisions,
  Links; every item `- [ID] YYYY-MM-DD text`, the id's letter naming its section (N, W, X, D, P,
  R) and unique, the date a real day not after `Updated`; at most one Now row; a Waiting row ends
  `unblocks: <ids of the file>`; a Done row ends in a parenthesised reference; a decision quotes
  the owner; Links are `name: where`. `problems(text)`, `check_file(path)`, `check_roadmap(root)`
  (`[guards.roadmap] file`, else `docs/ROADMAP.md` or `ROADMAP.md`; both is a problem), and a
  self-test that plants each break. From the video-ads harness, which has kept its roadmap in this
  format, held by its own test, since 2026-10-01. A second harness copied the format without a test and does not pass the guard yet: a harness
  still on the old template moves its "Start on a new machine" section to `docs/session-handoff.md`, gives each Done
  row its reference, each Decisions row the owner's quoted words and each Waiting row its `unblocks:` before it adds
  `tests/test_roadmap.py`.
### Other
- A new harness is generated with `docs/ROADMAP.md` (from `templates/ROADMAP.md`, now the format
  as a short skeleton, `{{today}}` filled by the scaffolder) and `tests/test_roadmap.py`: twelve
  day-one tests, not eleven. `templates/AGENT_INSTRUCTIONS.md` gains the **Roadmap** invariant.
  Starting on a new machine moves to `templates/session-handoff.md`.
- The playbook's own `ROADMAP.md` is in the format (`tests/test_playbook_roadmap.py`), its stale
  status (kit 0.7.0, 2026-10-05) refreshed as dated Done rows through kit 0.14.0.

## [0.13.0] - 2026-10-12
### Added
- Pack `learning` (new; a harness takes it with `[kit] packs = ["learning"]`): `patternstore.py`, a
  per-client pattern store, generalised from the video-ads harness's own (which keeps its copy for
  now). Each client's evidence stays in its own database; nothing crosses clients. Docs:
  `docs/pattern-store.md`.
  - The row: context x ICP archetype x playbook, the fields the harness's `Shape` names, every value
    an id of a vocabulary the harness passes or null, with `n`, hashed family tokens and outcome
    counts; `group()` pools items into rows, `row_id()` is the content id. `validate()` never raises
    and refuses free text, a trailing newline, unknown or missing keys, wrong types, a bad sum, an
    outcome on a presence-only source and an id that does not recompute; a problem never quotes the
    value. `shape_problems()` for the harness's own test.
  - Two coders: `agreement()`, `meets_floor()` (0.70, from the counts), `agreed_values()`.
  - The shrinkage: `prior()` (mean anchored off the edges, method-of-moments kappa clipped to 5-10
    tests; none for fewer than two labelled cells, `prior_reason()`), `shrunk()`, `ranks()` (ties
    share), `shrink()`; a stdlib Beta CDF and quantile.
  - The store: `TABLES` and `APPEND_ONLY` in kit.db's format (extra human tables), `state()`,
    `live_rows()` (a row retracted by hand in any scope is out of the all-scopes union), `plan()` (an
    export supersedes every live record of what it no longer makes; a duplicate id is inserted once
    and counted), `apply()` (refuses a row whose id does not recompute), `retract()`.

## [0.12.0] - 2026-10-11
### Added
- One human gate per client file (#66; the KOL harness's R34, owner's option B): the map of a
  client's workbook and the values the agent read from it pass ONE retype or ONE relayed code,
  once per version, never one number at a time.
  - `workbook_map.py`: an optional `values.tsv` (key, market, value, sheet, cell or range,
    status, note); fact keys checked when the harness gives them; a map without it keeps its
    version. `texts_confirmed()` (a staged bundle with every row confirmed), `confirmed_rows()`
    and `gate_rows()` (what a bundle confirms that the one in force did not).
  - `file_gate.py` (pack `workbook`): `plan()`, `ask()`, `confirm()`. The console ask and the
    gate are built from the same plan; the binding is both bundles' versions, the row count and
    every value, so it stays small whatever the map's size.
  - `facts.py`: `plan_file()` checks a file's values as `set` would, before any gate;
    `apply_file()` writes them confirmed with the gate's channel (history action `confirm`),
    all or nothing if one moved meanwhile.
  - `pending.py`: a `staged=` hook, one `file` ask per staged client file
    (`pending_summary_files` when there is one).
### Changed
- `pending.py`: with several markets declared or waiting, a fact or decision ask keeps its gate
  and names the market (`gate.market`); `pending_gate_market_ambiguous` is left for a market
  that is not a plain word. A one-market harness gets the same asks as before.

### Other
- Console: a gate may carry `market` (one plain word); the relay passes it as one `--market=M`
  element after the verb, and `expect` holds the same market, so the harness's subject must
  name it (#66).
- Client template: the first call is `doctor` and `pending`, not a status verb (#55).
- `templates/CHANGELOG.md`, rendered into every new harness, and a `changelog` job in both CI
  templates: a merge request that changes the code, a registry, the skill or the console must
  add its line under Unreleased, or say `[no changelog]` in its title. Tested by running the
  job's own shell on sample file lists.

## [0.11.0] - 2026-10-10
### Added
- Pack `workbook` (new; a harness takes it with `[kit] packs = ["workbook"]`): a client's
  .xlsx read as it is and kept as a generic store, for a client that runs every project
  in one workbook (the KOL harness's X33, R29). Stdlib only (`zipfile`, `xml.etree`).
  Design: `docs/workbook-and-browser-ops.md`.
  - `workbook.py`: `read()` gives every sheet's cells with their kind, text, number,
    number format, ISO date (1900 or 1904 system), formula text beside its saved value
    (never recalculated; shared and array formulas kept), hyperlink, comment mark,
    hidden row or column and merge anchor; sheet states (hidden kept), merges,
    validations, core properties and every part's sha256. A part with a DOCTYPE or an
    ENTITY, or over the size caps, is refused (`workbook_unsafe`); a file that is not an
    xlsx is `workbook_unreadable`.
  - `workbook_map.py`: the client's map, `tables.tsv` (where each table is: sheet,
    anchor text, search box, stop, key, market) and `columns.tsv` (what each column
    means: field, direction in/out/both, owning harness, type, proposed/confirmed).
    Three rules: an outgoing column has one owner; every field is in the harness's
    vocabulary; `pending()` lists what the owner has not confirmed and `confirmed()`
    is all a projection reads.
  - `workbook_store.py`: eight cache tables (`wb_file`, `wb_sheet`, `wb_table`,
    `wb_column`, `wb_row`, `wb_cell`, `wb_change`, `wb_issue`) in kit.db's format,
    none naming a business field; tables found by anchor (blocks side by side,
    group rows, a stop text, blank rows inside counted); rows keyed by the map; the
    change record between imports of one workbook (the write-back conflict list);
    `records()` for a harness's projection (typed values, each with its source cell,
    an empty cell None, never 0); `summary()` for a read verb, personal columns and
    any e-mail address masked.
- `testing/xlsx.py` (testkit): a small deterministic xlsx writer, so a test builds the
  workbook it reads. Tested on two SYNTHETIC clients' workbooks (`kit/tests/_workbooks.py`).

## [0.10.1] - 2026-10-10
### Changed
- `TakeStore.approve(version=)`: a caller may bind the spend code to its own approval record (the
  newest history id the approval moves) instead of the ledger's length. A Make page open while
  another job on the same project charges the ledger kept going stale, so the person clicked
  again (adcut-harness, once, after it queued Makes clicked during a running job). The default
  (None) is the ledger's length, as before; a caller that passes its own version keeps the code
  single use and checks the cap again before it pays.

## [0.10.0] - 2026-10-10
### Added
- `kit/guards/changelog.py` and `kit.testing.suites.changelog`: `CHANGELOG.md` starts with
  Unreleased, its releases are in order (versions descending, dates not increasing, each with
  a `### ` section), `SKILL.md`'s version is the newest release (a harness not yet released is
  at 0.1.0), and every `vX.Y.Z` tag has a heading. A self-test plants each fault. Seen in
  amazon-ads-harness, which wrote the same rule for itself.
### Other
- A new harness is generated with `tests/test_changelog.py`: eleven day-one tests, not ten.
  `templates/AGENT_INSTRUCTIONS.md` gains the **Changelog** invariant, naming that test.

## [0.9.0] - 2026-10-08 · `7569fb2`
### Added
- `tools/skill_upload.py` writes the copy of SKILL.md that claude.ai and the Skills API
  accept (`name`, `description`, `metadata`…), leaving the repository's frontmatter, which
  Zylos reads, untouched (#54).
### Other
- Evals: one method; the pair-run lessons move into `templates/evals/README.md` (#46).
- `build`: `apply_answers` applies the owner's answer to a builder's own proposal.
- Scaffold: client wrappers run `<PREFIX>_PYTHON` and refuse a Python older than 3.11.
- `CLAUDE.md`: the parent's rules for working here — small, decoupled, one concern per
  change (#52). Lessons for paid generation jobs (#53). Geo-china fixes (#51).
- Conformance: the `ppc` adapter follows its current CLI; adapters may type values and
  answer a TTY.

## [0.8.1] - 2026-10-05 · `e8e14c9`
### Fixed
- An agent-only row `asked` (sent to the console, not answered) is a legal status in the
  ssot guard; `proposed_statuses` defaults to `["proposed", "asked"]`. Seen in geo-china-harness.

## [0.8.0] - 2026-10-05 · `5867752`
### Changed
- **`facts set` and rollback never replace a confirmed value.** An ungated write over a
  confirmed value is refused (`fact_set_confirmed`), naming the two ways forward: unconfirm
  (lower trust, recorded) or `confirm --value`. Found by conformance C1 in
  amazon-ads-harness. **A harness that relied on the old replace must change.**

## [0.7.0] - 2026-10-05 · `8e13e16`
### Added
- **Packs and selective vendoring** (phase 2 of `RESTRUCTURE.md`): every module belongs to
  one pack in `kit/packs.tsv` (`base`, `compliance`, `generation`, `data`, `testkit`); a
  harness names its packs under `[kit] packs`; `vendor.py` copies only those plus `base`
  and `testkit`, and refuses to leave out a module the harness imports. With no
  `[kit] packs` a harness receives every pack, so existing harnesses see no change.
- `tools/fleet.py`: where each harness's vendored copies stand (phase 1).
### Removed
- `kit/pull.py` (no consumer).
### Other
- `conformance/`: the invariants every harness keeps and a black-box check for two of them
  (C1 a confirmed value is never replaced by an agent, C2 doctor).
- README is the overview; the detail moves unchanged into `docs/` (phase 4).
- `hosts/zylos` 0.1.1: a zombie job is not alive; Node 22 is the minimum (#21).
- `templates/SKILL.md`: agent rules from a second harness's client rehearsal; the
  playbook's own `ROADMAP.md`.

## [0.6.2] - 2026-10-01 · `c9aa076`
### Added
- `scaffold/new_client.py`: one folder per client — wrappers pinned to `./workspace`, its
  console, a `CLAUDE.md` brief, deny rules for gated verbs. `templates/ROADMAP.md`, the
  `docs/ROADMAP.md` every harness keeps.
### Changed
- `kit.copylint`: 兩 reads as 2. Law lens added to the prior-art scan; doctor check for a
  vendor API version still alive. Landed from KOL's local 0.3.1 work.

## [0.6.1] - 2026-10-01 · `b3ebb74`
### Changed
- `--help` examples and JSON keys come from the verb table; `--for-client` on facts and
  decisions `confirm`; the console user comes from the OS account (#37).

## [0.6.0] - 2026-10-01 · `1eb183d`
### Added
- Agent evals, the offline half: `kit/guards/evals.py` holds a suite to what needs no
  model — each case names the SKILL.md rule it tests, carries the forbidden-verbs grader
  generated from the verb table, and has a row in the ablation table (an eval counts only
  if it fails when its rule is cut). Template `templates/evals/` and fixture (#17).

## [0.5.0] - 2026-09-30 · `4b49354`
### Added
- Claim scope and phrasebook (#11).
- **0.4.0, the price registry, landed with this version** (it missed #9): `kit/prices.py`
  reads an owner TSV of prices whose rows stay pending until a person confirms them; a
  blank price is unknown, never zero.
### Other
- Evals run as a pair, the rule's text deleted from the twin. README and templates: sources
  kept apart, cited rules capped and re-verified, one-client rules stay proposed.
- Slimmed: CI and `run_all.py`, the kit tier registry, duplicates removed.

## [0.3.0] - 2026-09-29 · `3af9c7f`
### Added
- Generated creative and real people: `takes` (paid generation planned, priced, capped,
  approved for exactly the plan, kept once), `preflight`, consent, `copylint` (#9).
### Changed
- `kit.human`: attribution by OS account, never `LOGNAME`/`USER`; a third party's own
  confirmation bound into the code's subject (`--for-client`) (#8).
### Removed
- Team review (adviser agree/disagree) from the console, the Zylos adapter and the docs (#5).

## [0.2.0] - 2026-09-29 · `090f788`
### Added
- `scaffold/new_harness.py`: a new harness, green before its first feature.
### Changed
- The kit absorbs `core/` (owner decision, 2026-09-29): raw guards, store and gate checks,
  the one-clock guard, the verb-case contract; `core/` stays as a pointer.
- `queue add` never supersedes an approved row, only pending ones; a dry run says when
  `--apply` cannot send (new optional `allowlist` hook).

## [0.1.0] - 2026-09-29 · `ab1871a`
First kit: the shared modules distilled from amazon-ads-harness. Stdlib only, Python 3.11+,
configured once by a harness's `harness.toml`.
### Added
- `kit/`: config, messages (closed registries), contract (one `--json` document, `meta`,
  coded failures), dates/atomic/raw, paths/env/auth/retry, db (protected human tables:
  triggers, shrink guard, backups, lossy-rebuild refusal, version stamp), human (TTY retype
  or a relayed HMAC code bound to the subject), market, registry, facts, decisions, queue,
  execute (dry run by default), write_guard (kill switch, opt-in, allowlist, no retries),
  pending, stories, guards (ssot, release, layering, JSON contract, boundary, drift), the verb
  table, dispatcher and doctor, and `kit/README.md` with `MANIFEST.sha256`.
- `hosts/zylos` 0.1.0: a manifest-driven Zylos host adapter.
### Other (the playbook before the kit, 2026-09-27 to 09-29)
- The playbook itself: client meetings, the ten stages, decision rights, trust ladder, data
  layers, human-in-the-loop rules.
- `console/`, the owner console, forms sent in place.
- `templates/`: data bug classes each with its fixture test, a full GitLab CI, a test kit,
  the doctor's checks, many-agent workflows, golden-diff engine; `BUILD.md`.
