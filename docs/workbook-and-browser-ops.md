# Two proposed kit modules: `workbook` and `browser-ops`

Status: **design, no code yet** (proposed 2026-10-10 from the KOL harness's client Emma; the owner agreed to write it into the playbook). The modules are added as `extra` in their own packs and become more only when two independent harnesses use them in merged code ([the admission rule](kit-and-tools.md#the-admission-rule)).

## Why now

Two things came up in one client at once, and each is the kind of thing the next client will bring.

1. **The client runs everything in a spreadsheet.** Emma's team manages every influencer project in one Excel workbook (12 sheets, a budget and target block, a roster per quarter, a group-buy ledger, a discount calendar). The harness must run beside it and put each new result back where the team already looks, for a smooth change-over (KOL harness R29, X39). Any harness that serves a team with a living workbook has the same need, and the video-ad harness's hand-off sheet is the same shape.
2. **The platform has no API.** The client's ad tool (Smartly) can only be driven through a browser; opening an API to a customer takes months. Operating a platform through a browser has to follow the same ladder as an API: read first, then draft, then operate, each important step approved. The social-marketing harness already drives a real browser with Playwright and holds a one-entry writer allowlist; the video-ad harness reads a platform's review results in a person's browser.

## Consumers (evidence for the tiers)

| Module | Harness | Use | State |
| --- | --- | --- | --- |
| workbook | KOL harness | import the roster, codes, labels and calendar; export results back (X33, X39) | planned |
| workbook | video-ad harness | the hand-off sheet columns match the client's tracker | planned |
| browser-ops | KOL harness | read Smartly (X35), later draft and operate (X37) | planned |
| browser-ops | social-marketing harness | its Playwright reader and one-entry writer allowlist | exists, its own copy |
| browser-ops | video-ad harness | reading the platform's review results in a person's browser (its X17, X25) | exists, its own copy |

Until two of these are merged against the module, both stay `extra`.

## `workbook` (pack `workbook`, stdlib only)

The harness's relation to a client's file, in one sentence: **the harness never edits the client's file in place; it reads each new copy through `import`, and writes a new file.**

Three operations, in the order they should be built:

1. **Read.** `zipfile` and `xml.etree`: every sheet's cells with coordinates, shared strings resolved, the cached value of a formula (and the formula text), merged cells, hidden sheets. No recalculation. Tables are found by an anchor (a header text searched in a row range), not by fixed coordinates: the client's sheets keep several blocks side by side and the header is on row 2, 3 or 16 depending on the sheet.
2. **Companion export.** A new xlsx built from rows, with the client's own sheet names and header texts, so the client can open it beside the workbook, compare it and paste. Nothing is shared with the original file.
3. **Patch.** A copy of the latest workbook with a listed set of cells set. Everything else in the file is carried byte for byte: pivot tables and their caches, threaded comments, drawings, web extensions, styles. A general spreadsheet library can lose some of these, and the client's workbook has all of them. Rules: never overwrite a cell holding a formula; never overwrite a cell the client changed since the import the patch is based on (the conflict is listed, not resolved); set full recalculation on open; a fixed zip order and time stamp so the same inputs give the same bytes.

**The map.** A registry `workbook_map.tsv` (an owner file in the harness) says, per column: the sheet, the header text, the table anchor, the harness field, the direction (`in`, `out`, `both`), the owning harness, the type and a note. One rule is tested: **a column has one owner when it is `out`**; two harnesses cannot write the same column. A feature that has no place in the workbook says why in a row of its own.

Tests use a workbook the test builds itself (no client data). Guards: every part of the original not named in the patch is byte-identical; a formula cell refused; a changed cell reported.

## `browser-ops` (pack `browser`, policy only, no driver)

The module holds the **policy**, not the browser. The driver (Playwright, a browser extension, a person's own window) is an adapter the harness brings.

- **Ladder.** `read` (screens and exports; nothing is typed or saved), `draft` (fill fields, stop before the last click; the platform's own draft state), `operate` (the last click). Each step has a level; a harness opts in to a level in its config; read is the default.
- **Step record.** Every step is written, append only, to the raw layer: time, screen, action, field, value, who approved, and a hash of the screen before and after. A read is a step too.
- **Allowlist.** An owner TSV of (screen, action) pairs that may run at each level; anything else is refused before it runs. Important steps (create or change an ad, switch on, any budget change, pause, delete, anything on settings or payment) need the human gate with a relayed one-time code, like an API write.
- **The write path is the kit's.** A draft or operate step goes through `write_guard`: opt-in switch, allowlist, kill switch, no retries. A browser is another writer, not a way around the guard.
- **People, not tricks.** A login wall, a second factor or a bot check stops the session and asks a person; the module never solves one. Credentials come from the env chain or a keychain, never from a repository, a prompt or a log. The client's terms are asked before the first use (a W row in the harness).
- **Layering.** The browser layer never reaches the database; its output enters only through `pull` / `import` (the CLI is the only door).

Tests: a fake driver that records steps; a refused step; the kill switch; no retry; the record is append only; a read level cannot reach a draft step.

## Order of work

1. `workbook` read plus the map registry and its test (first user: KOL X33). Smallest slice, no platform needed.
2. `workbook` companion export (KOL X39).
3. `browser-ops` policy, step record and allowlist with a fake driver (KOL X35 reads Smartly with it).
4. `workbook` patch.
5. `browser-ops` draft and operate levels, after the KOL harness's X37 design is accepted by its owner.

Each step is its own pull request: a new module, a `kit/VERSION` bump, a regenerated manifest, a `packs.tsv` row and a `CHANGELOG.md` entry; the first consumer vendors it.

## Open questions

- Does the `workbook` pack deserve a pack of its own, or does it belong in `data`? Recommended: its own, so a harness with no spreadsheet never receives it.
- Does the module read `.xls` and `.csv`? Recommended: csv only through the existing import path; `.xls` is out.
- Is a Chrome-extension driver in the kit's reach or only in a host adapter? Recommended: adapter only.
