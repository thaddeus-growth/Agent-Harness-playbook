"""Simplified and Traditional Chinese from one text, with no dependency.

A client picks its language from English, Simplified Chinese (`zh-Hans`) and
Traditional Chinese (`zh-Hant`) (docs/languages.md). Nobody writes a message
twice: the text is written in one script, and `fill` writes the other into the
same file, once, to be read and committed. Nothing converts at run time, so
what a client sees is a line in a file a person can fix; a cell that is
written is never overwritten.

  convert(text, "zh-Hant" | "zh-Hans")   the conversion (longest phrase first,
                                         then character by character); Latin
                                         text, digits and {placeholders} pass
                                         through untouched
  script_problems(text, lang)            characters that belong only to the
                                         other script: [] means pure
  fill(path, source, target)             add/complete the `target` column of
                                         a registry TSV (`meaning_*`, `label_*`
                                         pairs) or the `target` key of the
                                         console's `{key: {lang: text}}` JSON
  problems(path, *langs)                 the conformance check of those files

    python3 -m kit.zhconv fill  FILE… --from zh --to zh-Hant
    python3 -m kit.zhconv check FILE… [--langs zh-Hans zh-Hant]

The table is kit/zhconv.tsv (built from OpenCC's dictionaries by
tools/build_zhconv.py, standard characters only: no regional vocabulary).
`zh` is the old tag of one Chinese column whose script the skill pack knows
and the kit does not: the kit's own `zh` is Simplified, a skill pack's is
whatever it wrote. `--from zh` is therefore allowed, and the direction comes
from `--to`.

Test: kit/tests/test_languages.py.
"""

from __future__ import annotations

import functools
import json
import re
import sys
from pathlib import Path

TABLE = Path(__file__).resolve().parent / "zhconv.tsv"
HANS, HANT = "zh-Hans", "zh-Hant"
_PLACEHOLDER = re.compile(r"{(\w+)}")


@functools.cache
def _tables() -> dict[str, tuple[dict[str, str], int]]:
    tabs: dict[str, dict[str, str]] = {"s2t": {}, "t2s": {}}
    for line in TABLE.read_text(encoding="utf-8").splitlines()[1:]:
        d, a, b = line.split("\t")
        tabs[d][a] = b
    return {d: (t, max(map(len, t))) for d, t in tabs.items()}


def convert(text: str, to: str) -> str:
    """`text` in the script of `to` (zh-Hant or zh-Hans). Text already in that
    script comes back as it was."""
    if to not in (HANS, HANT):
        raise ValueError(f"zhconv: {to!r} is not zh-Hans or zh-Hant")
    table, longest = _tables()["s2t" if to == HANT else "t2s"]
    out, i, n = [], 0, len(text)
    while i < n:
        for size in range(min(longest, n - i), 0, -1):
            hit = table.get(text[i:i + size])
            if hit is not None:
                out.append(hit)
                i += size
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# Characters both scripts write (a Traditional text has 台, 干, 后 too): never
# reported by script_problems(), so a clean Traditional line is not refused.
BOTH = frozenset("台干后只面里表松系制征范卷丑云于采谷准")


@functools.cache
def _only() -> dict[str, frozenset[str]]:
    """Characters that exist in one script only. A character that converts to
    another one and is not itself a target of the other direction belongs to
    its own script alone (发 is Simplified, 發 Traditional; 台 and 干 are both)."""
    s2t, _ = _tables()["s2t"]
    t2s, _ = _tables()["t2s"]
    one = lambda t: {k: v for k, v in t.items() if len(k) == 1}
    s2t, t2s = one(s2t), one(t2s)
    hans = {c for c in s2t if c not in t2s and c not in s2t.values()}
    hant = {c for c in t2s if c not in s2t and c not in t2s.values()}
    return {HANS: frozenset(hans) - BOTH, HANT: frozenset(hant) - BOTH}


def script_problems(text: str, lang: str) -> list[str]:
    """The characters of `text` that belong only to the other script than
    `lang` (zh-Hans or zh-Hant), each once, in order."""
    foreign = _only()[HANT if lang == HANS else HANS]
    seen: list[str] = []
    for c in text:
        if c in foreign and c not in seen:
            seen.append(c)
    return seen


# ---- files ----------------------------------------------------------------

def _direction(target: str) -> str:
    if target not in (HANS, HANT):
        raise ValueError(f"zhconv: --to must be zh-Hans or zh-Hant, got {target!r}")
    return target


def _tsv(path: Path):
    rows = [l.split("\t") for l in path.read_text(encoding="utf-8").split("\n")]
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    return rows


def fill(path: Path, source: str, target: str) -> int:
    """Complete the `target` language of `path` from `source`, in place;
    returns the number of cells written. A TSV: for every column
    `<prefix>_<source>` the column `<prefix>_<target>` is added (right after
    it) and each empty cell of it set. A JSON file `{key: {lang: text}}`:
    the `target` key of every entry. Cells that have text are left alone."""
    _direction(target)
    path = Path(path)
    n = 0
    if path.suffix == ".json":
        words = json.loads(path.read_text(encoding="utf-8"))
        for entry in words.values():
            if source in entry and not entry.get(target):
                entry[target] = convert(entry[source], target)
                n += 1
        path.write_text(json.dumps(words, ensure_ascii=False, indent=1)
                        + "\n", encoding="utf-8")
        return n
    rows = _tsv(path)
    head = rows[0]
    for col in [c for c in head if c.endswith(f"_{source}")]:
        new = col[: -len(source)] + target
        if new not in head:
            at = head.index(col) + 1
            for r in rows:
                r.insert(at, "" if r is not head else new)
        i, j = head.index(col), head.index(new)
        for r in rows[1:]:
            if len(r) == len(head) and not r[j].strip() and r[i].strip():
                r[j] = convert(r[i], target)
                n += 1
    path.write_text("\n".join("\t".join(r) for r in rows) + "\n",
                    encoding="utf-8")
    return n


def problems(path: Path, *langs: str) -> list[str]:
    """What is wrong with the zh-Hans / zh-Hant text of `path` (default
    both): an empty cell, a character of the other script, a {placeholder}
    the English text does not have (a meaning may leave one out). [] = conformant. A column that is not
    there is not checked: a skill pack declares which languages it has."""
    langs = langs or (HANS, HANT)
    path = Path(path)
    out: list[str] = []

    def one(where: str, en: str, lang: str, text: str) -> None:
        if not text.strip():
            out.append(f"{where}: {lang} is empty")
            return
        bad = script_problems(text, lang)
        if bad:
            out.append(f"{where}: {lang} holds {''.join(bad)} "
                       f"({'Traditional' if lang == HANS else 'Simplified'} only)")
        if en and set(_PLACEHOLDER.findall(text)) - set(_PLACEHOLDER.findall(en)):
            out.append(f"{where}: {lang} names a {{placeholder}} en does not")

    if path.suffix == ".json":
        for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
            for lang in langs:
                if lang in entry:
                    one(f"{path.name}:{key}", entry.get("en", ""), lang, entry[lang])
        return out
    rows = _tsv(path)
    head = rows[0]
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != len(head):
            continue
        cells = dict(zip(head, r))
        for col, text in cells.items():
            prefix, _, lang = col.partition("_")
            if lang in langs:
                one(f"{path.name}:{n}", cells.get(f"{prefix}_en", ""), lang, text)
    return out


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m kit.zhconv",
                                 description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill", help="write the missing target-language cells")
    f.add_argument("files", nargs="+", type=Path)
    f.add_argument("--from", dest="source", required=True,
                   help="the language written by hand: zh, zh-Hans or zh-Hant")
    f.add_argument("--to", required=True, choices=(HANS, HANT))
    c = sub.add_parser("check", help="empty cells, wrong script, placeholders")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--langs", nargs="+", choices=(HANS, HANT), default=[HANS, HANT])
    a = ap.parse_args(argv)
    if a.cmd == "fill":
        for p in a.files:
            print(f"{p}: {fill(p, a.source, a.to)} cells")
        return 0
    bad = [m for p in a.files for m in problems(p, *a.langs)]
    print("\n".join(bad) if bad else "ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
