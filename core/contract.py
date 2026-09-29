"""The message-code contract, as checks a harness's own test points at its own
sources, registry files and verb table. Each check returns a list of problems,
empty when the contract holds, so one failed assert names every problem at once.

    lint_registry(paths)       the registry files: header, unique snake_case codes,
                               every meaning filled, placeholders only from params
    closed(sources, registry)  closed both ways, by reading the code (AST): every
                               msg() call names a registered code as a literal, with
                               exactly its params as keywords; every registered code
                               is emitted by some msg() call
    uncoded(doc, registry)     every prose value in a --json document without a
                               valid code beside it (messages.coded's placement)
    check_verbs(table, cases, run, registry)
                               every verb in the dispatcher's table has at least
                               one case, so a new verb fails until it has one; each
                               case prints exactly one JSON document; a failure
                               (non-zero exit) is {error, next, code, params} with a
                               registered code that is not unclassified_error; and
                               nothing in any document is uncoded

Prose keys are named, not guessed: PROSE (one message) and PROSE_LISTS (a list of
messages) are the defaults; pass your own. A prose key whose value is a
snake_case word is a machine value and needs no code. `data_paths` exempts a key
that holds data rather than a message, by path (`.rows[].label`).
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path

from . import messages

PROSE = ("message", "reason", "note", "summary", "explanation", "label", "error")
PROSE_LISTS = ("warnings", "reasons", "notes")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER = re.compile(r"{(\w+)}")


def lint_registry(paths) -> list[str]:
    """Problems in the registry files, across all of them."""
    bad, seen = [], {}
    for path in map(Path, paths):
        try:
            rows = messages.read_tsv(path)
        except (OSError, ValueError) as e:
            bad.append(str(e))
            continue
        for r in rows:
            c, params = r["code"], {p.strip() for p in r["params"].split(",") if p.strip()}
            where = f"{path.name}: {c or '(no code)'}"
            if not CODE_RE.match(c):
                bad.append(f"{where}: a code is lowercase snake_case")
            if c in seen:
                bad.append(f"{where}: also in {seen[c]}")
            seen.setdefault(c, path.name)
            for col, text in r.items():
                if col.startswith("meaning_"):
                    if not text.strip():
                        bad.append(f"{where}: {col} is empty")
                    extra = set(_PLACEHOLDER.findall(text)) - params
                    if extra:
                        bad.append(f"{where}: {col} names {sorted(extra)}, not among its params")
    return bad


def scan(sources) -> list[tuple[str, str | None, tuple | None]]:
    """Every msg() call in `sources`: (file:line, the code if a literal, the
    keyword names if all are plain keywords)."""
    calls = []
    for path in map(Path, sources):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for n in ast.walk(tree):
            fn = n.func if isinstance(n, ast.Call) else None
            if not (isinstance(fn, ast.Name) and fn.id == "msg"
                    or isinstance(fn, ast.Attribute) and fn.attr == "msg"):
                continue
            lit = n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
            kws = None if any(k.arg is None for k in n.keywords) else tuple(sorted(k.arg for k in n.keywords))
            calls.append((f"{path.name}:{n.lineno}", n.args[0].value if lit else None, kws))
    return calls


def closed(sources, registry: dict) -> list[str]:
    """The registry is closed both ways over `sources` (see the module doc)."""
    bad, emitted = [], set()
    for where, c, kws in scan(sources):
        if c is None:
            bad.append(f"{where}: msg() names its code as a string literal")
            continue
        emitted.add(c)
        if c not in registry:
            bad.append(f"{where}: {c} is not registered")
        elif kws is None:
            bad.append(f"{where}: {c} passes its params as plain keywords, not **")
        elif set(kws) != set(registry[c]):
            bad.append(f"{where}: {c} takes {sorted(registry[c])}, this call passes {sorted(kws)}")
    bad += [f"{c}: registered, but no msg() call emits it" for c in sorted(set(registry) - emitted)]
    return bad


def code_ok(c, registry: dict) -> bool:
    """A {code, params} object: a registered code, exactly its registered params,
    every nested message param valid too."""
    if not (isinstance(c, dict) and set(c) == {"code", "params"} and c["code"] in registry
            and isinstance(c["params"], dict) and set(c["params"]) == set(registry[c["code"]])):
        return False
    return all(code_ok(v, registry) for v in c["params"].values()
               if isinstance(v, dict) and set(v) == {"code", "params"})


def uncoded(doc, registry: dict, *, prose=PROSE, prose_lists=PROSE_LISTS,
            data_paths=(), _where: str = "") -> list[str]:
    """Every prose value in `doc` without a valid code beside it, as paths."""
    bad = []
    if isinstance(doc, list):
        for i, x in enumerate(doc):
            bad += uncoded(x, registry, prose=prose, prose_lists=prose_lists,
                           data_paths=data_paths, _where=f"{_where}[{i}]")
        return bad
    if not isinstance(doc, dict):
        return bad
    for k, v in doc.items():
        path = f"{_where}.{k}"
        if re.sub(r"\[\d+\]", "[]", path) in data_paths:
            continue
        if k == "error" and not _where and code_ok({"code": doc.get("code"), "params": doc.get("params")},
                                                   registry):
            continue                       # a failure document's: coded by its top-level code + params
        if k in prose_lists and isinstance(v, list):
            cs = doc.get(f"{k.removesuffix('s')}_codes")
            if not (isinstance(cs, list) and len(cs) == len(v) and all(code_ok(c, registry) for c in cs)):
                bad.append(path)
        elif k in prose or k in prose_lists:
            if v is None:
                if doc.get(f"{k}_code", "absent") is not None:
                    bad.append(path)
            elif not (isinstance(v, str) and CODE_RE.match(v)) and not code_ok(doc.get(f"{k}_code"), registry):
                bad.append(path)
    for k, v in doc.items():
        if k.endswith(("_code", "_codes")) or (k == "params" and "code" in doc):
            continue                       # a code and its params are data, not prose
        bad += uncoded(v, registry, prose=prose, prose_lists=prose_lists,
                       data_paths=data_paths, _where=f"{_where}.{k}")
    return bad


def failure_problems(doc, registry: dict) -> list[str]:
    """What keeps `doc` from being a classified failure document."""
    if not isinstance(doc, dict):
        return ["a failure prints one {error, next, code, params} object"]
    bad = []
    if not (isinstance(doc.get("error"), str) and doc["error"].strip()):
        bad.append("no `error` text")
    nxt = doc.get("next")
    if not (isinstance(nxt, list) and all(isinstance(x, str) for x in nxt)):
        bad.append("`next` is not a list of commands")
    if not code_ok({"code": doc.get("code"), "params": doc.get("params")}, registry):
        bad.append(f"code {doc.get('code')!r}: not registered, or not exactly its params")
    elif doc["code"] == messages.UNCLASSIFIED:
        bad.append(f"{messages.UNCLASSIFIED}: a refusal the harness can name gets its own code")
    return bad


def check_verbs(table, cases: dict, run, registry: dict, **walk) -> list[str]:
    """`table`: the dispatcher's verb names. `cases`: verb -> [argv, …], run in
    order. `run(verb, argv)` -> (exit code, stdout). `walk`: uncoded()'s options."""
    verbs = list(table)
    bad = [f"{v}: no --json case (a new verb fails here until it has one)"
           for v in verbs if not cases.get(v)]
    bad += [f"{v}: a case for a verb the table does not have" for v in cases if v not in verbs]
    for verb, argvs in cases.items():
        for argv in argvs:
            rc, out = run(verb, argv)
            where = f"{verb} {shlex.join(argv)}"
            try:
                doc = json.loads(out)
            except ValueError:
                bad.append(f"{where}: stdout is not exactly one JSON document")
                continue
            if rc != 0:
                bad += [f"{where}: {p}" for p in failure_problems(doc, registry)]
            bad += [f"{where}: uncoded {p}" for p in uncoded(doc, registry, **walk)]
    return bad
