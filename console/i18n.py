"""The console's words. `i18n.json` is `{key: {"en": …, "zh": …}}` and this
is its one reader: everything the console itself says to the human comes
through `t()`; the agent's own text is never here.

A key that does not exist, a language that is not in `LANGS` and a `{param}`
the caller did not pass are programming errors, so they raise AssertionError
at the call rather than printing a hole in a page. `has()` is for the few
places that build a key from data (`err.<code>`) and fall back on a generic
sentence when a newer core has a code this dictionary has not caught up with.
"""

from __future__ import annotations

import json
import os

LANGS = ("en", "zh")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n.json"),
          encoding="utf-8") as _f:
    WORDS: dict[str, dict[str, str]] = json.load(_f)


def has(key: str) -> bool:
    return key in WORDS


def t(key: str, lang: str, **params) -> str:
    if lang not in LANGS:       # not `assert`: python -O must not turn this into a KeyError
        raise AssertionError(f"unknown language {lang!r}")
    if key not in WORDS:
        raise AssertionError(f"unknown i18n key {key!r}")
    try:
        return WORDS[key][lang].format(**params)
    except KeyError as missing:
        raise AssertionError(f"{key}: missing parameter {missing}") from None
