"""The closed registries a harness keeps in ssot/: fact keys, decision keys,
thresholds. What may be stored is closed: a key, a value domain, a bound
not in these TSVs is refused where it is typed, with a coded message.

  * read_tsv()          strict TSV: the header holds every required column,
                        every row has the header's cell count (a tab inside
                        a cell cannot hide), no stray carriage return, and
                        an id column (or tuple of columns) is non-empty and
                        unique when named. No quoting: a cell is everything
                        between two tabs.
  * FactKeys            ssot/fact_keys.tsv `key, group, type, unit, min,
                        max, label_<lang>…, why`; type = number | text |
                        date | choice:a|b. validate(key, raw) returns the
                        canonical value to store (numbers normalised: 15.0
                        -> 15) or refuses: fact_key_unknown,
                        fact_value_not_number, fact_value_out_of_range,
                        fact_value_not_choice, fact_value_not_date,
                        fact_value_empty. `market_declared` (the market
                        door, kit.market) and `threshold_*` (Thresholds')
                        are reserved: no row may take them.
  * DecisionRegistry    ssot/decision_keys.tsv `entity_type, key, domain,
                        confirm, label_<lang>…, story, why`; domain grammar
                        int:a..b | number:a..b (a side may be empty =
                        unbounded) | a|b|c | date | json | text | sha256,
                        plus the harness's own `domains={"name": fn}`
                        (fn(value) -> canonical str; raise to refuse).
                        confirm = human | none (in force at once: never
                        for a key that can move money) | harness (written
                        only by the harness inside another human confirm;
                        the reference's `with_stage`, generalised).
  * Thresholds          ssot/constants.tsv `name, default, unit, min, max,
                        group, label_<lang>…, explain, why`. In effect for
                        a market: an --assume value (this run only, never
                        stored, reported in meta.assumed_thresholds) over
                        the market's CONFIRMED `threshold_<name>` fact over
                        the TSV default. A pending fact is not in effect.

Labels: one `label_<lang>` column per language of `[harness].languages`.

Deviation (SPEC §registry, Thresholds.get): the SPEC sentence lists the
confirmed fact before --assume; the reference (constants.in_effect) puts
the assumed value over the client's own, which is the point of a what-if
("what would a change do before the client confirms it"), and
kit.contract.stale_warning already reports "in effect was <client_value
or default>". The kit keeps the reference order: assume > confirmed >
default. Like the reference, get()/load() without a connection and a
market are the plain TSV defaults (no assume, no facts).

Additions: FactKeys.validate(…, thresholds=) routes `threshold_<name>`
keys to Thresholds (the reference's SETTABLE_FACT_KEYS held both);
DecisionRegistry(entity_ids={et: regex}) + check_entity_id() port the
reference's per-entity-type id check; `confirm = harness` (above);
Thresholds(superseded={old: new}) + superseded_overrides() for doctor.
Registry file problems are coded (registry_file_missing,
registry_file_invalid), so doctor and fail() can say them.

Test: kit/tests/test_registry.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

from kit.config import config
from kit.contract import HarnessError
from kit.market import MARKET_KEY
from kit.messages import Msg, msg

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
FACT_PREFIX = "threshold_"
FACT_TYPES = ("number", "text", "date")      # and choice:a|b
DOMAINS = ("date", "json", "text", "sha256")  # and int:a..b, number:a..b, a|b|c
CONFIRM = ("human", "none", "harness")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_INT = re.compile(r"^[+-]?\d+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


# ---- the strict TSV reader -------------------------------------------------

def _bad(path: Path | str, problem: str) -> HarnessError:
    return HarnessError(msg("registry_file_invalid", f"{path}: {problem}",
                            path=str(path), problem=problem))


def read_tsv(path: Path | str, required: Iterable[str], *,
             id_col: str | tuple[str, ...] | None = None
             ) -> list[dict[str, str]]:
    """The rows of a registry TSV as dicts (cells stripped), strictly: see
    the module docstring. Blank lines are skipped."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HarnessError(msg("registry_file_missing",
                               f"{path}: no such registry file",
                               path=str(path))) from None
    except (OSError, UnicodeDecodeError) as e:
        raise _bad(path, str(e)) from None
    head: list[str] | None = None
    rows: list[dict[str, str]] = []
    for n, line in enumerate(text.split("\n"), start=1):
        line = line.removesuffix("\r")
        if not line.strip():
            continue
        if "\r" in line:
            raise _bad(path, f"line {n}: a carriage return inside a cell")
        cells = line.split("\t")
        if head is None:
            head = [c.strip() for c in cells]
            dup = sorted({c for c in head if head.count(c) > 1})
            missing = [c for c in required if c not in head]
            if dup:
                raise _bad(path, f"header repeats {', '.join(dup)}")
            if missing:
                raise _bad(path, f"header lacks {', '.join(missing)}")
            continue
        if len(cells) != len(head):
            raise _bad(path, f"line {n}: {len(cells)} cells, the header has "
                             f"{len(head)} (a tab inside a cell?)")
        rows.append(dict(zip(head, (c.strip() for c in cells))))
    if head is None:
        raise _bad(path, "empty (no header)")
    if id_col is not None:
        cols = (id_col,) if isinstance(id_col, str) else tuple(id_col)
        absent = [c for c in cols if c not in head]
        if absent:
            raise _bad(path, f"id column {', '.join(absent)} not in header")
        seen: set[tuple[str, ...]] = set()
        for r in rows:
            ident = tuple(r[c] for c in cols)
            if not all(ident):
                raise _bad(path, f"a row has an empty {'/'.join(cols)}")
            if ident in seen:
                raise _bad(path, f"{'/'.join(cols)} {'/'.join(ident)} is "
                                 f"listed twice")
            seen.add(ident)
    return rows


def _labels() -> list[str]:
    return [f"label_{lang}" for lang in config().languages]


def _source(path: Path | str | None, key: str) -> Path:
    """`path`, else the bound harness's [ssot].<key>."""
    if path is not None:
        return Path(path)
    p = config().ssot_path(key)
    if p is None:
        raise HarnessError(msg("registry_file_missing",
                               f"harness.toml declares no [ssot].{key}",
                               path=f"[ssot].{key}"))
    return p


def _check_labels(path: Path, ident: str, row: dict) -> None:
    empty = [c for c in _labels() if not row[c]]
    if empty:
        raise _bad(path, f"{ident}: empty {', '.join(empty)}")


# ---- numbers ---------------------------------------------------------------

def _decimal(text: str) -> Decimal | None:
    """A finite number typed by a person (full-width digits allowed), else
    None: no nan/inf, no `1_000`, no thousands separators."""
    t = unicodedata.normalize("NFKC", text).strip()
    if not _NUMBER.match(t):
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _canon(d: Decimal) -> str:
    """One stored form per number, so a human retypes one form at confirm:
    2.50 -> 2.5, 1e3 -> 1000, -0 -> 0."""
    return "0" if d == 0 else format(d.normalize(), "f")


def _num(d: Decimal | None) -> int | float | None:
    """A JSON-friendly number for message params."""
    if d is None:
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def _bound(path: Path, ident: str, col: str, cell: str) -> Decimal | None:
    if not cell:
        return None
    d = _decimal(cell)
    if d is None:
        raise _bad(path, f"{ident}: {col} {cell!r} is not a number")
    return d


def _span(lo: Decimal | None, hi: Decimal | None) -> str:
    return f"{'' if lo is None else _canon(lo)}..{'' if hi is None else _canon(hi)}"


# ---- fact keys -------------------------------------------------------------

def _fact_type(cell: str) -> tuple[str, tuple[str, ...]] | None:
    if cell in FACT_TYPES:
        return cell, ()
    if cell.startswith("choice:"):
        choices = tuple(c.strip() for c in cell[len("choice:"):].split("|"))
        if choices and all(choices) and len(set(choices)) == len(choices):
            return "choice", choices
    return None


class FactKeys:
    """The closed set of client fact keys (ssot/fact_keys.tsv)."""

    def __init__(self, path: Path | str | None = None):
        self.path = _source(path, "fact_keys")
        rows = read_tsv(self.path, ["key", "group", "type", "unit", "min",
                                    "max", *_labels(), "why"], id_col="key")
        self._rows: dict[str, dict] = {}
        for r in rows:
            key = r["key"]
            if not KEY_RE.match(key):
                raise _bad(self.path, f"key {key!r} is not snake_case")
            if key == MARKET_KEY or key.startswith(FACT_PREFIX):
                raise _bad(self.path, f"key {key!r} is reserved "
                           f"({MARKET_KEY}: the market door; {FACT_PREFIX}*: "
                           f"the thresholds registry)")
            if not r["group"]:
                raise _bad(self.path, f"{key}: empty group")
            kind = _fact_type(r["type"])
            if kind is None:
                raise _bad(self.path, f"{key}: type {r['type']!r} is not "
                           f"number, text, date or choice:a|b")
            lo = _bound(self.path, key, "min", r["min"])
            hi = _bound(self.path, key, "max", r["max"])
            if (lo is not None or hi is not None) and kind[0] != "number":
                raise _bad(self.path, f"{key}: min/max only bound a number")
            if lo is not None and hi is not None and lo > hi:
                raise _bad(self.path, f"{key}: min {r['min']} > max {r['max']}")
            _check_labels(self.path, key, r)
            self._rows[key] = {**r, "_kind": kind[0], "_choices": kind[1],
                               "_lo": lo, "_hi": hi}

    def settable(self, thresholds: "Thresholds | None" = None
                 ) -> tuple[str, ...]:
        """Every key `facts set` accepts, in file order (with `thresholds`,
        each `threshold_<name>` after them)."""
        return tuple(self._rows) + (thresholds.keys() if thresholds else ())

    def keys(self, group: str | None = None) -> tuple[str, ...]:
        return tuple(k for k, r in self._rows.items()
                     if group is None or r["group"] == group)

    def groups(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r["group"] for r in self._rows.values()))

    def row(self, key: str) -> dict | None:
        """The TSV row of `key` (its cells as written), None if unknown."""
        r = self._rows.get(key)
        return None if r is None else {k: v for k, v in r.items()
                                       if not k.startswith("_")}

    def bounds(self) -> dict[str, tuple[float | None, float | None]]:
        """key -> (min, max), inclusive, None = unbounded; every row."""
        return {k: (_num(r["_lo"]), _num(r["_hi"]))
                for k, r in self._rows.items()}

    def validate(self, key: str, raw: str, *,
                 thresholds: "Thresholds | None" = None) -> str:
        """The canonical value to store for `key`, or a coded HarnessError
        naming what is wrong. `threshold_<name>` keys go to `thresholds`."""
        r = self._rows.get(key)
        if r is None:
            if thresholds is not None and key in thresholds.keys():
                return thresholds.validate(key, raw)
            keys = list(self.settable(thresholds))
            raise HarnessError(msg(
                "fact_key_unknown",
                f"unknown key {key!r} — nothing reads it. Keys: "
                f"{', '.join(keys)}. A new key is added to the fact key "
                f"registry together with the code that reads it.",
                key=key, keys=keys))
        return _fact_value(key, raw, r["_kind"], r["_choices"], r["_lo"],
                           r["_hi"])


def _fact_value(key: str, raw: str, kind: str, choices: tuple[str, ...],
                lo: Decimal | None, hi: Decimal | None) -> str:
    v = (raw or "").strip()
    if kind == "number":
        d = _decimal(v)
        if d is None:
            raise HarnessError(msg("fact_value_not_number",
                                   f"{key} must be a number, got {raw!r}",
                                   key=key, value=raw))
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            raise HarnessError(msg(
                "fact_value_out_of_range",
                f"{key}={_canon(d)} is outside the allowed range "
                f"{_span(lo, hi)}", key=key, value=raw, min=_num(lo),
                max=_num(hi)))
        return _canon(d)
    if kind == "date":
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise HarnessError(msg(
                "fact_value_not_date",
                f"{key}: {raw!r} is not an ISO date (YYYY-MM-DD)",
                key=key, value=raw)) from None
    if kind == "choice":
        if v not in choices:
            raise HarnessError(msg(
                "fact_value_not_choice",
                f"{key}: {raw!r} must be one of {', '.join(choices)}",
                key=key, value=raw, choices=list(choices)))
        return v
    if not v:
        raise HarnessError(msg("fact_value_empty",
                               f"{key}: a value cannot be empty", key=key))
    return v


# ---- decision keys ---------------------------------------------------------

Domain = Callable[[str], str]


def _parse_domain(cell: str, harness: dict[str, Domain]
                  ) -> tuple[str, object] | None:
    """(kind, arg) of a domain cell, None when it is not one."""
    if cell in DOMAINS:
        return cell, None
    if cell in harness:
        return "harness", cell
    for kind, pattern in (("int", _INT), ("number", _NUMBER)):
        if cell.startswith(kind + ":"):
            lo, sep, hi = cell[len(kind) + 1:].partition("..")
            if not sep or any(x and not pattern.match(x) for x in (lo, hi)):
                return None
            b = tuple(Decimal(x) if x else None for x in (lo, hi))
            if b[0] is not None and b[1] is not None and b[0] > b[1]:
                return None
            return kind, b
    if "|" in cell:
        choices = tuple(c.strip() for c in cell.split("|"))
        if all(choices) and len(set(choices)) == len(choices):
            return "choice", choices
    return None


class DecisionRegistry:
    """The closed set of (entity_type, key) decisions and their value
    domains (ssot/decision_keys.tsv)."""

    def __init__(self, path: Path | str | None = None, *,
                 domains: dict[str, Domain] | None = None,
                 entity_ids: dict[str, str | re.Pattern] | None = None):
        self.path = _source(path, "decision_keys")
        self.domains = dict(domains or {})
        for name in self.domains:
            if name in DOMAINS or not KEY_RE.match(name):
                raise _bad(self.path, f"harness domain {name!r}: a new "
                           f"snake_case name, not a built-in domain")
        self.entity_ids = {et: re.compile(p) if isinstance(p, str) else p
                           for et, p in (entity_ids or {}).items()}
        rows = read_tsv(self.path, ["entity_type", "key", "domain", "confirm",
                                    *_labels(), "story", "why"],
                        id_col=("entity_type", "key"))
        self._rows: dict[tuple[str, str], dict] = {}
        for r in rows:
            et, key = r["entity_type"], r["key"]
            ident = f"{et} {key}"
            if not (KEY_RE.match(et) and KEY_RE.match(key)):
                raise _bad(self.path, f"{ident}: entity_type and key must be "
                                      f"snake_case")
            spec = _parse_domain(r["domain"], self.domains)
            if spec is None:
                raise _bad(self.path, f"{ident}: domain {r['domain']!r} is not "
                           f"int:a..b, number:a..b, a|b|c, "
                           f"{', '.join(DOMAINS)} or a harness domain")
            if r["confirm"] not in CONFIRM:
                raise _bad(self.path, f"{ident}: confirm must be "
                                      f"{'|'.join(CONFIRM)}")
            _check_labels(self.path, ident, r)
            self._rows[(et, key)] = {**r, "_spec": spec}

    def entity_types(self) -> list[str]:
        return list(dict.fromkeys(et for et, _ in self._rows))

    def keys(self, entity_type: str) -> list[str]:
        return [k for et, k in self._rows if et == entity_type]

    def row(self, entity_type: str, key: str) -> dict | None:
        r = self._rows.get((entity_type, key))
        return None if r is None else {k: v for k, v in r.items()
                                       if not k.startswith("_")}

    def check_key(self, entity_type: str, key: str) -> None:
        """HarnessError unless (entity_type, key) is registered."""
        if (entity_type, key) in self._rows:
            return
        if entity_type not in self.entity_types():
            raise HarnessError(msg(
                "decision_entity_type_unknown",
                f"unknown entity_type {entity_type!r} "
                f"({', '.join(self.entity_types())})",
                entity_type=entity_type, entity_types=self.entity_types()))
        allowed = sorted(self.keys(entity_type))
        raise HarnessError(msg(
            "decision_key_unknown",
            f"unknown key {key!r} for entity_type {entity_type!r} — not in "
            f"the decision registry (allowed: {', '.join(allowed)})",
            key=key, entity_type=entity_type, allowed=allowed))

    def domain(self, entity_type: str, key: str) -> str:
        self.check_key(entity_type, key)
        return self._rows[(entity_type, key)]["domain"]

    def check_entity_id(self, entity_type: str, entity_id: str) -> str:
        """`entity_id` when it looks like an id of `entity_type` (the
        harness's `entity_ids` pattern; none declared = any non-blank)."""
        pattern = self.entity_ids.get(entity_type)
        ok = bool(entity_id and entity_id.strip() == entity_id) and (
            pattern is None or pattern.fullmatch(entity_id) is not None)
        if not ok:
            raise HarnessError(msg(
                "decision_entity_id_invalid",
                f"{entity_id!r} is not a valid {entity_type} id",
                entity_id=entity_id, entity_type=entity_type))
        return entity_id

    def validate(self, entity_type: str, key: str, value: str) -> str:
        """The canonical value to store, or a coded HarnessError."""
        self.check_key(entity_type, key)
        kind, arg = self._rows[(entity_type, key)]["_spec"]
        return self._value(kind, arg, value)

    def confirm_exempt(self, entity_type: str, key: str) -> bool:
        """True when the registry marks the key `confirm = none`: its value
        is in force as soon as it is set. An unregistered key (a retired one
        still in an old DB) is never exempt."""
        r = self._rows.get((entity_type, key))
        return r is not None and r["confirm"] == "none"

    def harness_written(self, entity_type: str, key: str) -> bool:
        """True when the key is `confirm = harness`: only the harness writes
        it, inside another human confirm; `set`/`confirm` refuse it."""
        r = self._rows.get((entity_type, key))
        return r is not None and r["confirm"] == "harness"

    def _value(self, kind: str, arg, value: str) -> str:
        v = (value or "").strip()
        if kind in ("int", "number"):
            lo, hi = arg
            if kind == "number":
                d = _decimal(v)
            else:
                t = unicodedata.normalize("NFKC", v)
                d = Decimal(t) if _INT.match(t) else None
            if d is None:
                if kind == "int":
                    raise HarnessError(msg(
                        "decision_value_not_integer",
                        f"{v!r} is not an integer", value=v))
                raise HarnessError(msg("decision_value_not_number",
                                       f"{v!r} is not a number", value=v))
            if (lo is not None and d < lo) or (hi is not None and d > hi):
                raise HarnessError(msg(
                    "decision_value_out_of_range",
                    f"{_canon(d)} is outside {_span(lo, hi)}",
                    value=_num(d), min=_num(lo), max=_num(hi)))
            return str(int(d)) if kind == "int" else _canon(d)
        if kind == "choice":
            if v not in arg:
                raise HarnessError(msg(
                    "decision_value_not_choice",
                    f"{v!r} must be one of {', '.join(arg)}",
                    value=v, choices=list(arg)))
            return v
        if kind == "date":
            try:
                return date.fromisoformat(v).isoformat()
            except ValueError:
                raise HarnessError(msg(
                    "decision_value_not_date",
                    f"{v!r} is not an ISO date (YYYY-MM-DD)",
                    value=v)) from None
        if kind == "json":
            try:
                doc = json.loads(v)
            except (ValueError, RecursionError):
                doc = None
            if not isinstance(doc, dict):
                raise HarnessError(msg(
                    "decision_value_not_json",
                    "the value must be one JSON object", value=v))
            return json.dumps(doc, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        if kind == "sha256":
            if not _SHA256.match(v.lower()):
                raise HarnessError(msg(
                    "decision_value_not_sha256",
                    f"{v!r} is not a sha256 (64 hex digits)", value=v))
            return v.lower()
        if kind == "harness":
            return self._harness_value(arg, v)
        if not v:
            raise HarnessError(msg("decision_value_empty",
                                   "a value cannot be empty"))
        return v

    def _harness_value(self, name: str, v: str) -> str:
        try:
            out = self.domains[name](v)
        except HarnessError:
            raise
        except ValueError as e:
            first = e.args[0] if e.args else None
            if isinstance(first, Msg):
                raise HarnessError(first) from None
            raise HarnessError(msg(
                "decision_value_invalid", f"{v!r} is not a valid {name}: {e}",
                value=v, domain=name, detail=str(e))) from None
        if not isinstance(out, str):
            raise TypeError(f"domain {name!r} returned {type(out).__name__}, "
                            f"not the canonical str")
        return out


# ---- thresholds ------------------------------------------------------------

def _has_facts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='client_facts'").fetchone() is not None


class Thresholds:
    """The cross-client constants (ssot/constants.tsv) and the per-market
    values that override them. One instance per run: its --assume values
    live on it and nowhere else."""

    def __init__(self, path: Path | str | None = None, *,
                 superseded: dict[str, str] | None = None):
        self.path = _source(path, "constants")
        rows = read_tsv(self.path, ["name", "default", "unit", "min", "max",
                                    "group", *_labels(), "explain", "why"],
                        id_col="name")
        self._rows: dict[str, dict] = {}
        for r in rows:
            name = r["name"]
            if not KEY_RE.match(name):
                raise _bad(self.path, f"name {name!r} is not snake_case")
            lo = _bound(self.path, name, "min", r["min"])
            hi = _bound(self.path, name, "max", r["max"])
            d = _decimal(r["default"])
            if d is None:
                raise _bad(self.path, f"{name}: default {r['default']!r} is "
                                      f"not a number")
            if (lo is not None and d < lo) or (hi is not None and d > hi):
                raise _bad(self.path, f"{name}: default {r['default']} is "
                                      f"outside {_span(lo, hi)}")
            _check_labels(self.path, name, r)
            self._rows[name] = {**r, "_default": float(d), "_lo": lo,
                                "_hi": hi}
        self.superseded = dict(superseded or {})
        self._assumed: dict[str, float] = {}

    # -- the registry --

    def names(self) -> tuple[str, ...]:
        return tuple(self._rows)

    def keys(self) -> tuple[str, ...]:
        """The client fact key of each threshold: `threshold_<name>`."""
        return tuple(FACT_PREFIX + n for n in self._rows)

    def overridable(self) -> dict[str, float]:
        """name -> TSV default, in file order."""
        return {n: r["_default"] for n, r in self._rows.items()}

    def bounds(self, name: str) -> tuple[float | None, float | None]:
        r = self._row(name)
        return _num(r["_lo"]), _num(r["_hi"])

    def row(self, name: str) -> dict | None:
        r = self._rows.get(name)
        return None if r is None else {k: v for k, v in r.items()
                                       if not k.startswith("_")}

    def _row(self, name: str) -> dict:
        r = self._rows.get(name)
        if r is None:
            raise HarnessError(msg(
                "threshold_unknown", f"{name!r} is not a threshold (known: "
                f"{', '.join(self._rows)})", name=name,
                names=list(self._rows)))
        return r

    def validate(self, key: str, raw: str) -> str:
        """The canonical value of fact `threshold_<name>` (or bare name),
        within the TSV min..max, or a coded HarnessError."""
        name = key.removeprefix(FACT_PREFIX)
        r = self._row(name)
        return _fact_value(FACT_PREFIX + name, raw, "number", (), r["_lo"],
                           r["_hi"])

    # -- --assume: this run only, never stored --

    def assume(self, values: dict[str, float]) -> None:
        """Set this run's assumed thresholds, replacing any earlier ones."""
        for name in values:
            self._row(name)
        self._assumed = {n: float(v) for n, v in values.items()}

    def assume_pairs(self, pairs: Iterable[str]) -> dict[str, float]:
        """Parse `threshold_<name>=<value>` strings (the --assume flags) into
        this run's assumptions; a value `facts set` would refuse is refused
        here too. Clears earlier assumptions first."""
        self._assumed = {}
        nxt = [f"{config().cli} facts list --thresholds"]
        out: dict[str, float] = {}
        for pair in pairs:
            key, sep, raw = (x.strip() for x in pair.partition("="))
            name = key.removeprefix(FACT_PREFIX)
            if not sep or key == name or name not in self._rows:
                raise HarnessError(msg(
                    "assume_unknown_threshold", f"--assume {pair!r}: not "
                    f"threshold_<name>=<value> for a known threshold",
                    pair=pair), nxt)
            try:
                value = self.validate(key, raw)
            except HarnessError as e:
                raise HarnessError(msg(
                    "assume_bad_value", f"--assume {pair!r}: {e}", key=key,
                    value=raw), nxt) from None
            out[name] = float(value)
        self._assumed = out
        return dict(out)

    # -- what is in effect --

    def confirmed(self, con: sqlite3.Connection | None, market: str | None,
                  names: Iterable[str] | None = None) -> dict[str, float]:
        """name -> the market's CONFIRMED `threshold_<name>` fact (a pending
        or non-numeric one is not in effect), for `names` (default: all)."""
        if con is None or not market or not _has_facts(con):
            return {}
        want = set(self._rows if names is None else names)
        out = {}
        for key, value in con.execute(
                "SELECT key, value FROM client_facts WHERE market=? AND "
                "is_assumption=0 AND key GLOB ?", (market, FACT_PREFIX + "*")):
            name = key.removeprefix(FACT_PREFIX)
            d = _decimal(str(value)) if value is not None else None
            if name in want and name in self._rows and d is not None:
                out[name] = float(d)
        return {n: out[n] for n in self._rows if n in out}

    def load(self, con: sqlite3.Connection | None = None,
             market: str | None = None) -> dict[str, float]:
        """Every threshold's value in effect: the defaults; with con+market,
        the market's confirmed facts over them, then this run's --assume."""
        k = self.overridable()
        if con is not None and market:
            k.update(self.confirmed(con, market))
            k.update(self._assumed)
        return k

    def get(self, name: str, con: sqlite3.Connection | None = None,
            market: str | None = None) -> float:
        self._row(name)
        return self.load(con, market)[name]

    def overridden(self, con: sqlite3.Connection | None,
                   market: str | None) -> dict[str, dict[str, float]]:
        """{name: {value, default}} for each threshold the market's
        confirmed facts override: meta.thresholds_overridden."""
        d = self.overridable()
        return {n: {"value": v, "default": d[n]}
                for n, v in self.confirmed(con, market).items()}

    def assumed(self, con: sqlite3.Connection | None = None,
                market: str | None = None) -> dict[str, dict]:
        """{name: {value, default, client_value}} for each assumed
        threshold: meta.assumed_thresholds ({} = not a what-if run).
        client_value is the market's confirmed fact, None when the default
        was in effect."""
        d = self.overridable()
        live = self.confirmed(con, market, self._assumed)
        return {n: {"value": v, "default": d[n], "client_value": live.get(n)}
                for n, v in self._assumed.items()}

    def listing(self, con: sqlite3.Connection | None,
                market: str) -> list[dict]:
        """One row per threshold for `market`: default, unit, bounds, labels,
        the market's fact if any (confirmed or pending) and the value in
        effect from stored facts (no --assume): `facts list --thresholds`.
        Read-only."""
        facts: dict[str, tuple] = {}
        if con is not None and _has_facts(con):
            facts = {k: (v, s, a, u) for k, v, s, a, u in con.execute(
                "SELECT key, value, source, is_assumption, updated_at FROM "
                "client_facts WHERE market=? AND key GLOB ?",
                (market, FACT_PREFIX + "*"))}
        live = self.confirmed(con, market)
        out = []
        for name, r in self._rows.items():
            value, source, pending, updated = facts.get(
                FACT_PREFIX + name, (None, None, None, None))
            d = _decimal(str(value)) if value is not None else None
            out.append({
                "market": market, "name": name, "key": FACT_PREFIX + name,
                "default": r["_default"], "unit": r["unit"],
                "min": _num(r["_lo"]), "max": _num(r["_hi"]),
                "value": value if d is None else float(d),
                "is_assumption": pending, "source": source,
                "updated_at": updated,
                "effective": live.get(name, r["_default"]),
                "in_effect": "client" if name in live else "default",
                "group": r["group"], "explain": r["explain"],
                **{c: r[c] for c in _labels()}})
        return out

    def superseded_overrides(self, con: sqlite3.Connection | None
                             ) -> list[dict]:
        """{market, key, value, is_assumption, new_key} for each client fact
        `threshold_<old>` of a superseded name: a value nothing reads any
        more (doctor). Read-only."""
        if not self.superseded or con is None or not _has_facts(con):
            return []
        old = {FACT_PREFIX + o: FACT_PREFIX + n
               for o, n in self.superseded.items()}
        rows = con.execute(
            "SELECT market, key, value, is_assumption FROM client_facts WHERE "
            f"key IN ({','.join('?' * len(old))}) ORDER BY market, key",
            tuple(old)).fetchall()
        return [{"market": m, "key": k, "value": v, "is_assumption": a,
                 "new_key": old[k]} for m, k, v, a in rows]


def add_assume_arg(parser: argparse.ArgumentParser) -> None:
    """--assume threshold_<name>=<value> (repeatable), for a compute; pass
    `args.assume` to Thresholds.assume_pairs()."""
    parser.add_argument(
        "--assume", action="append", default=[],
        metavar="threshold_<name>=<value>",
        help="what-if for this run only, never stored: use this threshold "
             "over the default and the client's own (repeatable; names and "
             f"values: `{config().cli} facts list --thresholds`)")
