#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""raw.py: raw only grows, a reader never sees half a file, one run at a
time, and a throttled pull becomes a recorded gap, not a silent hole. Each
test's docstring names the bug it guards. Tests that need a real kill run a
child process that kills itself mid-step.
"""

import email.message
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _safety  # noqa: E402  (puts the folder that holds core/ on sys.path)
from core import dates, raw  # noqa: E402
from _safety import raises, stderr, tmpdir  # noqa: E402

KEY = ("day", "entity")
T1, T2, T3 = "2026-01-10T06:00:00Z", "2026-01-11T06:00:00Z", "2026-01-12T06:00:00Z"


def child(code: str, *args: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter with raw.py importable."""
    pre = f"import sys; sys.path.insert(0, {_safety.ROOT!r}); from core import raw; import os, signal\n"
    return subprocess.run([sys.executable, "-c", pre + textwrap.dedent(code), *args],
                          capture_output=True, text=True, timeout=60)


def rows(*spec) -> list[dict]:
    return [{"day": d, "entity": e, "cost": c} for d, e, c in spec]


def read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ atomic writes --

def test_a_failed_write_leaves_the_old_file_whole():
    """A crash mid-write left a truncated raw file that the next ingest read as data."""
    with tmpdir() as d:
        path = os.path.join(d, "r.json")
        raw.write_json_atomic(path, {"rows": [1, 2, 3]})
        with raises(RuntimeError):
            with raw.open_atomic(path) as f:
                f.write('{"rows": [1')
                raise RuntimeError("disk full")
        assert read(path) == {"rows": [1, 2, 3]} and os.listdir(d) == ["r.json"]


def test_a_killed_writer_leaves_the_old_file_whole():
    with tmpdir() as d:
        path = os.path.join(d, "r.json")
        raw.write_json_atomic(path, {"rows": [1, 2, 3]})
        p = child("""
            with raw.open_atomic(sys.argv[1]) as f:
                f.write('{"rows": [1')
                f.flush()
                os.kill(os.getpid(), signal.SIGKILL)
        """, path)
        assert p.returncode == -9
        assert read(path) == {"rows": [1, 2, 3]}
        raw.write_json_atomic(path, {"rows": []})                  # the leftover .tmp does not block the next write
        assert read(path) == {"rows": []} and os.listdir(d) == ["r.json"]


# -------------------------------------------------------------------- merge --

def test_the_newer_pull_wins_per_key_and_days_outside_its_window_stay():
    """Each pull used to replace raw, and the platform keeps only a few months."""
    old = raw.merge_rows([], rows(("2026-01-01", "e1", 1), ("2026-01-02", "e1", 2)), KEY, T1)
    new = raw.merge_rows(old, rows(("2026-01-02", "e1", 5), ("2026-01-03", "e1", 3)), KEY, T2)
    assert [(r["day"], r["cost"], r["pulledAt"]) for r in new] == [
        ("2026-01-01", 1, T1), ("2026-01-02", 5, T2), ("2026-01-03", 3, T2)]


def test_an_older_pull_landing_late_never_overwrites_a_newer_row():
    """A resumed report finished after a newer pull and put its older numbers back."""
    newer = raw.merge_rows([], rows(("2026-01-02", "e1", 5)), KEY, T2)
    late = raw.merge_rows(newer, rows(("2026-01-01", "e1", 1), ("2026-01-02", "e1", 2)), KEY, T1)
    assert [(r["day"], r["cost"], r["pulledAt"]) for r in late] == [("2026-01-01", 1, T1), ("2026-01-02", 5, T2)]


def test_a_key_this_pull_no_longer_returns_stays():
    """Raw never shrinks: a rebuild can only refill what raw still holds."""
    old = raw.merge_rows([], rows(("2026-01-01", "e1", 1), ("2026-01-01", "e2", 2)), KEY, T1)
    assert len(raw.merge_rows(old, rows(("2026-01-01", "e1", 1)), KEY, T2)) == 2


def test_two_rows_of_one_pull_with_one_key_are_refused():
    """A key coarser than the natural key let rows overwrite each other."""
    with raises(ValueError) as c:
        raw.merge_rows([], rows(("2026-01-01", "e1", 1), ("2026-01-01", "e1", 2)), ("day",), T1)
    assert "natural key" in str(c.err)


def test_a_dotted_key_reaches_into_nested_rows():
    new = [{"day": "2026-01-01", "meta": {"slot": "top"}}, {"day": "2026-01-01", "meta": {"slot": "rest"}}]
    assert len(raw.merge_rows([], new, ("day", "meta.slot"), T1)) == 2


def test_accumulate_grows_across_pulls_and_stamps_each_row():
    with tmpdir() as d:
        path = os.path.join(d, "daily.json")
        raw.accumulate(path, rows(("2026-01-01", "e1", 1), ("2026-01-02", "e1", 2)), KEY, T1, window=["a", "b"])
        raw.accumulate(path, rows(("2026-01-02", "e1", 7), ("2026-01-03", "e1", 3)), KEY, T2, window=["c", "d"])
        doc = read(path)
        assert doc["pulledOn"] == T2 and doc["rowCount"] == 3 and doc["window"] == ["c", "d"]
        assert [(r["day"], r["cost"], r["pulledAt"]) for r in doc["rows"]] == [
            ("2026-01-01", 1, T1), ("2026-01-02", 7, T2), ("2026-01-03", 3, T2)]


def test_rows_saved_before_rows_carried_a_stamp_keep_the_files_old_one():
    """A new pull's stamp relabelled old rows as fresh."""
    with tmpdir() as d:
        path = os.path.join(d, "daily.json")
        raw.write_json_atomic(path, {"pulledOn": T1, "rows": rows(("2026-01-01", "e1", 1))})
        doc = {r["day"]: r["pulledAt"] for r in raw.accumulate(path, rows(("2026-01-02", "e1", 2)), KEY, T3)}
        assert doc == {"2026-01-01": T1, "2026-01-02": T3}
        raw.write_json_atomic(path, {"rows": rows(("2026-01-01", "e1", 1))})             # no pulledOn: the mtime
        os.utime(path, (1767225600, 1767225600))
        [r] = raw.accumulate(path, [], KEY, T3)
        assert r["pulledAt"] == "2026-01-01T00:00:00Z"


def test_an_unreadable_raw_file_is_moved_aside_never_overwritten():
    """An unreadable raw file was replaced by one pull's rows: every older day gone."""
    with tmpdir() as d:
        path = os.path.join(d, "daily.json")
        for i, broken in enumerate((b'{"rows": [{"day": "2026-01-0', b'{"rows": "not a list"}', b"[1, 2]")):
            with open(path, "wb") as f:
                f.write(broken)
            with stderr() as err:
                raw.accumulate(path, rows(("2026-01-05", "e1", 1)), KEY, T1)
            assert "moved aside" in err.text
            aside = sorted(n for n in os.listdir(d) if ".unreadable-" in n)
            assert len(aside) == i + 1                               # a second one never lands on the first
            with open(os.path.join(d, aside[-1]), "rb") as f:        # byte for byte
                assert f.read() == broken
            assert [r["day"] for r in read(path)["rows"]] == ["2026-01-05"]


# ---------------------------------------------------------- single instance --

def test_a_second_run_does_not_wait_it_skips():
    """An hourly run outlasted the hour and two drains raced each other."""
    with tmpdir() as d:
        lock = os.path.join(d, "locks", "pull.lock")
        with raw.hold(lock) as first:
            with raw.hold(lock) as second:
                assert first and not second
        with raw.hold(lock) as again:
            assert again
        assert os.path.exists(lock)                               # kept: a deleted file lets two runs lock two files


def test_an_overlapping_scheduled_run_prints_skipped_and_exits_0():
    with tmpdir() as d:
        lock = os.path.join(d, "pull.lock")
        run = """
            with raw.hold(sys.argv[1]) as mine:
                if not mine:
                    print("skipped: another run holds the lock")
                    sys.exit(0)
                print("ran")
        """
        with raw.hold(lock) as mine:
            assert mine
            p = child(run, lock)
        assert (p.returncode, p.stdout.strip()) == (0, "skipped: another run holds the lock")
        assert child(run, lock).stdout.strip() == "ran"


def test_a_killed_run_leaves_no_stale_lock():
    with tmpdir() as d:
        lock = os.path.join(d, "pull.lock")
        p = child("""
            with raw.hold(sys.argv[1]) as mine:
                assert mine
                os.kill(os.getpid(), signal.SIGKILL)
        """, lock)
        assert p.returncode == -9
        with raw.hold(lock) as mine:
            assert mine


# -------------------------------------------------------------------- retry --

class Reply:
    def __init__(self, status: int, headers: dict | None = None):
        self.status_code, self.headers = status, headers or {}


def script(*items):
    """A call that returns (or raises) `items` in turn, and counts its calls."""
    left = list(items)

    def fn():
        fn.calls += 1
        x = left.pop(0)
        if isinstance(x, BaseException):
            raise x
        return x
    fn.calls = 0
    return fn


def run(fn, policy, **kw):
    slept: list[float] = []
    with stderr():
        out = raw.call(fn, policy, endpoint="reports.create", sleep=slept.append, **kw)
    return out, slept


def test_retry_after_is_taken_as_the_server_says():
    out, slept = run(script(Reply(429, {"retry-after": "7"}), Reply(200)), raw.RetryPolicy(refill_s=30))
    assert out.status_code == 200 and slept == [7.0]


def test_a_wait_is_never_shorter_than_the_quota_refill():
    """Days were lost as rate-limit gaps: the exponential
    guess retried long before the quota refilled, and the tries ran out."""
    out, slept = run(script(Reply(429), Reply(429), Reply(200)), raw.RetryPolicy(refill_s=20, backoff_s=2))
    assert out.status_code == 200 and slept == [20, 20]


def test_the_cap_never_cuts_a_wait_below_the_refill():
    _, slept = run(script(Reply(503), Reply(200)), raw.RetryPolicy(refill_s=120, cap_s=60))
    assert slept == [120]


def test_a_refill_read_from_the_reply_raises_the_floor():
    policy = raw.RetryPolicy(refill_s=1, refill_from=lambda h: 1 / float(h["x-rate-limit"]))
    _, slept = run(script(Reply(429, {"x-rate-limit": "0.05"}), Reply(200)), policy)
    assert slept == [20]


def test_without_a_floor_the_guess_doubles_up_to_the_cap():
    _, slept = run(script(*[Reply(500)] * 4, Reply(200)), raw.RetryPolicy(refill_s=0, backoff_s=2, cap_s=5))
    assert slept == [2, 4, 5, 5]


def test_a_spent_budget_gives_up_with_the_http_reason():
    """A throttled day must become a recorded gap, never a day with no data."""
    fn = script(*[Reply(429)] * 3)
    with raises(raw.GaveUp) as c:
        run(fn, raw.RetryPolicy(refill_s=1, tries=2))
    assert (c.err.endpoint, c.err.reason, c.err.tries, fn.calls) == ("reports.create", "HTTP 429", 3, 3)


def test_each_endpoint_spends_its_own_budget():
    create, listing = raw.RetryPolicy(refill_s=60, tries=6), raw.RetryPolicy(refill_s=1, tries=1)
    out, slept = run(script(*[Reply(429)] * 5, Reply(200)), create)
    assert out.status_code == 200 and slept == [60] * 5
    with raises(raw.GaveUp):
        run(script(Reply(429), Reply(429), Reply(200)), listing)


def test_an_answer_that_is_not_retryable_comes_back_at_once():
    fn = script(Reply(400), Reply(200))
    out, slept = run(fn, raw.RetryPolicy(refill_s=5))
    assert out.status_code == 400 and slept == [] and fn.calls == 1


def test_transport_errors_are_retried_and_named():
    out, slept = run(script(TimeoutError("read timed out"), Reply(200)), raw.RetryPolicy(refill_s=3))
    assert out.status_code == 200 and slept == [3]
    with raises(raw.GaveUp) as c:
        run(script(ConnectionResetError("reset"), ConnectionResetError("reset")), raw.RetryPolicy(refill_s=3, tries=1))
    assert c.err.reason == "ConnectionResetError: reset"


def test_urllib_http_errors_are_answers():
    def http_error(code: int, **headers: str) -> urllib.error.HTTPError:
        h = email.message.Message()
        for k, v in headers.items():
            h[k.replace("_", "-")] = v
        return urllib.error.HTTPError("https://api.example.com/x", code, "x", h, None)
    out, slept = run(script(http_error(429, Retry_After="3"), Reply(200)), raw.RetryPolicy(refill_s=1))
    assert out.status_code == 200 and slept == [3]
    with raises(urllib.error.HTTPError) as c:
        run(script(http_error(404)), raw.RetryPolicy(refill_s=1))
    assert c.err.code == 404


def test_the_run_deadline_stops_before_a_wait_that_would_pass_it():
    with raises(raw.GaveUp) as c:
        run(script(Reply(429), Reply(200)), raw.RetryPolicy(refill_s=20), deadline=110, clock=lambda: 100)
    assert "deadline" in c.err.reason and c.err.tries == 1


# ---------------------------------------------------------------- chunk plan --

def test_chunk_windows_cover_the_window_oldest_first_within_the_limit():
    w = raw.chunk_windows("2026-01-01", "2026-03-15", 31)
    assert w == [["2026-01-01", "2026-01-31"], ["2026-02-01", "2026-03-03"], ["2026-03-04", "2026-03-15"]]
    assert raw.chunk_windows("2026-01-01", "2026-01-01", 31) == [["2026-01-01", "2026-01-01"]]


def test_a_killed_job_resumes_only_the_chunks_not_saved():
    """A long pull killed halfway started over, and the quota ran out again."""
    with tmpdir() as d:
        plan = os.path.join(d, "daily.plan.json")
        p = child("""
            plan = raw.ChunkPlan.start(sys.argv[1], "2026-01-01", "2026-03-15", 31, "2026-03-16T01:00:00Z")
            plan.update(0, request_id="r1", status="done")
            plan.update(1, request_id="r2")
            os.kill(os.getpid(), signal.SIGKILL)
        """, plan)
        assert p.returncode == -9
        resumed = raw.ChunkPlan.resume(plan)
        assert resumed.todo() == [1, 2] and resumed.pulled_at == "2026-03-16T01:00:00Z"
        assert resumed.chunks[1]["request_id"] == "r2"                 # the platform may still finish it: poll it
        resumed.update(1, status="done")
        resumed.update(2, status="skipped")
        assert raw.ChunkPlan.resume(plan) is None                       # nothing left
        assert raw.ChunkPlan.resume(os.path.join(d, "none.json")) is None


def test_a_newer_pull_since_the_plan_means_request_again_never_poll():
    with tmpdir() as d:
        path = os.path.join(d, "daily.plan.json")
        plan = raw.ChunkPlan.start(path, "2026-01-01", "2026-03-15", 31, T1)
        plan.update(0, request_id="r1", status="done")
        plan.update(1, request_id="r2")
        resumed = raw.ChunkPlan.resume(path, raw_pulled_on=T2)
        assert [c["request_id"] for c in resumed.chunks] == ["r1", None, None]
        assert read(path)["chunks"][1]["request_id"] is None            # saved, not only in memory
        assert raw.ChunkPlan.resume(path, raw_pulled_on=T1).todo() == [1, 2]


# ---------------------------------------------------------------- gap ledger --

def test_a_gap_records_the_http_reason_until_a_later_pull_fills_it():
    with tmpdir() as d:
        led = os.path.join(d, "gaps.jsonl")
        assert raw.open_gaps(led) == {}
        raw.record_gap(led, endpoint="daily_by_entity", days=["2026-01-03", "2026-01-02"], reason="HTTP 429")
        raw.record_gap(led, endpoint="returns", days=["2026-01-02"], reason="HTTP 403: no access", fatal=True)
        raw.record_filled(led, endpoint="daily_by_entity", days=["2026-01-02"])
        assert raw.open_gaps(led) == {"daily_by_entity": {"2026-01-03": "HTTP 429"},
                                      "returns": {"2026-01-02": "HTTP 403: no access"}}
        assert [e["event"] for e in raw.read_ledger(led)] == ["gap", "gap", "filled"]


def test_the_ledger_only_appends():
    """A run that cleared the last run's gaps lost the list a gaps-only pull needed."""
    with tmpdir() as d:
        led = os.path.join(d, "gaps.jsonl")
        raw.record_gap(led, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
        first = open(led, "rb").read()
        raw.record_filled(led, endpoint="e", days=["2026-01-01"])
        raw.record_gap(led, endpoint="e", days=["2026-01-02"], reason="HTTP 500")
        assert open(led, "rb").read().startswith(first) and len(raw.read_ledger(led)) == 3


def test_a_torn_last_line_is_no_event_and_the_next_write_cuts_it():
    with tmpdir() as d:
        led = os.path.join(d, "gaps.jsonl")
        raw.record_gap(led, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
        with open(led, "ab") as f:
            f.write(b'{"event": "filled", "endp')                       # a writer killed mid-line
        assert len(raw.read_ledger(led)) == 1
        raw.record_gap(led, endpoint="e", days=["2026-01-02"], reason="HTTP 429")
        assert [e["days"] for e in raw.read_ledger(led)] == [["2026-01-01"], ["2026-01-02"]]


def test_an_edited_ledger_refuses():
    with tmpdir() as d:
        led = os.path.join(d, "gaps.jsonl")
        raw.record_gap(led, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
        with open(led, "ab") as f:
            f.write(b"not json\n")
        raw.record_gap(led, endpoint="e", days=["2026-01-02"], reason="HTTP 429")
        with raises(ValueError) as c:
            raw.open_gaps(led)
        assert "gaps.jsonl:2" in str(c.err)


def test_every_stamp_follows_the_one_clock():
    """Stamps made off `dates.now` escaped the patch that pins a test or the
    golden diff to one instant."""
    at = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    with tmpdir() as d, mock.patch.object(dates, "now", return_value=at):
        led = os.path.join(d, "gaps.jsonl")
        gap = raw.record_gap(led, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
        filled = raw.record_filled(led, endpoint="e", days=["2026-01-01"])
        assert gap["seen_at"] == filled["seen_at"] == "2030-05-06T07:08:09Z"
        assert not hasattr(raw, "utc_stamp")                      # no second stamp to drift from dates'


if __name__ == "__main__":
    _safety.main(globals())
