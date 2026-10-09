"""CHANGELOG.md names every kit version and stays in order.

A harness re-vendors the kit and reads this file to learn what changed, so a
`kit/VERSION` bump without an entry is a defect. Checked: `## [Unreleased]` first,
then one `## [X.Y.Z] - DATE` per release, versions strictly descending, dates never
increasing, the newest heading equal to `kit/VERSION`, and the Zylos host version
named in the file.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
HEAD = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d\d-\d\d))?", re.M)


def read(path: str) -> str:
    return open(os.path.join(ROOT, path), encoding="utf-8").read().strip()


def releases() -> list[tuple[str, str]]:
    return [(n, d) for n, d in HEAD.findall(LOG) if n != "Unreleased"]


def key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def test_shape():
    names = [n for n, _ in HEAD.findall(LOG)]
    assert names[:1] == ["Unreleased"], names[:2]
    rel = releases()
    assert rel and all(re.fullmatch(r"\d+\.\d+\.\d+", n) and d for n, d in rel), rel[:3]
    versions = [key(n) for n, _ in rel]
    assert versions == sorted(set(versions), reverse=True), [n for n, _ in rel]
    dates = [d for _, d in rel]
    assert dates == sorted(dates, reverse=True), dates


def test_kit_version_has_its_entry():
    newest = releases()[0][0]
    assert read("kit/VERSION") == newest, (
        f"kit/VERSION is {read('kit/VERSION')} but the newest CHANGELOG entry is "
        f"{newest}: a version bump needs its entry (CLAUDE.md)")


def test_host_version_is_named():
    host = read("hosts/zylos/VERSION")
    assert f"hosts/zylos` {host}" in LOG, f"hosts/zylos {host} is not named in CHANGELOG.md"


if __name__ == "__main__":
    _t.main(globals())
