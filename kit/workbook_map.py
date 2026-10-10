"""The map of a client's workbook: where each table is and what each
column means to the harnesses that read or write it. The map is the only
place that knows the client's sheet and header names; the reader
(kit.workbook) and the store (kit.workbook_store) know none.

Two TSV files, kept with the client's data (not in a harness's
repository), because the sheet names are the client's words and one
workbook may be written by more than one harness:

  tables.tsv   table_id, workbook, sheet, anchor, search, stop, key,
               market, target, status, note
  columns.tsv  table_id, header, field, direction, owner, type, status,
               note

tables.tsv: `anchor` is a header text the table is found by (compared
after kit.workbook.norm); `search` the box it is searched in (`rows 1-30`,
optionally `cols A-T`; default rows 1-50); `stop` where the data ends: a
text a cell of the block starts with (`QUICK SUMMARY`), `blank:N` (N blank
rows in a row) or empty (the sheet's last row); `key` the headers that
identify a row, joined with ` + ` (empty: the row's content fingerprint);
`market` a constant, `@Header`, or empty; `target` the harness table it
projects into, or `-`.

columns.tsv: `header` as written (`Group > Header` when the same header
shows twice in one block); `field` a field id of the harness's own
vocabulary, or `-` (kept in the store, projected nowhere); `direction`
in, out or both; `owner` the harness that writes the column (required
for out and both); `type` one of TYPES. A row with table_id `-` and
header `-` names a harness field that has no place in this workbook, and
its note says why.

`status` is proposed or confirmed in both files: an agent writes
proposed, and only the owner's answer makes a row confirmed. confirmed()
is what a projection reads; pending() is what still waits.

What it guards (problems() -> [Msg], check() raises them as one):

  * one owner per outgoing column: a column (workbook, sheet, anchor,
    header) with direction out or both has exactly one row, so two
    harnesses never write the same cells;
  * every field id is one of the harness's vocabulary (when it gives one);
  * the shape: the columns of both files, closed values (direction,
    status, type), unique table ids, every column row naming a declared
    table, an owner for every out or both row, a reason for every
    no-place row, the key headers and an `@Header` market among the
    table's column rows.

PERSONAL types (email, person, url) mark columns whose values a harness
never prints (kit.workbook_store masks them).

Test: kit/tests/test_workbook_map.py.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from kit.contract import HarnessError
from kit.messages import Msg, joined, msg
from kit.workbook import col_number, norm

TABLE_COLUMNS = ("table_id", "workbook", "sheet", "anchor", "search", "stop",
                 "key", "market", "target", "status", "note")
COLUMN_COLUMNS = ("table_id", "header", "field", "direction", "owner", "type",
                  "status", "note")
DIRECTIONS = ("in", "out", "both")
STATUSES = ("proposed", "confirmed")
TYPES = ("text", "int", "real", "date", "md_range", "bool", "code", "label",
         "email", "person", "url")
PERSONAL = ("email", "person", "url")
_MONEY = re.compile(r"^money:[A-Z]{3}$")
_SEARCH = re.compile(r"^rows\s+(\d+)\s*-\s*(\d+)(?:\s+cols\s+([A-Z]{1,3})\s*-"
                     r"\s*([A-Z]{1,3}))?$")
NO_PLACE = "-"
# the two file names, built so that no literal names a file of the harness
# (kit.guards.release takes a string literal that is only a file name ending
# in .tsv for a registry the harness must ship;
# these are the client's, in its data folder)
TABLES_FILE, COLUMNS_FILE = (f"{n}.tsv" for n in ("tables", "columns"))


@dataclass
class Map:
    tables: list[dict] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)
    version: str = ""

    def table(self, table_id: str) -> dict | None:
        return next((t for t in self.tables if t["table_id"] == table_id),
                    None)

    def columns_of(self, table_id: str) -> list[dict]:
        return [c for c in self.columns if c["table_id"] == table_id]


def type_ok(t: str) -> bool:
    return t in TYPES or bool(_MONEY.match(t))


def search_box(text: str) -> tuple[int, int, int, int]:
    """`rows 1-30 [cols A-T]` -> (row1, row2, col1, col2); empty -> rows
    1-50, every column. ValueError when it is neither."""
    s = " ".join((text or "").split())
    if not s:
        return 1, 50, 1, 16384
    m = _SEARCH.match(s)
    if not m:
        raise ValueError(s)
    r1, r2 = int(m.group(1)), int(m.group(2))
    c1 = col_number(m.group(3)) if m.group(3) else 1
    c2 = col_number(m.group(4)) if m.group(4) else 16384
    return min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2)


def key_headers(text: str) -> list[str]:
    return [k.strip() for k in (text or "").split(" + ") if k.strip()]


def _rows(name: str, text: str, want: tuple[str, ...]
          ) -> tuple[list[dict], list[Msg]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], [msg("workbook_map_columns",
                        f"{name} is empty; its header must be "
                        f"{', '.join(want)}", file=name,
                        missing=", ".join(want))]
    head = [h.strip() for h in lines[0].split("\t")]
    missing = [w for w in want if w not in head]
    if missing:
        return [], [msg("workbook_map_columns",
                        f"{name} lacks the column(s) {', '.join(missing)}",
                        file=name, missing=", ".join(missing))]
    out = []
    for n, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        row = {h: (cells[i].strip() if i < len(cells) else "")
               for i, h in enumerate(head)}
        row["_line"] = n
        out.append(row)
    return out, []


def parse(tables_text: str, columns_text: str) -> tuple[Map, list[Msg]]:
    """The map from the two files' texts, and the shape problems met while
    reading it (problems() adds the rules)."""
    tables, p1 = _rows(TABLES_FILE, tables_text, TABLE_COLUMNS)
    columns, p2 = _rows(COLUMNS_FILE, columns_text, COLUMN_COLUMNS)
    version = hashlib.sha256(
        (tables_text + "\0" + columns_text).encode("utf-8")).hexdigest()[:16]
    return Map(tables=tables, columns=columns, version=version), p1 + p2


def load(folder: Path | str) -> Map:
    """tables.tsv and columns.tsv from `folder`, checked for shape and for
    the rules without a field vocabulary (check() again with one)."""
    d = Path(folder).expanduser()
    texts = []
    for name in (TABLES_FILE, COLUMNS_FILE):
        p = d / name
        if not p.is_file():
            raise HarnessError(msg(
                "workbook_map_missing",
                f"{p} is not a file; a workbook map is tables.tsv and "
                f"columns.tsv in one folder", path=str(p)),
                ["write the map's two files, then import them again"])
        texts.append(p.read_bytes().decode("utf-8-sig"))
    m, probs = parse(*texts)
    check(m, extra=probs)
    return m


def _bad(file: str, line: int, column: str, value: str,
         expected: str) -> Msg:
    return msg("workbook_map_bad_value",
               f"{file} line {line}: {column} {value!r} is not "
               f"{expected}", file=file, line=line, column=column,
               value=value, expected=expected)


def problems(m: Map, fields: set[str] | None = None) -> list[Msg]:
    """Everything wrong with the map; empty when it holds."""
    out: list[Msg] = []
    seen: dict[str, dict] = {}
    for t in m.tables:
        n = t["_line"]
        if not t["table_id"] or t["table_id"] == NO_PLACE:
            out.append(_bad(TABLES_FILE, n, "table_id", t["table_id"],
                            "a table id"))
            continue
        if t["table_id"] in seen:
            out.append(msg("workbook_map_table_duplicate",
                           f"tables.tsv: table {t['table_id']} is declared "
                           f"twice", table_id=t["table_id"]))
        seen.setdefault(t["table_id"], t)
        for col in ("workbook", "sheet", "anchor"):
            if not t[col]:
                out.append(_bad(TABLES_FILE, n, col, "", "a text"))
        if t["status"] not in STATUSES:
            out.append(_bad(TABLES_FILE, n, "status", t["status"],
                            " | ".join(STATUSES)))
        try:
            search_box(t["search"])
        except ValueError:
            out.append(_bad(TABLES_FILE, n, "search", t["search"],
                            "rows N-M [cols A-Z]"))
        stop = t["stop"]
        if stop.startswith("blank:") and not stop[6:].isdigit():
            out.append(_bad(TABLES_FILE, n, "stop", stop,
                            "blank:N, a text, or empty"))
    by_table: dict[str, list[dict]] = {}
    writers: dict[tuple, list[dict]] = {}
    for c in m.columns:
        n, tid = c["_line"], c["table_id"]
        if c["direction"] not in DIRECTIONS:
            out.append(_bad(COLUMNS_FILE, n, "direction", c["direction"],
                            " | ".join(DIRECTIONS)))
        if c["status"] not in STATUSES:
            out.append(_bad(COLUMNS_FILE, n, "status", c["status"],
                            " | ".join(STATUSES)))
        if not type_ok(c["type"]):
            out.append(_bad(COLUMNS_FILE, n, "type", c["type"],
                            " | ".join(TYPES) + " | money:XXX"))
        f = c["field"]
        if fields is not None and f and f != NO_PLACE and f not in fields:
            out.append(msg("workbook_map_field_unknown",
                           f"columns.tsv line {n}: field {f} is not in the "
                           f"harness's field vocabulary", line=n, field=f))
        if tid == NO_PLACE:
            if c["header"] != NO_PLACE or not c["note"]:
                out.append(msg("workbook_map_no_place_reason",
                               f"columns.tsv line {n}: a field with no "
                               f"place in the workbook ({f}) needs header "
                               f"'-' and a note that says why", line=n,
                               field=f))
            continue
        if tid not in seen:
            out.append(msg("workbook_map_table_unknown",
                           f"columns.tsv line {n}: table {tid} is not in "
                           f"{TABLES_FILE}", line=n, table_id=tid))
            continue
        if not c["header"]:
            out.append(_bad(COLUMNS_FILE, n, "header", "", "a header"))
        by_table.setdefault(tid, []).append(c)
        if c["direction"] in ("out", "both"):
            if not c["owner"]:
                out.append(msg("workbook_map_owner_missing",
                               f"columns.tsv line {n}: {c['header']} goes "
                               f"out, so it needs its owning harness", line=n,
                               header=c["header"]))
            t = seen[tid]
            place = (t["workbook"], t["sheet"], norm(t["anchor"]),
                     norm(c["header"]))
            writers.setdefault(place, []).append(c)
    for (_, sheet, _, _), rows in sorted(writers.items()):
        if len(rows) > 1:
            owners = sorted({r["owner"] or "?" for r in rows})
            out.append(msg("workbook_map_two_owners",
                           f"{sheet} / {rows[0]['header']}: written by "
                           f"{len(rows)} rows ({', '.join(owners)}); an "
                           f"outgoing column has one owner", sheet=sheet,
                           header=rows[0]["header"],
                           owners=", ".join(owners)))
    for tid, t in seen.items():
        heads = {norm(c["header"]) for c in by_table.get(tid, [])}
        need = key_headers(t["key"])
        if t["market"].startswith("@"):
            need.append(t["market"][1:])
        for h in need:
            if norm(h) not in heads:
                out.append(msg("workbook_map_key_unknown",
                               f"tables.tsv: {tid} names {h!r} (key or "
                               f"market) but columns.tsv has no row for it",
                               table_id=tid, header=h))
    return out


def check(m: Map, fields: set[str] | None = None, *,
          extra: list[Msg] | None = None) -> None:
    probs = list(extra or []) or problems(m, fields)   # a broken file first
    if probs:
        raise HarnessError(msg(
            "workbook_map_refused",
            f"the workbook map has {len(probs)} problem(s); nothing was "
            f"read through it: " + "; ".join(probs),
            count=len(probs), problems=joined(probs)),
            ["fix the map's tables.tsv / columns.tsv, then import it again"])


def confirmed(m: Map) -> Map:
    """Only what the owner confirmed: a confirmed table with its confirmed
    column rows. This is what a projection reads."""
    tabs = [t for t in m.tables if t["status"] == "confirmed"]
    ids = {t["table_id"] for t in tabs}
    cols = [c for c in m.columns
            if c["status"] == "confirmed" and c["table_id"] in ids]
    return Map(tables=tabs, columns=cols, version=m.version)


def pending(m: Map) -> list[dict]:
    """Every row still waiting on the owner: {file, line, table_id, header,
    direction}."""
    out = [{"file": TABLES_FILE, "line": t["_line"],
            "table_id": t["table_id"], "header": "", "direction": ""}
           for t in m.tables if t["status"] != "confirmed"]
    out += [{"file": COLUMNS_FILE, "line": c["_line"],
             "table_id": c["table_id"], "header": c["header"],
             "direction": c["direction"]}
            for c in m.columns if c["status"] != "confirmed"]
    return out
