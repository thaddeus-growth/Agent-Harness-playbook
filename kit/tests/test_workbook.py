#!/usr/bin/env python3
"""The workbook reader (kit/workbook.py) and the test writer
(kit/testing/xlsx.py), on workbooks the test builds (SYNTHETIC).

  1. The writer is deterministic: the same call, the same bytes.
  2. Cells as the file holds them: shared strings, numbers, booleans,
     errors, formulas with their saved value (a shared formula's child
     carries the master's text and the group id; a formula saved with no
     value reads as None), date serials in both date systems and through
     built-in and custom formats, hyperlinks, comments, hidden rows and
     sheets, merges (a covered position reads its anchor), data
     validations, the core properties and every part's sha256.
  3. Rich and phonetic text, inline strings, and a workbook in the Strict
     namespace read the same.
  4. Dates: serial_to_iso in the 1900 and 1904 systems, times, and which
     format codes are dates.
  5. Refusals, coded: not a zip, no workbook part, a missing file, a part
     with a DOCTYPE or an ENTITY, a part over the size cap.
  6. workbook.py's msg() calls are closed over its fragment.
"""

import io
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
import _workbooks as X  # noqa: E402
from kit import messages, workbook as W  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402
from kit.testing.xlsx import HTTP, M, D, F, build, serial, write  # noqa: E402


def patched(data: bytes, edits: dict[str, bytes | None]) -> bytes:
    """A copy of an xlsx with some parts replaced (None removes one)."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            if info.filename in edits:
                if edits[info.filename] is not None:
                    out.writestr(info, edits[info.filename])
            else:
                out.writestr(info, src.read(info.filename))
        for name, body in edits.items():
            if name not in src.namelist() and body is not None:
                out.writestr(name, body)
    return buf.getvalue()


def code_of(fn) -> str | None:
    try:
        fn()
    except HarnessError as e:
        return getattr(e.message, "code", None)
    return None


def test_writer() -> None:
    print("[1] the writer is deterministic")
    check("the same call gives the same bytes", X.im(1) == X.im(1))
    p = write(Path(tmp_dir("wb-")) / "a.xlsx", [{"name": "S",
                                                 "cells": {"A1": "x"}}])
    check("write() puts the bytes on disk", p.read_bytes() == build(
        [{"name": "S", "cells": {"A1": "x"}}]))
    check("serial() counts from 1899-12-30, or 1904-01-01",
          serial("2026-01-03") == 46025 and serial("1904-01-02", True) == 1)


def test_cells() -> None:
    print("\n[2] cells as the file holds them")
    wb = W.read(X.im(1), label="im.xlsx")
    names = [(s.name, s.state) for s in wb.sheets]
    check("every sheet in order with its state, the hidden one kept",
          names == [("Use cases", "visible"), ("Creators", "visible"),
                    ("Q1", "visible"), ("Calendar", "visible"),
                    ("Lists", "hidden")], names)
    q1, cr, uc = wb.sheet("Q1"), wb.sheet("Creators"), wb.sheet("Use cases")
    a3 = q1.at(3, 1)
    check("a shared string", (a3.kind, a3.text) == ("s", "Jan"), a3)
    check("a text keeps its trailing newline as written",
          q1.at(4, 5).text == "RING01\n", repr(q1.at(4, 5).text))
    f3 = q1.at(3, 6)
    check("a number", (f3.kind, f3.number, f3.text) == ("n", 50000.0,
                                                        "50000"), f3)
    m3 = q1.at(3, 13)
    check("a boolean", (m3.kind, m3.text, m3.number) == ("b", "TRUE", 1.0),
          m3)
    g3 = q1.at(3, 7)
    check("a formula with its saved value, never recalculated",
          (g3.formula, g3.formula_kind, g3.number) == ("F3/38", "normal",
                                                       1315.79), g3)
    g10 = q1.at(10, 7)
    check("a formula saved with no value reads as None",
          (g10.formula, g10.text, g10.number) == ("F10/38", None, None), g10)
    h17, h18 = cr.at(17, 8), cr.at(18, 8)
    check("a shared formula: the master's text and group id on its child",
          (h17.formula_kind, h17.shared_id, h18.formula_kind, h18.shared_id,
           h18.formula, h18.number) == ("shared", "0", "shared", "0", "G17/38",
                                        394.74), (h17, h18))
    i3 = q1.at(3, 9)
    check("a date serial under the built-in date format",
          (i3.number, i3.iso_date, i3.num_fmt) == (46025.0, "2026-01-03",
                                                   "mm-dd-yy"), i3)
    i10 = q1.at(10, 9)
    check("a date serial under a custom date format",
          (i10.iso_date, i10.num_fmt) == ("2026-03-30", "yyyy/m/d;@"), i10)
    check("a number under General is no date", f3.iso_date is None)
    l15 = q1.at(15, 12)
    check("an error value", (l15.kind, l15.text) == ("e", "#DIV/0!"), l15)
    h2 = uc.at(2, 8)
    check("a header that is a formula reads by its saved text",
          (h2.kind, h2.text, h2.formula) == ("str", "Counts (Q1)",
                                             '"Counts (Q1)"'), h2)
    e17 = cr.at(17, 5)
    check("a hyperlink's target", e17.link ==
          "https://example.com/page/1", e17.link)
    check("a comment is marked, its text is not kept", cr.at(18, 7).comment
          and not any(c.comment for k, c in cr.cells.items() if k != (18, 7)))
    check("a hidden row: the row set and its cells",
          q1.hidden_rows == {9} and q1.at(9, 1).hidden
          and not q1.at(8, 1).hidden)
    check("merges kept", q1.merged == ["J1:L1", "A12:M12"], q1.merged)
    check("a covered position reads its anchor, held or not",
          q1.value(1, 11) is q1.at(1, 10) and q1.value(12, 5) is q1.at(12, 1)
          and q1.anchor_of(1, 10) is None, q1.anchor_of(1, 11))
    check("data validations kept", q1.validations == [
        {"sqref": "C3:C10", "type": "list", "formula1": "Lists!$A$1:$A$4"}],
        q1.validations)
    check("core properties: created, last saved and by whom",
          (wb.props["created"], wb.props["modified"],
           wb.props["modified_by"]) == ("2025-10-01T00:00:00Z",
                                        "2026-10-09T10:44:00Z",
                                        "Test Author"), wb.props)
    check("every part with its sha256, and the file's own",
          "xl/workbook.xml" in wb.parts and len(wb.sha256) == 64
          and all(len(v) == 64 for v in wb.parts.values())
          and wb.size == len(X.im(1)))
    check("Workbook.sheet() of an unknown name is None",
          wb.sheet("nope") is None)
    lumi = W.read(X.lumi())
    g2 = lumi.sheet("DK").at(2, 7)
    check("the 1904 date system", lumi.date1904
          and g2.iso_date == "2026-03-02", (lumi.date1904, g2))
    check("a held covered cell names its merge's anchor",
          lumi.sheet("Betalinger").anchor_of(1, 3) == (1, 1))


NS = M.encode()
RICH = (b'<?xml version="1.0"?><sst xmlns="' + NS + b'"><si><r><t>Ri</t></r><r><t>ch</t></r>'
        b'<rPh sb="0" eb="1"><t>PHONETIC</t></rPh></si></sst>')
SHEET = (b'<?xml version="1.0"?><worksheet xmlns="' + NS + b'"><sheetData><row r="1">'
         b'<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>inline'
         b'</t></is></c><c><v>7</v></c></row><row><c r="A2" t="d"><v>'
         b'2026-02-01</v></c></row></sheetData></worksheet>')


def test_text() -> None:
    print("\n[3] rich, phonetic and inline text; the Strict namespace")
    base = build([{"name": "S", "cells": {"A1": "x"}}])
    wb = W.read(patched(base, {"xl/sharedStrings.xml": RICH,
                               "xl/worksheets/sheet1.xml": SHEET}))
    s = wb.sheets[0]
    check("rich runs joined, the phonetic run left out",
          s.at(1, 1).text == "Rich", s.at(1, 1))
    check("an inline string", s.at(1, 2).text == "inline", s.at(1, 2))
    check("a cell without r takes the next column, a row without r the "
          "next row", s.at(1, 3).number == 7.0 and s.at(2, 1) is not None)
    check("an ISO date cell (t=d)", s.at(2, 1).iso_date == "2026-02-01")
    strict = HTTP + "purl.oclc.org/ooxml/spreadsheetml/main"
    src = zipfile.ZipFile(io.BytesIO(X.im(1)))
    edits = {n: src.read(n).replace(
        NS, strict.encode()) for n in src.namelist() if n.startswith("xl/")}
    a, b = W.read(X.im(1)), W.read(patched(X.im(1), edits))
    same = all((c.text, c.number, c.formula, c.iso_date) ==
               (b.sheets[i].cells[k].text, b.sheets[i].cells[k].number,
                b.sheets[i].cells[k].formula, b.sheets[i].cells[k].iso_date)
               for i, sh in enumerate(a.sheets) for k, c in sh.cells.items())
    check("a Strict Open XML workbook reads the same", same)


def test_dates() -> None:
    print("\n[4] dates")
    cases = [((1, False), "1900-01-01"), ((59, False), "1900-02-28"),
             ((61, False), "1900-03-01"), ((46025, False), "2026-01-03"),
             ((0, True), "1904-01-01"), ((46025.5, False),
                                         "2026-01-03T12:00:00"),
             ((0.25, False), "06:00:00"), ((-1, False), None),
             ((3e6, False), None)]
    bad = [(a, W.serial_to_iso(*a), want) for a, want in cases
           if W.serial_to_iso(*a) != want]
    check("serial_to_iso in both systems, with times and bounds", not bad,
          bad)
    fmts = {"yyyy/m/d;@": True, "mm-dd-yy": True, "h:mm": True,
            "General": False, "0.00": False, '"d"0': False, "[Red]0.00": False,
            "@": False, "#,##0": False, "[$-404]e/m/d": True, "": False,
            None: False}
    bad = [(f, W.is_date_format(f)) for f, want in fmts.items()
           if W.is_date_format(f) != want]
    check("which format codes are dates", not bad, bad)
    wb = W.read(build([{"name": "S", "cells": {"A1": D("2026-05-01"),
                                                 "A2": F("A1+1", 46144)}}]))
    check("a formula's saved number under General is no date",
          wb.sheets[0].at(2, 1).iso_date is None)


def test_refusals() -> None:
    print("\n[5] refusals, coded")
    good = X.im(1)
    check("not a zip: workbook_unreadable",
          code_of(lambda: W.read(b"not a zip")) == "workbook_unreadable")
    check("no workbook part: workbook_unreadable", code_of(lambda: W.read(
        patched(good, {"xl/workbook.xml": None, "_rels/.rels": None})))
        == "workbook_unreadable")
    check("a missing file: workbook_unreadable", code_of(lambda: W.read(
        Path(tmp_dir("wb-")) / "none.xlsx")) == "workbook_unreadable")
    check("broken XML: workbook_unreadable", code_of(lambda: W.read(
        patched(good, {"xl/worksheets/sheet1.xml": b"<worksheet"})))
        == "workbook_unreadable")
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]>'
            b'<sst xmlns="' + NS + b'"><si><t>&a;</t></si></sst>')
    check("a DOCTYPE or ENTITY declaration: workbook_unsafe",
          code_of(lambda: W.read(patched(good, {"xl/sharedStrings.xml":
                                                bomb})))
          == "workbook_unsafe")
    keep = W.MAX_PART
    W.MAX_PART = 1000
    try:
        c = code_of(lambda: W.read(good))
    finally:
        W.MAX_PART = keep
    check("a part over the size cap: workbook_unsafe",
          c == "workbook_unsafe", c)
    keep = W.MAX_TOTAL
    W.MAX_TOTAL = 1000
    try:
        c = code_of(lambda: W.read(good))
    finally:
        W.MAX_TOTAL = keep
    check("parts adding up over the cap: workbook_unsafe",
          c == "workbook_unsafe", c)
    keep = W.MAX_PARTS
    W.MAX_PARTS = 3
    try:
        c = code_of(lambda: W.read(good))
    finally:
        W.MAX_PARTS = keep
    check("too many parts: workbook_unsafe", c == "workbook_unsafe", c)
    check("references: split, join, ranges", (
        W.split_ref("AB12"), W.ref(12, 28), W.col_letters(703),
        W.range_cells("A1:B2")) == ((12, 28), "AB12", "AAA",
                                    [(1, 1), (1, 2), (2, 1), (2, 2)]))
    try:
        W.split_ref("12")
        ok = False
    except ValueError:
        ok = True
    check("a bad reference is a ValueError", ok)
    check("norm(): NFKC, spaces, case", W.norm(" Ｃｏｓｔ  -EU\n") == "cost -eu"
          and W.norm(None) == "")


def test_closure() -> None:
    print("\n[6] workbook.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items() if Path(r["file"]).name ==
           "workbook.tsv"}
    check("the fragment holds its two codes",
          sorted(own) == ["workbook_unreadable", "workbook_unsafe"],
          sorted(own))
    probs = messages.check_registry_closed(_shop.KIT, ["workbook.py"], own,
                                           strict_kit=True)
    check("every msg() in workbook.py is registered and every code emitted",
          probs == [], probs)


def main() -> int:
    _shop.use()
    for fn in (test_writer, test_cells, test_text, test_dates,
               test_refusals, test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
