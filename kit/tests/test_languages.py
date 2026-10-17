#!/usr/bin/env python3
"""English, Simplified and Traditional Chinese (docs/languages.md): kit.zhconv,
kit.lang, kit.guards.languages, the kit's own three-language text, and a skill
pack that declares each of the old and the new language sets.

  [1] zhconv: the conversion, both ways, the one-to-many words, placeholders
  [2] the kit's own text: every row and every console line is complete and in
      its script; Traditional converts back to the Simplified it came from
  [3] lang: the client's language from <P>_LANG, spellings, resolve()
  [4] a skill pack declaring en + zh-Hans + zh-Hant: loads, the guard is quiet,
      and says each way it can break
  [5] the old en + zh skill pack loads and reads as it did
"""

import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, lang, messages, pagestyle, zhconv  # noqa: E402
from kit.guards import languages  # noqa: E402
from kit.testing import suites  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402

PLAYBOOK = _shop.PLAYBOOK


def three_language_pack() -> Path:
    """A copy of the fake skill pack declaring en + zh-Hans + zh-Hant, its zh
    columns renamed zh-Hans and the Traditional ones written by `fill`."""
    root = Path(tmp_dir("lang-pack-")) / "pack"
    shutil.copytree(_shop.SHOP, root, ignore=shutil.ignore_patterns("__pycache__"))
    toml = root / "skill-pack.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'languages = ["en", "zh"]', 'languages = ["en", "zh-Hans", "zh-Hant"]'),
        encoding="utf-8")
    for f in (root / "ssot").glob("*.tsv"):
        head, _, rest = f.read_text(encoding="utf-8").partition("\n")
        f.write_text(head.replace("_zh", "_zh-Hans") + "\n" + rest, encoding="utf-8")
        zhconv.fill(f, "zh-Hans", "zh-Hant")
    return root


def main() -> int:
    print("[1] zhconv")
    s = "发现后面的软件里有干净的数据，头发和平台，修复"
    t = zhconv.convert(s, "zh-Hant")
    check("Simplified to Traditional: the one-to-many words (发 後 里 干 髮 台 復)",
          t == "發現後面的軟件裡有乾淨的數據，頭髮和平台，修復", t)
    check("and back", zhconv.convert(t, "zh-Hans") == s)
    check("text already in the target script, Latin, digits and {placeholders} are kept",
          zhconv.convert("{n} items 3 項", "zh-Hant") == "{n} items 3 項")
    check("被发送 is 被發送, not 被髮送; 声明了 is 聲明了, not 聲明瞭",
          zhconv.convert("会被发送，声明了", "zh-Hant") == "會被發送，聲明了")
    check("only zh-Hans and zh-Hant are targets", _raises(lambda: zhconv.convert("a", "zh")))
    check("script_problems: 发 is not Traditional, 發 is not Simplified; 台 is both",
          zhconv.script_problems("发 台", "zh-Hant") == ["发"]
          and zhconv.script_problems("發 台", "zh-Hans") == ["發"])
    check("an empty string converts to itself", zhconv.convert("", "zh-Hant") == "")
    d = Path(tmp_dir("zh-fill-"))
    f = d / "r.tsv"
    f.write_text("code\tparams\tmeaning_en\tmeaning_zh\nx\tn\tWait {n}\t等待 {n} 个\ny\t\tNo\t\n",
                 encoding="utf-8")
    n = zhconv.fill(f, "zh", "zh-Hant")
    rows = [l.split("\t") for l in f.read_text(encoding="utf-8").splitlines()]
    check("fill adds the column after the source and fills only cells with a source",
          n == 1 and rows[0] == ["code", "params", "meaning_en", "meaning_zh", "meaning_zh-Hant"]
          and rows[1][4] == "等待 {n} 個" and rows[2][4] == "", rows)
    rows[1][4] = "我寫的"
    f.write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    check("a second fill changes nothing, and a cell a person wrote is never replaced",
          zhconv.fill(f, "zh", "zh-Hant") == 0 and "我寫的" in f.read_text(encoding="utf-8"))
    check("problems(): an empty cell is named with its row, not its text",
          zhconv.problems(f, "zh-Hant") == [f"r.tsv:3: zh-Hant is empty"], zhconv.problems(f, "zh-Hant"))
    g = d / "bad.tsv"
    g.write_text("code\tparams\tmeaning_en\tmeaning_zh-Hans\tmeaning_zh-Hant\n"
                 "x\tn\tWait {n}\t等待 {m} 個\t发现\n", encoding="utf-8")
    bad = zhconv.problems(g)
    check("problems(): Traditional in the Simplified column, the reverse, a stray placeholder",
          len(bad) == 3 and any("zh-Hans" in b and "{placeholder}" in b for b in bad)
          and any("zh-Hant holds 发" in b for b in bad), bad)

    print("\n[2] the kit's own text")
    kit_files = messages.kit_files()
    check("every kit registry file's Traditional column is complete and in its script",
          all(not zhconv.problems(p, "zh-Hant") for p in kit_files),
          [m for p in kit_files for m in zhconv.problems(p, "zh-Hant")][:3])
    check("the kit's zh column is Simplified (no Traditional-only character)",
          all(not zhconv.script_problems(r["meaning_zh"], "zh-Hans")
              for r in messages.base_registry().values()))
    check("Traditional converts back to the Simplified it came from, in every row",
          all(zhconv.convert(r["meaning_zh-Hant"], "zh-Hans") == r["meaning_zh"]
              for r in messages.base_registry().values()
              if "簡" not in r["meaning_zh-Hant"]),     # a hand-written cell may differ
          [c for c, r in messages.base_registry().items()
           if zhconv.convert(r["meaning_zh-Hant"], "zh-Hans") != r["meaning_zh"]][:5])
    check("a kit row is served as en, zh, zh-Hans (= zh) and zh-Hant",
          all(r["meaning_zh-Hans"] == r["meaning_zh"] and r["meaning_zh-Hant"]
              for r in messages.base_registry().values()))
    i18n = PLAYBOOK / "console" / "i18n.json"
    words = json.loads(i18n.read_text(encoding="utf-8"))
    check("the console's words: every line in en, zh-Hans and zh-Hant, each in its script",
          all(set(v) == {"en", "zh-Hans", "zh-Hant"} for v in words.values())
          and not zhconv.problems(i18n), zhconv.problems(i18n)[:3])
    check("pagestyle: ZH_HANT is ZH in Traditional, word for word",
          set(pagestyle.ZH_HANT) == set(pagestyle.ZH)
          and all(pagestyle.ZH_HANT[k] == zhconv.convert(v, "zh-Hant")
                  for k, v in pagestyle.ZH.items())
          and pagestyle.Style(pagestyle.ZH_HANT, lang="zh-Hant").lang == "zh-Hant")

    print("\n[3] the client's language")
    check("spellings", [lang.normalize(x) for x in
                        ("en", "EN", "zh", "zh-CN", "zh_Hans", "zh-TW", "zh-HK", "zh-Hant", "fr", "", None)]
          == ["en", "en", "zh-Hans", "zh-Hans", "zh-Hans", "zh-Hant", "zh-Hant", "zh-Hant", None, None, None])
    check("resolve: itself, the other script, the bare zh, English",
          lang.resolve("zh-Hant", ["en", "zh-Hans", "zh-Hant"]) == "zh-Hant"
          and lang.resolve("zh-Hant", ["en", "zh-Hans"]) == "zh-Hans"
          and lang.resolve("zh-Hant", ["en", "zh"]) == "zh"
          and lang.resolve("zh-Hans", ["en", "zh"]) == "zh"
          and lang.resolve("zh-Hans", ["en"]) == "en"
          and lang.resolve("zh", ["en", "zh-Hant"]) == "zh-Hant")

    print("\n[4] a skill pack with three languages")
    root = three_language_pack()
    cfg = config.use(root)
    var = cfg.env("LANG")
    os.environ.pop(var, None)
    check("the registry loads: the pack's rows carry the three columns, the kit's too",
          messages.registry()["shop_price_missing"]["meaning_zh-Hant"].endswith("沒有價格")
          and messages.registry()["joined"]["meaning_zh-Hant"])
    check("the guard is quiet, and so is its suite (suites.languages)",
          languages.problems(cfg) == [] and all(not bad for _, bad in suites.languages(root)),
          languages.problems(cfg))
    check("no setting: the first declared language", lang.client_language(cfg) == "en")
    os.environ[var] = "zh-TW"
    check("<P>_LANG=zh-TW is zh-Hant, and meaning() says the pack's and the kit's codes in it",
          lang.client_language(cfg) == "zh-Hant"
          and lang.meaning("shop_stock_low", {"sku": "A1", "units": 3}) == "A1 只剩 3 件"
          and lang.meaning("shop_stock_low", {"sku": "A1", "units": 3}, "zh-Hans") == "A1 只剩 3 件"
          and lang.meaning("joined", {"parts": "x"}) == "多條說明：x"
          and lang.meaning("joined", {"parts": "x"}, "en").startswith("Several notes"))
    os.environ[var] = "klingon"
    check("a value that is no language is refused, naming the variable",
          _raises(lambda: lang.client_language(cfg), ValueError, var))
    check("… and the guard says so", any(var in p for p in languages.problems(cfg)))
    os.environ.pop(var)
    reg = root / "ssot" / "message_codes.tsv"
    keep = reg.read_text(encoding="utf-8")
    lines = keep.splitlines()
    cells = lines[1].split("\t")
    cells[4] = "沒有价格"                                   # Simplified 价 in the Traditional column
    lines[1] = "\t".join(cells)
    reg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    check("a Simplified character in a Traditional column is named by file and row",
          any("message_codes.tsv:2" in p and "价" in p for p in languages.problems(cfg)),
          languages.problems(cfg))
    reg.write_text(keep, encoding="utf-8")
    toml = root / "skill-pack.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('["en", "zh-Hans", "zh-Hant"]', '["en", "zh", "zh-Hant"]'),
                    encoding="utf-8")
    check("zh beside zh-Hant is ambiguous",
          any("zh and zh-Hans/zh-Hant" in p for p in languages.problems(config.use(root))))
    toml.write_text(text.replace('["en", "zh-Hans", "zh-Hant"]', '["en", "zh-Hans", "zh-Hant", "ja"]'),
                    encoding="utf-8")
    check("a declared language a registry has no column for is named",
          any("lacks label_ja" in p for p in languages.problems(config.use(root))))

    print("\n[5] the old skill pack")
    cfg = _shop.use()
    os.environ.pop(cfg.env("LANG"), None)
    check("languages = [en, zh] loads; the guard is quiet; zh is its Chinese either way",
          cfg.languages == ("en", "zh") and languages.problems(cfg) == []
          and messages.registry()["shop_price_missing"]["meaning_zh"] == "{sku} 没有价格")
    os.environ[cfg.env("LANG")] = "zh-Hant"
    check("a client who says zh-Hant reads the pack's zh column, and the kit's own Traditional",
          lang.client_language(cfg) == "zh-Hant"
          and lang.resolve(lang.client_language(cfg), cfg.languages) == "zh"
          and lang.meaning("shop_price_missing", {"sku": "S"}) == "S 没有价格"
          and lang.meaning("joined", {"parts": "x"}) == "多條說明：x")
    os.environ.pop(cfg.env("LANG"))
    return finish()


def _raises(fn, kind=Exception, inside: str = "") -> bool:
    try:
        fn()
    except kind as e:
        return inside in str(e)
    return False


if __name__ == "__main__":
    sys.exit(main())
