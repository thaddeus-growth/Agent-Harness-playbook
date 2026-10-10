"""The generic workbook store: a client's workbook as cache tables a
harness keeps beside its own, found through the client's map
(kit.workbook_map), with a change record between two imports of the same
workbook. No table or column here names a business field; what a column
means is the map's, and what a harness makes of it is the harness's.

The tables (TABLES, in kit.db's table format; a harness adds them to its
cache tables and its ingest rebuilds them from raw):

  wb_file    one row per distinct file content (sha256): which workbook,
             market, raw path, when imported, the file's own last-saved
             time and author, the parts and their hashes, the import
             before it (prev_sha)
  wb_sheet   each sheet: name, state (hidden sheets kept), dimension,
             merges, data-validation lists, hidden rows and columns
  wb_table   each table the map locates: its anchor cell, header row,
             group row, column and row span, data rows and blank rows
  wb_column  each column of a located table: header, group header, path,
             and the map's field, direction, type and personal flag
  wb_row     each data row: its key (the map's key headers, or a content
             fingerprint), the occurrence of that key, the fingerprint
  wb_cell    the cells, as kit.workbook reads them: every cell of the
             newest import of each workbook, the in-table cells of older
             ones (all of them stay in raw)
  wb_change  between each import and the one before it of the same
             workbook: row_added, row_removed, cell_changed,
             formula_changed, column_added, column_removed, table_missing,
             with the old and new text and both cell refs; it is also the
             conflict list for any write-back
  wb_issue   what the store could not do, coded, with the cell ref and
             never a cell's value

What it guards:

  * a table is found by its anchor, never by coordinates: the anchor is
    searched in the map's box and must be there exactly once; the block
    is the run of non-empty header cells around it (so blocks side by
    side are separate tables); a merged cell above the header row is its
    group header; the data runs to the stop (a text, `blank:N`, or the
    sheet's end) and blank rows inside are skipped and counted; a header
    that is a formula is read by its saved value;
  * a covered cell of a merge reads its anchor's value (and says so);
  * rows are matched between imports by the map's key, never guessed; a
    duplicate key gets its occurrence number and an issue; a table with
    no key is matched by content (added and removed, never changed);
  * imports of one workbook are ordered by the file's own last-saved
    time, then the import time; the same content imported twice is one
    wb_file row;
  * personal data stays in the store and out of every output: a column
    the map types email, person or url, and any text that looks like an
    e-mail address, are masked by summary() and mask(); an issue carries
    a ref, never a value;
  * records() gives a harness's projection what the owner confirmed only:
    typed values (an empty cell is None, never 0), each with its source
    cell `<sha12>:<sheet>!<ref>`, and an issue for a value that does not
    fit its type.

Test: kit/tests/test_workbook_store.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from kit.messages import Msg, code, msg
from kit.workbook import Sheet, Workbook, norm, ref
from kit.workbook_map import (NO_PLACE, PERSONAL, Map, confirmed,
                              key_headers, search_box)

T, I, R = "TEXT", "INTEGER", "REAL"
TABLES: dict[str, dict] = {
    "wb_file": {
        "columns": {"file_sha": T, "workbook": T, "market": T, "raw_path": T,
                    "imported_at": T, "saved_at": T, "saved_by": T,
                    "created_at": T, "date1904": I, "sheet_count": I,
                    "size": I, "parts": T, "prev_sha": T, "map_version": T},
        "pk": ("file_sha",), "not_null": ("workbook",),
        "doc": "One row per distinct workbook file content.",
        "grain": "file content"},
    "wb_sheet": {
        "columns": {"file_sha": T, "sheet_no": I, "name": T, "state": T,
                    "dimension": T, "merged": T, "validations": T,
                    "hidden_rows": T, "hidden_cols": T, "cell_count": I},
        "pk": ("file_sha", "sheet_no"),
        "doc": "Each sheet of a file: state, merges, validations.",
        "grain": "file × sheet"},
    "wb_table": {
        "columns": {"file_sha": T, "table_id": T, "workbook": T,
                    "sheet_no": I, "sheet": T, "anchor": T, "anchor_ref": T,
                    "header_row": I, "group_row": I, "first_col": I,
                    "last_col": I, "first_row": I, "last_row": I,
                    "row_count": I, "blank_count": I, "key_headers": T},
        "pk": ("file_sha", "table_id"),
        "doc": "Each table the map located in a file.",
        "grain": "file × table"},
    "wb_column": {
        "columns": {"file_sha": T, "table_id": T, "col_no": I,
                    "header_ref": T, "header": T, "header_group": T,
                    "path": T, "field": T, "direction": T, "value_type": T,
                    "personal": I, "status": T},
        "pk": ("file_sha", "table_id", "col_no"),
        "doc": "Each column of a located table and what the map says of "
               "it (field NULL: unmapped).",
        "grain": "file × table × column"},
    "wb_row": {
        "columns": {"file_sha": T, "table_id": T, "row_key": T,
                    "occurrence": I, "row_no": I, "fingerprint": T,
                    "market": T},
        "pk": ("file_sha", "table_id", "row_key", "occurrence"),
        "doc": "Each data row of a located table, by its key.",
        "grain": "file × table × row"},
    "wb_cell": {
        "columns": {"file_sha": T, "sheet_no": I, "ref": T, "row_no": I,
                    "col_no": I, "kind": T, "text": T, "number": R,
                    "num_fmt": T, "iso_date": T, "formula": T,
                    "formula_kind": T, "shared_id": T, "merged_from": T,
                    "link": T, "comment": I, "hidden": I, "personal": I,
                    "table_id": T},
        "pk": ("file_sha", "sheet_no", "ref"),
        "doc": "The cells as read, values saved by the file, nothing "
               "recalculated.",
        "grain": "file × sheet × cell"},
    "wb_change": {
        "columns": {"to_sha": T, "table_id": T, "row_key": T,
                    "occurrence": I, "header": T, "kind": T, "workbook": T,
                    "from_sha": T, "old_text": T, "new_text": T,
                    "old_ref": T, "new_ref": T},
        "pk": ("to_sha", "table_id", "row_key", "occurrence", "header",
               "kind"),
        "doc": "What changed from the previous import of the same "
               "workbook; the conflict list of a write-back.",
        "grain": "import × table × row × column × kind"},
    "wb_issue": {
        "columns": {"file_sha": T, "n": I, "table_id": T, "code": T,
                    "ref": T, "detail": T, "params": T},
        "pk": ("file_sha", "n"),
        "doc": "What the store could not do, coded; a ref, never a value.",
        "grain": "file × issue"},
}
FILLS = tuple(TABLES)
CHANGE_KINDS = ("row_added", "row_removed", "cell_changed",
                "formula_changed", "column_added", "column_removed",
                "table_missing")
EMAIL = re.compile(r"[^\s@<>()\"',;:]+@[^\s@<>()\"',;:]+\.[A-Za-z]{2,}")
MASK = "<personal>"


@dataclass
class Import:
    """One raw file and what its import record says."""

    workbook: Workbook
    workbook_id: str
    market: str | None = None
    raw_path: str | None = None
    imported_at: str | None = None


@dataclass
class Column:
    col: int
    header: str
    group: str
    path: str
    spec: dict | None = None


@dataclass
class Located:
    table_id: str
    sheet: Sheet
    anchor_ref: str
    header_row: int
    group_row: int | None
    columns: list[Column]
    rows: list[int]
    blank: int
    last_row: int
    keys: list[str] = field(default_factory=list)


@dataclass
class Record:
    """One row for a projection: typed values by field and their cells."""

    table_id: str
    row_key: str
    occurrence: int
    market: str | None
    values: dict
    refs: dict


def mask(text: object) -> object:
    """A text with every e-mail address in it masked; other values as
    they are."""
    if isinstance(text, str):
        return EMAIL.sub("<email>", text)
    return text


def _text(sheet: Sheet, row: int, col: int) -> tuple[str, str | None]:
    """(the text that shows at row, col; the ref of the cell holding it)."""
    c = sheet.value(row, col)
    if c is None or c.text is None:
        return "", None
    return c.text, c.ref


def _blank(sheet: Sheet, row: int, cols: list[int]) -> bool:
    return all(not _text(sheet, row, k)[0].strip() for k in cols)


def _issue(issues: list, table_id: str, ref_: str, m: Msg) -> None:
    issues.append((table_id, ref_, m))


def locate(wb: Workbook, spec: dict, columns: list[dict],
           issues: list) -> Located | None:
    """The table `spec` (a tables.tsv row) in `wb`, or None with an issue."""
    tid = spec["table_id"]
    sheet = wb.sheet(spec["sheet"])
    if sheet is None:
        _issue(issues, tid, "", msg(
            "workbook_sheet_missing",
            f"{tid}: the workbook has no sheet {spec['sheet']!r}",
            table_id=tid, sheet=spec["sheet"]))
        return None
    r1, r2, c1, c2 = search_box(spec["search"])
    want = norm(spec["anchor"])
    hits = sorted((c.row, c.col) for c in sheet.cells.values()
                  if r1 <= c.row <= r2 and c1 <= c.col <= c2
                  and not c.merged_from and c.text is not None
                  and norm(c.text) == want)
    if len(hits) != 1:
        if not hits:
            _issue(issues, tid, "", msg(
                "workbook_anchor_missing",
                f"{tid}: no cell reads {spec['anchor']!r} in "
                f"{spec['sheet']} ({spec['search'] or 'rows 1-50'})",
                table_id=tid, sheet=spec["sheet"], anchor=spec["anchor"],
                search=spec["search"] or "rows 1-50"))
        else:
            refs = ", ".join(ref(*h) for h in hits)
            _issue(issues, tid, ref(*hits[0]), msg(
                "workbook_anchor_ambiguous",
                f"{tid}: {spec['anchor']!r} is in {len(hits)} cells of "
                f"{spec['sheet']} ({refs}); narrow the search box",
                table_id=tid, sheet=spec["sheet"], anchor=spec["anchor"],
                refs=refs))
        return None
    h, a = hits[0]
    lo = hi = a
    while lo > 1 and _text(sheet, h, lo - 1)[0].strip():
        lo -= 1
    while hi < 16384 and _text(sheet, h, hi + 1)[0].strip():
        hi += 1
    cols = list(range(lo, hi + 1))
    group_row = h - 1 if h > 1 and any(
        _text(sheet, h - 1, k)[0].strip() for k in cols) else None
    out: list[Column] = []
    for k in cols:
        head = " ".join(_text(sheet, h, k)[0].split())
        grp = " ".join(_text(sheet, group_row, k)[0].split()) \
            if group_row else ""
        out.append(Column(col=k, header=head, group=grp,
                          path=f"{grp} > {head}" if grp else head))
    seen: dict[str, list[Column]] = {}
    for c in out:
        seen.setdefault(norm(c.header), []).append(c)
    for dup in (v for v in seen.values() if len(v) > 1):
        paths = {norm(c.path) for c in dup}
        if len(paths) < len(dup):
            refs = ", ".join(ref(h, c.col) for c in dup)
            _issue(issues, tid, ref(h, dup[0].col), msg(
                "workbook_header_duplicate",
                f"{tid}: header {dup[0].header!r} shows {len(dup)} times "
                f"({refs}); only the first is mapped", table_id=tid,
                header=dup[0].header, refs=refs))
    by_name = {}
    for c in out:
        by_name.setdefault(norm(c.path), c)
        if len(seen[norm(c.header)]) == 1:
            by_name.setdefault(norm(c.header), c)
    for cs in columns:
        c = by_name.get(norm(cs["header"]))
        if c is not None and c.spec is None:
            c.spec = cs
    stop, end = spec["stop"], sheet.max_row()
    if stop and not stop.startswith("blank:"):
        s = norm(stop)
        found = next((r for r in range(h + 1, end + 1)
                      if any(norm(_text(sheet, r, k)[0]).startswith(s)
                             for k in cols)), None)
        if found is None:
            _issue(issues, tid, "", msg(
                "workbook_stop_missing",
                f"{tid}: no row of {spec['sheet']} starts with {stop!r} "
                f"below the header; read to the sheet's last row",
                table_id=tid, sheet=spec["sheet"], stop=stop))
        else:
            end = found - 1
    rows, run = [], 0
    limit = int(stop[6:]) if stop.startswith("blank:") else 0
    for r in range(h + 1, end + 1):
        if _blank(sheet, r, cols):
            run += 1
            if limit and run >= limit:
                break
            continue
        run = 0
        rows.append(r)
    last = rows[-1] if rows else h
    blank = (last - h) - len(rows)          # blank rows inside the table
    loc = Located(table_id=tid, sheet=sheet, anchor_ref=ref(h, a),
                  header_row=h, group_row=group_row, columns=out, rows=rows,
                  blank=blank, last_row=last,
                  keys=key_headers(spec["key"]))
    names = {norm(c.header) for c in out} | {norm(c.path) for c in out}
    for k in loc.keys:
        if norm(k) not in names:
            _issue(issues, tid, ref(h, a), msg(
                "workbook_key_missing",
                f"{tid}: key header {k!r} is not in the table's header row",
                table_id=tid, header=k))
    return loc


def _col(loc: Located, header: str) -> Column | None:
    n = norm(header)
    return next((c for c in loc.columns if norm(c.path) == n), None) or \
        next((c for c in loc.columns if norm(c.header) == n), None)


def _fingerprint(loc: Located, r: int) -> str:
    vals = [[c.path, _text(loc.sheet, r, c.col)[0]] for c in loc.columns]
    return hashlib.sha256(json.dumps(vals, ensure_ascii=False).encode()
                          ).hexdigest()


def keyed_rows(loc: Located, issues: list) -> list[tuple[str, int, int, str]]:
    """[(row_key, occurrence, row_no, fingerprint)] in row order."""
    out, count = [], {}
    cols = [_col(loc, k) for k in loc.keys]
    for r in loc.rows:
        fp = _fingerprint(loc, r)
        if loc.keys and all(cols):
            parts = [_text(loc.sheet, r, c.col)[0].strip() for c in cols]
            key = " + ".join(parts)
            if not any(parts):
                _issue(issues, loc.table_id, ref(r, cols[0].col), msg(
                    "workbook_key_empty",
                    f"{loc.table_id}: row {r} has an empty key; matched by "
                    f"its content", table_id=loc.table_id, row=r))
                key = "fp:" + fp[:16]
        else:
            key = "fp:" + fp[:16]
        n = count[key] = count.get(key, 0) + 1
        if n > 1 and not key.startswith("fp:"):
            _issue(issues, loc.table_id, ref(r, cols[0].col), msg(
                "workbook_key_duplicate",
                f"{loc.table_id}: row {r} repeats the key of an earlier row "
                f"(occurrence {n})", table_id=loc.table_id, row=r,
                occurrence=n))
        out.append((key, n, r, fp))
    return out


def _market(loc: Located, spec: dict, r: int, default: str | None
            ) -> str | None:
    m = spec.get("market") or ""
    if m.startswith("@"):
        c = _col(loc, m[1:])
        return _text(loc.sheet, r, c.col)[0].strip() or None if c else None
    return m or default


def _personal(c: Column) -> bool:
    return bool(c.spec and c.spec["type"] in PERSONAL)


def _snapshot(loc: Located, keyed) -> dict:
    """{(key, occ): {path: (text, formula, ref)}} for the change record."""
    snap = {}
    for key, occ, r, _ in keyed:
        vals = {}
        for c in loc.columns:
            cell = loc.sheet.value(r, c.col)
            vals[c.path] = (cell.text if cell else None,
                            cell.formula if cell else None,
                            cell.ref if cell else ref(r, c.col))
        snap[(key, occ)] = (vals, ref(r, loc.columns[0].col))
    return snap


def _changes(workbook_id: str, prev: tuple[str, dict], cur: tuple[str, dict]
             ) -> list[dict]:
    """wb_change rows between two imports: {table_id: (columns, snap)}."""
    (psha, ptabs), (csha, ctabs) = prev, cur
    out = []

    def row(tid, key, occ, header, kind, old=None, new=None, oref=None,
            nref=None):
        out.append({"to_sha": csha, "table_id": tid, "row_key": key,
                    "occurrence": occ, "header": header, "kind": kind,
                    "workbook": workbook_id, "from_sha": psha,
                    "old_text": old, "new_text": new, "old_ref": oref,
                    "new_ref": nref})

    for tid in sorted(ptabs):
        if tid not in ctabs:
            row(tid, "", 0, "", "table_missing")
            continue
        (pcols, psnap), (ccols, csnap) = ptabs[tid], ctabs[tid]
        for h in sorted(set(ccols) - set(pcols)):
            row(tid, "", 0, h, "column_added")
        for h in sorted(set(pcols) - set(ccols)):
            row(tid, "", 0, h, "column_removed")
        common = [h for h in ccols if h in pcols]
        for k in sorted(set(psnap) - set(csnap)):
            row(tid, k[0], k[1], "", "row_removed", oref=psnap[k][1])
        for k in sorted(set(csnap) - set(psnap)):
            row(tid, k[0], k[1], "", "row_added", nref=csnap[k][1])
        for k in sorted(set(psnap) & set(csnap)):
            pv, cv = psnap[k][0], csnap[k][0]
            for h in common:
                (pt, pf, pr), (ct, cf, cr) = pv[h], cv[h]
                if (pt or "") != (ct or ""):
                    row(tid, k[0], k[1], h, "cell_changed", pt, ct, pr, cr)
                elif (pf or "") != (cf or ""):
                    row(tid, k[0], k[1], h, "formula_changed", pf, cf, pr, cr)
    return out


def _cell_row(sha: str, sheet: Sheet, c, table_id: str | None,
              personal: bool) -> dict:
    return {"file_sha": sha, "sheet_no": sheet.no, "ref": c.ref,
            "row_no": c.row, "col_no": c.col, "kind": c.kind or None,
            "text": c.text, "number": c.number, "num_fmt": c.num_fmt,
            "iso_date": c.iso_date, "formula": c.formula,
            "formula_kind": c.formula_kind, "shared_id": c.shared_id,
            "merged_from": c.merged_from, "link": c.link,
            "comment": int(c.comment), "hidden": int(c.hidden),
            "personal": int(personal or bool(c.text and EMAIL.search(c.text))),
            "table_id": table_id}


def build(imports: list[Import], wmap: Map) -> dict[str, list[dict]]:
    """Every wb_* table's rows from the raw files' imports and the map.
    Deterministic: the same imports and map give the same rows."""
    rows: dict[str, list[dict]] = {t: [] for t in TABLES}
    first: dict[str, Import] = {}
    for imp in imports:                      # one row per content
        sha = imp.workbook.sha256
        if sha not in first or (imp.imported_at or "") < (
                first[sha].imported_at or ""):
            first[sha] = imp
    chains: dict[str, list[Import]] = {}
    for imp in first.values():
        chains.setdefault(imp.workbook_id, []).append(imp)
    for wid in sorted(chains):
        chain = sorted(chains[wid], key=lambda i: (
            i.workbook.props.get("modified") or "", i.imported_at or "",
            i.workbook.sha256))
        specs = [t for t in wmap.tables if t["workbook"] == wid]
        prev = None
        for n, imp in enumerate(chain):
            newest = n == len(chain) - 1
            snap = _one(rows, imp, specs, wmap, prev, newest)
            if prev is not None:
                rows["wb_change"] += _changes(wid, prev, snap)
            prev = snap
    return rows


def _one(rows: dict, imp: Import, specs: list[dict], wmap: Map,
         prev: tuple | None, newest: bool) -> tuple[str, dict]:
    wb, sha = imp.workbook, imp.workbook.sha256
    p = wb.props
    rows["wb_file"].append({
        "file_sha": sha, "workbook": imp.workbook_id, "market": imp.market,
        "raw_path": imp.raw_path, "imported_at": imp.imported_at,
        "saved_at": p.get("modified"), "saved_by": p.get("modified_by"),
        "created_at": p.get("created"), "date1904": int(wb.date1904),
        "sheet_count": len(wb.sheets), "size": wb.size,
        "parts": json.dumps(wb.parts, sort_keys=True),
        "prev_sha": prev[0] if prev else None, "map_version": wmap.version})
    for s in wb.sheets:
        rows["wb_sheet"].append({
            "file_sha": sha, "sheet_no": s.no, "name": s.name,
            "state": s.state, "dimension": s.dimension,
            "merged": json.dumps(s.merged),
            "validations": json.dumps(s.validations, ensure_ascii=False),
            "hidden_rows": json.dumps(sorted(s.hidden_rows)),
            "hidden_cols": json.dumps(sorted(s.hidden_cols)),
            "cell_count": len(s.cells)})
    issues: list = []
    tables: dict[str, tuple] = {}
    in_table: dict[tuple[int, int, int], tuple[str, bool]] = {}
    for spec in specs:
        tid = spec["table_id"]
        loc = locate(wb, spec, wmap.columns_of(tid), issues)
        if loc is None:
            continue
        keyed = keyed_rows(loc, issues)
        rows["wb_table"].append({
            "file_sha": sha, "table_id": tid, "workbook": spec["workbook"],
            "sheet_no": loc.sheet.no, "sheet": loc.sheet.name,
            "anchor": spec["anchor"], "anchor_ref": loc.anchor_ref,
            "header_row": loc.header_row, "group_row": loc.group_row,
            "first_col": loc.columns[0].col, "last_col": loc.columns[-1].col,
            "first_row": loc.header_row + 1, "last_row": loc.last_row,
            "row_count": len(loc.rows), "blank_count": loc.blank,
            "key_headers": " + ".join(loc.keys)})
        for c in loc.columns:
            s = c.spec or {}
            rows["wb_column"].append({
                "file_sha": sha, "table_id": tid, "col_no": c.col,
                "header_ref": ref(loc.header_row, c.col), "header": c.header,
                "header_group": c.group or None, "path": c.path,
                "field": (s.get("field") or None) if s else None,
                "direction": s.get("direction"), "value_type": s.get("type"),
                "personal": int(_personal(c)), "status": s.get("status")})
            for r in [loc.header_row, *loc.rows]:
                in_table[(loc.sheet.no, r, c.col)] = (tid, _personal(c))
        for key, occ, r, fp in keyed:
            rows["wb_row"].append({
                "file_sha": sha, "table_id": tid, "row_key": key,
                "occurrence": occ, "row_no": r, "fingerprint": fp,
                "market": _market(loc, spec, r, imp.market)})
            for c in loc.columns:
                cell = loc.sheet.value(r, c.col)
                if cell is not None and cell.formula_kind and \
                        cell.text is None:
                    _issue(issues, tid, cell.ref, msg(
                        "workbook_formula_no_value",
                        f"{tid}: {loc.sheet.name}!{cell.ref} is a formula "
                        f"the file saved no value for; read as empty",
                        table_id=tid, ref=f"{loc.sheet.name}!{cell.ref}"))
        tables[tid] = ([c.path for c in loc.columns],
                       _snapshot(loc, keyed))
    for s in wb.sheets:
        for (r, k), c in sorted(s.cells.items()):
            where = in_table.get((s.no, r, k))
            if newest or where:
                rows["wb_cell"].append(_cell_row(
                    sha, s, c, where[0] if where else None,
                    where[1] if where else False))
    for n, (tid, ref_, m) in enumerate(issues, 1):
        c = code(m)
        rows["wb_issue"].append({
            "file_sha": sha, "n": n, "table_id": tid, "code": c["code"],
            "ref": ref_ or None, "detail": str(m),
            "params": json.dumps(c["params"], ensure_ascii=False,
                                 sort_keys=True)})
    return sha, tables


# ---- what a projection reads -------------------------------------------

_MD = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*[~～\-–—至到]\s*"
                 r"(\d{1,2})\s*/\s*(\d{1,2})\s*$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _typed(cell, t: str):
    """(value, ok) of a cell for a map type; an empty cell is (None, True)."""
    if cell is None or cell.text is None or not str(cell.text).strip():
        return None, True
    text = str(cell.text).strip()
    if t in ("text", "label", "code", "email", "person", "url"):
        return text, True
    if t == "int":
        if cell.number is not None and float(cell.number).is_integer():
            return int(cell.number), True
        return None, False
    if t == "real" or t.startswith("money:"):
        return (cell.number, True) if cell.number is not None and \
            cell.kind not in ("b", "e") else (None, False)
    if t == "date":
        if cell.iso_date:
            return cell.iso_date[:10], True
        return (text[:10], True) if _ISO.match(text) else (None, False)
    if t == "md_range":
        m = _MD.match(text)
        if not m:
            return None, False
        a, b, c, d = (int(x) for x in m.groups())
        if not (1 <= a <= 12 and 1 <= b <= 31 and 1 <= c <= 12
                and 1 <= d <= 31):
            return None, False
        return (f"{a:02d}-{b:02d}", f"{c:02d}-{d:02d}"), True
    if t == "bool":
        if cell.kind == "b":
            return cell.text == "TRUE", True
        if text.upper() in ("TRUE", "FALSE"):
            return text.upper() == "TRUE", True
        if cell.number in (0.0, 1.0):
            return cell.number == 1.0, True
        return None, False
    return None, False


def records(imp: Import, wmap: Map, table_id: str
            ) -> tuple[list[Record], list[Msg]]:
    """The confirmed rows of `table_id` in one import, typed for a
    projection, and the issues met (a value that does not fit its type is
    None with an issue). Only confirmed map rows are read."""
    m = confirmed(wmap)
    spec = m.table(table_id)
    if spec is None:
        return [], [msg("workbook_table_unconfirmed",
                        f"{table_id} is not a confirmed table of the map; "
                        f"nothing is projected from it", table_id=table_id)]
    issues: list = []
    cols = [c for c in m.columns_of(table_id)
            if c["field"] and c["field"] != NO_PLACE]
    loc = locate(imp.workbook, spec, cols, issues)
    if loc is None:
        return [], [i[2] for i in issues]
    keyed = keyed_rows(loc, issues)
    sha12, sheet = imp.workbook.sha256[:12], loc.sheet.name
    out = []
    for key, occ, r, _ in keyed:
        values, refs = {}, {}
        for c in loc.columns:
            if not c.spec or c.spec not in cols:
                continue
            cell = loc.sheet.value(r, c.col)
            v, ok = _typed(cell, c.spec["type"])
            where = f"{sheet}!{cell.ref if cell else ref(r, c.col)}"
            if not ok:
                issues.append((table_id, where, msg(
                    "workbook_value_type",
                    f"{table_id}: {where} does not read as "
                    f"{c.spec['type']}; left empty", table_id=table_id,
                    ref=where, type=c.spec["type"])))
            values[c.spec["field"]] = v
            refs[c.spec["field"]] = f"{sha12}:{where}"
        out.append(Record(table_id=table_id, row_key=key, occurrence=occ,
                          market=_market(loc, spec, r, imp.market),
                          values=values, refs=refs))
    return out, [i[2] for i in issues]


# ---- what a harness prints ---------------------------------------------

def summary(rows: dict[str, list[dict]], workbook: str | None = None
            ) -> dict:
    """A JSON-able view of the store for a read verb: per file its tables,
    unmapped columns, issues and changes. Personal columns' values and any
    e-mail address are masked; the author of a save is masked."""
    personal = {(r["file_sha"], r["table_id"], r["path"])
                for r in rows["wb_column"] if r["personal"]}
    personal |= {(r["file_sha"], r["table_id"], r["header"])
                 for r in rows["wb_column"] if r["personal"]}
    secret_key = {(t["file_sha"], t["table_id"]) for t in rows["wb_table"]
                  if any((t["file_sha"], t["table_id"], k) in personal
                         for k in key_headers(t["key_headers"] or ""))}
    files = []
    for f in sorted(rows["wb_file"], key=lambda r: (
            r["workbook"], r["saved_at"] or "", r["imported_at"] or "")):
        if workbook and f["workbook"] != workbook:
            continue
        sha = f["file_sha"]
        tabs = []
        for t in (t for t in rows["wb_table"] if t["file_sha"] == sha):
            cols = [c for c in rows["wb_column"] if c["file_sha"] == sha
                    and c["table_id"] == t["table_id"]]
            tabs.append({
                "table_id": t["table_id"], "sheet": t["sheet"],
                "anchor_ref": t["anchor_ref"],
                "header_row": t["header_row"], "rows": t["row_count"],
                "blank_rows": t["blank_count"],
                "columns": len(cols),
                "mapped": sorted({c["field"] for c in cols if c["field"]}),
                "unmapped": [mask(c["path"]) for c in cols
                             if not c["value_type"]]})
        changes = []
        for c in (c for c in rows["wb_change"] if c["to_sha"] == sha):
            hidden = (c["from_sha"], c["table_id"], c["header"]) in personal \
                or (sha, c["table_id"], c["header"]) in personal
            changes.append({
                "table_id": c["table_id"], "kind": c["kind"],
                "row_key": MASK if c["row_key"] and not c["row_key"].startswith(
                    "fp:") and ((sha, c["table_id"]) in secret_key or (
                        c["from_sha"], c["table_id"]) in secret_key)
                else mask(c["row_key"]), "header": c["header"],
                "old": MASK if hidden and c["old_text"] else mask(
                    c["old_text"]),
                "new": MASK if hidden and c["new_text"] else mask(
                    c["new_text"]),
                "old_ref": c["old_ref"], "new_ref": c["new_ref"]})
        files.append({
            "workbook": f["workbook"], "sha12": sha[:12],
            "market": f["market"], "saved_at": f["saved_at"],
            "saved_by": MASK if f["saved_by"] else None,
            "imported_at": f["imported_at"],
            "previous": (f["prev_sha"] or "")[:12] or None,
            "sheets": [{"name": s["name"], "state": s["state"]}
                       for s in rows["wb_sheet"] if s["file_sha"] == sha],
            "tables": tabs,
            "issues": [{"table_id": i["table_id"], "code": i["code"],
                        "ref": i["ref"], "detail": mask(i["detail"])}
                       for i in rows["wb_issue"] if i["file_sha"] == sha],
            "changes": changes})
    return {"files": files}


def personal_columns(rows: dict[str, list[dict]]) -> list[str]:
    """Every column path the store holds as personal (for a harness's own
    output guard)."""
    return sorted({r["path"] for r in rows["wb_column"] if r["personal"]})
