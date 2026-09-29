#!/usr/bin/env python3
"""kit.pull: a pull is merged into raw, a long one is planned in chunks, and
what could not be pulled is a recorded gap. Each check names the bug it
guards; the ones that need a real kill run a child process that kills
itself mid-step.

  * merge by natural key: the newer pull wins per key, days outside its
    window stay, a late older pull never overwrites a newer row, a key the
    pull no longer returns stays (raw never shrinks), two rows of one pull
    with one key are refused, a dotted key reaches into nested rows;
  * accumulate: grows across pulls, each row keeps its own pulledAt (an
    old row without one keeps the file's, else its mtime), an unreadable
    file is moved aside (byte for byte, never over an earlier one);
  * chunk_windows / ChunkPlan: windows cover the range oldest first; a
    killed job resumes only the chunks not saved; a newer pull since the
    plan means request again, never poll;
  * the gap ledger only appends; open_gaps = days whose last event is a
    gap; a torn last line is no event and is cut by the next write; an
    edited ledger refuses;
  * every stamp follows the one clock (dates.now);
  * it binds no harness (stdlib + kit.dates + kit.atomic only).
"""

import ast
import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import dates, pull  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

KEY = ("day", "entity")
T1, T2, T3 = ("2026-01-10T06:00:00Z", "2026-01-11T06:00:00Z",
              "2026-01-12T06:00:00Z")


def child(code: str, *args: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter with kit.pull importable."""
    pre = (f"import sys, os, signal; sys.path.insert(0, {str(_shop.PLAYBOOK)!r})\n"
           "from kit import pull\n")
    return subprocess.run([sys.executable, "-B", "-c",
                           pre + textwrap.dedent(code), *args],
                          capture_output=True, text=True, timeout=60)


def rows(*spec) -> list[dict]:
    return [{"day": d, "entity": e, "cost": c} for d, e, c in spec]


def read(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def view(rs) -> list[tuple]:
    return [(r["day"], r["cost"], r["pulledAt"]) for r in rs]


def main() -> int:
    d = Path(tmp_dir("pull-"))

    print("[1] merge by natural key")
    old = pull.merge_rows([], rows(("2026-01-01", "e1", 1),
                                   ("2026-01-02", "e1", 2)), KEY, T1)
    new = pull.merge_rows(old, rows(("2026-01-02", "e1", 5),
                                    ("2026-01-03", "e1", 3)), KEY, T2)
    check("the newer pull wins per key; a day outside its window stays "
          "(each pull used to replace raw)",
          view(new) == [("2026-01-01", 1, T1), ("2026-01-02", 5, T2),
                        ("2026-01-03", 3, T2)], view(new))
    newer = pull.merge_rows([], rows(("2026-01-02", "e1", 5)), KEY, T2)
    late = pull.merge_rows(newer, rows(("2026-01-01", "e1", 1),
                                       ("2026-01-02", "e1", 2)), KEY, T1)
    check("an older pull landing late never overwrites a newer row (a "
          "resumed report put its older numbers back)",
          view(late) == [("2026-01-01", 1, T1), ("2026-01-02", 5, T2)],
          view(late))
    two = pull.merge_rows([], rows(("2026-01-01", "e1", 1),
                                   ("2026-01-01", "e2", 2)), KEY, T1)
    check("a key this pull no longer returns stays: raw never shrinks",
          len(pull.merge_rows(two, rows(("2026-01-01", "e1", 1)), KEY, T2)) == 2)
    e = raises(lambda: pull.merge_rows(
        [], rows(("2026-01-01", "e1", 1), ("2026-01-01", "e1", 2)),
        ("day",), T1), ValueError)
    check("two rows of one pull with one key are refused (a key coarser "
          "than the natural key let rows overwrite each other)",
          e is not None and "natural key" in str(e), e)
    nested = [{"day": "2026-01-01", "meta": {"slot": "top"}},
              {"day": "2026-01-01", "meta": {"slot": "rest"}}]
    check("a dotted key reaches into nested rows",
          len(pull.merge_rows([], nested, ("day", "meta.slot"), T1)) == 2)
    check("the inputs are not mutated",
          all("pulledAt" not in r for r in nested) and len(old) == 2)

    print("\n[2] accumulate")
    path = d / "daily.json"
    pull.accumulate(str(path), rows(("2026-01-01", "e1", 1),
                                    ("2026-01-02", "e1", 2)), KEY, T1,
                    window=["a", "b"])
    pull.accumulate(str(path), rows(("2026-01-02", "e1", 7),
                                    ("2026-01-03", "e1", 3)), KEY, T2,
                    window=["c", "d"])
    doc = read(path)
    check("it grows across pulls, stamps each row, keeps the last meta",
          doc["pulledOn"] == T2 and doc["rowCount"] == 3
          and doc["window"] == ["c", "d"]
          and view(doc["rows"]) == [("2026-01-01", 1, T1),
                                    ("2026-01-02", 7, T2),
                                    ("2026-01-03", 3, T2)], doc)
    check("a list_key other than rows is honoured", (
        pull.accumulate(str(d / "l.json"), rows(("2026-01-01", "e", 1)), KEY,
                        T1, list_key="items")
        and "items" in read(d / "l.json")))
    old_style = d / "old.json"
    old_style.write_text(json.dumps({"pulledOn": T1, "rows": rows(
        ("2026-01-01", "e1", 1))}))
    got = {r["day"]: r["pulledAt"] for r in pull.accumulate(
        str(old_style), rows(("2026-01-02", "e1", 2)), KEY, T3)}
    check("rows saved before rows carried a stamp keep the file's old one "
          "(a new pull's stamp relabelled old rows as fresh)",
          got == {"2026-01-01": T1, "2026-01-02": T3}, got)
    old_style.write_text(json.dumps({"rows": rows(("2026-01-01", "e1", 1))}))
    os.utime(old_style, (1767225600, 1767225600))
    [r] = pull.accumulate(str(old_style), [], KEY, T3)
    check("… and without a pulledOn, the file's mtime",
          r["pulledAt"] == "2026-01-01T00:00:00Z", r)
    check("no temp file lingers", not [n for n in os.listdir(d)
                                       if n.endswith(".tmp")], os.listdir(d))

    print("\n[3] an unreadable raw file is moved aside, never overwritten")
    dd = Path(tmp_dir("aside-"))
    target = dd / "daily.json"
    bad = (b'{"rows": [{"day": "2026-01-0', b'{"rows": "not a list"}',
           b"[1, 2]", b'{"rows": [1, 2]}')
    ok = True
    for i, broken in enumerate(bad):
        target.write_bytes(broken)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pull.accumulate(str(target), rows(("2026-01-05", "e1", 1)), KEY,
                            T1)
        aside = sorted(n for n in os.listdir(dd) if ".unreadable-" in n)
        ok = ok and "moved aside" in err.getvalue() and len(aside) == i + 1 \
            and (dd / aside[-1]).read_bytes() == broken \
            and [r["day"] for r in read(target)["rows"]] == ["2026-01-05"]
    check("each broken shape is kept byte for byte, a second never lands "
          "on the first (a broken file was replaced by one pull's rows: "
          "every older day gone)", ok, os.listdir(dd))
    check("the aside name carries the stamp",
          any(n == "daily.json.unreadable-2026-01-10T060000Z"
              for n in os.listdir(dd)), os.listdir(dd))

    print("\n[4] chunk_windows")
    check("windows cover the range, oldest first, within the limit",
          pull.chunk_windows("2026-01-01", "2026-03-15", 31)
          == [["2026-01-01", "2026-01-31"], ["2026-02-01", "2026-03-03"],
              ["2026-03-04", "2026-03-15"]])
    check("one day = one window; an empty range = none",
          pull.chunk_windows("2026-01-01", "2026-01-01", 31)
          == [["2026-01-01", "2026-01-01"]]
          and pull.chunk_windows("2026-01-02", "2026-01-01", 31) == [])
    check("max_days < 1 is refused (it would never advance)",
          raises(lambda: pull.chunk_windows("2026-01-01", "2026-01-05", 0),
                 ValueError) is not None)

    print("\n[5] ChunkPlan")
    plan_path = str(d / "daily.plan.json")
    p = child("""
        plan = pull.ChunkPlan.start(sys.argv[1], "2026-01-01", "2026-03-15",
                                    31, "2026-03-16T01:00:00Z")
        plan.update(0, request_id="r1", status="done")
        plan.update(1, request_id="r2")
        os.kill(os.getpid(), signal.SIGKILL)
    """, plan_path)
    check("the child was killed mid-plan", p.returncode == -9, p)
    resumed = pull.ChunkPlan.resume(plan_path)
    check("a killed job resumes only the chunks not saved (a long pull "
          "killed halfway started over, and the quota ran out again)",
          resumed.todo() == [1, 2] and resumed.pulled_at ==
          "2026-03-16T01:00:00Z", resumed)
    check("… and a chunk with a request id is polled, not requested again",
          resumed.chunks[1]["request_id"] == "r2")
    resumed.update(1, status="done")
    resumed.update(2, status="skipped")
    check("nothing left = no plan; a missing or broken file = no plan",
          pull.ChunkPlan.resume(plan_path) is None
          and pull.ChunkPlan.resume(str(d / "none.json")) is None)
    (d / "broken.plan.json").write_text('{"window": ')
    check("an unreadable plan is no plan",
          pull.ChunkPlan.resume(str(d / "broken.plan.json")) is None)
    pp = str(d / "again.plan.json")
    plan = pull.ChunkPlan.start(pp, "2026-01-01", "2026-03-15", 31, T1)
    plan.update(0, request_id="r1", status="done")
    plan.update(1, request_id="r2")
    check("a plan is on disk from the start",
          len(read(pp)["chunks"]) == 3 and read(pp)["pulled_at"] == T1)
    same = pull.ChunkPlan.resume(pp, raw_pulled_on=T1)
    check("raw not newer than the plan: request ids kept",
          same.todo() == [1, 2] and same.chunks[1]["request_id"] == "r2")
    fresh = pull.ChunkPlan.resume(pp, raw_pulled_on=T2)
    check("a newer pull landed since: unfinished chunks are requested "
          "again, never polled (an old report would overwrite newer rows)",
          [c["request_id"] for c in fresh.chunks] == ["r1", None, None])
    check("… saved, not only in memory",
          read(pp)["chunks"][1]["request_id"] is None)

    print("\n[6] the gap ledger")
    led = str(d / "gaps.jsonl")
    check("no ledger = no gaps", pull.open_gaps(led) == {}
          and pull.read_ledger(led) == [])
    pull.record_gap(led, endpoint="daily_by_entity",
                    days=["2026-01-03", "2026-01-02"], reason="HTTP 429")
    pull.record_gap(led, endpoint="returns", days=["2026-01-02"],
                    reason="HTTP 403: no access", fatal=True)
    pull.record_filled(led, endpoint="daily_by_entity", days=["2026-01-02"])
    check("a gap keeps its reason until a later pull fills it",
          pull.open_gaps(led) == {
              "daily_by_entity": {"2026-01-03": "HTTP 429"},
              "returns": {"2026-01-02": "HTTP 403: no access"}},
          pull.open_gaps(led))
    evs = pull.read_ledger(led)
    check("the events are in order, days sorted, fatal kept",
          [e["event"] for e in evs] == ["gap", "gap", "filled"]
          and evs[0]["days"] == ["2026-01-02", "2026-01-03"]
          and evs[1]["fatal"] is True and evs[0]["fatal"] is False)
    pull.record_gap(led, endpoint="daily_by_entity", days=["2026-01-02"],
                    reason="HTTP 500")
    check("a day filled and then lost again is a gap again",
          pull.open_gaps(led)["daily_by_entity"]["2026-01-02"] == "HTTP 500")
    led2 = str(d / "gaps2.jsonl")
    pull.record_gap(led2, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
    first = Path(led2).read_bytes()
    pull.record_filled(led2, endpoint="e", days=["2026-01-01"])
    pull.record_gap(led2, endpoint="e", days=["2026-01-02"], reason="HTTP 500")
    check("the ledger only appends (a run that cleared the last run's gaps "
          "lost the list a gaps-only pull needed)",
          Path(led2).read_bytes().startswith(first)
          and len(pull.read_ledger(led2)) == 3)
    led3 = str(d / "torn.jsonl")
    pull.record_gap(led3, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
    with open(led3, "ab") as f:
        f.write(b'{"event": "filled", "endp')        # a writer killed mid-line
    check("a torn last line is no event",
          len(pull.read_ledger(led3)) == 1
          and pull.open_gaps(led3) == {"e": {"2026-01-01": "HTTP 429"}})
    pull.record_gap(led3, endpoint="e", days=["2026-01-02"], reason="HTTP 429")
    check("… and the next write cuts it",
          [e["days"] for e in pull.read_ledger(led3)]
          == [["2026-01-01"], ["2026-01-02"]])
    led4 = str(d / "edited.jsonl")
    pull.record_gap(led4, endpoint="e", days=["2026-01-01"], reason="HTTP 429")
    with open(led4, "ab") as f:
        f.write(b"not json\n")
    pull.record_gap(led4, endpoint="e", days=["2026-01-02"], reason="HTTP 429")
    e = raises(lambda: pull.open_gaps(led4), ValueError)
    check("an edited ledger refuses, naming the line",
          e is not None and "edited.jsonl:2" in str(e), e)

    print("\n[7] one clock; no harness")
    at = datetime.datetime(2030, 5, 6, 7, 8, 9, tzinfo=datetime.timezone.utc)
    with mock.patch.object(dates, "now", return_value=at):
        led5 = str(d / "clock.jsonl")
        gap = pull.record_gap(led5, endpoint="e", days=["2026-01-01"],
                              reason="HTTP 429")
        filled = pull.record_filled(led5, endpoint="e", days=["2026-01-01"])
    check("gap and filled stamps follow dates.now (stamps made off another "
          "clock escaped the patch that pins a test to one instant)",
          gap["seen_at"] == filled["seen_at"] == "2030-05-06T07:08:09Z", gap)
    check("an explicit seen_at wins",
          pull.record_filled(led5, endpoint="e", days=["2026-01-01"],
                             seen_at=T1)["seen_at"] == T1)
    check("no second stamp function to drift from dates'",
          not hasattr(pull, "utc_stamp"))
    tree = ast.parse(Path(pull.__file__).read_text(encoding="utf-8"))
    kit_imports = sorted(
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
         and (n.module or "").split(".")[0] == "kit"}
        | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
           for a in n.names if a.name.split(".")[0] == "kit"})
    check("kit.pull imports only kit.dates and kit.atomic",
          kit_imports == ["kit", "kit.atomic"], kit_imports)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
