# Two proposed kit modules: `workbook` and `browser-ops`

Status: `workbook` slice 1 (read, the map, the store) is **kit 0.11.0**; the rest is design (proposed 2026-10-10 from the KOL harness's client Emma; the owner agreed to write it into the playbook). The modules are added as `extra` in their own packs and become more only when two independent harnesses use them in merged code ([the admission rule](kit-and-tools.md#the-admission-rule)).

## Why now

Two things came up in one client at once, and each is the kind of thing the next client will bring.

1. **The client runs everything in a spreadsheet.** Emma's team manages every influencer project in one Excel workbook (12 sheets, a budget and target block, a roster per quarter, a group-buy ledger, a discount calendar). The harness must run beside it and put each new result back where the team already looks, for a smooth change-over (KOL harness R29, X39). Any harness that serves a team with a living workbook has the same need, and the video-ad harness's hand-off sheet is the same shape.
2. **The platform has no API.** The client's ad tool (Smartly) can only be driven through a browser; opening an API to a customer takes months. Operating a platform through a browser has to follow the same ladder as an API: read first, then draft, then operate, each important step approved. The social-marketing harness already drives a real browser with Playwright and holds a one-entry writer allowlist; the video-ad harness reads a platform's review results in a person's browser.

## Consumers (evidence for the tiers)

| Module | Harness | Use | State |
| --- | --- | --- | --- |
| workbook | KOL harness | import the roster, codes, labels and calendar; export results back (X33, X39) | planned (kit 0.11.0 holds slice 1) |
| workbook | video-ad harness | the hand-off sheet columns match the client's tracker | planned |
| browser-ops | KOL harness | read Smartly (X35), later draft and operate (X37) | planned |
| browser-ops | social-marketing harness | its Playwright reader and one-entry writer allowlist | exists, its own copy |
| browser-ops | video-ad harness | reading the platform's review results in a person's browser (its X17, X25) | exists, its own copy |

Until two of these are merged against the module, both stay `extra`.

## `workbook` (pack `workbook`, stdlib only)

The harness's relation to a client's file, in one sentence: **the harness never edits the client's file in place; it reads each new copy through `import`, and writes a new file.**

Settled with the KOL harness's owner on 2026-10-10 (its X33: "an abstract, generic store that serves this client and can be reused by any client with the same need"). Slice 1 (read, the map, the store) is kit 0.11.0 (PR #64).

### Five layers, three homes

| Layer | What | Where the code is | Where the data is |
| --- | --- | --- | --- |
| 0 raw | the file as received, under a content-hash name (`kit.raw.import_file`), with a write-once import record (workbook id, market, import time); the file's own last-saved time comes from `docProps/core.xml` | `kit.raw` | the client's data folder |
| 1 store | eight cache tables `wb_*`, rebuilt by the harness's ingest from raw only | `kit.workbook`, `kit.workbook_store` | the harness's database |
| 2 map | `tables.tsv` and `columns.tsv`: where each table is and what each column means | `kit.workbook_map` (format and rules) | **the client's data folder**, entered through the harness's `import` |
| 3 projection | the store's rows, through the confirmed map, into the harness's own tables, each value with its source cell | the harness (it reads `kit.workbook_store.records()`) | the harness's database |
| 4 export | a new file with the client's sheet and header names (later a patch of a copy) | the pack (slice 2 and 4) | a new file |

The map is the only place that knows the client's words. It lives with the client's data, not in a harness's repository, for two reasons: the sheet and header names are the client's, and one workbook may be written by more than one harness, so the one-owner rule has to see every harness's columns at once. The harness keeps only its **field vocabulary** (its own field ids, no client word), and the map binds the client's headers to them.

### Read

`zipfile` and `xml.etree`: every sheet's cells with coordinates, kind (s, n, b, e, str, inlineStr, d), text (shared and rich strings resolved, phonetic runs left out), number, number format, an ISO date when the format is a date one (the 1900 or the 1904 system), the formula text beside the value the file saved (never recalculated; a shared formula's child carries the group id and the master's text, untranslated; an array formula its range; a formula saved without a value reads as empty), the hyperlink target, whether a comment is on it (its text is not kept), and whether its row or column is hidden. Per sheet: the state (hidden and veryHidden sheets kept), the dimension, the merges, the data-validation lists. Per file: the sha256, the core properties, and every zip part with its sha256 (the patch will prove the parts it did not touch are byte for byte the same; pivot caches, drawings, threaded comments and web extensions are not parsed). Namespaces are matched by local name, so a Strict Open XML file reads the same.

A covered cell of a merge is not stored for every covered position (one real workbook merges about 17,000 cells in one range): the merge ranges are kept, and a covered position reads its anchor's value and says so (`merged_from`).

Unsafe input is refused before it is parsed: a part that declares a DOCTYPE or an ENTITY (entity expansion), a part or a total over the size caps, too many parts. A file that is not an xlsx is refused, coded. `.xls` is out; a CSV goes through a harness's own import.

### The store

| Table | One row per | Holds |
| --- | --- | --- |
| `wb_file` | distinct file content (sha256) | workbook id, market, raw path, import time, the file's saved time and author, created, date system, sheet count, size, parts and hashes, the previous import of the same workbook, the map version |
| `wb_sheet` | file × sheet | name, state, dimension, merges, validations, hidden rows and columns, cell count |
| `wb_table` | file × located table | sheet, anchor and its cell, header row, group row, column and row span, data rows, blank rows inside, key headers |
| `wb_column` | file × table × column | header cell, header, group header, path (`Group > Header`), and the map's field, direction, type, personal flag and status (no field: unmapped, still kept) |
| `wb_row` | file × table × key × occurrence | row number, content fingerprint, market |
| `wb_cell` | file × sheet × cell | everything the reader holds, plus the table it is in and a personal flag; every cell of the newest import of each workbook, the in-table cells of older ones (raw keeps all) |
| `wb_change` | import × table × key × column × kind | `row_added`, `row_removed`, `cell_changed`, `formula_changed` (same value, other formula), `column_added`, `column_removed`, `table_missing`, with the old and new text and both cell refs |
| `wb_issue` | file × issue | a coded issue with its cell ref, never a cell's value |

No column names a business field, and none is called `date` (kit.db's default period column); a test holds the column list fixed.

**Tables by anchor, not coordinates.** The map gives a sheet, an anchor text and a search box (`rows 1-30`, optionally `cols A-T`). The anchor must be in exactly one cell of the box (none or several is an issue and that table is skipped). Its row is the header row. The block is the run of non-empty header cells around the anchor, so blocks side by side are separate tables. A non-empty row right above is the group row (a merged group header reads on every column it covers). A header that is a formula reads by its saved text; headers compare after NFKC, collapsed spaces and case folding. The data runs from the next row to the stop: a cell of the block that starts with the stop text (`QUICK SUMMARY`), `blank:N` (N blank rows in a row), or the sheet's last row. Blank rows inside are skipped and counted; hidden rows are data.

**Keyed rows and the change record.** A table's key is the map's key headers (trimmed text joined with ` + `), never guessed. A repeated key gets an occurrence number and an issue; an empty key, or a table with no key, is matched by its content fingerprint (so only added and removed, never changed). Imports of one workbook are chained by the file's own saved time, then the import time; the same content imported twice is one `wb_file` row; two workbooks are two chains and never compared. `wb_change` is also **the conflict list for write-back**: a patch based on import B lists every change after B on a cell it would set and does not overwrite it.

### The map

`tables.tsv`: `table_id, workbook, sheet, anchor, search, stop, key, market, target, status, note` (`market` a constant, `@Header` or empty; `target` the harness table it feeds, or `-`).
`columns.tsv`: `table_id, header, field, direction, owner, type, status, note`. `header` as written, or `Group > Header` where one block shows a header twice; `field` a harness field id, or `-` (kept, projected nowhere); `direction` `in`, `out` or `both`; `owner` the harness that writes the column; `type` one of `text, int, real, money:XXX, date, md_range, bool, code, label, email, person, url`. A row with table `-` and header `-` names a harness field that has no place in this workbook, and its note says why (the export's "say why" rule).

`status` is `proposed` or `confirmed` in both files: an agent writes `proposed`; only the owner's answer makes a row `confirmed`. A projection reads confirmed rows only (a proposed table drops its columns with it); `pending()` lists everything still waiting.

Three rules, tested: **an outgoing column has one owner** (one `out` or `both` row per workbook, sheet, anchor and header, whichever harness writes it); **every field is the harness's** (in its vocabulary, when it gives one); **nothing unconfirmed is read** (`confirmed()` and `pending()`). Shape problems (columns, closed values, unknown tables, a missing owner or reason, a key header with no column row) are reported first, as one coded refusal.

### Projection

`records(import, map, table_id)` gives a harness's ingest the confirmed rows of one table in one raw file: typed values by field (an empty cell is `None`, never 0; money a number in the type's currency; dates ISO in either date system; booleans; `M/D～M/D` ranges as MM-DD pairs; codes trimmed), each with its source cell `<sha12>:<Sheet>!<ref>`, and an issue for a value that does not fit its type. The harness projects from raw, like every ingest; what it does with a row it cannot take (no fee, an unknown label) is the harness's rule, not the pack's.

### Personal data

The client's workbook holds people's names, addresses and pages. They stay in the client's data folder and its database, and out of every output: a column the map types `email`, `person` or `url`, and any text that looks like an e-mail address, are masked by `summary()` (the read verb's view: tables, unmapped columns, issues, changes) and `mask()`; a key holding a name is masked; the author of a save is masked; an issue carries a cell ref, never a value. The tests plant addresses, names and pages in both synthetic workbooks and look for each one in every output.

### Not wired to one client

The tests build two **SYNTHETIC** workbooks with `kit.testing.xlsx` (no client file is needed or committed) and run both through the same code; only the map differs.

- An influencer-programme tracker shaped like the KOL harness's client's: a label list with a side-by-side block whose headers are formulas (header row 2), a roster under a counters block with a merged group row (header row 16), a quarter sheet with a group row, blank rows inside, a hidden row, a duplicate key, a code with a trailing newline, a formula saved without a value and a summary block below the stop, a calendar of text date ranges (header row 3), and a hidden list sheet behind a dropdown.
- **Lumi Haircare Nordics**: one sheet per market (DK, SE, NO) with headers on row 1 in three languages and fees in DKK, SEK and NOK, a campaign column merged down its rows, real dates in the 1904 system, a status dropdown fed by a hidden sheet, and a payments sheet whose header sits on row 4 under a title block, keyed by invoice number; it has no use-case column, so that field is a "no place" row.

A test also holds that no sheet or header name of either client is in the pack's code.

### Companion export and patch (later slices)

- **Slice 2, companion export.** A new xlsx built from rows, with the client's own sheet names and header texts from the map, so the client can open it beside the workbook, compare it and paste. Nothing is shared with the original file. `kit.testing.xlsx` is the seed; the export is its own module.
- **Slice 4, patch.** A copy of the latest workbook with a listed set of cells set. Everything else is carried byte for byte (the part hashes on `wb_file` prove it). Rules: never overwrite a cell holding a formula; never overwrite a cell `wb_change` shows the client changed since the import the patch is based on (listed, not resolved); set full recalculation on open; a fixed zip order and time stamp so the same inputs give the same bytes.

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

1. `workbook` read, the map and the store, with their tests (first user: KOL X33). Smallest slice, no platform needed. Kit 0.11.0.
2. `workbook` companion export (KOL X39).
3. `browser-ops` policy, step record and allowlist with a fake driver (KOL X35 reads Smartly with it).
4. `workbook` patch.
5. `browser-ops` draft and operate levels, after the KOL harness's X37 design is accepted by its owner.

Each step is its own pull request: a new module, a `kit/VERSION` bump, a regenerated manifest, a `packs.tsv` row and a `CHANGELOG.md` entry; the first consumer vendors it.

## Open questions

- ~~Does the `workbook` pack deserve a pack of its own?~~ Yes: pack `workbook`, so a harness with no spreadsheet never receives it.
- ~~Does the module read `.xls` and `.csv`?~~ Neither: a CSV goes through a harness's own import; `.xls` is out.
- Is a Chrome-extension driver in the kit's reach or only in a host adapter? Recommended: adapter only.
