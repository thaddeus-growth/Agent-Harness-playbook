"""The ssot index — the one list of registries and owner files — and the
owner-file lint.

`check_index(root)` returns every problem of these rules:

  * the index (`[ssot].index`, default <ssot dir>/index.tsv) has exactly
    the configured header (default `file owner reader test id_column
    purpose`); one row per file; every <ssot dir>/**.tsv has a row (a
    message-code fragment, <ssot dir>/message_codes.d/*.tsv, is covered by
    the `[ssot].message_codes` row);
  * each row's file exists (`path:SYMBOL` = a code-held registry: the file
    assigns SYMBOL); the owner kind is owner | agent | registry; every
    reader and test path exists and a test is named; a TSV's id column
    (`a+b` for a compound key) is one of its header columns; the purpose
    is set;
  * every ssot TSV has one column count (a stray tab is caught here, not
    by an agent reading a shifted column); ids are unique and non-empty;
  * an owner file (kind owner) holds business meaning only: no issue ref
    `#12`, no `<cli> <verb>` command (`[guards.ssot].cli_verbs` = the verb
    words, when the CLI word is also a business word), no `--flag`, no
    file name (`x.py`, `x.tsv`, …) or `_lib`, no table name (the kit's
    human tables and `[guards.ssot].table_prefixes`), no number written
    right after a threshold name (the number lives in
    `[ssot].constants`). Engineering detail goes to the same id's
    `.agent.tsv` row. `held` lists the owner cells still allowed to fail;
    an entry that no longer fails is itself a problem, so the list only
    shrinks;
  * an `X.agent.tsv` whose owner sibling `X.tsv` exists carries the same
    ids (agent rows whose status is `proposed` or `asked` aside: the owner
    has not adopted them yet; an `asked` row's `ask` reference is the
    trail check's, kit.testing.suites.trail_problems); an agent file with
    no owner sibling is agent-only;
  * ids are never deleted: every id an owner or agent file held at git
    HEAD is still there (retire a row, never delete or renumber it);
    skipped per file when git or the file at HEAD is absent;
  * no ssot text uses a banned term (`[guards.ssot].banned_terms`,
    {banned: preferred}).

Parameters, all optional, in harness.toml `[guards.ssot]`:
index_columns {file, owner, reader, test, id, purpose -> header name},
owner_kinds, cli_verbs, none ("—"), table_prefixes, file_exts, held
[[file, id, column]…], banned_terms, status_column ("status"),
proposed_statuses (["proposed", "asked"]: the statuses of a row the
owner has not adopted yet).

Deviations (SPEC §guards): these parameters live in [guards.ssot], not
[ssot], because kit.config requires every [ssot] value to be a path
string; `[ssot].index` may name the index. "Lists every file in ssot/"
is every ssot TSV (as the reference did): README.md and other prose are
not registries.

A reader cell of — (or empty) marks a planning file: kit.guards.release
keeps it out of a release. The reader column, not the owner kind,
decides what ships.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kit.config import HarnessConfig
from kit.guards import NONE, harness, paths, read, report, section, tsv

ORDER = ("file", "owner", "reader", "test", "id", "purpose")
COLUMNS = {"file": "file", "owner": "owner", "reader": "reader",
           "test": "test", "id": "id_column", "purpose": "purpose"}
KINDS = ("owner", "agent", "registry")
# The kit's own human tables with a compound name (a bare English word such
# as `decisions` is prose, not a table name).
HUMAN_TABLES = ("client_facts", "client_facts_history", "decisions_history",
                "action_queue", "action_effects")
FILE_EXTS = ("py", "tsv", "json", "toml", "sh")
# An agent row the owner has not adopted yet: the agent's idea, or one sent
# to the console and not answered (templates/ssot/README.md, the trail).
PROPOSED = ("proposed", "asked")


@dataclass(frozen=True)
class Rules:
    """Everything the ssot guard reads from one harness.toml."""
    root: Path
    ssot_dir: str
    index: str
    columns: dict
    kinds: tuple[str, ...]
    none: str
    cli: str
    cli_verbs: tuple[str, ...]
    table_prefixes: tuple[str, ...]
    file_exts: tuple[str, ...]
    held: frozenset
    banned: dict
    status_column: str
    proposed: tuple[str, ...]
    constants: str | None
    message_codes: str | None = None


def rules(root: Path | str | None = None,
          cfg: HarnessConfig | None = None) -> Rules:
    cfg = cfg or harness(root)
    g = section(cfg, "guards", "ssot")
    ssot_dir = cfg.ssot.get("dir", "ssot").strip("/")
    constants = cfg.ssot.get("constants")
    return Rules(
        root=cfg.root, ssot_dir=ssot_dir,
        index=cfg.ssot.get("index", f"{ssot_dir}/index.tsv"),
        columns={**COLUMNS, **dict(g.get("index_columns", {}))},
        kinds=tuple(g.get("owner_kinds", KINDS)),
        none=str(g.get("none", NONE)), cli=cfg.cli,
        cli_verbs=tuple(g.get("cli_verbs", ())),
        table_prefixes=tuple(g.get("table_prefixes", ())),
        file_exts=tuple(g.get("file_exts", FILE_EXTS)),
        held=frozenset(tuple(h) for h in g.get("held", ())),
        banned=dict(g.get("banned_terms", {})),
        status_column=str(g.get("status_column", "status")),
        proposed=tuple(g.get("proposed_statuses", PROPOSED)),
        constants=constants,
        message_codes=(cfg.ssot.get("message_codes") or "").strip("/")
        or None)


def _is_none(cell: str, r: Rules) -> bool:
    return cell.strip() in ("", r.none, NONE)


def index_rows(r: Rules) -> tuple[list[str], list[dict[str, str]]]:
    """(header, rows keyed by the logical names file/owner/reader/test/id/
    purpose); ([], []) when there is no index."""
    path = r.root / r.index
    if not path.is_file():
        return [], []
    table = tsv(path)
    if not table:
        return [], []
    head = [c.strip() for c in table[0]]
    pos = {k: head.index(r.columns[k]) for k in ORDER
           if r.columns[k] in head}
    rows = []
    for row in table[1:]:
        cells = [c.strip() for c in row]
        rows.append({k: cells[i] if i < len(cells) else ""
                     for k, i in pos.items()})
    return head, rows


def planning_files(r: Rules) -> set[str]:
    """Index rows with no reader: planning files, never shipped."""
    return {row["file"] for row in index_rows(r)[1]
            if _is_none(row.get("reader", ""), r)}


def read_files(r: Rules) -> set[str]:
    """Files the index names a reader for (a `path:SYMBOL` row = its path):
    they ship."""
    return {row["file"].partition(":")[0] for row in index_rows(r)[1]
            if not _is_none(row.get("reader", ""), r)}


def owner_files(r: Rules) -> set[str]:
    return {row["file"] for row in index_rows(r)[1]
            if row.get("owner") == "owner"}


def thresholds(r: Rules) -> set[str]:
    """The registered threshold names: the first column of [ssot].constants."""
    if not r.constants or not (r.root / r.constants).is_file():
        return set()
    return {row[0].strip() for row in tsv(r.root / r.constants)[1:]
            if row and row[0].strip()}


def lint(text: str, r: Rules, names: set[str] | None = None) -> list[str]:
    """The engineering tokens an owner cell must not carry."""
    names = thresholds(r) if names is None else names
    hits = []
    hits += re.findall(r"#\d+", text)
    verb = ("|".join(map(re.escape, r.cli_verbs)) if r.cli_verbs
            else r"[a-z][\w-]*")
    hits += re.findall(rf"\b{re.escape(r.cli)}(?:\.py)?[ \t]+(?:{verb})\b",
                       text)
    hits += re.findall(r"(?<![\w-])--[a-z][\w-]*", text)
    if r.file_exts:
        exts = "|".join(map(re.escape, r.file_exts))
        hits += re.findall(rf"\w*\.(?:{exts})\b", text)
    hits += re.findall(r"\b_lib\b", text)
    if r.table_prefixes:
        pre = "|".join(map(re.escape, r.table_prefixes))
        hits += [t for t in re.findall(rf"\b(?:{pre})[a-z0-9_]+", text)
                 if t not in names]
    hits += [t for t in HUMAN_TABLES if re.search(rf"\b{t}\b", text)]
    for name in sorted(names):
        hits += re.findall(
            rf"\b{re.escape(name)}\s*[(（]?\s*[=:：]?\s*\d[\d.,]*", text)
    return hits


def lint_file(rel: str, r: Rules, held: frozenset | set = frozenset()
              ) -> tuple[dict[str, list[str]], set]:
    """({"<file> <row id> <column>": hits} outside `held`, the `held`
    (file, id, column) cells that were hit) for the owner file `rel`,
    every column but the id."""
    names = thresholds(r)
    table = tsv(r.root / rel)
    flagged: dict[str, list[str]] = {}
    held_hit: set = set()
    if not table:
        return flagged, held_hit
    for row in table[1:]:
        for col, cell in zip(table[0][1:], row[1:]):
            hits = lint(cell, r, names)
            if not hits:
                continue
            key = (rel, row[0].strip(), col.strip())
            if key in held:
                held_hit.add(key)
            else:
                flagged[" ".join(key)] = hits
    return flagged, held_hit


def _ids(table: list[list[str]], id_cols: list[str]) -> list[tuple] | None:
    """The id of every row (a tuple over the id columns), None when a
    column is missing from the header."""
    head = [c.strip() for c in table[0]]
    if not all(c in head for c in id_cols):
        return None
    pos = [head.index(c) for c in id_cols]
    return [tuple(row[i].strip() if i < len(row) else "" for i in pos)
            for row in table[1:]]


def _show(ids: set[tuple]) -> list[str]:
    return sorted("+".join(i) for i in ids)


def _head_table(root: Path, rel: str) -> list[list[str]] | None:
    """The TSV as git HEAD holds it; None without git, a HEAD or the file."""
    try:
        res = subprocess.run(["git", "-C", str(root), "show", f"HEAD:./{rel}"],
                             capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
    except OSError:
        return None
    if res.returncode != 0:
        return None
    rows = [line.rstrip("\r").split("\t") for line in res.stdout.split("\n")]
    rows = [row for row in rows if any(c.strip() for c in row)]
    return rows or None


def check_index(root: Path | str | None = None) -> list[str]:
    """Every problem of the ssot index and owner-file rules (module
    docstring); empty = the rules hold."""
    r = rules(root)
    out: list[str] = []
    want = [r.columns[k] for k in ORDER]
    head, rows = index_rows(r)
    if not head:
        return [f"{r.index}: missing or empty (the one list of registries "
                f"and owner files)"]
    if head != want:
        out.append(f"{r.index}: header is {head}, expected {want}")
    if not set(want) <= set(head):
        return out

    files = [row["file"] for row in rows]
    dup = sorted({f for f in files if files.count(f) > 1})
    if dup:
        out.append(f"{r.index}: more than one row for {dup}")
    ssot = r.root / r.ssot_dir
    tsvs = sorted(p.relative_to(r.root).as_posix() for p in ssot.rglob("*.tsv")
                  ) if ssot.is_dir() else []
    # message-code fragments (kit.messages.harness_files) are rows of the
    # [ssot].message_codes registry: its index row covers them
    frag_dir = f"{r.ssot_dir}/message_codes.d/"
    covered = {t for t in tsvs if t.startswith(frag_dir)} \
        if r.message_codes in files else set()
    unlisted = sorted(set(tsvs) - set(files) - covered)
    if unlisted:
        out.append(f"{r.index}: does not list {unlisted}")

    id_of: dict[str, list[str]] = {}
    for row in rows:
        f = row["file"]
        path, _, symbol = f.partition(":")
        exists = (r.root / path).is_file()
        if exists and symbol:
            exists = re.search(rf"^{re.escape(symbol)}\s*[:=]",
                               read(r.root / path), re.M) is not None
        if not exists:
            out.append(f"{r.index} row {f}: the file does not exist")
        if row["owner"] not in r.kinds:
            out.append(f"{r.index} row {f}: owner is {row['owner']!r}, not "
                       f"one of {list(r.kinds)}")
        missing = [p for p in paths(row["reader"]) + paths(row["test"])
                   if p != r.none and not (r.root / p).is_file()]
        if missing:
            out.append(f"{r.index} row {f}: reader/test paths do not exist: "
                       f"{missing}")
        if not [p for p in paths(row["test"]) if p != r.none]:
            out.append(f"{r.index} row {f}: names no test")
        if not row["purpose"].strip():
            out.append(f"{r.index} row {f}: purpose is empty")
        if f.endswith(".tsv") and exists:
            cols = [c.strip() for c in (tsv(r.root / f) or [[]])[0]]
            id_cols = [c.strip() for c in row["id"].split("+")]
            if not row["id"].strip() or not all(c in cols for c in id_cols):
                out.append(f"{r.index} row {f}: id column {row['id']!r} is "
                           f"not a column of {cols}")
            else:
                id_of[f] = id_cols

    # every TSV: one column count; ids unique and non-empty
    for rel in tsvs:
        table = tsv(r.root / rel)
        widths = sorted({len(row) for row in table})
        if len(widths) > 1:
            bad = [n + 1 for n, row in enumerate(table)
                   if len(row) != len(table[0])]
            out.append(f"{rel}: rows of different widths {widths} (lines "
                       f"{bad[:10]} differ from the header)")
    for rel, cols in sorted(id_of.items()):
        ids = _ids(tsv(r.root / rel), cols) or []
        empty = sum(1 for i in ids if not all(i))
        if empty:
            out.append(f"{rel}: {empty} row(s) with an empty id "
                       f"({'+'.join(cols)})")
        dups = sorted({"+".join(i) for i in ids if ids.count(i) > 1})
        if dups:
            out.append(f"{rel}: duplicate id(s) {dups}")

    # owner / agent siblings carry the same ids
    kind = {row["file"]: row["owner"] for row in rows}
    for f, k in sorted(kind.items()):
        if k != "agent" or not f.endswith(".agent.tsv"):
            continue
        owner = f.removesuffix(".agent.tsv") + ".tsv"
        if owner not in kind and (r.root / owner).is_file():
            out.append(f"{f}: its owner sibling {owner} is not in the index")
            continue
        if f not in id_of or owner not in id_of:    # an agent-only file
            continue
        agent_t, owner_t = tsv(r.root / f), tsv(r.root / owner)
        head_a = [c.strip() for c in agent_t[0]]
        st = head_a.index(r.status_column) if r.status_column in head_a \
            else None
        agent_ids = {i for i, row in zip(_ids(agent_t, id_of[f]) or [],
                                         agent_t[1:])
                     if st is None or st >= len(row)
                     or row[st].strip() not in r.proposed}
        owner_ids = set(_ids(owner_t, id_of[owner]) or [])
        if agent_ids != owner_ids:
            out.append(f"{f}: ids differ from {owner}: only in the agent "
                       f"file {_show(agent_ids - owner_ids)}, only in the "
                       f"owner file {_show(owner_ids - agent_ids)}")

    # owner files: business meaning only (+ the held ratchet)
    flagged: dict[str, list[str]] = {}
    held_hit: set = set()
    for f, k in sorted(kind.items()):
        if k == "owner" and f.endswith(".tsv") and (r.root / f).is_file():
            got, hit = lint_file(f, r, r.held)
            flagged.update(got)
            held_hit |= hit
    for cell, hits in sorted(flagged.items()):
        out.append(f"owner file {cell}: engineering tokens {hits} (keep "
                   f"business meaning here; the detail goes to .agent.tsv)")
    for h in sorted(r.held - held_hit):
        out.append(f"held {list(h)} no longer fails the lint: drop it from "
                   f"[guards.ssot].held")

    # ids are retired, never deleted
    for f, k in sorted(kind.items()):
        if k not in ("owner", "agent") or f not in id_of:
            continue
        old = _head_table(r.root, f)
        if old is None:
            continue
        before = set(_ids(old, id_of[f]) or [])
        now = set(_ids(tsv(r.root / f), id_of[f]) or [])
        gone = sorted("+".join(i) for i in before - now)
        if gone:
            out.append(f"{f}: id(s) {gone} were at git HEAD and are gone: "
                       f"retire a row (mark it retired), never delete or "
                       f"renumber it")

    # banned terms
    if r.banned and ssot.is_dir():
        for p in sorted(ssot.rglob("*")):
            if p.suffix in (".tsv", ".md") and p.is_file():
                text = read(p)
                for bad, good in sorted(r.banned.items()):
                    if bad in text:
                        out.append(f"{p.relative_to(r.root).as_posix()}: "
                                   f"says {bad!r}; the term is {good!r}")
    return out


def check_ssot(root: Path | str | None = None) -> bool:
    """check_index() as one kit.testing.check line."""
    return report("ssot: the index, owner files and ids keep their rules",
                  check_index(root))
