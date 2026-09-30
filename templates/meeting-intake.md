# Meeting intake (Stage 0)

Turn one recorded client meeting into drafts the owner can accept, change or drop in a few minutes.

## Before the meeting

- Ask for consent to record. Note who gave it and when. If it was not given or not recorded, `meeting.consent` says so in words (a blank line is refused by the check); the owner is asked about it as a question.
- Store the recording and transcript in the client's own data folder (for example `$DATA_DIR/meetings/2026-09-27/`), never in the code repository.

## Organizer prompt

> You are organizing the transcript of a meeting between our team and a client. Read the whole transcript. Produce five lists, and nothing else:
>
> 1. **Goals** — what the client wants to achieve, in their words.
> 2. **Words** — the client's own names for things (products, metrics, steps). For each: the word, what it means in one sentence, and the quote.
> 3. **Draft stories** — "I want … / so that … / done when ① ② ③ / human step: none | confirm | approve". Only for needs the client actually stated.
> 4. **Numbers** — every number the client stated (costs, prices, lead times, budgets, limits, targets). For each: the value, the unit, what it applies to, and the exact quote.
> 5. **Open questions** — what was unclear, contradictory or left for later.
>
> Rules:
> - Every item carries `source: <meeting date> <hh:mm:ss>` pointing at the quote. No quote, no item.
> - Never invent a number, estimate one, or round one. If the client gave a range, keep the range.
> - If a later statement contradicts an earlier one, list both with both quotes under Open questions.
> - Keep the client's words; do not translate their terms into ours.
> - Mark anything you are unsure about with `unsure: true`.
> - Give every item an `iid`: its bucket's letter and a number, counted per bucket (`g1 g2 …` goals, `w1 …` words, `s1 …` stories, `n1 …` numbers, `q1 …` questions). Continue after the client's highest `iid` in the earlier intake files; never reuse one.
> - Give every item an `audience`: `client` (meaning, stories, numbers) or `builder` (the shape of the harness itself: host, repository, how data gets in).
> - A story and a number each carry a `quote`: the client's exact words, never translated. A number's `value` is digits (`45`, `0.5`) or the range the client gave (`20-30`); their words stay in the `quote`.

## Output shape

The shape is [intake.schema.json](intake.schema.json), which `build/check_intake.py` reads; the example shows every required key of each bucket, with `…` for what the organizer fills in.

```json
{
  "meeting": {"date": "2026-09-27", "participants": ["client: …", "us: …"], "consent": "given by … at 00:00:12"},
  "goals":     [{"iid": "g1", "audience": "client", "text": "…", "source": "2026-09-27 00:04:31"}],
  "words":     [{"iid": "w1", "audience": "client", "word": "…", "meaning": "…", "quote": "…", "source": "…"}],
  "stories":   [{"iid": "s1", "audience": "client", "want": "…", "so_that": "…", "done_when": ["…", "…"], "human_step": "confirm", "quote": "…", "source": "…"}],
  "numbers":   [{"iid": "n1", "audience": "client", "key": "lead_time_days", "value": 60, "unit": "days", "applies_to": "…", "quote": "…", "source": "…"}],
  "questions": [{"iid": "q1", "audience": "client", "text": "…", "source": "…", "unsure": true}]
}
```

Optional: `quote` on a goal or a question; on a question, `about` (the iids it concerns), `options` (the answers, when they can be listed) and `suggest` (`{"value": …, "because": …}`); `unsure` and `note` on any item.

## After the meeting

1. Run `python3 build/check_intake.py` on the file. It refuses an item that does not fit the shape before anything reaches the owner.
2. Pick the **at most 10** items that change meaning or carry a number. Everything else waits for the next review.
3. The owner accepts, changes or drops each one.
4. Accepted words → glossary rows; accepted stories → story rows; accepted numbers → **pending** facts with `source: client_meeting_<date>` (a human still confirms them before any rule uses them).
5. A number or goal that changed since an earlier meeting becomes a pending change showing the old and new quotes side by side — never a silent overwrite.
