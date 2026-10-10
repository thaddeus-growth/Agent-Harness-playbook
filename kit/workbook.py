"""Read a client's .xlsx workbook as it is, with the standard library only
(`zipfile` and `xml.etree`): every sheet's cells with their coordinates,
nothing recalculated, nothing lost that a harness may need to read or to
write back.

What it guards:

  * read(path or bytes) -> Workbook. Per cell: its kind as the file says
    it (s, n, b, e, str, inlineStr, d), its text (shared and rich strings
    resolved, phonetic runs left out), its number, its number format, an
    ISO date when the format is a date one (the workbook's 1900 or 1904
    system), the formula text and the cached value beside it (shared
    formulas: the group id, the master's text, untranslated; array
    formulas: their range), its hyperlink target, whether a comment
    is on it, whether its row or column is hidden, and for a cell covered
    by a merge the merge's anchor (`merged_from`; Sheet.value() reads any
    covered position through its anchor). Per sheet: the state
    (visible, hidden, veryHidden), the dimension, the merges, the
    data-validation lists. Per file: the sha256, the size, the core
    properties (created, last saved and by whom), the date system and
    every zip part with its sha256, so a later patch can prove the parts
    it did not touch are byte for byte the same.
  * A formula is never evaluated: its value is the one the file saved.
    A formula with no saved value has text None.
  * Unsafe input is refused before it is parsed (workbook_unsafe): a part
    that declares a DOCTYPE or an ENTITY (entity expansion), a part larger
    than MAX_PART, a file whose parts add up to more than MAX_TOTAL, more
    than MAX_PARTS parts. Nothing is fetched: xml.etree resolves no
    external entity, and a DOCTYPE never reaches it.
  * A file that is not an xlsx (not a zip, no workbook part, broken XML)
    is refused, coded (workbook_unreadable). `.xls` (the binary format)
    is out; a CSV goes through a harness's own import.

Namespaces are matched by local name, so a workbook saved as Strict Open
XML reads the same.

Test: kit/tests/test_workbook.py.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from kit.contract import HarnessError
from kit.messages import msg

MAX_PART = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_PARTS = 10_000

# built-in number formats (ECMA-376 18.8.30) a harness is likely to meet;
# the date ones are every id the standard and the CJK locales reserve
BUILTIN = {0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00",
           9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?", 13: "# ??/??",
           14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
           18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
           22: "m/d/yy h:mm", 37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)",
           39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
           45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0",
           49: "@"}
DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} \
    | set(range(50, 59))
STATES = ("visible", "hidden", "veryHidden")
_REF = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")


@dataclass
class Cell:
    """One cell as the file holds it; see the module docstring."""

    ref: str
    row: int
    col: int
    kind: str = ""
    text: str | None = None
    number: float | None = None
    num_fmt: str | None = None
    iso_date: str | None = None
    formula: str | None = None
    formula_kind: str | None = None
    shared_id: str | None = None
    link: str | None = None
    comment: bool = False
    hidden: bool = False
    merged_from: str | None = None


@dataclass
class Sheet:
    no: int
    name: str
    state: str
    dimension: str | None = None
    merged: list[str] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)

    def at(self, row: int, col: int) -> Cell | None:
        """The cell at (row, col) as the file holds it, or None."""
        return self.cells.get((row, col))

    def anchor_of(self, row: int, col: int) -> tuple[int, int] | None:
        """The top-left cell of the merge covering (row, col), when (row,
        col) is covered and is not that cell itself."""
        for r1, c1, r2, c2 in self._boxes():
            if r1 <= row <= r2 and c1 <= col <= c2 and (row, col) != (r1, c1):
                return r1, c1
        return None

    def value(self, row: int, col: int) -> Cell | None:
        """The cell whose value shows at (row, col): a covered cell reads
        its merge's anchor."""
        a = self.anchor_of(row, col)
        return self.cells.get(a if a else (row, col))

    def _boxes(self) -> list[tuple[int, int, int, int]]:
        if getattr(self, "_box_n", None) != len(self.merged):
            boxes = []
            for rng in self.merged:
                a, _, b = rng.partition(":")
                r1, c1 = split_ref(a)
                r2, c2 = split_ref(b) if b else (r1, c1)
                boxes.append((min(r1, r2), min(c1, c2), max(r1, r2),
                              max(c1, c2)))
            self._box_cache, self._box_n = boxes, len(self.merged)
        return self._box_cache

    def max_row(self) -> int:
        return max((r for r, _ in self.cells), default=0)

    def max_col(self) -> int:
        return max((k for _, k in self.cells), default=0)


@dataclass
class Workbook:
    sha256: str
    size: int
    sheets: list[Sheet]
    date1904: bool = False
    props: dict = field(default_factory=dict)
    parts: dict[str, str] = field(default_factory=dict)

    def sheet(self, name: str) -> Sheet | None:
        return next((s for s in self.sheets if s.name == name), None)


# ---- references --------------------------------------------------------

def col_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def split_ref(ref: str) -> tuple[int, int]:
    """'AB12' -> (12, 28)."""
    m = _REF.match(ref.strip().upper())
    if not m:
        raise ValueError(f"not a cell reference: {ref!r}")
    return int(m.group(2)), col_number(m.group(1))


def ref(row: int, col: int) -> str:
    return f"{col_letters(col)}{row}"


def range_cells(rng: str) -> list[tuple[int, int]]:
    """'A1:B2' -> [(1, 1), (1, 2), (2, 1), (2, 2)]; one ref -> itself."""
    a, _, b = rng.partition(":")
    r1, c1 = split_ref(a)
    r2, c2 = split_ref(b) if b else (r1, c1)
    return [(r, c) for r in range(min(r1, r2), max(r1, r2) + 1)
            for c in range(min(c1, c2), max(c1, c2) + 1)]


def norm(text: object) -> str:
    """A header or an anchor as compared: NFKC (full-width folds to
    half-width), whitespace collapsed, case folded."""
    if text is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(text)).split()
                    ).casefold()


# ---- dates -------------------------------------------------------------

def is_date_format(code: str | None) -> bool:
    """A number format that shows a date or a time (quoted text, escapes
    and [colour]/[locale] sections are not looked at)."""
    if not code:
        return False
    s = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", code).lower()
    return s not in ("general", "@") and bool(re.search(r"[dmyhs]", s))


def serial_to_iso(x: float, date1904: bool = False) -> str | None:
    """A date serial as ISO: YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, or HH:MM:SS
    for a time alone. The 1900 system keeps Excel's own count (serial 60
    is its 29 February 1900, which never was)."""
    if x < 0 or x > 2958465:
        return None
    days = int(x)
    secs = round((x - days) * 86400)
    if secs >= 86400:
        days, secs = days + 1, 0
    if days == 0 and secs and not date1904:
        return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
    if date1904:
        base = datetime(1904, 1, 1)
    else:
        base = datetime(1899, 12, 31) if days < 60 else datetime(1899, 12, 30)
    d = base + timedelta(days=days, seconds=secs)
    return d.strftime("%Y-%m-%dT%H:%M:%S") if secs else d.strftime(
        "%Y-%m-%d")


# ---- the zip -----------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    """An attribute by local name (r:id and id alike)."""
    if name in el.attrib:
        return el.attrib[name]
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _kids(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _kid(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


class _Zip:
    def __init__(self, data: bytes, label: str):
        self.label = label
        try:
            self.z = zipfile.ZipFile(io.BytesIO(data))
            infos = self.z.infolist()
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            raise _unreadable(label, f"not a zip archive ({e})") from None
        if len(infos) > MAX_PARTS:
            raise _unsafe(label, "-", f"{len(infos)} parts, more than "
                                      f"{MAX_PARTS}")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL:
            raise _unsafe(label, "-", f"{total} bytes unpacked, more than "
                                      f"{MAX_TOTAL}")
        self.names = {i.filename for i in infos}
        self.cache: dict[str, bytes] = {}

    def has(self, name: str) -> bool:
        return name in self.names

    def bytes(self, name: str) -> bytes:
        if name not in self.cache:
            with self.z.open(name) as f:
                data = f.read(MAX_PART + 1)
            if len(data) > MAX_PART:
                raise _unsafe(self.label, name, f"larger than {MAX_PART} "
                                                f"bytes unpacked")
            self.cache[name] = data
        return self.cache[name]

    def xml(self, name: str) -> ET.Element:
        data = self.bytes(name)
        low = data.lower()
        if b"<!doctype" in low or b"<!entity" in low:
            raise _unsafe(self.label, name, "declares a DOCTYPE or an "
                                            "ENTITY")
        try:
            return ET.fromstring(data)
        except ET.ParseError as e:
            raise _unreadable(self.label, f"{name}: broken XML ({e})"
                              ) from None

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """{id: (type, target part)} of a part's relationships."""
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        if not self.has(rp):
            return {}
        out = {}
        for r in self.xml(rp):
            target = _attr(r, "Target") or ""
            if (_attr(r, "TargetMode") or "") == "External":
                full = target
            elif target.startswith("/"):
                full = target.lstrip("/")
            else:
                full = posixpath.normpath(posixpath.join(d, target))
            out[_attr(r, "Id") or ""] = (_attr(r, "Type") or "", full)
        return out


def _unreadable(label: str, reason: str) -> HarnessError:
    return HarnessError(msg(
        "workbook_unreadable",
        f"{label} cannot be read as an xlsx workbook: {reason}",
        path=label, reason=reason),
        ["save it from the spreadsheet program as .xlsx and import it "
         "again"])


def _unsafe(label: str, part: str, reason: str) -> HarnessError:
    return HarnessError(msg(
        "workbook_unsafe",
        f"{label}: part {part} is refused before it is read: {reason}",
        path=label, part=part, reason=reason),
        ["ask the sender for a copy saved again from the spreadsheet "
         "program"])


# ---- the parts ---------------------------------------------------------

def _shared_strings(z: _Zip, part: str | None) -> list[str]:
    if not part or not z.has(part):
        return []
    out = []
    for si in z.xml(part):
        out.append(_rich(si))
    return out


def _rich(el: ET.Element) -> str:
    """The text of an <si> or <is>: its <t>, or its runs' <t>; phonetic
    runs (<rPh>) are not the text."""
    parts = []
    for c in el:
        n = _local(c.tag)
        if n == "t":
            parts.append(c.text or "")
        elif n == "r":
            t = _kid(c, "t")
            parts.append(t.text or "" if t is not None else "")
    return "".join(parts)


def _formats(z: _Zip, part: str | None) -> list[str | None]:
    """The number format code of each cell style (cellXfs index)."""
    if not part or not z.has(part):
        return []
    root = z.xml(part)
    custom = {}
    nf = _kid(root, "numFmts")
    for f in (nf if nf is not None else []):
        custom[int(_attr(f, "numFmtId") or 0)] = _attr(f, "formatCode")
    out: list[str | None] = []
    xfs = _kid(root, "cellXfs")
    for xf in (xfs if xfs is not None else []):
        i = int(_attr(xf, "numFmtId") or 0)
        code = custom.get(i) or BUILTIN.get(i)
        if code is None:
            code = f"builtin:{i}"
        if i in DATE_IDS and i not in custom:
            code = code if code != f"builtin:{i}" else f"builtin-date:{i}"
        out.append(code)
    return out


def _is_date(code: str | None) -> bool:
    if code and code.startswith("builtin-date:"):
        return True
    if code and code.startswith("builtin:"):
        return False
    return is_date_format(code)


def _props(z: _Zip) -> dict:
    out = {"created": None, "modified": None, "modified_by": None,
           "title": None}
    if not z.has("docProps/core.xml"):
        return out
    names = {"created": "created", "modified": "modified",
             "lastModifiedBy": "modified_by", "title": "title"}
    for el in z.xml("docProps/core.xml"):
        k = names.get(_local(el.tag))
        if k:
            out[k] = (el.text or "").strip() or None
    return out


def _sheet(z: _Zip, part: str, no: int, name: str, state: str,
           sst: list[str], fmts: list[str | None], date1904: bool) -> Sheet:
    sh = Sheet(no=no, name=name, state=state)
    root = z.xml(part)
    dim = _kid(root, "dimension")
    sh.dimension = _attr(dim, "ref") if dim is not None else None
    cols = _kid(root, "cols")
    for c in (cols if cols is not None else []):
        if (_attr(c, "hidden") or "") in ("1", "true"):
            lo, hi = int(_attr(c, "min") or 0), int(_attr(c, "max") or 0)
            sh.hidden_cols.update(range(lo, min(hi, 16384) + 1))
    shared: dict[str, str] = {}
    data = _kid(root, "sheetData")
    last_row = 0
    for row in (data if data is not None else []):
        r = int(_attr(row, "r") or last_row + 1)
        last_row = r
        if (_attr(row, "hidden") or "") in ("1", "true"):
            sh.hidden_rows.add(r)
        last_col = 0
        for c in _kids(row, "c"):
            at = _attr(c, "r")
            rr, k = split_ref(at) if at else (r, last_col + 1)
            last_col = k
            cell = _cell(c, rr, k, sst, fmts, date1904, shared)
            if cell is not None:
                sh.cells[(rr, k)] = cell
    _mark_hidden(sh)
    mc = _kid(root, "mergeCells")
    for m in (mc if mc is not None else []):
        rng = _attr(m, "ref") or ""
        if not rng:
            continue
        sh.merged.append(rng)
    dvs = _kid(root, "dataValidations")
    for dv in (dvs if dvs is not None else []):
        f1 = _kid(dv, "formula1")
        sh.validations.append({"sqref": _attr(dv, "sqref") or "",
                               "type": _attr(dv, "type") or "",
                               "formula1": (f1.text or "") if f1 is not None
                               else ""})
    rels = z.rels(part)
    hl = _kid(root, "hyperlinks")
    for h in (hl if hl is not None else []):
        at = (_attr(h, "ref") or "").split(":")[0]
        if not at:
            continue
        rid = _attr(h, "id")
        target = rels.get(rid, ("", ""))[1] if rid else ""
        loc = _attr(h, "location")
        link = target or (f"#{loc}" if loc else None)
        rr, k = split_ref(at)
        cell = sh.cells.get((rr, k)) or Cell(ref=ref(rr, k), row=rr, col=k)
        cell.link = link
        sh.cells[(rr, k)] = cell
    for typ, target in rels.values():
        if typ.endswith("/comments") or typ.endswith("/threadedComment"):
            if not z.has(target):
                continue
            for el in z.xml(target).iter():
                if _local(el.tag) in ("comment", "threadedComment"):
                    at = _attr(el, "ref")
                    if at:
                        rr, k = split_ref(at.split(":")[0])
                        cell = sh.cells.get((rr, k)) or Cell(
                            ref=ref(rr, k), row=rr, col=k)
                        cell.comment = True
                        sh.cells[(rr, k)] = cell
    for (rr, k), cell in sh.cells.items():     # covered cells the file holds
        a = sh.anchor_of(rr, k)
        if a:
            cell.merged_from = ref(*a)
    _mark_hidden(sh)
    return sh


def _mark_hidden(sh: Sheet) -> None:
    for (r, k), c in sh.cells.items():
        c.hidden = r in sh.hidden_rows or k in sh.hidden_cols


def _cell(c: ET.Element, r: int, k: int, sst: list[str],
          fmts: list[str | None], date1904: bool,
          shared: dict[str, str]) -> Cell | None:
    t = _attr(c, "t") or "n"
    v = _kid(c, "v")
    f = _kid(c, "f")
    is_ = _kid(c, "is")
    if v is None and f is None and is_ is None:
        return None
    s = int(_attr(c, "s") or 0)
    cell = Cell(ref=ref(r, k), row=r, col=k, kind=t,
                num_fmt=fmts[s] if s < len(fmts) else None)
    raw = v.text if v is not None else None
    if t == "s" and raw is not None:
        i = int(raw)
        cell.text = sst[i] if 0 <= i < len(sst) else None
    elif t == "inlineStr":
        cell.text = _rich(is_) if is_ is not None else None
    elif t == "b":
        cell.text = None if raw is None else ("TRUE" if raw.strip() in
                                              ("1", "true") else "FALSE")
        cell.number = None if raw is None else float(cell.text == "TRUE")
    elif t in ("e", "str"):
        cell.text = raw
    elif t == "d":
        cell.text = raw
        cell.iso_date = raw
    else:
        cell.text = raw
        if raw is not None:
            try:
                cell.number = float(raw)
            except ValueError:
                cell.number = None
            if cell.number is not None and _is_date(cell.num_fmt):
                cell.iso_date = serial_to_iso(cell.number, date1904)
    if f is not None:
        kind = _attr(f, "t") or "normal"
        cell.formula_kind = kind
        text = f.text
        if kind == "shared":
            si = _attr(f, "si")
            cell.shared_id = si
            if text:
                shared[si or ""] = text
            else:
                text = shared.get(si or "")
        cell.formula = text
    return cell


def read(src: Path | str | bytes, *, label: str | None = None) -> Workbook:
    """The workbook at `src` (a path or the file's bytes); see the module
    docstring for what each object holds."""
    if isinstance(src, (bytes, bytearray)):
        data, name = bytes(src), label or "<bytes>"
    else:
        p = Path(src).expanduser()
        name = label or p.name
        try:
            data = p.read_bytes()
        except OSError as e:
            raise _unreadable(name, f"cannot open the file ({e.strerror})"
                              ) from None
    z = _Zip(data, name)
    parts = {n: hashlib.sha256(z.bytes(n)).hexdigest()
             for n in sorted(z.names) if not n.endswith("/")}
    root_rels = z.rels("") if z.has("_rels/.rels") else {}
    wb_part = next((t for typ, t in root_rels.values()
                    if typ.endswith("/officeDocument")), "xl/workbook.xml")
    if not z.has(wb_part):
        raise _unreadable(name, "no workbook part")
    wb = z.xml(wb_part)
    pr = _kid(wb, "workbookPr")
    date1904 = pr is not None and (_attr(pr, "date1904") or "") in (
        "1", "true")
    rels = z.rels(wb_part)
    sst_part = next((t for typ, t in rels.values()
                     if typ.endswith("/sharedStrings")), None)
    sty_part = next((t for typ, t in rels.values()
                     if typ.endswith("/styles")), None)
    sst = _shared_strings(z, sst_part)
    fmts = _formats(z, sty_part)
    sheets = []
    el = _kid(wb, "sheets")
    for n, s in enumerate(el if el is not None else [], 1):
        typ, target = rels.get(_attr(s, "id") or "", ("", ""))
        if not typ.endswith("/worksheet") or not z.has(target):
            continue
        state = _attr(s, "state") or "visible"
        sheets.append(_sheet(z, target, n, _attr(s, "name") or f"#{n}",
                             state if state in STATES else "visible",
                             sst, fmts, date1904))
    return Workbook(sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                    sheets=sheets, date1904=date1904, props=_props(z),
                    parts=parts)
