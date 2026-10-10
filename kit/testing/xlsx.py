"""A small stdlib xlsx writer for tests: a test builds the workbook it
reads, so no client file is ever needed (or committed) to test a reader.

    from kit.testing.xlsx import F, D, E, write
    write(path, [
        {"name": "Roster",
         "cells": {"A2": "Name", "B2": "Fee", "A3": "x",
                   "B3": F("C3/38", 12.5), "C3": D("2026-01-03")},
         "merged": ["A1:B1"], "hidden_rows": [9], "state": "hidden",
         "links": {"A3": "https://example.com/x"},
         "comments": {"B3": "note"},
         "validations": [{"sqref": "B3:B9", "type": "list",
                          "formula1": "Lists!$A$1:$A$4"}]},
    ], date1904=False, props={"modified": "2026-10-09T10:44:00Z"})

Values: str (a shared string), int or float (a number), bool, None (no
cell), F(formula, cached, shared=si, master=True | False, array=range)
(a formula and the value the file saves beside it; cached None saves no
value), D("YYYY-MM-DD", fmt="date" | "custom") (a date serial with a date
style), E("#N/A") (an error value). The zip is deterministic: fixed part
order and time stamps, so the same call gives the same bytes. It writes
what the tests need, no more; it is not the companion export (that will
be its own module).

Test: kit/tests/test_workbook.py.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HTTP = "http:" "//"   # namespace URIs are names, not links; kept unlinkable
M = f"{HTTP}schemas.openxmlformats.org/spreadsheetml/2006/main"
R = f"{HTTP}schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = f"{HTTP}schemas.openxmlformats.org/package/2006/relationships"
OD = f"{HTTP}schemas.openxmlformats.org/officeDocument/2006/relationships"
STAMP = (2026, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class F:
    formula: str | None
    cached: object = None
    shared: str | None = None
    master: bool = True
    array: str | None = None
    ref: str | None = None          # the shared group's range, on its master


@dataclass(frozen=True)
class D:
    iso: str
    fmt: str = "date"


@dataclass(frozen=True)
class E:
    code: str


def serial(iso: str, date1904: bool = False) -> int:
    d = date.fromisoformat(iso)
    base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    return (d - base).days


def _split(ref: str) -> tuple[int, int]:
    m = re.match(r"^([A-Z]+)(\d+)$", ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return int(m.group(2)), n


def _num(v: float) -> str:
    return repr(float(v)) if not float(v).is_integer() else str(int(v))


class _Strings:
    def __init__(self):
        self.items: list[str] = []
        self.index: dict[str, int] = {}

    def id(self, s: str) -> int:
        if s not in self.index:
            self.index[s] = len(self.items)
            self.items.append(s)
        return self.index[s]


def _cell(ref: str, v, sst: _Strings, date1904: bool) -> str:
    if isinstance(v, F):
        attrs, cached = "", v.cached
        if v.array:
            fx = f'<f t="array" ref="{v.array}">{escape(v.formula or "")}</f>'
        elif v.shared is not None and v.master:
            fx = (f'<f t="shared" ref="{v.ref or ref}" si="{v.shared}">'
                  f'{escape(v.formula or "")}</f>')
        elif v.shared is not None:
            fx = f'<f t="shared" si="{v.shared}"/>'
        else:
            fx = f"<f>{escape(v.formula or '')}</f>"
        if cached is None:
            return f'<c r="{ref}">{fx}</c>'
        if isinstance(cached, bool):
            attrs, val = ' t="b"', "1" if cached else "0"
        elif isinstance(cached, str):
            attrs, val = ' t="str"', escape(cached)
        elif isinstance(cached, E):
            attrs, val = ' t="e"', escape(cached.code)
        else:
            val = _num(cached)
        return f'<c r="{ref}"{attrs}>{fx}<v>{val}</v></c>'
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"><v>{1 if v else 0}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{_num(v)}</v></c>'
    if isinstance(v, D):
        s = 2 if v.fmt == "custom" else 1
        return f'<c r="{ref}" s="{s}"><v>{serial(v.iso, date1904)}</v></c>'
    if isinstance(v, E):
        return f'<c r="{ref}" t="e"><v>{escape(v.code)}</v></c>'
    return f'<c r="{ref}" t="s"><v>{sst.id(str(v))}</v></c>'


def _sheet_xml(sh: dict, sst: _Strings, date1904: bool,
               link_ids: dict[str, str]) -> str:
    rows: dict[int, list[tuple[int, str, object]]] = {}
    for ref, v in sh.get("cells", {}).items():
        if v is None:
            continue
        r, c = _split(ref)
        rows.setdefault(r, []).append((c, ref, v))
    hidden = set(sh.get("hidden_rows", ()))
    for r in hidden:
        rows.setdefault(r, [])
    out = [f'<worksheet xmlns="{M}" xmlns:r="{R}">']
    if rows:
        cells = [c for v in rows.values() for c in v]
        if cells:
            lo = min(c for c, _, _ in cells)
            hi = max(c for c, _, _ in cells)
            from_ = f"{_letters(lo)}{min(rows)}"
            to = f"{_letters(hi)}{max(rows)}"
            out.append(f'<dimension ref="{from_}:{to}"/>')
    if sh.get("hidden_cols"):
        out.append("<cols>" + "".join(
            f'<col min="{c}" max="{c}" hidden="1"/>'
            for c in sorted(sh["hidden_cols"])) + "</cols>")
    out.append("<sheetData>")
    for r in sorted(rows):
        h = ' hidden="1"' if r in hidden else ""
        out.append(f'<row r="{r}"{h}>' + "".join(
            _cell(ref, v, sst, date1904) for _, ref, v in sorted(rows[r]))
            + "</row>")
    out.append("</sheetData>")
    if sh.get("merged"):
        out.append(f'<mergeCells count="{len(sh["merged"])}">' + "".join(
            f'<mergeCell ref="{m}"/>' for m in sh["merged"]) + "</mergeCells>")
    if sh.get("validations"):
        out.append("<dataValidations>" + "".join(
            f'<dataValidation type={quoteattr(v.get("type", "list"))} '
            f'sqref={quoteattr(v["sqref"])}><formula1>'
            f'{escape(v.get("formula1", ""))}</formula1></dataValidation>'
            for v in sh["validations"]) + "</dataValidations>")
    if link_ids:
        out.append("<hyperlinks>" + "".join(
            f'<hyperlink ref="{ref}" r:id="{rid}"/>'
            for ref, rid in link_ids.items()) + "</hyperlinks>")
    out.append("</worksheet>")
    return "".join(out)


def _letters(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


STYLES = (f'<styleSheet xmlns="{M}"><numFmts count="1"><numFmt numFmtId="164" '
          'formatCode="yyyy/m/d;@"/></numFmts><fonts count="1"><font/></fonts>'
          '<fills count="1"><fill/></fills><borders count="1"><border/></borders>'
          '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3">'
          '<xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/>'
          '<xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>')


def build(sheets: list[dict], *, date1904: bool = False,
          props: dict | None = None) -> bytes:
    """The bytes of an xlsx holding `sheets` (see the module docstring)."""
    sst = _Strings()
    parts: dict[str, str] = {}
    ct = ['<Default Extension="rels" ContentType="application/vnd.'
          'openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
          '<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
          '<Override PartName="/docProps/core.xml" ContentType="application/'
          'vnd.openxmlformats-package.core-properties+xml"/>']
    wb_rels = []
    sheet_tags = []
    for n, sh in enumerate(sheets, 1):
        rels = []
        link_ids = {}
        for i, (ref, url) in enumerate(sorted(sh.get("links", {}).items()), 1):
            link_ids[ref] = f"rIdL{i}"
            rels.append(f'<Relationship Id="rIdL{i}" Type="{OD}/hyperlink" '
                        f'Target={quoteattr(url)} TargetMode="External"/>')
        if sh.get("comments"):
            rels.append(f'<Relationship Id="rIdC1" Type="{OD}/comments" '
                        f'Target="../comments{n}.xml"/>')
            parts[f"xl/comments{n}.xml"] = (
                f'<comments xmlns="{M}"><authors><author>t</author></authors>'
                '<commentList>' + "".join(
                    f'<comment ref="{ref}" authorId="0"><text><t>{escape(t)}'
                    '</t></text></comment>'
                    for ref, t in sorted(sh["comments"].items()))
                + "</commentList></comments>")
            ct.append(f'<Override PartName="/xl/comments{n}.xml" ContentType='
                      '"application/vnd.openxmlformats-officedocument.'
                      'spreadsheetml.comments+xml"/>')
        parts[f"xl/worksheets/sheet{n}.xml"] = _sheet_xml(
            sh, sst, date1904, link_ids)
        if rels:
            parts[f"xl/worksheets/_rels/sheet{n}.xml.rels"] = (
                f'<Relationships xmlns="{PR}">' + "".join(rels)
                + "</Relationships>")
        ct.append(f'<Override PartName="/xl/worksheets/sheet{n}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.'
                  'spreadsheetml.worksheet+xml"/>')
        wb_rels.append(f'<Relationship Id="rId{n}" Type="{OD}/worksheet" '
                       f'Target="worksheets/sheet{n}.xml"/>')
        state = sh.get("state")
        st = f' state="{state}"' if state and state != "visible" else ""
        sheet_tags.append(f'<sheet name={quoteattr(sh["name"])} sheetId="{n}"'
                          f'{st} r:id="rId{n}"/>')
    k = len(sheets)
    wb_rels.append(f'<Relationship Id="rId{k + 1}" Type="{OD}/styles" '
                   'Target="styles.xml"/>')
    wb_rels.append(f'<Relationship Id="rId{k + 2}" Type="{OD}/sharedStrings" '
                   'Target="sharedStrings.xml"/>')
    pr = '<workbookPr date1904="1"/>' if date1904 else "<workbookPr/>"
    parts["xl/workbook.xml"] = (f'<workbook xmlns="{M}" xmlns:r="{R}">{pr}'
                                f'<sheets>{"".join(sheet_tags)}</sheets>'
                                "</workbook>")
    parts["xl/_rels/workbook.xml.rels"] = (f'<Relationships xmlns="{PR}">'
                                           + "".join(wb_rels)
                                           + "</Relationships>")
    parts["xl/styles.xml"] = STYLES
    parts["xl/sharedStrings.xml"] = (
        f'<sst xmlns="{M}" count="{len(sst.items)}" '
        f'uniqueCount="{len(sst.items)}">' + "".join(
            f'<si><t xml:space="preserve">{escape(s)}</t></si>'
            for s in sst.items) + "</sst>")
    p = {"created": "2025-10-01T00:00:00Z", "modified": "2026-01-01T00:00:00Z",
         "modified_by": "Test Author", **(props or {})}
    parts["docProps/core.xml"] = (
        f'<cp:coreProperties xmlns:cp="{HTTP}schemas.openxmlformats.org/'
        f'package/2006/metadata/core-properties" xmlns:dc="{HTTP}purl.org/'
        f'dc/elements/1.1/" xmlns:dcterms="{HTTP}purl.org/dc/terms/" '
        f'xmlns:xsi="{HTTP}www.w3.org/2001/XMLSchema-instance">'
        f'<cp:lastModifiedBy>{escape(p["modified_by"])}</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{p["created"]}'
        '</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{p["modified"]}'
        '</dcterms:modified></cp:coreProperties>')
    parts["_rels/.rels"] = (
        f'<Relationships xmlns="{PR}"><Relationship Id="rId1" Type="{OD}/'
        'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    parts["[Content_Types].xml"] = (
        f'<Types xmlns="{HTTP}schemas.openxmlformats.org/package/2006/'
        f'content-types">{"".join(ct)}</Types>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["[Content_Types].xml", "_rels/.rels",
                     *sorted(n for n in parts
                             if n not in ("[Content_Types].xml",
                                          "_rels/.rels"))]:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, '<?xml version="1.0" encoding="UTF-8" '
                       'standalone="yes"?>\n' + parts[name])
    return buf.getvalue()


def write(path: Path | str, sheets: list[dict], **kw) -> Path:
    p = Path(path)
    p.write_bytes(build(sheets, **kw))
    return p
