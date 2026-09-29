"""Adapter boundaries: the core never names an adapter, and a consumer
(the vendored console, a host adapter) only reads.

  * `check_not_mentioned(root, core_dirs, pattern)`: no text file under
    the core dirs matches `pattern` (case-insensitive when a str). The
    owner files of the ssot index (business prose, not code) and the
    vendored kit (<scripts_dir>/kit, checked by its own tests) are not
    scanned. Delete the adapter and the harness still runs.
  * `check_consumer(root, consumer_dir)`: in its code and config (prose
    files .md/.tsv/.txt and its own tests/ are not scanned: they run
    nothing), the consumer invokes no verb of
    kind ingest, human or external (a verb that writes the database or
    reaches an external system), as shell prose (`<cli> facts set`) or as an argv
    list (`("facts", "set")`); a gated verb only in a code file that also
    passes the relayed `--code`, `--relay-user` and `--relay-at` (a
    human's click, recorded in history as relayed); never `--apply`;
    never opens the database (the DB file name, `<P>_DB`, sqlite);
    optionally reads only the ssot registries `allowed_reads` names.
    The verb kinds come from the harness's verb table (kit.verbs), so a
    new write verb is covered the day it is listed.
  * `check_boundaries(root)`: both, from `[guards.boundary]`:
    core_dirs (default the scripts and ssot dirs), adapters (dir names the
    core must not name as a path or an import; default console/ when
    present), consumers (default console/ when present).

Deviations (the reference): a consumer's prose files and its own
tests/ are skipped, and the relay-flag rule reads code files only (the
playbook console completes an ask's gate verb in relay.py); the verb
patterns come from the verb table's kinds, not a hand-written list.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kit.guards import (SKIP_DIRS, harness, read, report, section, strs,
                        text_files, verb_table)
from kit.guards import ssot as _ssot

READ, GATED, WRITE = ("read",), ("gated",), ("ingest", "human", "external")
RELAY_FLAGS = ("--code", "--relay-user", "--relay-at")
# What a consumer runs lives in code and config, not in prose: a rule that
# says "the button never says --apply" invokes nothing. And the relayed
# flags are added by the code that runs a gated verb, not by the data
# (an example ask's gate) it runs it from.
PROSE_EXT = (".md", ".tsv", ".txt")
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".html")
_S = r"[\"'`,\s\[\]()]+"   # separators: shell spaces or a JS/Python argv list
_Q = r"[\"']"


def verb_pattern(cli: str, pairs: dict[str, list[str]],
                 singles: list[str] = ()) -> re.Pattern | None:
    """Matches the verbs `pairs` ({first word: [second words]}) and
    `singles` (first words whose every verb matches) after the CLI word
    (`<cli> facts set`, `<cli>.py pull`), or as quoted argv tokens
    (`("facts", "set")`, `["pull", …`). None when there is nothing to
    match."""
    pairs = {a: sorted(set(bs)) for a, bs in pairs.items() if bs}
    singles = sorted(set(singles))
    if not pairs and not singles:
        return None
    alt = [rf"{re.escape(a)}{_S}(?:{'|'.join(map(re.escape, bs))})"
           for a, bs in sorted(pairs.items())]
    one = "|".join(map(re.escape, singles))
    if one:
        alt.append(f"(?:{one})")
    prose = rf"\b{re.escape(cli)}(?:\.py)?{_S}(?:{'|'.join(alt)})\b"
    argv = [rf"{_Q}{re.escape(a)}{_Q}\s*,\s*{_Q}"
            rf"(?:{'|'.join(map(re.escape, bs))}){_Q}"
            for a, bs in sorted(pairs.items())]
    if one:
        argv.append(rf"{_Q}(?:{one}){_Q}\s*,\s*{_Q}")
    return re.compile("|".join([prose, *argv]))


def patterns(cli: str, verbs: list) -> tuple[re.Pattern | None,
                                             re.Pattern | None]:
    """(write, gated) patterns of a verb table: a first word all of whose
    verbs are writes is matched alone (any second word); otherwise each
    write verb by its first two words."""
    by_first: dict[str, list] = {}
    for v in verbs:
        words = tuple(v.words)
        if words:
            by_first.setdefault(words[0], []).append(v)

    def build(kinds: tuple[str, ...]) -> re.Pattern | None:
        pairs: dict[str, list[str]] = {}
        singles: list[str] = []
        for first, vs in by_first.items():
            hit = [v for v in vs if v.kind in kinds]
            if not hit:
                continue
            if len(hit) == len(vs) or any(len(v.words) == 1 for v in hit):
                singles.append(first)
            else:
                pairs[first] = [v.words[1] for v in hit]
        return verb_pattern(cli, pairs, singles)
    return build(WRITE), build(GATED)


def _flag(f: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(f)}(?![\w-])")


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def check_not_mentioned(root: Path | str | None, core_dirs: list[str],
                        pattern: str | re.Pattern, *,
                        exclude: tuple[str, ...] = ()) -> list[str]:
    """Core text files that match `pattern` (owner files and the vendored
    kit left out, plus `exclude` paths or dirs)."""
    cfg = harness(root)
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    owners = _ssot.owner_files(_ssot.rules(cfg=cfg))
    skip = (f"{cfg.scripts_dir}/kit", *exclude)
    out = []
    for d in core_dirs:
        for p in text_files(cfg.root / d):
            rel = _rel(cfg.root, p)
            if rel in owners or any(rel == s or rel.startswith(s.rstrip("/")
                                                               + "/")
                                    for s in skip):
                continue
            m = rx.search(read(p))
            if m:
                out.append(f"{rel}: names {m.group(0)!r}")
    return out


def check_consumer(root: Path | str | None, consumer_dir: str, *,
                   verbs: Any = None,
                   relay_flags: tuple[str, ...] = RELAY_FLAGS,
                   writer_flag: str = "--apply",
                   allowed_reads: dict[str, set[str]] | None = None
                   ) -> list[str]:
    """Every file of `consumer_dir` that breaks the consumer rule (module
    docstring). `allowed_reads` = {file relative to the consumer dir: the
    ssot file names it may read}; None = not checked."""
    cfg = harness(root)
    base = cfg.root / consumer_dir
    if not base.is_dir():
        return []
    write, gated = patterns(cfg.cli, verb_table(cfg, verbs))
    relay = [_flag(f) for f in relay_flags]
    apply = _flag(writer_flag)
    db = re.compile("|".join([re.escape(cfg.db_file), r"better-sqlite3",
                              r"\bsqlite3?\b", rf"\b{cfg.env('DB')}\b"]),
                    re.I)
    ssot_dir = cfg.ssot.get("dir", "ssot").strip("/")
    names_ssot = re.compile(rf"[\"'/]{re.escape(ssot_dir)}[\"'/]")
    out = []
    for p in text_files(base, skip=(*SKIP_DIRS, "tests")):
        rel, text = _rel(cfg.root, p), read(p)
        if p.suffix in PROSE_EXT:
            continue
        if write and (m := write.search(text)):
            out.append(f"{rel}: invokes a write verb ({m.group(0).strip()!r})"
                       f"; a consumer only reads")
        if p.suffix in CODE_EXT and gated and (m := gated.search(text)) \
                and not all(r.search(text) for r in relay):
            out.append(f"{rel}: invokes the gated {m.group(0).strip()!r} "
                       f"without the relayed {', '.join(relay_flags)}")
        if m := apply.search(text):
            out.append(f"{rel}: passes {writer_flag}")
        if m := db.search(text):
            out.append(f"{rel}: opens the database directly "
                       f"({m.group(0)!r}); read the --json contract instead")
        if allowed_reads is not None and names_ssot.search(text):
            own = _rel(base, p)
            got = set(re.findall(r"[\w.-]+\.tsv", text))
            if own not in allowed_reads:
                out.append(f"{rel}: reads {ssot_dir}/ (only "
                           f"{sorted(allowed_reads)} may)")
            elif not got <= set(allowed_reads[own]):
                extra = sorted(got - set(allowed_reads[own]))
                out.append(f"{rel}: reads {extra} (allowed: "
                           f"{sorted(allowed_reads[own])})")
    return out


def adapter_pattern(name: str) -> re.Pattern:
    """A path into the adapter dir (`console/…`) or an import of it."""
    n = re.escape(name.strip("/"))
    return re.compile(rf"(?<![\w.-]){n}/|\bfrom\s+{n}\b|\bimport\s+{n}\b")


def check_boundaries(root: Path | str | None = None, *, verbs: Any = None
                     ) -> list[str]:
    """check_not_mentioned() for every adapter and check_consumer() for
    every consumer named in [guards.boundary] (defaults: console/)."""
    cfg = harness(root)
    b = section(cfg, "guards", "boundary")
    present = [d for d in ("console",) if (cfg.root / d).is_dir()]
    core = list(strs(b.get("core_dirs"),
                     (cfg.scripts_dir, cfg.ssot.get("dir", "ssot"))))
    out = []
    for a in strs(b.get("adapters"), present):
        out += [f"core names the adapter {a}/: {p}" for p in
                check_not_mentioned(cfg.root, core, adapter_pattern(a))]
    for c in strs(b.get("consumers"), present):
        out += check_consumer(cfg.root, c, verbs=verbs)
    return out


def check_boundary(root: Path | str | None = None, **kw: Any) -> bool:
    """check_boundaries() as one kit.testing.check line."""
    return report("boundary: the core names no adapter; a consumer only "
                  "reads and relays", check_boundaries(root, **kw))
