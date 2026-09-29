# Data bug classes

Most data bugs a channel harness ships are not new. They fall into a few classes that come back on every platform. Each row is one class, with the fixture test that pins it.

**Before the first report,** write one small fixture test for each of these ten: `zero-as-null`, `not-recorded`, `failed-lookup-null`, `sparse-report`, `unmatched-days`, `window-found`, `count-not-set`, `unordered-pick`, `unvalidated-raw`, `units-and-ids`. Each is a fixture with the edge case and one assertion. The other rows land with the part they guard: proposals with the queue, alerts with the first alert rule.

**When a data bug is triaged,** the triage line's `Class:` field names its row ([checklist](triage-checklist.md)), so repeats get counted. A class that comes back gets one guard test over the whole class, not one more fix for the instance. A bug that fits no row adds one.

*Paid for:* in the source project about 45 fix commits fell into these classes. Several came back three to six times, and unit tests caught almost none of them: client data did, often only after several merge requests.

Columns: **class** is the id the triage line uses; **usually found by** says who finds it when no test does, in the triage line's `Found by:` words.

## Kinds of nothing

Zero, absent, not recorded, not estimated and failed are five different values. Read fields with a first-present helper, write down the meaning of 0 for each source (in its registry row), and make `unknown` a status of its own: never 0, never a pass.

```python
def first(raw: dict, *keys):
    """The first key whose value is present and not null. A real 0 counts."""
    for k in keys:
        if raw.get(k) is not None:
            return raw[k]
    return None
```

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `zero-as-null` | A day the platform reported as 0 is stored as missing | Read fields with `first(raw, "a", "b")`, never `a or b` | A 0 ingests as 0, an absent field as NULL, and `a` wins when both are present | client's agent |
| `zero-baseline` | A ratio rule fires on an entity's first active day and reads like a measured spike | Check for a zero baseline before comparing; fire with a coded "from zero" reason, or not at all, as the rule says | Seven zero days, then one active day: the hit carries the from-zero reason | client's agent |
| `gate-on-absent` | A gate keyed on a field being absent also lets through every other path that leaves it absent | Key the gate on the value it guards (the computed ratio), not on a side field | Every early return (too little data, zero baseline) gives no verdict | building |
| `not-recorded` | Rows from before a column existed read NULL, and NULL is taken as "none" | NULL in a later column means "not recorded": record each column's first day and flag a window that starts before it | A rebuild that adds a column keeps old rows with NULL; a window across the first day is flagged | golden diff |
| `third-party-zero` | A research source's 0 reads as a real drop | Its registry row says what its 0 means; an estimate's 0 is "no estimate" and lowers coverage | A third-party 0 adds nothing and starts no decline; the platform's own 0 stays 0 | recheck |
| `failed-lookup-null` | A failed lookup is stored as NULL, and the entity quietly changes identity (it loses its parent group) | A failed call keeps the last known value and records a gap with its reason | A 429 or 5xx on the lookup keeps the previous value and writes one gap row | owner in console |
| `negative-delta` | Negative counts or money are read as errors or as "not reported" | Check the platform's docs: negative rows may be corrections to add up, not sentinels | A negative row nets against its positive one; the total matches the platform's own | recheck |

## Sparse reports and windows

A day is missing only when a clock says so, and every figure says which days it covers.

*Paid for:* sparse report types took 5 merge requests over about 2 days, one table and one screen at a time. An equal day count once hid 3 dropped days.

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `sparse-report` | A report type with rows only on active days shows every quiet day as missing, asks for a backfill that can never fill it, and looks stale forever | Give each sparse table a clock: a dense table the same pull fills. A day is missing only when the clock lacks it too, and lag is the clock's | Sparse table lacks day D: missing only if the clock lacks D; an empty sparse table beside a full clock is "none of that type", not a failure | nightly check |
| `unmatched-days` | A figure built from two ledgers goes wrong (a negative remainder) when one ledger misses days | Compute on the days both ledgers hold; list the days dropped | Ledger A lacks two days B has: the result uses matched days only and names the two | recheck |
| `window-found` | A report says "last 30 days" but covers fewer | `meta` carries the window asked, the window found and the missing days per source | A fixture with a gap: found differs from asked, and the gap days are listed | recheck |
| `partial-period` | A week or month in progress, compared with a full one, reads as a drop | A partial period's change is null, with a coded reason | A 3-day current week: change null, reason "partial" | owner in console |
| `count-not-set` | Equal day counts hide dropped days (a rolling window moved by three days) | Compare day sets, never counts | Table days 1–27, new raw days 4–30: refused, naming days 1–3 | building |
| `adjacent-periods` | "N weeks in a row" is met across a skipped week | Consecutive means calendar-adjacent; otherwise a determined "no" that names the gap | Weeks 30, 31 and 33 are not three in a row | owner in console |
| `since-change` | An entity changed yesterday is judged on the weeks before the change | Judge only on days since its last change (from change history), after the rule's wait | A change on day D: earlier days are left out; too few days since gives "wait" | recheck |

## Proposals

Resolve every proposal in one fixed order, and drop nothing silently:

1. existing entities (paused ones too) and blockers, at every level the platform applies them;
2. holds and exemptions;
3. caps;
4. duplicates within the run, keyed on the entity the write would create.

*Paid for:* duplicate or blocked proposals took 7 issues over about 5 days, each closing one path.

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `exists-elsewhere` | Proposes creating what already exists in another container, or exists paused | Resolve existing entities at every level first, paused ones included (switching one back on is a human's call); skip archived ones | The item exists paused in a sibling group: no proposal, one dropped row naming it | demo run |
| `blocked-destination` | Proposes into a destination that a blocker (an exclusion at a parent level) makes useless | Check blockers at every level the platform applies them, before proposing | An exclusion on the parent blocks; one on a sibling does not | building |
| `cap-before-hold` | A held entity reads "cap reached" because caps ran first | Holds and exemptions before caps; a held change draws nothing from a cap | Cap used up plus a hold: the reason is the hold | building |
| `duplicate-in-run` | The same create is proposed twice in one run, from two source groups or two rules | Dedupe on the identity of the entity the write would create, after holds; keep one by evidence, then by stable ids | One item arriving through two groups gives one proposal; the other is a dropped row, scope `run` | client's agent |
| `silent-drop` | A dropped proposal vanishes, so "never considered" and "blocked" look the same | List every dropped row with its scope (`existing` or `run`), a coded reason and what it collided with | Each drop path yields exactly one listed row with its code | demo run |
| `thin-identity` | Two different rows print identically (a held row lacks its parent group) | Every row carries its full identity: scope, parent group, container, entity | The same item held for two groups gives two distinct rows | client's agent |

## Determinism and entity lifecycle

The same data must give the same answer, and an entity's history must survive its changes.

*Paid for:* a pick from a set's order changed one report in 45 places between two runs of the same code; only the golden diff saw it. A failed lookup once orphaned decisions the client had confirmed.

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `unordered-pick` | Two runs of the same code on the same data differ | Never pick from set, dict or glob order: sort, break ties by evidence and then by stable ids, and pick none if still ambiguous | Run each report under two hash seeds (`PYTHONHASHSEED=1`, `=2`) and diff the JSON | golden diff |
| `newest-by-order` | "Latest" comes from a file name, a list position or insertion order | Newest means a timestamp field, stamped in UTC at fetch | Files named out of order: the timestamp wins | building |
| `retire-on-partial` | A deleted entity lives on as an empty row, or a failed pull retires a live one | Retire only when a complete listing pull no longer lists it; warn once; never touch human decisions | Absent from a complete pull: retired; absent from a failed or partial pull: still live | owner in console |
| `membership-today` | History moves when an entity changes group, because today's grouping is applied to every past day | Resolve group membership per day, from history | An entity moves on day D: earlier days stay with the old group | recheck |
| `silent-regroup` | A regroup orphans confirmed decisions keyed on the old group | Ingest warns per entity whose group changed, naming the old and new group and how many confirmed decisions the old one holds | A fixture regroup gives one coded warning with the count; decisions untouched | owner in console |

## Pulls and platform facts

Raw is the only copy the platform may still hold next month, so a pull saves as it goes and says what it missed.

*Paid for:* retries that gave up before the quota refilled turned days into permanent gaps, and columns not asked for in the first pulls left most history on a fallback method.

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `range-too-long` | A long window fails, or comes back silently truncated | Chunk requests to the platform's maximum range, with a persisted plan that a rerun resumes | A 90-day ask on a 31-day maximum makes 3 requests; a killed pull resumes at the next one | building |
| `save-at-end` | A crash mid-pull loses everything fetched so far | Save raw after each request | Fail request 3: requests 1 and 2 are on disk | nightly check |
| `retry-under-refill` | Retries give up before the quota refills, so days become permanent gaps | Floor the backoff at the quota's refill period (a Retry-After header still wins); give the slowest endpoints a longer budget | A stubbed 429 with a rate-limit header waits at least the refill period | nightly check |
| `lost-gaps` | A gap is overwritten or forgotten, so nobody reruns it | An append-only gap ledger with the reason, and a rerun of only the gaps that merges without loss | A gap is recorded with its reason; rerunning it fills the day and keeps the rest | nightly check |
| `unvalidated-raw` | An error payload, `{}`, `[]` or a truncated file ingests as "no data" and wipes rows | Validate all raw before any write, and refuse naming the file; an empty answer counts only when the pull recorded it as one | Each of the four bad shapes: ingest refuses, database unchanged | building |
| `unattributed-rows` | Rows that don't name their entity are all filed under the first entity asked | One entity per call when rows don't carry it; refuse a multi-entity file with a coded reason | A two-entity file is skipped with its reason; a one-entity file maps | recheck |
| `columns-from-day-one` | History can't be recomputed once the platform's retention passes, because the first pulls asked for few columns | Request every documented column from the first pull; record each column's first day | The request list equals a fixture copy of the documented list | recheck |
| `blended-sources` | A third-party estimate is shown as the platform's own number, or two sources are summed or picked per row without saying so | Every row carries `source`; one preference order per metric picks one source per entity; the report names the source it used | Two sources for one entity: the report uses the preferred one, names it, and never sums them | client data |
| `units-and-ids` | Money off by a factor of 100 or a million; joins miss on id case or aliases | Normalize at the ingest boundary: one money unit (not micros, not minor units), one id form, aliases mapped in one place | A micros fixture ingests in currency units; `AB12` and `ab12` join | demo run |

## Alerts

An alert is a report run on a schedule, so every class above applies, plus four of its own.

| Class | Symptom | Fix pattern | Test that pins it | Usually found by |
| --- | --- | --- | --- | --- |
| `cooldown-jitter` | A daily alert on a 24-hour cooldown repeats or skips by a few seconds of scheduler jitter | Allow a margin for jitter of at most half the cooldown | Fired 23 h 50 min ago: repeats; two runs minutes apart: one send | nightly check |
| `alert-on-stale` | A stalled input repeats an old hit every cooldown | A rule stops, with a coded reason, when its input is past its maximum lag | Input older than its maximum lag: no hit, a stale reason | recheck |
| `silent-unknown` | A missing input makes the rule quietly pass | Alert on "unknown" too | A missing day fires the rule's unknown status | owner in console |
| `fixed-percent` | A fixed-percent change fires on every noisy entity and never on a steady one | Measure change against the entity's own volatility; too little history gives no verdict, with a reason | The same move: a hit on a steady entity, none on a noisy one; three weeks of history: no verdict | owner in console |
