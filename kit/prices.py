"""Prices: the registry of what each paid vendor call costs, an owner file
whose rows are pending until a person confirms them through the gate.

The file is a TSV the harness names (e.g. ssot/provider_prices.tsv), one
row per (provider, kind, model):

    provider, kind, model   the key (what is called, by whom)
    unit                    what the price is per (per_second, per_call,
                            per_10k_chars ...); a quantity is in this unit
    <value column>          the price: `value`, or `price_<currency>` which
                            names the currency (price_usd -> USD); blank =
                            unknown, never zero
    status                  pending | confirmed | retired (blank = pending)
    source                  where the number was read (a page, an API, a
                            quote), in the reader's words
    read_on                 the date it was read (optional column)
    confirmed_by, confirmed_at, confirmed_value
                            written by confirm() only (added to the header
                            the first time a row is confirmed)

Any other column is kept as it is. Every write goes through kit.atomic and
is logged, one JSON line per change, in `<file minus suffix>.log.jsonl`
next to it (append-only, fsynced), with who and why.

What it guards:

  * An unknown price is never zero: a blank value is None, and estimate()
    returns cost None for it (and for a key with no row). A caller that
    plans paid work (kit.takes) refuses an unpriced item.
  * Trust lives in the row. A price counts as confirmed only when a person
    passed the gate for it: status `confirmed` AND confirmed_by and
    confirmed_at present AND confirmed_value equal to the value. A status
    typed `confirmed` by hand, or a value edited after its confirmation,
    reads as pending. estimate() says which: `confirmed` False and a coded
    note (prices_unconfirmed), so a caller can say "unconfirmed" and
    kit.takes keeps the standing allowance off it.
  * A retired row is not found: lookup() and estimate() treat it as no
    row, and proposals() never revives it.
  * The file is strict: required columns, one value column, the header's
    cell count on every row, a known status, a number >= 0 or blank, no
    duplicate key. A broken file is refused (coded), never half-read.
  * proposals(registry, observed) turns what was read off a vendor's
    catalog into pending rows: a key missing from the registry, or a
    pending row whose value or unit differs, each with its source and the
    date it was read. A confirmed row is never overwritten: a changed
    confirmed price comes back as a proposal (`confirmed_differs`) that
    propose() holds and does not write. The owner decides.
  * confirm() raises trust through the human gate (kit.human.confirm): the
    person sees provider, kind, model, value, unit, source and date, and
    retypes the value exactly; or a relayed code comes back bound to
    {key: value, unit, source} with the file's content sha as the version,
    so the write it unlocks makes it stale (one use). A blank value is
    refused before the gate. confirmed_by is derived by the gate
    (kit.human.changed_by), never typed. The file is checked again after
    the gate: a file that moved meanwhile is refused.
  * Anyone may lower trust: lower() sets pending or retired without the
    gate (a --reason is still required) and clears the confirmation.

Public API: Row, Registry, Estimate, key_text(), load(), lookup(),
estimate(), proposals(), propose(), confirm(), lower(), log_path().

Paid for: a price read off a vendor's page was its member price (0.45)
while the list price was 0.50, and about 30 rows proposed from the vendor's
catalog waited on the owner, while the harness read every row as a price
and approved spend against prices nobody had confirmed: its reader ignored
the status column.

Test: kit/tests/test_prices.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from kit import dates, human
from kit.atomic import open_atomic
from kit.contract import HarnessError
from kit.messages import Msg, msg

KEY_COLS = ("provider", "kind", "model")
REQUIRED = (*KEY_COLS, "unit", "status", "source")
GATE_COLS = ("confirmed_by", "confirmed_at", "confirmed_value")
STATUSES = ("pending", "confirmed", "retired")
LOWER = ("pending", "retired")
VALUE_COL = re.compile(r"value|price_([a-z]{2,8})")
LOG_SUFFIX = ".log.jsonl"
VERB = "prices confirm"
EPSILON = 1e-12

Key = tuple[str, str, str]


def key_text(key: Key) -> str:
    """A key as people read it: provider/kind/model."""
    return "/".join(key)


def _shown(x: float) -> str:
    """A price as it is stored and retyped: 0.45, 30, 0.0000125."""
    return f"{x:.12f}".rstrip("0").rstrip(".") or "0"


def _cell(v: object) -> str:
    """A cell as written: no tab or line break can split a row."""
    return " ".join(str(v if v is not None else "").split())


@dataclass(frozen=True)
class Row:
    """One price row. `value` None = unknown. `status` is what the row is
    worth (a hand-set `confirmed` reads as pending); `written` is the
    status cell as it stands in the file."""
    provider: str
    kind: str
    model: str
    unit: str
    value: float | None
    status: str
    written: str
    source: str
    read_on: str
    confirmed_by: str
    confirmed_at: str
    line: int

    @property
    def key(self) -> Key:
        return (self.provider, self.kind, self.model)

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True)
class Registry:
    """A loaded price file: its rows by key (retired ones included, see
    get()), the value column and the currency it names (None for a plain
    `value` column: the caller names it), and the file's content sha."""
    path: Path
    column: str
    currency: str | None
    head: tuple[str, ...]
    rows: dict
    cells: tuple
    sha: str

    def row(self, key: Key) -> Row | None:
        """The row for `key` whatever its status, None when there is none."""
        return self.rows.get(tuple(key))

    def get(self, key: Key) -> Row | None:
        """The row for `key`; a retired row is not found."""
        r = self.row(key)
        return None if r is None or r.status == "retired" else r


@dataclass(frozen=True)
class Estimate:
    """What a call would cost: cost None when the price is unknown or there
    is no row; `confirmed` only for a price a person confirmed; `status` is
    the row's, or "missing"; `note` says why it is not a confirmed price."""
    key: Key
    cost: float | None
    confirmed: bool
    status: str
    unit: str
    currency: str | None
    note: Msg | None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = key_text(self.key)
        return d


# ---- reading ---------------------------------------------------------------

def log_path(path: Path | str) -> Path:
    """The history next to the file: prices.tsv -> prices.log.jsonl."""
    p = Path(path)
    return p.with_name(p.stem + LOG_SUFFIX)


def _invalid(path: Path, detail: str) -> HarnessError:
    return HarnessError(msg("prices_file_invalid",
                            f"{path} is not a price file: {detail}. Nothing "
                            f"was read.", path=str(path), detail=detail))


def _bad_row(path: Path, line: int, detail: str) -> HarnessError:
    return HarnessError(msg("prices_row_invalid",
                            f"{path} line {line}: {detail}. Nothing was "
                            f"read.", path=str(path), line=line,
                            detail=detail))


def _value(path: Path, line: int, raw: str) -> float | None:
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        v = math.nan
    if not math.isfinite(v) or v < 0:
        raise _bad_row(path, line, f"price {raw!r} is not a number >= 0 "
                                   f"(blank = unknown)")
    return v


def _effective(written: str, value: float | None, cells: dict) -> str:
    """confirmed only with the gate's own trace for this very value."""
    if written != "confirmed":
        return written
    try:
        same = (value is not None and cells.get("confirmed_by")
                and cells.get("confirmed_at")
                and abs(float(cells.get("confirmed_value", "")) - value)
                <= EPSILON)
    except ValueError:
        same = False
    return "confirmed" if same else "pending"


def load(path: Path | str, *, column: str | None = None) -> Registry:
    """The price file at `path`, strictly. The value column is `column`, or
    the one column named `value` or `price_<currency>`."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise HarnessError(msg("prices_file_missing",
                               f"no price file at {path}", path=str(path))
                           ) from None
    except OSError as e:
        raise _invalid(path, str(e)) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise _invalid(path, str(e)) from None
    lines = [(n, ln.removesuffix("\r")) for n, ln in
             enumerate(text.split("\n"), 1) if ln.strip()]
    if not lines:
        raise _invalid(path, "no header")
    head = tuple(c.strip() for c in lines[0][1].split("\t"))
    missing = [c for c in REQUIRED if c not in head]
    if missing or len(set(head)) != len(head):
        raise _invalid(path, f"the header lacks {', '.join(missing)}"
                       if missing else "the header repeats a column")
    if column is None:
        found = [c for c in head if VALUE_COL.fullmatch(c)]
        if len(found) != 1:
            raise _invalid(path, "one value column (value or price_<currency>)"
                                 f" is needed, found {found or 'none'}")
        column = found[0]
    elif column not in head:
        raise _invalid(path, f"no column {column}")
    m = VALUE_COL.fullmatch(column)
    currency = m.group(1).upper() if m and m.group(1) else None
    rows, cells = {}, []
    for n, ln in lines[1:]:
        parts = ln.split("\t")
        if "\r" in ln or len(parts) != len(head):
            raise _bad_row(path, n, f"{len(parts)} cells, the header has "
                                    f"{len(head)}")
        c = dict(zip(head, (p.strip() for p in parts)))
        key = tuple(c[k] for k in KEY_COLS)
        if not all(key):
            raise _bad_row(path, n, "provider, kind and model are required")
        written = c["status"] or "pending"
        if written not in STATUSES:
            raise _bad_row(path, n, f"status {written!r} is not one of "
                                    f"{', '.join(STATUSES)}")
        if key in rows:
            raise HarnessError(msg(
                "prices_row_duplicate",
                f"{path} line {n}: {key_text(key)} is already on line "
                f"{rows[key].line}. Nothing was read.", path=str(path),
                line=n, key=key_text(key)))
        value = _value(path, n, c[column])
        rows[key] = Row(*key, unit=c["unit"], value=value,
                        status=_effective(written, value, c),
                        written=written, source=c["source"],
                        read_on=c.get("read_on", ""),
                        confirmed_by=c.get("confirmed_by", ""),
                        confirmed_at=c.get("confirmed_at", ""), line=n)
        cells.append(c)
    return Registry(path, column, currency, head, rows, tuple(cells),
                    hashlib.sha256(raw).hexdigest())


def lookup(registry: Registry, key: Key) -> Row | None:
    """The row priced for `key` = (provider, kind, model); a retired row,
    or none, is None."""
    return registry.get(key)


def estimate(registry: Registry, key: Key, quantity: float, *,
             unit: str | None = None) -> Estimate:
    """What `quantity` units of `key` would cost. `unit`, when the caller
    names the unit its quantity is in, must be the row's (else
    prices_unit_mismatch). Unknown is None, never 0."""
    if quantity < 0:
        raise ValueError(f"quantity {quantity!r} is negative")
    key = tuple(key)
    row, name, ccy = registry.get(key), key_text(key), registry.currency
    if row is None:
        return Estimate(key, None, False, "missing", unit or "", ccy,
                        msg("prices_missing", f"{name} has no price row",
                            key=name))
    if unit is not None and unit != row.unit:
        raise HarnessError(msg(
            "prices_unit_mismatch",
            f"{name} is priced {row.unit}, the quantity is {unit}. Nothing "
            f"was estimated.", key=name, unit=unit, expected=row.unit))
    if row.value is None:
        return Estimate(key, None, False, row.status, row.unit, ccy,
                        msg("prices_unknown", f"{name}: the price is blank "
                            f"(unknown, not zero)", key=name))
    note = None if row.confirmed else msg(
        "prices_unconfirmed", f"{name}: the price is {row.status}, not "
        f"confirmed by a person", key=name, status=row.status)
    return Estimate(key, row.value * float(quantity), row.confirmed,
                    row.status, row.unit, ccy, note)


# ---- proposals -------------------------------------------------------------

def _observed(i: int, o: object, source: str | None,
              read_on: str | None) -> dict:
    def bad(detail: str) -> HarnessError:
        return HarnessError(msg("prices_observed_invalid",
                                f"observed row {i}: {detail}. Nothing was "
                                f"proposed.", index=i, detail=detail))
    if not isinstance(o, dict):
        raise bad("not a mapping")
    key = tuple(_cell(o.get(k)) for k in KEY_COLS)
    if not all(key) or not _cell(o.get("unit")):
        raise bad("provider, kind, model and unit are required")
    v = o.get("value")
    if v is not None and v != "":
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = math.nan
        if not math.isfinite(v) or v < 0:
            raise bad(f"value {o.get('value')!r} is not a number >= 0")
    else:
        v = None
    src = _cell(o.get("source") or source)
    if not src:
        raise bad("no source: say where the price was read")
    return {"provider": key[0], "kind": key[1], "model": key[2],
            "unit": _cell(o.get("unit")), "value": v, "status": "pending",
            "source": src, "read_on": _cell(o.get("read_on") or read_on
                                            or dates.host_today())}


def proposals(registry: Registry, observed: list[dict], *,
              source: str | None = None,
              read_on: str | None = None) -> list[dict]:
    """What `observed` ([{provider, kind, model, unit, value, source?,
    read_on?}], read off a vendor) would change, each as a pending row with
    `change` = new | changed | confirmed_differs and `was` (the row's
    value, unit, status; None for new). Same value and unit: nothing. A
    retired row is not revived; a blank observed value never replaces a
    known one. Nothing is written."""
    out = []
    for i, o in enumerate(observed):
        p = _observed(i, o, source, read_on)
        row = registry.row((p["provider"], p["kind"], p["model"]))
        if row is None:
            out.append({**p, "change": "new", "was": None})
            continue
        if row.status == "retired" or p["value"] is None:
            continue
        same = (row.value is not None and abs(row.value - p["value"])
                <= EPSILON and row.unit == p["unit"])
        if same:
            continue
        out.append({**p, "change": "confirmed_differs" if row.confirmed
                    else "changed",
                    "was": {"value": row.value, "unit": row.unit,
                            "status": row.status}})
    return out


# ---- writing ---------------------------------------------------------------

def _write(path: Path, head: tuple[str, ...], cells: list[dict]) -> None:
    with open_atomic(path) as f:
        f.write("\t".join(head) + "\n")
        for c in cells:
            f.write("\t".join(_cell(c.get(h, "")) for h in head) + "\n")


def _log(path: Path, row: dict) -> None:
    lp = log_path(path)
    line = json.dumps({"at": human.now(), **row}, ensure_ascii=False,
                      sort_keys=True, default=str)
    with open(lp, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _index(reg: Registry, key: Key) -> int:
    for i, c in enumerate(reg.cells):
        if tuple(c[k] for k in KEY_COLS) == tuple(key):
            return i
    raise HarnessError(msg("prices_not_found",
                           f"{key_text(key)} has no row in {reg.path}",
                           key=key_text(key), path=str(reg.path)))


def propose(path: Path | str, observed: list[dict], *,
            source: str | None = None, read_on: str | None = None,
            reason: str | None, column: str | None = None) -> dict:
    """Write proposals() into the file as pending rows: a new key is
    appended, a pending row takes the observed value, unit, source and
    date. A confirmed row is never written: its proposal is held and
    returned for the owner. No gate: a pending row approves nothing."""
    why = human.why(reason)
    reg = load(path, column=column)
    props = proposals(reg, observed, source=source, read_on=read_on)
    head = list(reg.head)
    if "read_on" not in head:
        head.append("read_on")
    cells = [dict(c) for c in reg.cells]
    added = updated = 0
    held, by = [], human.changed_by("cli")
    for p in props:
        if p["change"] == "confirmed_differs":
            held.append(p)
            continue
        new = {"unit": p["unit"], reg.column: "" if p["value"] is None
               else _shown(p["value"]), "status": "pending",
               "source": p["source"], "read_on": p["read_on"]}
        if p["change"] == "new":
            cells.append({**dict(zip(KEY_COLS, (p[k] for k in KEY_COLS))),
                          **new})
            added += 1
        else:
            cells[_index(reg, (p["provider"], p["kind"], p["model"]))
                  ].update(new)
            updated += 1
    if added or updated:
        _write(reg.path, tuple(head), cells)
        for p in props:
            if p["change"] != "confirmed_differs":
                _log(reg.path, {"action": "propose", "key": key_text(
                    (p["provider"], p["kind"], p["model"])),
                    "old": p["was"], "new": p["value"], "unit": p["unit"],
                    "source": p["source"], "reason": why, "changed_by": by})
    return {"added": added, "updated": updated, "held": held,
            "message": msg("prices_propose_done",
                           f"{added} new and {updated} changed prices "
                           f"written as pending to {reg.path}; {len(held)} "
                           f"differ from a confirmed price and are held for "
                           f"the owner", added=added, updated=updated,
                           held=len(held), path=str(reg.path))}


def confirm(path: Path | str, key: Key, *, reason: str | None,
            code: str | None = None, relay_user: str | None = None,
            relay_at: str | None = None, verb: str = VERB, market: str = "",
            column: str | None = None) -> dict:
    """A person confirms the price of `key`: they see provider, kind,
    model, value, unit, source and date, and retype the value at the
    terminal, or relay back a code bound to exactly that and the file's
    content sha. Raises HarnessError (prices_not_found, prices_value_blank,
    prices_changed_meanwhile) or kit.human.Refused / CodeRequired."""
    key = tuple(key)
    name = key_text(key)
    reg = load(path, column=column)
    i = _index(reg, key)
    row = reg.row(key)
    if row.value is None:
        raise HarnessError(msg(
            "prices_value_blank",
            f"{name}: the price is blank; fill it from its source before a "
            f"person confirms it. Nothing was written.", key=name))
    why = human.why(reason)
    value = _shown(row.value)
    shown = {"value": value, "unit": row.unit, "source": row.source,
             "read_on": row.read_on}
    subj = human.subject(verb, market, "price", {name: shown},
                         version=reg.sha)
    ccy = f" {reg.currency}" if reg.currency else ""
    channel = human.confirm(
        f"confirm price {name}",
        f"{row.provider} / {row.kind} / {row.model}: {value}{ccy} "
        f"{row.unit}\n  source: {row.source or '(none given)'} (read "
        f"{row.read_on or 'on an unknown date'})\nretype the price to "
        f"confirm it: ", value, subj=subj, code=code)
    why += human.relay_audit(channel, relay_user, relay_at)
    now = load(path, column=column)
    if now.sha != reg.sha:
        raise HarnessError(msg(
            "prices_changed_meanwhile",
            f"{reg.path} changed while the person was being asked, so the "
            f"confirmation of {name} no longer matches it. Nothing was "
            f"written.", key=name, path=str(reg.path)))
    by, at = human.changed_by(channel), human.now()
    head = tuple(reg.head) + tuple(c for c in GATE_COLS if c not in reg.head)
    cells = [dict(c) for c in reg.cells]
    cells[i].update({"status": "confirmed", "confirmed_by": by,
                     "confirmed_at": at, "confirmed_value": value})
    _write(reg.path, head, cells)
    _log(reg.path, {"action": "confirm", "key": name, "old": row.written,
                    "new": "confirmed", **shown, "reason": why,
                    "changed_by": by})
    return {"key": name, "value": value, "unit": row.unit,
            "status": "confirmed", "confirmed_by": by, "confirmed_at": at,
            "message": msg("prices_confirm_done",
                           f"{name}: {value} {row.unit} confirmed by {by}",
                           key=name, value=value, unit=row.unit, by=by)}


def lower(path: Path | str, key: Key, *, status: str, reason: str | None,
          column: str | None = None) -> dict:
    """Anyone lowers trust: `status` pending or retired, no gate, a
    required reason; the confirmation is cleared and the change logged."""
    key = tuple(key)
    name = key_text(key)
    if status not in LOWER:
        raise HarnessError(msg(
            "prices_status_invalid",
            f"status {status!r} is not one trust can be lowered to "
            f"({', '.join(LOWER)}); raising it is confirm(), through the "
            f"gate", status=status, allowed=list(LOWER)))
    why = human.why(reason)
    reg = load(path, column=column)
    i = _index(reg, key)
    old = reg.row(key).written
    cells = [dict(c) for c in reg.cells]
    cells[i]["status"] = status
    for c in GATE_COLS:
        if c in cells[i]:
            cells[i][c] = ""
    _write(reg.path, reg.head, cells)
    by = human.changed_by("cli")
    _log(reg.path, {"action": "lower", "key": name, "old": old,
                    "new": status, "reason": why, "changed_by": by})
    return {"key": name, "status": status, "changed_by": by,
            "message": msg("prices_lower_done",
                           f"{name} is {status} now; only a person, through "
                           f"the gate, confirms it again", key=name,
                           status=status)}
