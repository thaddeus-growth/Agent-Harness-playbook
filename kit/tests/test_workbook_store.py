#!/usr/bin/env python3
"""The generic workbook store (kit/workbook_store.py), on two SYNTHETIC
clients whose workbooks share nothing but the code that reads them.

  1. The store is generic: its column list is this fixed one, no column
     names a business field, no client word from either map is in the
     module, no column is called `date` (kit.db's default period column),
     and the tables are a valid kit.db cache spec.
  2. The influencer tracker: each table found by its anchor (header rows
     2, 16, 2 and 3; a block beside another is its own table; merged
     group rows; blank rows inside skipped and counted; the stop text;
     a hidden row kept), the key with a trailing newline trimmed, a
     duplicate key numbered and an issue, a formula with no saved value
     an issue, a missing anchor, an ambiguous one, a missing sheet, a
     missing stop and a key header the block lacks, each coded.
  3. Lumi Haircare Nordics: the same code, only the map differs: one
     sheet per market, a header on row 1 and one on row 4 under a title
     block, a campaign merged down its rows read on every row, the 1904
     date system; a field with no place in this workbook carries no row.
  4. The change record: v1 then v2 gives row_added, row_removed,
     cell_changed, formula_changed and column_added at the right cells;
     imports are ordered by the file's own save time, not the import's;
     the same content twice is one file; a table gone is table_missing;
     the newest import keeps every cell, an older one its in-table cells.
  5. records(): confirmed rows only, typed (an empty fee is None, never 0;
     money, dates in both systems, booleans, md ranges, codes trimmed),
     each value with its source cell, a value that does not fit an issue,
     an unconfirmed table refused, the market from the map.
  6. Personal data: no planted address, name or page reaches summary(),
     an issue or a records() issue; a changed address and a key holding a
     name are masked; the cells are flagged personal in the store.
  7. Into a kit.db database: every row written (rows in = rows out), the
     same imports give the same rows.
  8. workbook_store.py's msg() calls are closed over its fragment.
"""

import contextlib
import io
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
import _workbooks as X  # noqa: E402
from kit import db, messages, workbook as W  # noqa: E402
from kit import workbook_map as WM, workbook_store as WS  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402
from kit.testing.xlsx import build  # noqa: E402

COLUMNS = {
    "wb_file": ["file_sha", "workbook", "market", "raw_path", "imported_at",
                "saved_at", "saved_by", "created_at", "date1904",
                "sheet_count", "size", "parts", "prev_sha", "map_version"],
    "wb_sheet": ["file_sha", "sheet_no", "name", "state", "dimension",
                 "merged", "validations", "hidden_rows", "hidden_cols",
                 "cell_count"],
    "wb_table": ["file_sha", "table_id", "workbook", "sheet_no", "sheet",
                 "anchor", "anchor_ref", "header_row", "group_row",
                 "first_col", "last_col", "first_row", "last_row",
                 "row_count", "blank_count", "key_headers"],
    "wb_column": ["file_sha", "table_id", "col_no", "header_ref", "header",
                  "header_group", "path", "field", "direction", "value_type",
                  "personal", "status"],
    "wb_row": ["file_sha", "table_id", "row_key", "occurrence", "row_no",
               "fingerprint", "market"],
    "wb_cell": ["file_sha", "sheet_no", "ref", "row_no", "col_no", "kind",
                "text", "number", "num_fmt", "iso_date", "formula",
                "formula_kind", "shared_id", "merged_from", "link", "comment",
                "hidden", "personal", "table_id"],
    "wb_change": ["to_sha", "table_id", "row_key", "occurrence", "header",
                  "kind", "workbook", "from_sha", "old_text", "new_text",
                  "old_ref", "new_ref"],
    "wb_issue": ["file_sha", "n", "table_id", "code", "ref", "detail",
                 "params"],
}
BUSINESS = re.compile(r"coupon|code_|fee|creator|influencer|brand|order|"
                      r"revenue|spend|campaign|price|label|tier|scenario",
                      re.I)


def imp(data: bytes, wid: str, at: str, market=None) -> WS.Import:
    return WS.Import(W.read(data), wid, market, f"raw/{wid}.xlsx", at)


def im_map() -> WM.Map:
    return WM.parse(X.IM_TABLES, X.IM_COLUMNS)[0]


def lumi_map() -> WM.Map:
    return WM.parse(X.LUMI_TABLES, X.LUMI_COLUMNS)[0]


def table(rows, tid, sha=None) -> dict:
    return next(t for t in rows["wb_table"] if t["table_id"] == tid
                and (sha is None or t["file_sha"] == sha))


def issues(rows) -> list[tuple]:
    return [(i["table_id"], i["code"], i["ref"]) for i in rows["wb_issue"]]


def test_generic() -> None:
    print("[1] the store is generic")
    got = {t: list(spec["columns"]) for t, spec in WS.TABLES.items()}
    check("the store's tables and columns are this fixed list",
          got == COLUMNS, got)
    cols = sorted({c for v in COLUMNS.values() for c in v})
    check("no column names a business field",
          not [c for c in cols if BUSINESS.search(c)],
          [c for c in cols if BUSINESS.search(c)])
    check("no column is called date (kit.db's default period column)",
          "date" not in cols)
    src = (_shop.KIT / "workbook_store.py").read_text(encoding="utf-8") + \
        (_shop.KIT / "workbook.py").read_text(encoding="utf-8")
    words = set()
    for m in (im_map(), lumi_map()):
        words |= {t["sheet"] for t in m.tables} | {
            c["header"] for c in m.columns if c["header"] != "-"}
    words = {w for w in words if len(w) > 5}    # shorter ones are any text
    hits = sorted(w for w in words if w in src)
    check("no sheet or header of either client is in the code", not hits,
          hits)
    spec = db.with_human(WS.TABLES, version=1)
    check("a valid kit.db cache spec, every table a cache table",
          set(WS.TABLES) <= set(spec.tables)
          and not set(WS.TABLES) & set(spec.human_tables)
          and WS.FILLS == tuple(WS.TABLES))


def test_im() -> None:
    print("\n[2] the influencer tracker")
    rows = WS.build([imp(X.im(1), "im", "2026-10-10T00:00:00Z", "TW")],
                    im_map())
    spans = {t["table_id"]: (t["anchor_ref"], t["header_row"], t["group_row"],
                             t["first_col"], t["last_col"], t["row_count"],
                             t["blank_count"]) for t in rows["wb_table"]}
    want = {"labels": ("D2", 2, 1, 1, 5, 6, 0),
            "label_counts": ("H2", 2, None, 8, 10, 3, 0),
            "roster": ("D16", 16, 15, 1, 15, 5, 0),
            "q1": ("H2", 2, 1, 1, 13, 7, 1),
            "calendar": ("B3", 3, None, 2, 6, 3, 0)}
    check("each table by its anchor: header row, group row, block, rows, "
          "blank rows", spans == want, spans)
    q1rows = [r for r in rows["wb_row"] if r["table_id"] == "q1"]
    check("the stop text ends the table before the summary blocks; the "
          "hidden row is kept", [r["row_no"] for r in q1rows] ==
          [3, 4, 5, 7, 8, 9, 10], [r["row_no"] for r in q1rows])
    keys = {r["row_no"]: (r["row_key"], r["occurrence"]) for r in q1rows}
    check("the key joins the map's headers, trimmed",
          keys[4] == ("Jan + Ring Talk + RING01", 1), keys[4])
    check("a duplicate key is numbered", keys[8] == (
        "Feb + Twin Posts + TWIN", 2), keys[8])
    check("the issues: the duplicate key and the formula with no value, "
          "each at its cell", issues(rows) == [
              ("q1", "workbook_key_duplicate", "A8"),
              ("q1", "workbook_formula_no_value", "G10")], issues(rows))
    cols = {c["path"]: c for c in rows["wb_column"]
            if c["table_id"] == "q1"}
    check("a group header gives the path; the map's row is found by it",
          cols["Activity Type > IG Reels"]["field"] == "roster.flag.reels"
          and cols["Activity Type > IG Post"]["field"] is None, sorted(cols))
    lc = {c["header"]: c for c in rows["wb_column"]
          if c["table_id"] == "label_counts"}
    check("headers that are formulas are read by their saved text",
          sorted(lc) == ["Counts (Q1)", "Product A", "Product B"], sorted(lc))
    check("an unmapped column is kept, with no field",
          rows_where(rows, "wb_column", table_id="roster",
                     header="Direction")[0]["field"] is None)
    check("the market of a row comes from the map, or the import",
          {r["market"] for r in rows["wb_row"] if r["table_id"] == "roster"}
          == {"TW"} and {r["market"] for r in rows["wb_row"]
                         if r["table_id"] == "labels"} == {"TW"})
    check("hidden sheets are kept", [s["state"] for s in rows["wb_sheet"]]
          == ["visible"] * 4 + ["hidden"])
    bad = X.IM_TABLES.replace("Event\trows 1-5", "Nowhere\trows 1-5") \
        .replace("\tLABEL\trows 1-5", "\tSwap\trows 1-10") \
        .replace("\tCreators\t", "\tCreators (old)\t") \
        .replace("QUICK SUMMARY", "GRAND TOTAL")
    m = WM.parse(bad, X.IM_COLUMNS)[0]
    got = WS.build([imp(X.im(1), "im", "x")], m)
    check("a missing anchor, an ambiguous one, a missing sheet and a "
          "missing stop, each coded", sorted({i[1] for i in issues(got)}) ==
          ["workbook_anchor_ambiguous", "workbook_anchor_missing",
           "workbook_formula_no_value", "workbook_key_duplicate",
           "workbook_sheet_missing", "workbook_stop_missing"], issues(got))
    q = table(got, "q1")
    check("with no stop found the table reads to the sheet's last row "
          "(the summary rows come in)", q["row_count"] > 7, q["row_count"])
    m = WM.parse(X.IM_TABLES.replace("Original Code\tTW", "Code\tTW"),
                 X.IM_COLUMNS + "roster\tCode\t-\tin\t\ttext\tproposed\t\n"
                 )[0]
    got = WS.build([imp(X.im(1), "im", "x")], m)
    check("a key header the block lacks: workbook_key_missing, rows matched "
          "by content", ("roster", "workbook_key_missing", "D16")
          in issues(got) and all(r["row_key"].startswith("fp:")
                                 for r in got["wb_row"]
                                 if r["table_id"] == "roster"))
    m = WM.parse(X.IM_TABLES.replace("Original Code\tTW", "Notes\tTW"),
                 X.IM_COLUMNS)[0]
    got = WS.build([imp(X.im(1), "im", "x")], m)
    check("an empty key: workbook_key_empty, matched by content",
          [i for i in issues(got) if i[1] == "workbook_key_empty"] ==
          [("roster", "workbook_key_empty", "I18"),
           ("roster", "workbook_key_empty", "I20"),
           ("roster", "workbook_key_empty", "I21")], issues(got))
    blank = X.IM_TABLES.replace("QUICK SUMMARY", "blank:1")
    got = WS.build([imp(X.im(1), "im", "x")],
                   WM.parse(blank, X.IM_COLUMNS)[0])
    check("stop blank:N ends at N blank rows",
          table(got, "q1")["row_count"] == 3, table(got, "q1")["row_count"])


def rows_where(rows, t, **kw) -> list[dict]:
    return [r for r in rows[t] if all(r.get(k) == v for k, v in kw.items())]


def test_lumi() -> None:
    print("\n[3] Lumi Haircare Nordics: the same code, another map")
    rows = WS.build([imp(X.lumi(), "lumi", "2026-10-01T00:00:00Z")],
                    lumi_map())
    spans = {t["table_id"]: (t["sheet"], t["header_row"], t["row_count"])
             for t in rows["wb_table"]}
    check("one table per market sheet, the payments under their title",
          spans == {"dk": ("DK", 1, 4), "se": ("SE", 1, 2),
                    "no": ("NO", 1, 1), "pay": ("Betalinger", 4, 3)}, spans)
    check("no issue", rows["wb_issue"] == [], issues(rows))
    check("markets from the map", {(r["table_id"], r["market"])
                                   for r in rows["wb_row"]} >= {
        ("dk", "DK"), ("se", "SE"), ("no", "NO"), ("pay", None)})
    recs, iss = WS.records(imp(X.lumi(), "lumi", "x"), lumi_map(), "dk")
    camp = [r.values["roster.campaign"] for r in recs]
    check("a campaign merged down its rows is read on every row",
          camp == ["Forår", "Forår", "Sommer", "Sommer"] and iss == [], camp)
    check("its source is the merge's anchor cell",
          recs[1].refs["roster.campaign"].endswith(":DK!F2"),
          recs[1].refs["roster.campaign"])
    check("dates in the 1904 system", [r.values["roster.posted_on"]
                                       for r in recs] ==
          ["2026-03-02", "2026-03-09", "2026-06-01", "2026-06-15"])
    check("a fee of 0 is a value, not a missing one",
          recs[3].values["roster.fee"] == 0.0)
    check("the field with no place in this workbook carries no row",
          not any(c["field"] == "roster.label" for c in rows["wb_column"]))
    check("the same tables carry both clients",
          set(WS.build([imp(X.im(1), "im", "x")], im_map())) == set(rows))


def test_changes() -> None:
    print("\n[4] the change record")
    v1, v2 = X.im(1), X.im(2)
    a, b = W.read(v1).sha256, W.read(v2).sha256
    rows = WS.build([imp(v2, "im", "2026-10-01T00:00:00Z"),      # imported
                     imp(v1, "im", "2026-10-20T00:00:00Z"),      # in reverse
                     imp(v1, "im", "2026-10-21T00:00:00Z")], im_map())
    files = {f["file_sha"]: f for f in rows["wb_file"]}
    check("the same content twice is one file", len(files) == 2)
    check("ordered by the file's own save time: v1 before v2",
          files[b]["prev_sha"] == a and files[a]["prev_sha"] is None)
    check("the earliest import of a content is its import time",
          files[a]["imported_at"] == "2026-10-20T00:00:00Z")
    got = {(c["table_id"], c["kind"], c["row_key"], c["header"],
            c["old_text"], c["new_text"], c["old_ref"], c["new_ref"])
           for c in rows["wb_change"]}
    want = {
        ("roster", "column_added", "", "Priority", None, None,
         None, None),
        ("roster", "row_removed", "nano05", "", None, None, "A21", None),
        ("roster", "row_added", "nano06", "", None, None, None, "A21"),
        ("roster", "cell_changed", "nano01", "Basic Info > Cost-NTD",
         "15000", "16000", "G17", "G17"),
        ("roster", "cell_changed", "nano01", "Basic Info > Cost-EU",
         "394.74", "421.05", "H17", "H17"),
        ("roster", "cell_changed", "nano01", "Ads Setting > Data Login",
         "FALSE", "TRUE", "N17", "N17"),
        ("roster", "cell_changed", "nano02", "Basic Info > Email",
         "creator.two@example.invalid", "creator.two.new@example.invalid",
         "F18", "F18"),
        ("roster", "cell_changed", "nano03", "Basic Info > Notes",
         "no voice-over", "write to agent.x@example.invalid", "I19", "I19"),
        ("labels", "formula_changed", "#1", "LABEL",
         'A3&"|"&B3&"|"&C3', 'CONCAT(A3,"|",B3,"|",C3)', "D3", "D3"),
    }
    check("every change at its cells, and nothing else", got == want,
          sorted(got ^ want))
    check("every change names both imports",
          all((c["from_sha"], c["to_sha"], c["workbook"]) == (a, b, "im")
              for c in rows["wb_change"]))
    newest = {c["ref"] for c in rows["wb_cell"] if c["file_sha"] == b
              and c["sheet_no"] == 3}
    older = [c for c in rows["wb_cell"] if c["file_sha"] == a]
    check("the newest import keeps every cell (the summary block too)",
          "A15" in newest and "J15" in newest)
    check("an older import keeps its in-table cells only",
          older and all(c["table_id"] for c in older)
          and not any(c["ref"] == "J15" and c["sheet_no"] == 3
                      for c in older))
    gone = X.IM_TABLES + "extra\tim\tUse cases\tProduct B\trows 1-5\t\t\t\t" \
        "-\tproposed\t\n"
    m = WM.parse(gone, X.IM_COLUMNS)[0]
    v2b = build([{"name": "Use cases", "cells": {"A2": "NO.", "D2": "LABEL"}}],
                props={"modified": "2026-11-01T00:00:00Z"})
    got = WS.build([imp(v1, "im", "1"), imp(v2b, "im", "2")], m)
    kinds = sorted({(c["table_id"], c["kind"]) for c in got["wb_change"]})
    check("a table the newer file lacks is table_missing",
          ("extra", "table_missing") in kinds
          and ("roster", "table_missing") in kinds, kinds)
    other = WS.build([imp(v1, "im", "1"), imp(X.lumi(), "lumi", "2")],
                     WM.Map(tables=im_map().tables + lumi_map().tables,
                            columns=im_map().columns + lumi_map().columns))
    check("two workbooks are two chains, never compared",
          other["wb_change"] == [] and all(f["prev_sha"] is None
                                           for f in other["wb_file"]))


def test_records() -> None:
    print("\n[5] records(): what a projection reads")
    i1 = imp(X.im(1), "im", "x", "TW")
    recs, iss = WS.records(i1, im_map(), "q1")
    by = {r.row_key: r for r in recs}
    check("one record per row with its key and occurrence",
          [(r.row_key, r.occurrence) for r in recs][4:6] ==
          [("Feb + Twin Posts + TWIN", 2), ("Mar + Hidden Row + HID01", 1)],
          [(r.row_key, r.occurrence) for r in recs])
    r4 = by["Jan + Ring Talk + RING01"]
    check("a code is trimmed", r4.values["roster.code"] == "RING01")
    check("money and a date", (r4.values["roster.fee"],
                               r4.values["roster.posted_on"]) ==
          (131.58, "2026-01-20"), r4.values)
    check("an empty fee and a formula with no saved value are None, never 0",
          by["Feb + Plan Only + PLAN01"].values["roster.fee"] is None
          and by["Mar + Late Save + LATE01"].values["roster.fee"] is None)
    check("a boolean and an int", (r4.values["roster.flag.partnership_upload"],
                                   r4.values["roster.flag.reels"]) ==
          (False, 0), r4.values)
    check("each value names its source cell",
          r4.refs["roster.fee"] == f"{i1.workbook.sha256[:12]}:Q1!G4",
          r4.refs["roster.fee"])
    check("the market from the map", {r.market for r in recs} == {"TW"})
    check("a duplicate key is an issue here too",
          [m.code for m in iss] == ["workbook_key_duplicate"],
          [m.code for m in iss])
    check("a proposed column is not read",
          "roster.flag.assets_uploaded" not in WS.records(
              i1, im_map(), "roster")[0][0].values)
    recs, iss = WS.records(i1, im_map(), "calendar")
    check("md ranges, full-width and plain",
          [r.values["calendar.window"] for r in recs] ==
          [("01-06", "01-24"), ("02-08", "03-01"), None],
          [r.values["calendar.window"] for r in recs])
    check("a value that does not fit its type: None and an issue at its cell",
          [(m.code, m.params["ref"]) for m in iss] ==
          [("workbook_value_type", "Calendar!C6")],
          [(m.code, m.params) for m in iss])
    recs, iss = WS.records(i1, im_map(), "label_counts")
    check("a proposed table is refused, coded",
          recs == [] and [m.code for m in iss] ==
          ["workbook_table_unconfirmed"])
    m = WM.parse(X.IM_TABLES.replace("Event\trows 1-5", "Nowhere\trows 1-5"),
                 X.IM_COLUMNS)[0]
    recs, iss = WS.records(i1, m, "calendar")
    check("a table that cannot be found: no record, the issue",
          recs == [] and [x.code for x in iss] == ["workbook_anchor_missing"])
    typed = [("int", "3", 3.0, "n", (3, True)), ("int", "3.5", 3.5, "n",
                                                   (None, False)),
             ("real", "TRUE", 1.0, "b", (None, False)),
             ("date", "2026-02-01 x", None, "s", ("2026-02-01", True)),
             ("date", "soon", None, "s", (None, False)),
             ("bool", "true", None, "s", (True, True)),
             ("bool", "1", 1.0, "n", (True, True)),
             ("bool", "maybe", None, "s", (None, False)),
             ("md_range", "13/1-2/2", None, "s", (None, False)),
             ("text", "  ", None, "s", (None, True)),
             ("nonsense", "x", None, "s", (None, False))]
    bad = []
    for t, text, num, kind, want in typed:
        c = W.Cell(ref="A1", row=1, col=1, kind=kind, text=text, number=num)
        if WS._typed(c, t) != want:
            bad.append((t, text, WS._typed(c, t), want))
    check("each type's reading", not bad, bad)


def outputs(rows, *imports) -> str:
    out = [json.dumps(WS.summary(rows), ensure_ascii=False)]
    out += [i["detail"] + i["params"] for i in rows["wb_issue"]]
    for im_, m in imports:
        for t in m.tables:
            out += [str(x) + json.dumps(messages.code(x), ensure_ascii=False)
                    for x in WS.records(im_, m, t["table_id"])[1]]
    return "\n".join(out)


def test_personal() -> None:
    print("\n[6] personal data never reaches an output")
    i1, i2 = imp(X.im(1), "im", "1", "TW"), imp(X.im(2), "im", "2", "TW")
    il = imp(X.lumi(), "lumi", "3")
    m = WM.Map(tables=im_map().tables + lumi_map().tables,
               columns=im_map().columns + lumi_map().columns)
    rows = WS.build([i1, i2, il], m)
    text = outputs(rows, (i1, im_map()), (i2, im_map()), (il, lumi_map()))
    leaks = [p for p in X.PERSONAL if p in text]
    check("no planted address, name or page in summary(), issues or "
          "records() issues", not leaks, leaks)
    check("nothing that looks like an address", not WS.EMAIL.search(text),
          WS.EMAIL.search(text))
    s = WS.summary(rows, workbook="im")
    ch = {(c["kind"], c["header"]): c for f in s["files"]
          for c in f["changes"]}
    email = ch[("cell_changed", "Basic Info > Email")]
    check("a changed address is masked both ways",
          (email["old"], email["new"]) == (WS.MASK, WS.MASK), email)
    note = ch[("cell_changed", "Basic Info > Notes")]
    check("an address inside free text is masked, the rest kept",
          note["new"] == "write to <email>", note["new"])
    check("the author of a save is masked",
          {f["saved_by"] for f in s["files"]} == {WS.MASK})
    q1 = WS.build([i1, imp(build(
        [{"name": "Q1", "cells": {"A2": "Month", "B2": "KOL", "E2": "Coupon",
                                  "H2": "Usecase_Label", "A3": "Jan",
                                  "B3": "Creator Six", "E3": "SIX"}}],
        props={"modified": "2026-12-01T00:00:00Z"}), "im", "9")], im_map())
    keys = [c["row_key"] for f in WS.summary(q1)["files"]
            for c in f["changes"] if c["table_id"] == "q1" and c["row_key"]]
    check("a key that holds a name is masked",
          keys and set(keys) == {WS.MASK}, keys)
    check("the cells are flagged personal in the store",
          all(c["personal"] for c in rows["wb_cell"]
              if c["text"] and "@example.invalid" in c["text"])
          and any(c["personal"] and c["text"] == "Creator One"
                  for c in rows["wb_cell"]))
    check("personal_columns() names them for a harness's own guard",
          {"Basic Info > Email", "E-mail", "Creator"}
          <= set(WS.personal_columns(rows)), WS.personal_columns(rows))
    check("mask() leaves a non-text as it is",
          WS.mask(None) is None and WS.mask(3) == 3)


def test_database() -> None:
    print("\n[7] into a kit.db database")
    spec = db.with_human(WS.TABLES, version=1)
    path = Path(tmp_dir("wbdb-")) / "store.db"
    im_ = [imp(X.im(1), "im", "1", "TW"), imp(X.im(2), "im", "2", "TW")]
    rows = WS.build(im_, im_map())
    with contextlib.redirect_stderr(io.StringIO()):    # "created a NEW …"
        con = db.connect(spec, path)
    try:
        written = {t: db.upsert(spec, con, t, r) for t, r in rows.items()}
        held = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in rows}
    finally:
        con.close()
    check("rows in = rows out, every table",
          written == held == {t: len(r) for t, r in rows.items()}, held)
    check("the same imports give the same rows",
          WS.build(im_, im_map()) == rows)


def test_closure() -> None:
    print("\n[8] workbook_store.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items() if Path(r["file"]).name ==
           "workbook_store.tsv"}
    check("the fragment holds workbook_* codes that are not the map's",
          own and all(c.startswith("workbook_") and not
                      c.startswith("workbook_map_") for c in own),
          sorted(own))
    probs = messages.check_registry_closed(_shop.KIT, ["workbook_store.py"],
                                           own, strict_kit=True)
    check("every msg() is registered and every code emitted", probs == [],
          probs)


def main() -> int:
    _shop.use()
    for fn in (test_generic, test_im, test_lumi, test_changes, test_records,
               test_personal, test_database, test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
