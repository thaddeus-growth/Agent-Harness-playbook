"""The client's language: English, Simplified or Traditional Chinese.

One client has one language (docs/languages.md). The client says it once, in
`<P>_LANG` in its own env chain (process env, then `<DATA_DIR>/.env`: each client
has its own data dir, so the setting is per client), as `en`, `zh-Hans` or
`zh-Hant`. Common spellings are read as those (`zh-CN` -> zh-Hans, `zh-TW`,
`zh-HK` -> zh-Hant) and the old `zh` is zh-Hans in the kit's own text, because
that is what the kit's `zh` always was.

  normalize(tag)                  the tag as one of TAGS, or None
  client_language()               the client's tag: `<P>_LANG`, else the first
                                  language the skill pack declares; a value that
                                  is not a language is a ValueError (doctor
                                  reports it)
  resolve(want, available)        the language to use when the text exists in
                                  `available` only: itself, else the other script
                                  or the bare `zh`, else English
  meaning(code, params, lang)     a registered message said in `lang`

A skill pack that declares `languages = ["en", "zh"]` keeps working: `zh` is its
one Chinese column in whatever script it wrote, `resolve("zh-Hant", ["en", "zh"])`
is `zh`, and the client's `zh-Hans` setting reads that column too, while the kit's own text is
served in the script the client named.

Test: kit/tests/test_languages.py.
"""

from __future__ import annotations

import os

from kit import config as _config

EN, HANS, HANT = "en", "zh-Hans", "zh-Hant"
TAGS = (EN, HANS, HANT)
_ALIASES = {"en": EN, "en-us": EN, "en-gb": EN,
            "zh": HANS, "zh-hans": HANS, "zh-cn": HANS, "zh-sg": HANS,
            "zh-hant": HANT, "zh-tw": HANT, "zh-hk": HANT, "zh-mo": HANT}


def normalize(tag: str | None) -> str | None:
    """`tag` as en, zh-Hans or zh-Hant (case and `_` do not matter), else None."""
    if not isinstance(tag, str):
        return None
    return _ALIASES.get(tag.strip().replace("_", "-").lower())


def env_name() -> str:
    return _config.config().env("LANG")


def client_language(cfg: _config.SkillPackConfig | None = None) -> str:
    """The client's language as one of TAGS: what `<P>_LANG` says, else the
    first language the skill pack declares. A value that names no language
    raises ValueError. It is the client's wish; the column that says it is
    resolve(client_language(), cfg.languages)."""
    cfg = cfg or _config.config()
    name = cfg.env("LANG")
    value = os.environ.get(name)
    if value is None:
        try:
            from kit import env
            value = env.chain().resolve(name)[0]
        except Exception:            # no data dir yet: process env only
            value = None
    if not value or not value.strip():
        return normalize(cfg.languages[0]) or cfg.languages[0]
    tag = normalize(value)
    if tag is None:
        raise ValueError(f"{name}={value!r} is not a language: use "
                         f"{', '.join(TAGS)}")
    return tag


def resolve(want: str, available) -> str:
    """The member of `available` that says `want`: itself; else the other
    script of Chinese, then the bare `zh` (a skill pack's own Chinese column);
    else English; else the first one."""
    available = tuple(available)
    tag = normalize(want) or want
    order = {HANS: (HANS, "zh", HANT), HANT: (HANT, "zh", HANS),
             EN: (EN,)}.get(tag, (tag,))
    for cand in (*order, EN):
        if cand in available:
            return cand
    return available[0]


def meaning(code: str, params: dict | None = None, lang: str | None = None) -> str:
    """The registered meaning of `code` in the client's language (or `lang`),
    its {placeholders} filled from `params`; a param left out stays as typed."""
    from kit import messages
    row = messages.registry()[code]
    langs = tuple(k[len("meaning_"):] for k in row if k.startswith("meaning_"))
    use = resolve(lang or client_language(), langs)
    text = row[f"meaning_{use}"]
    for k, v in (params or {}).items():
        text = text.replace("{" + k + "}", str(v))
    return text
