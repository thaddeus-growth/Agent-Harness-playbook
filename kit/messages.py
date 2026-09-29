"""Stable machine codes for every human-readable message a harness emits.

Every English sentence a harness puts in a `--json` document is a Msg:
still that same string (text output and every JSON key are unchanged),
plus a `code` from a closed registry and the `params` that fill it. The
agent renders code + params in the client's own language (each registry
row carries `meaning_<lang>` for every language the harness declares)
instead of pasting the English.

The registry = the kit base (kit/message_codes.tsv, then every
kit/message_codes.d/<module>.tsv in sorted order) + the harness's own
(`[ssot].message_codes` in harness.toml, then every
`<ssot dir>/message_codes.d/*.tsv` in sorted order: one fragment per unit
of a fan-out, so parallel workers never edit the same file; `harness_files()`
lists them). Columns: `code`, `params`
(comma-separated names, may be empty), `meaning_<lang>`…; extra columns
are allowed. A code defined twice (in any two files) or a row missing a
required meaning is an error at load.

One placement rule, written only here (coded(), failure()); additive:

  * a message at key `k`         -> sibling `k_code`: {code, params} | null
  * a message list at key `ks`   -> sibling `k_codes`: [{code, params}, …]
                                    same order and length (`warnings` ->
                                    `warning_codes`)
  * a failure document {error, next} is itself one message -> top-level
    `code` + `params` (kit.contract.fail); a verb relaying a child's
    failure keeps the child's (relayed())

A param that is itself a message is nested as its own {code, params}.

The registry is closed both ways, and check_registry_closed() is that
guard as a library call: every msg() call names its code as a string
literal, the code is registered, the call passes exactly the registered
params, and every registered harness code is emitted somewhere.

Deviation (SPEC §messages): kit base rows must carry the kit's own
languages (KIT_LANGUAGES = en, zh), not every language of
`config().languages`: the kit ships en + zh only, so a harness declaring
a third language would otherwise be unable to load the kit at all.
Harness rows must carry every declared language.

Test: kit/tests/test_messages.py.
"""

from __future__ import annotations

import ast
import functools
import glob
import re
from datetime import date
from pathlib import Path

from kit import config as _config

KIT_DIR = Path(__file__).resolve().parent
BASE_TSV = KIT_DIR / "message_codes.tsv"
FRAGMENTS_DIR = KIT_DIR / "message_codes.d"
KIT_LANGUAGES = ("en", "zh")
# An error the harness has no finer code for (sqlite, a crash…): its one
# param is the raw English text.
UNCLASSIFIED = "unclassified_error"
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER = re.compile(r"{(\w+)}")


class RegistryError(ValueError):
    """A registry file is malformed: duplicate code, missing meaning, bad
    header or row (plain text: the registry cannot code its own errors)."""


def read_rows(path: Path, languages: tuple[str, ...]) -> list[dict]:
    """The rows of one registry TSV, strictly: header has `code`, `params`
    and `meaning_<lang>` for each of `languages`; every row has exactly the
    header's cell count, a non-empty code and non-empty meanings. No
    quoting: a cell is everything between two tabs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryError(f"{path}: {e}") from None
    rows = [line.rstrip("\r").split("\t") for line in text.split("\n")]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise RegistryError(f"{path}: empty (no header)")
    head = [c.strip() for c in rows[0]]
    need = ["code", "params", *(f"meaning_{lang}" for lang in languages)]
    missing = [c for c in need if c not in head]
    if missing:
        raise RegistryError(f"{path}: header lacks {', '.join(missing)}")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != len(head):
            raise RegistryError(f"{path}:{n}: {len(r)} cells, header has "
                                f"{len(head)}")
        row = dict(zip(head, (c.strip() for c in r)))
        if not row["code"]:
            raise RegistryError(f"{path}:{n}: empty code")
        empty = [c for c in need[2:] if not row[c]]
        if empty:
            raise RegistryError(f"{path}:{n}: {row['code']} has no "
                                f"{', '.join(empty)}")
        row["params"] = tuple(p.strip() for p in row["params"].split(",")
                              if p.strip())
        row["file"] = str(path)
        out.append(row)
    return out


def _merge(into: dict[str, dict], rows: list[dict], origin: str) -> None:
    for row in rows:
        code = row["code"]
        if code in into:
            raise RegistryError(f"message code {code!r} is defined twice: "
                                f"{into[code]['file']} and {row['file']}")
        into[code] = {**row, "origin": origin}


def kit_files() -> list[Path]:
    """The kit base registry files in load order."""
    return [BASE_TSV, *sorted(FRAGMENTS_DIR.glob("*.tsv"))]


@functools.cache
def base_registry() -> dict[str, dict]:
    """The kit base registry alone (no harness needed)."""
    reg: dict[str, dict] = {}
    for f in kit_files():
        _merge(reg, read_rows(f, KIT_LANGUAGES), "kit")
    return reg


HARNESS_FRAGMENTS = "message_codes.d"


def harness_files(cfg: _config.HarnessConfig | None = None) -> list[Path]:
    """The bound harness's registry files in load order: `[ssot].
    message_codes` (when declared), then every `<ssot dir>/message_codes.d/
    *.tsv`, sorted by name."""
    cfg = cfg or _config.config()
    out: list[Path] = []
    path = cfg.ssot_path("message_codes")
    if path is not None:
        out.append(path)
    frag = cfg.root / cfg.ssot.get("dir", "ssot") / HARNESS_FRAGMENTS
    if frag.is_dir():
        out += sorted(frag.glob("*.tsv"))
    return out


@functools.cache
def registry() -> dict[str, dict]:
    """code -> its row: {"code", "params": (names…), "meaning_<lang>"…,
    "file", "origin": "kit"|"harness"}; kit base ∪ the bound harness's
    (its registry and its fragments). A code defined twice anywhere is a
    RegistryError naming both files."""
    reg = dict(base_registry())
    cfg = _config.config()
    for path in harness_files(cfg):
        _merge(reg, read_rows(path, cfg.languages), "harness")
    return reg


_full_registry = registry


def _clear() -> None:
    base_registry.cache_clear()
    registry.cache_clear()


_config.on_reset(_clear)


class Msg(str):
    """A message: the English text (it IS the str) + code + params."""

    code: str
    params: dict

    def __new__(cls, code: str, text: str, params: dict) -> "Msg":
        reg = registry()
        if code not in reg:
            raise ValueError(f"message code {code!r} is not registered")
        want = set(reg[code]["params"])
        if set(params) != want:
            raise ValueError(f"message {code!r} takes params "
                             f"{sorted(want)}, got {sorted(params)}")
        self = super().__new__(cls, text)
        self.code, self.params = code, dict(params)
        return self

    def __getnewargs__(self):          # copy / deepcopy / pickle
        return (self.code, str(self), self.params)


def msg(code: str, text: str, **params) -> Msg:
    """`text` tagged with its registry `code` and the `params` an agent
    needs to say it again in another language. `code` must be a string
    literal at the call site (check_registry_closed scans for it)."""
    return Msg(code, text, params)


def _param(v):
    if isinstance(v, Msg):
        return code(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_param(x) for x in v]
    if isinstance(v, dict):
        return {k: _param(x) for k, x in v.items()}
    return v


def code(m: Msg | None) -> dict | None:
    """{code, params} of a message, None for None. A plain str is a bug:
    every message a harness emits under --json is a Msg."""
    if m is None:
        return None
    if not isinstance(m, Msg):
        raise TypeError(f"uncoded message (wrap it in msg()): {m!r}")
    return {"code": m.code,
            "params": {k: _param(v) for k, v in m.params.items()}}


def codes(ms) -> list[dict]:
    return [code(m) for m in ms]


def coded(key: str, value) -> dict:
    """{key: value, key's code sibling}: the placement rule above."""
    if isinstance(value, (list, tuple)):
        return {key: value, f"{key.removesuffix('s')}_codes": codes(value)}
    return {key: value, f"{key}_code": code(value)}


def failure(e: BaseException) -> dict:
    """{code, params} of an exception carrying a Msg (the first in its
    args); unclassified_error with the raw text when it carries none."""
    m = next((a for a in e.args if isinstance(a, Msg)), None)
    if m is None:
        m = msg("unclassified_error", str(e), detail=str(e))
    return code(m)


def relayed(c: dict | None, text: str) -> Msg:
    """`text` tagged with the {code, params} another harness process put in
    its failure document (a child's, kit.runner.ChildFailed), unchanged;
    unclassified_error when it had none (a crash) or one this registry
    cannot hold (unknown code, other params)."""
    reg = registry()
    params = dict((c or {}).get("params") or {})
    if (not c or c.get("code") not in reg
            or set(params) != set(reg[c["code"]]["params"])):
        return msg("unclassified_error", text, detail=text)
    return Msg(c["code"], text, params)


def joined(parts: list[Msg], sep: str = "; ") -> Msg:
    """Several messages as one: the texts joined by `sep`, each part kept
    as its own nested code."""
    return msg("joined", sep.join(parts), parts=list(parts))


# ---- the closure guard ----------------------------------------------------

def lint_registry(reg: dict[str, dict]) -> list[str]:
    """Row-level problems: a code that is not snake_case, a meaning that
    names a {placeholder} the row's params do not declare."""
    out = []
    for c, row in sorted(reg.items()):
        if not CODE_RE.match(c):
            out.append(f"{row['file']}: code {c!r} is not lowercase "
                       f"snake_case")
        names = set()
        for k, v in row.items():
            if k.startswith("meaning_") and isinstance(v, str):
                names |= set(_PLACEHOLDER.findall(v))
        extra = names - set(row["params"])
        if extra:
            out.append(f"{row['file']}: {c}: meaning names "
                       f"{sorted(extra)} not in params {list(row['params'])}")
    return out


def msg_calls(files: list[Path], root: Path
              ) -> list[tuple[str, str | None, tuple[str, ...] | None]]:
    """Every msg(...) / x.msg(...) call in `files`: (where, code literal or
    None, sorted keyword names or None when it passes **params)."""
    calls = []
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), str(f))
        except SyntaxError as e:
            calls.append((f"{f}:{e.lineno}", None, None))
            continue
        for n in ast.walk(tree):
            fn = n.func if isinstance(n, ast.Call) else None
            if not (isinstance(fn, ast.Name) and fn.id == "msg"
                    or isinstance(fn, ast.Attribute) and fn.attr == "msg"):
                continue
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            where = f"{rel}:{n.lineno}"
            lit = (n.args and isinstance(n.args[0], ast.Constant)
                   and isinstance(n.args[0].value, str))
            kws = (None if any(k.arg is None for k in n.keywords)
                   else tuple(sorted(k.arg for k in n.keywords)))
            calls.append((where, n.args[0].value if lit else None, kws))
    return calls


def check_registry_closed(root: Path | str, sources: list[str],
                          registry: dict[str, dict] | None = None, *,
                          strict_kit: bool = False) -> list[str]:
    """Problems (empty = closed) of the msg() calls in the files matching
    `sources` (globs relative to `root`, `**` recursive) against `registry`
    (default: the bound harness's full registry): a code that is not a
    literal, an unregistered code, params other than the registered ones,
    a registered code never emitted, plus lint_registry(). Codes of origin
    "kit" are exempt from "never emitted" unless `strict_kit` (the kit's
    own closure test); a harness's own codes never are."""
    root = Path(root)
    reg = _full_registry() if registry is None else registry
    files = sorted({Path(p) for s in sources
                    for p in glob.glob(str(root / s), recursive=True)
                    if p.endswith(".py")})
    out = lint_registry(reg)
    emitted: set[str] = set()
    for where, c, kws in msg_calls(files, root):
        if c is None:
            out.append(f"{where}: msg() code is not a string literal")
            continue
        emitted.add(c)
        if c not in reg:
            out.append(f"{where}: code {c!r} is not registered")
        elif kws is None:
            out.append(f"{where}: {c}: params passed as **mapping; name "
                       f"them")
        elif set(kws) != set(reg[c]["params"]):
            out.append(f"{where}: {c} takes params "
                       f"{sorted(reg[c]['params'])}, the call passes "
                       f"{sorted(kws)}")
    for c, row in sorted(reg.items()):
        if c not in emitted and (row.get("origin") != "kit" or strict_kit):
            out.append(f"{row['file']}: code {c!r} is registered but no "
                       f"msg() call emits it")
    return out
