#!/usr/bin/env python3
"""Build kit/zhconv.tsv, the data of kit.zhconv, from OpenCC's dictionaries.

Run once when the table needs to change, never at install or test time. The
kit holds the result; nothing else needs OpenCC.

    python3 tools/build_zhconv.py DIR      DIR = OpenCC's `dictionary/` folder
                                           (the opencc-python-reimplemented
                                           wheel, Apache-2.0, ships it)

Rows `dir<TAB>from<TAB>to`: `s2t` Simplified to Traditional, `t2s` the other
way; `from` is a character or a phrase. Characters take OpenCC's first
candidate. A phrase is kept only when converting it character by character
would give another answer (发/髮, 后/後, 里/裡, 干/乾: the one-to-many cases),
which keeps 49,051 + 277 phrases down to what changes a result. Standard
characters only: no Taiwan or Hong Kong vocabulary (软件 stays 軟件, never 軟體):
a client who wants a local word writes the cell itself, and a cell that is
written always wins over a converted one.
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "kit" / "zhconv.tsv"
# OpenCC's first candidate, replaced where the first one is not what a Hong Kong
# or Taiwan reader writes: 里 is 裡 (裏 is the older form).
OVERRIDES = {"s2t": {"里": "裡", "启": "啟", "为": "為"}}
# Older or rare forms in OpenCC's answers, written the way a modern reader does.
NORMALIZE = str.maketrans({"爲": "為", "啓": "啟", "裏": "裡", "纔": "才", "覈": "核"})
# Not converted at all: 平台, 控制台 are 平台, 控制台 in every Chinese-writing place.
NOT_CONVERTED = ("台",)
# A phrase with 台 is kept only for Taiwan and typhoon words; 平台, 控制台, 舞台,
# 后台 keep their 台.
TAIWAN = ("台湾", "台风", "台北", "台南", "台中", "台东", "台币", "台胞")
# 了 is 瞭 only in 了望, 了然, 了如指掌: OpenCC also has 声明了 -> 聲明瞭.
LIAO = ("了望", "了然", "了如指掌")
# Phrases the dictionary gets wrong for this use, and what they should say.
PHRASES = {"s2t": {"修复": "修復", "台风": "颱風"}}
# 发 is 髮 only for hair: a phrase that says 髮 is kept only with a hair word in
# it, so 被发送 stays 被發送 (OpenCC has 被发 -> 被髮).
HAIR = set("头理剪烫染白黑金毛假长短卷鬓须丝夹型廊胶蜡指冠辫梳吹")
PAIRS = (("s2t", "STCharacters", "STPhrases"), ("t2s", "TSCharacters", "TSPhrases"))


def load(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("\t")[:2]
            out[k] = v.split(" ")[0]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    d = Path(argv[1])
    rows = ["dir\tfrom\tto"]
    for name, chars_f, phrases_f in PAIRS:
        chars = {k: v for k, v in load(d / f"{chars_f}.txt").items() if k != v}
        chars.update(OVERRIDES.get(name, {}))
        chars = {k: v.translate(NORMALIZE) for k, v in chars.items()
                 if k not in NOT_CONVERTED}
        phrases = {k: v.translate(NORMALIZE)
                   for k, v in load(d / f"{phrases_f}.txt").items()
                   if ("髮" not in v or HAIR & set(k))
                   and ("瞭" not in v or any(t in k for t in LIAO))
                   and ("台" not in k or any(t in k for t in TAIWAN))}
        phrases.update(PHRASES.get(name, {}))
        need = {k: v for k, v in phrases.items()
                if "".join(chars.get(c, c) for c in k) != v}
        rows += [f"{name}\t{k}\t{v}" for k, v in sorted(chars.items())]
        rows += [f"{name}\t{k}\t{v}" for k, v in sorted(need.items())]
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(rows) - 1} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
