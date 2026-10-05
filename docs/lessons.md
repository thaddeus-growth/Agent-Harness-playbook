# Lessons

What the source project and the harnesses after it paid for, kept here when the code that carried a lesson is gone or not yet written.

## Pulling into raw (from `kit/pull.py`, removed in kit 0.7.0)

`kit/pull.py` merged each pull into raw, planned long pulls in chunks and kept a ledger of gaps. No harness imported it, so it was removed (RESTRUCTURE.md, phase 5). The last version is at commit `8e13e16` (`git show 8e13e16:kit/pull.py`), with its test in `kit/tests/test_pull.py`. Before writing a pull layer again, compare dlt (incremental cursors and state) and Airbyte's Amazon Ads source, which re-pulls a lookback window (3 days by default) for numbers the platform restates.

*Paid for:* each pull replaced raw and the platform keeps only a few months; a late resumed report put older numbers back; an unreadable raw file was overwritten by one pull's rows; a long pull killed halfway started over and the quota ran out again; a run that cleared the last run's gaps lost the list a gaps-only pull needed.

The rules, each learned from one of those bugs:

- A pull is merged, never saved over the last one: rows are keyed by
  their natural key, the newer pull wins per key, and days outside this
  pull's window stay. Each row carries its own `pulledAt`, so an old row
  is never relabelled by a new pull, and an older pull that lands late (a
  resumed report) never overwrites a newer row. A key this pull no longer
  returns stays: raw never shrinks. Two rows of one pull with the same
  key are refused: the key is wrong, and merging would silently drop one.
- A raw file that cannot be read is moved aside to
  `<path>.unreadable-<stamp>` (never over an earlier one), never
  overwritten; the pull starts a new one.
- A long window is cut into chunks the platform accepts, and the plan
  (each chunk's window, request id and status) is saved after every step,
  so a killed job resumes the chunks not yet saved. If a newer pull
  landed since, a resumed chunk is requested again, never polled: an old
  report would overwrite newer rows.
- Every gap goes into an append-only ledger with its reason, and a later
  pull that gets the day appends that it was filled. `open_gaps` is what
  a gaps-only pull asks for again; no run clears the list the next one
  needs. A torn last line (a writer killed mid-line) was never a record
  and is cut off by the next write; any other broken line raises.

Its neighbours stay in the kit: `kit/raw.py` (write-once raw files), `kit/atomic.py` (the write), `kit/single_instance.py` (one run at a time) and `kit/retry.py` (`call` waits at least the quota's refill period; `GaveUp` carries the reason a gap is recorded with).
