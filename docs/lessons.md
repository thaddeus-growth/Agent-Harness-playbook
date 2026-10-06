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

## Paid generation jobs (from adcut-harness, its first real batch, 2026-10-06)

A background job generates paid takes (a video model behind a hosted workflow), renders, checks and exports each file under
a code. The first real batch ran three times before one pair of files was usable. The fixes are in adcut-harness
(merge requests 9 and 10 and the lessons commit after them); the rules carry to any harness that pays per generated asset.

*Paid for:* the plan priced a re-run that the job then skipped (the codes were exported, and a code names one file); a
provider task finished and was paid, its download failed, and the resumed job refused the take as already paid, so the only
way on was to send it again; QC passed files whose generated takes had burned their own captions in and showed an invented
product, because QC reads the finished video, not the takes.

The rules:

- A plan prices only what the run will do. An item the run skips (already exported, already cached) is priced 0 and
  named as skipped, or the person approves money that is never spent and the work they wanted is not done.
- An identity that names a delivered file is never reused for a different file. A fix after delivery is a new identity
  (a new batch, new codes) with a one-command path to make it; the old ones are retired, not overwritten.
- A paid call is resumable by its provider task id. Write the id before waiting on the result; a later run that finds the
  id fetches that task instead of sending a new one, and settles the reservation the first run left, so one call is
  charged once. A task the provider failed is set aside, so the next run may send afresh.
- Look at the generated parts, not only the finished product. A generator adds what it was told not to (captions despite
  "no captions"), and draws what it was not given a picture of (a product it was told to hold). Check the raw takes, and
  warn at authoring time on instructions that need a reference the generator does not have.
- Prefer a free cover to a paid retry when the defect is in a known place (footage over the lower third), and verify it
  on a free sample render before asking a person to click.
- When a download fails, test the host before resuming: a local proxy can route one storage host to a dead exit while
  the API host works.
- A command handed to a person is one complete line they can paste, including the environment it needs.
