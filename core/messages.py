"""Stable machine codes for every sentence a harness puts in a `--json` document.

A message is a `Msg`: it IS its English text (a str, so text output and every key
a consumer already reads stay as they were) and it carries a `code` from a closed
registry plus the `params` that fill it. The agent says it again in the client's
own language from code + params; the harness never words anything for one UI.

One placement rule, written only here (`coded()`, `failure()`), and additive:

    a message at key `k`            -> sibling `k_code`: {code, params} | null
    a message list at key `ks`      -> sibling `k_codes`: [{code, params}, …], same
                                       order and length (`warnings` -> `warning_codes`)
    a failure document {error, next} -> top-level `code` + `params` (`failure_doc()`)
    a child's failure, passed on     -> the child's code and params, unchanged (`relayed()`)

A param that is itself a Msg nests as its own {code, params}; a date becomes ISO
text. Codes can be retrofitted onto an existing harness: every step adds a key and
removes none, so no consumer breaks.

The registry is TSV, `code, meaning_en, meaning_<lang>…, params` (params
comma-separated), read from two files: core's own codes, `message_codes.tsv` next
to this file (the gate's refusals, `unclassified_error`), and the harness's,
`ssot/message_codes.tsv` in the folder that holds `core/` (or the file
`use_registry()` names). A code in both is refused. `contract.py` holds the
registry closed both ways.
"""

from __future__ import annotations

import csv
import functools
from datetime import date
from pathlib import Path

CORE_TSV = Path(__file__).with_name("message_codes.tsv")
HARNESS_TSV = Path(__file__).resolve().parents[1] / "ssot" / "message_codes.tsv"
# an error the harness has no finer code for (a crash, a database error): its one
# param is the raw text. The gate's refusals must never end up here (contract.py)
UNCLASSIFIED = "unclassified_error"

_harness = HARNESS_TSV


def use_registry(path) -> None:
    """Read the harness's codes from `path` instead of `ssot/message_codes.tsv`
    beside `core/` (another layout, or a test). The file must exist."""
    global _harness
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"no message registry at {path}")
    _harness = path
    registry.cache_clear()


def registry_files() -> list[Path]:
    """The registry files in use: core's, then the harness's when there is one."""
    return [CORE_TSV] + ([_harness] if _harness.is_file() else [])


def read_tsv(path) -> list[dict]:
    """The rows of one registry file. ValueError on a header of another shape."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    head = rows[0] if rows else []
    if not (len(head) >= 3 and head[:2] == ["code", "meaning_en"] and head[-1] == "params"
            and all(h.startswith("meaning_") for h in head[1:-1])):
        raise ValueError(f"{path}: the header must be code, meaning_en, meaning_<lang>…, params; "
                         f"it is {head}")
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows[1:] if any(r)]


@functools.cache
def registry() -> dict[str, tuple[str, ...]]:
    """code -> its param names, from every registry file. A code in two files is refused."""
    out, where = {}, {}
    for path in registry_files():
        for row in read_tsv(path):
            c = row["code"]
            if c in out:
                raise ValueError(f"message code {c!r} is in both {where[c]} and {path}")
            out[c] = tuple(p.strip() for p in row["params"].split(",") if p.strip())
            where[c] = path
    return out


class Msg(str):
    """A message: the English text (it IS the str), plus code and params."""

    code: str
    params: dict

    def __new__(cls, code: str, text: str, params: dict) -> "Msg":
        if code not in registry():
            raise ValueError(f"message code {code!r} is not registered in "
                             f"{', '.join(str(p) for p in registry_files())}")
        self = super().__new__(cls, text)
        self.code, self.params = code, dict(params)
        return self


def msg(code: str, text: str, **params) -> Msg:
    """`text`, tagged with its registered `code` and the `params` an agent needs
    to say it again in another language. `contract.py` reads every call: the
    code is a literal and the params are exactly the registered ones."""
    return Msg(code, text, params)


def _param(v):
    if isinstance(v, Msg):
        return code(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_param(x) for x in v]
    return v


def code(m: Msg | None) -> dict | None:
    """{code, params} of a message, None for None. A plain str is a bug: every
    message under --json is a Msg."""
    if m is None:
        return None
    if not isinstance(m, Msg):
        raise TypeError(f"uncoded message (wrap it in msg()): {m!r}")
    return {"code": m.code, "params": {k: _param(v) for k, v in m.params.items()}}


def coded(key: str, value) -> dict:
    """{key: value, and its code sibling}: the placement rule above."""
    if isinstance(value, (list, tuple)):
        return {key: list(value), f"{key.removesuffix('s')}_codes": [code(m) for m in value]}
    return {key: value, f"{key}_code": code(value)}


def failure(e: BaseException) -> dict:
    """{code, params} of any exception: the Msg it carries, else
    unclassified_error with its raw text."""
    m = next((a for a in e.args if isinstance(a, Msg)), None)
    return code(m or msg("unclassified_error", str(e), detail=str(e)))


def failure_doc(e: BaseException, next_steps=()) -> dict:
    """The one --json document of a failed verb: {error, next, code, params}.
    `next_steps` are the commands that fix it."""
    return {"error": str(e), "next": list(next_steps), **failure(e)}


def relayed(c: dict | None, text: str) -> Msg:
    """`text` tagged with the {code, params} another process put in its failure
    document (a child verb's, runner.ChildFailed.code), unchanged;
    unclassified_error when it had none (a crash) or one this registry lacks."""
    if not isinstance(c, dict) or c.get("code") not in registry():
        return msg("unclassified_error", text, detail=text)
    return Msg(c["code"], text, dict(c.get("params") or {}))
