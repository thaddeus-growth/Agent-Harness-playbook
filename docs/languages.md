# Languages: English, Simplified and Traditional Chinese

One client, one language, chosen from `en`, `zh-Hans` (Simplified) and `zh-Hant` (Traditional). The first client of the video-ads skill pack writes Traditional (Hong Kong and Taiwan), the second writes Simplified (mainland China). The kit's own Chinese was Simplified from the start and every skill pack's `zh` was whatever its first client wrote, so one tag `zh` meant two scripts. This page is the mechanism, decided before the code (owner rule R57 of the video-ads skill pack; found in X73).

## The decisions

| Question | Decision |
| --- | --- |
| How does a client declare its language? | `<PREFIX>_LANG` in its own env chain (process env, then `<DATA_DIR>/.env`; each client has its own data dir, so it is one per client). Values: `en`, `zh-Hans`, `zh-Hant`; `zh-CN` reads as zh-Hans, `zh-TW` and `zh-HK` as zh-Hant, the old `zh` as zh-Hans. Unset: the first language the skill pack declares. Anything else is refused by name, by `doctor` (`language`) and by `kit.guards.languages`. `scaffold/new_client.py --lang` writes it in the client's `bin/` wrappers, passes the same value to the console as `--lang`, and names it in the client's brief. |
| Where is it used? | The CLI (`kit.lang.client_language()`, `kit.lang.meaning(code, params)`: the agent renders a message code in the client's language), the owner console (`--lang`, a person's `?lang=` pick, the cookie) and the pages (`pagestyle.ZH`, `pagestyle.ZH_HANT`, `Style(words, lang="zh-Hant")`). |
| How do the tables carry three languages without translating twice? | **One script is written by a person, the other is written out by `kit.zhconv` once and then reviewed and committed.** Nothing converts at run time: what a client reads is a cell in a file that a person can correct, and `fill` never replaces a cell that has text. A skill pack writes either script and fills the other (`python3 -m kit.zhconv fill FILE… --from zh-Hans --to zh-Hant`, or the reverse; Traditional to Simplified is the safer direction, since several Traditional characters become one). `kit.zhconv` has no dependency: a longest-match table, `kit/zhconv.tsv`, built once from OpenCC's dictionaries (Apache-2.0) by `tools/build_zhconv.py`, standard characters only, no regional vocabulary (a client who wants 軟體 for 軟件 writes the cell). The one-to-many characters (发 as 發 or 髮, 后 as 後, 里 as 裡, 干 as 乾 or 幹) are answered by a phrase table kept to the phrases that change an answer, with the known bad entries removed (`被发送` is not `被髮送`). |
| What stops a wrong script? | `kit.zhconv.problems(file)` and `kit.guards.languages.problems()`: an empty cell, a character that exists in the other script only (Traditional-only in a Simplified column and the reverse; characters both scripts write, such as 台 and 干, are never flagged), a `{placeholder}` the English does not have. Each names file and row, never the text. A skill pack runs it in its own tests. |
| How does an old skill pack keep loading? | `languages = ["en", "zh"]` is unchanged: `zh` is its one Chinese column in whatever script it wrote and is read as it was. The kit's own rows have `meaning_zh` (Simplified, as before), a new `meaning_zh-Hant`, and are served as `meaning_zh-Hans` (the same text as `zh`), so a skill pack declaring `zh`, `zh-Hans` or `zh-Hant` loads. A client who says `zh-Hant` on a pack that only has `zh` gets that column for the pack's own rows and the Traditional column for the kit's (`kit.lang.resolve`). A pack may not declare `zh` together with `zh-Hans` or `zh-Hant` (which script would `zh` be?). |
| Console | `i18n.json` lines carry `en`, `zh-Hans`, `zh-Hant`; the bar links the two other languages by their own names (English, 简体中文, 繁體中文); `zh` in a cookie, `?lang=` or `--lang` is read as zh-Hans, so a console started before keeps working. |

## Moving a skill pack onto it

1. Re-vendor the kit and the console (`kit/tools/vendor.py`).
2. In `skill-pack.toml`: `languages = ["en", "zh-Hant"]` for a Traditional-written pack (or `zh-Hans`); add both for a pack that serves both. Rename its `meaning_zh` / `label_zh` columns to the script they are written in.
3. Fill the other script and read the result: `python3 -m kit.zhconv fill ssot/*.tsv ssot/message_codes.d/*.tsv --from zh-Hant --to zh-Hans`.
4. Add `kit.guards.languages.problems()` to the skill pack's tests; set `<PREFIX>_LANG` per client (`scaffold/new_client.py --lang`).
5. Anything the skill pack renders itself in Chinese (pages, SKILL.md prose) is its own to write in both scripts or to keep in one: the kit does not translate prose.

## What it does not do

It does not translate English into Chinese (a person or the agent writes the first Chinese), does not localise vocabulary (mainland 软件, Taiwan 軟體, Hong Kong 軟件), and does not convert at run time. A line a reviewer corrects is edited in the file and stays.
