# Meeting intake (Stage 0)

Turn one recorded client meeting into drafts the owner can accept, change or drop in a few minutes.

## Before the meeting

- Ask for consent to record. Note who gave it and when.
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

## Output shape

```json
{
  "meeting": {"date": "2026-09-27", "participants": ["client: …", "us: …"], "consent": "given by … at 00:00:12"},
  "goals":     [{"text": "…", "source": "2026-09-27 00:04:31"}],
  "words":     [{"word": "…", "meaning": "…", "quote": "…", "source": "…"}],
  "stories":   [{"want": "…", "so_that": "…", "done_when": ["…", "…"], "human_step": "confirm", "source": "…"}],
  "numbers":   [{"key": "lead_time_days", "value": 60, "unit": "days", "applies_to": "…", "quote": "…", "source": "…"}],
  "questions": [{"text": "…", "source": "…", "unsure": true}]
}
```

## After the meeting

1. Pick the **at most 10** items that change meaning or carry a number. Everything else waits for the next review.
2. The owner accepts, changes or drops each one.
3. Accepted words → glossary rows; accepted stories → story rows; accepted numbers → **pending** facts with `source: meeting_<date>` (a human still confirms them before any rule uses them).
4. A number or goal that changed since an earlier meeting becomes a pending change showing the old and new quotes side by side — never a silent overwrite.
