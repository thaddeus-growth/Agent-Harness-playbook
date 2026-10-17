"""A skill pack that declares Chinese says it in the script it declares.

`problems()` is the check every skill pack runs in its own tests
(docs/languages.md). For the bound skill pack:

  * `[skill_pack].languages` has `en` first, no language twice, and does not
    mix the old `zh` with `zh-Hans` or `zh-Hant` (which script would `zh` be?);
  * every TSV in its ssot directory (registries, message codes, fragments) with
    an `en` column of its own (`label_en`, `meaning_en`) has the column of each
    declared language, none of its cells empty;
  * each zh-Hans / zh-Hant cell is in that script (no Traditional-only
    character in a Simplified column, no Simplified-only one in a Traditional
    column) and names no {placeholder} the English does not;
  * the client's own `<P>_LANG`, if set, is a language the skill pack
    declares or one the kit can serve from the declared ones (`ja` is not).

A returned problem names the file and row, never the text it checked. A skill
pack with `languages = ["en"]`, or the old `["en", "zh"]`, has nothing here to
break: `zh` is its own column in its own script and is read as it was.

Test: kit/tests/test_languages.py.
"""

from __future__ import annotations

from pathlib import Path

from kit import config as _config
from kit import lang as _lang
from kit import zhconv


def _tsv_heads(root: Path, ssot_dir: str) -> list[tuple[Path, list[str]]]:
    out = []
    for f in sorted((root / ssot_dir).rglob("*.tsv")):
        head = f.read_text(encoding="utf-8").split("\n", 1)[0].rstrip("\r").split("\t")
        out.append((f, [c.strip() for c in head]))
    return out


def problems(cfg: _config.SkillPackConfig | None = None) -> list[str]:
    cfg = cfg or _config.config()
    langs = cfg.languages
    out: list[str] = []
    if langs[0] != "en":
        out.append(f"skill-pack.toml: languages must start with en, not {langs[0]}")
    if "zh" in langs and (_lang.HANS in langs or _lang.HANT in langs):
        out.append("skill-pack.toml: languages names zh and zh-Hans/zh-Hant; "
                   "pick the script (zh is the old tag of one Chinese column)")
    chinese = tuple(l for l in langs if l in (_lang.HANS, _lang.HANT))
    for f, head in _tsv_heads(cfg.root, cfg.ssot.get("dir", "ssot")):
        rel = f.relative_to(cfg.root)
        for prefix in ("label", "meaning"):
            if f"{prefix}_en" not in head:
                continue
            for l in langs:
                if f"{prefix}_{l}" not in head:
                    out.append(f"{rel}: lacks {prefix}_{l}")
        if chinese:
            out += zhconv.problems(f, *chinese)
    try:
        _lang.client_language(cfg)
    except ValueError as e:
        out.append(f"language: {e}")
    return out
