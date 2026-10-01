"""One roadmap format for every project: ROADMAP.md says where a project is, in
rows an agent can search, so a session on a new machine starts from the file and
not from a transcript or a memory note.

The file (templates/ROADMAP.md is the starting point):

    # <Project> roadmap
    Updated: 2026-10-01
    ## Now / ## Waiting on owner / ## Next / ## Done / ## Parked / ## Decisions / ## Links

`check_file(text)` returns every problem of these rules:

  1. the title line, then `Updated: YYYY-MM-DD`, then exactly those seven `##`
     sections, in that order;
  2. every item is `- [ID] YYYY-MM-DD text`, the ID a letter and a number, the
     letter naming its section (N now, W waiting, X next, D done, P parked,
     R decisions), unique in the file; the date is a real day not after
     `Updated`. An ID is never reused for another row: git history keeps what
     a file cannot;
  3. Now holds at most one item: one turn per session;
  4. a Waiting row ends `unblocks: <ID>[, <ID>]`, naming rows that exist, so no
     session starts blocked without knowing on what;
  5. a Done row ends in a reference in parentheses, `(commit abc1234)`,
     `(PR #17)` or `(no ref)`;
  6. a Decisions row quotes the owner's words verbatim in double quotes, so a
     decision is never a paraphrase;
  7. Links holds `- name: where` rows, with no ID.

Paid for: one project's roadmap was prose in its own layout, another's a TSV,
a third a memory note that did not travel when the work moved to another
machine; none could be searched for what was waiting on the owner.

harness.toml `[guards.roadmap]`, optional: `file` (default `ROADMAP.md`).

Test: kit/tests/test_roadmap.py.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from kit.guards import harness, report, section

SECTIONS = ("Now", "Waiting on owner", "Next", "Done", "Parked", "Decisions", "Links")
LETTER = {"Now": "N", "Waiting on owner": "W", "Next": "X", "Done": "D", "Parked": "P", "Decisions": "R"}
ROW = re.compile(r"^\[([A-Z])(\d+)\]\s+(\d{4}-\d{2}-\d{2})\s+(\S.*)$", re.S)


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def items(lines: list[str]) -> list[str]:
    """The `- ` items of a section, an indented line joined to its item."""
    out: list[str] = []
    for ln in lines:
        if ln.startswith("- "):
            out.append(ln[2:].strip())
        elif out and ln.strip() and ln[:1] in " \t":
            out[-1] += " " + ln.strip()
    return out


def check_file(text: str) -> list[str]:
    p: list[str] = []
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    body = [ln for ln in lines if ln.strip()]
    if not body or not body[0].startswith("# "):
        p.append("the first line must be `# <Project> roadmap`")
    upd = next((m for ln in body[1:3] if (m := re.fullmatch(r"Updated:\s*(\d{4}-\d{2}-\d{2})", ln.strip()))), None)
    updated = _day(upd.group(1)) if upd else None
    if updated is None:
        p.append("the second line must be `Updated: YYYY-MM-DD` (a real day)")
    heads = [(i, ln[3:].strip()) for i, ln in enumerate(lines) if ln.startswith("## ")]
    if tuple(h for _, h in heads) != SECTIONS:
        p.append(f"the sections must be exactly, in order: {', '.join('## ' + s for s in SECTIONS)}; "
                 f"found {', '.join(h for _, h in heads) or 'none'}")
        return p
    ends = [i for i, _ in heads[1:]] + [len(lines)]
    rows: dict[str, tuple[str, str]] = {}                  # id -> (section, text)
    for (start, name), end in zip(heads, ends):
        its = items(lines[start + 1:end])
        if name == "Links":
            for it in its:
                if not re.match(r"^[^:\[\]]+:\s*\S", it):
                    p.append(f"Links: `{it[:50]}` is not `name: where`")
            continue
        if name == "Now" and len(its) > 1:
            p.append("Now holds more than one item: one turn per session")
        for it in its:
            m = ROW.match(it)
            if not m:
                p.append(f"{name}: `{it[:60]}` is not `[ID] YYYY-MM-DD text`")
                continue
            letter, num, day, rest = m.groups()
            rid = f"{letter}{num}"
            if letter != LETTER[name]:
                p.append(f"{name}: [{rid}] must start with {LETTER[name]}")
            if rid in rows:
                p.append(f"[{rid}] is used twice")
            rows[rid] = (name, rest)
            d = _day(day)
            if d is None:
                p.append(f"[{rid}]: {day} is not a real day")
            elif updated and d > updated:
                p.append(f"[{rid}]: dated {day}, after Updated {updated}")
            if name == "Done" and not re.search(r"\([^()]+\)\s*$", rest):
                p.append(f"[{rid}]: a Done row ends in a reference in parentheses, `(commit abc1234)`, `(PR #17)` or `(no ref)`")
            if name == "Decisions" and not re.search(r'"[^"]{3,}"', rest):
                p.append(f"[{rid}]: a Decisions row quotes the owner's words in double quotes")
    for rid, (name, rest) in rows.items():
        if name != "Waiting on owner":
            continue
        m = re.search(r"unblocks:\s*([A-Z]\d+(?:\s*,\s*[A-Z]\d+)*)\s*$", rest)
        if not m:
            p.append(f"[{rid}]: a Waiting row ends `unblocks: <ID>[, <ID>]`")
            continue
        for target in re.split(r"\s*,\s*", m.group(1)):
            if target not in rows:
                p.append(f"[{rid}]: unblocks {target}, which is not a row of this file")
    return p


def check_roadmap(root: Path | str | None = None) -> list[str]:
    cfg = harness(root)
    base = Path(root) if root is not None else cfg.root
    rel = str(section(cfg, "guards", "roadmap").get("file", "ROADMAP.md"))
    f = base / rel
    if not f.is_file():
        return [f"{rel} is missing: every project keeps its state in one roadmap (templates/ROADMAP.md)"]
    return [f"{rel}: {x}" for x in check_file(f.read_text(encoding="utf-8", errors="replace"))]


_SOUND = """# Demo roadmap
Updated: 2026-10-01
## Now
- [N1] 2026-10-01 write the first-run example
## Waiting on owner
- [W1] 2026-09-30 confirm the rule list. unblocks: X1, N1
## Next
- [X1] 2026-10-01 doctor as the first call
## Done
- [D1] 2026-09-30 merged the tracking layer (commit abc1234)
## Parked
- [P1] 2026-09-30 no budget rules
## Decisions
- [R1] 2026-09-30 "Skip the portfolio management"
## Links
- repository: the demo repository
"""


def self_test() -> list[str]:
    """[] when the sound roadmap passes and every planted break is caught."""
    out = [f"a sound roadmap is refused: {check_file(_SOUND)}"] if check_file(_SOUND) else []
    plants = {
        "no title": _SOUND.replace("# Demo roadmap", "Demo roadmap"),
        "no Updated": _SOUND.replace("Updated: 2026-10-01\n", ""),
        "an unreal Updated": _SOUND.replace("Updated: 2026-10-01", "Updated: 2026-13-01"),
        "a section missing": _SOUND.replace("## Parked\n- [P1] 2026-09-30 no budget rules\n", ""),
        "sections out of order": _SOUND.replace("## Done", "## Zed").replace("## Parked", "## Done").replace("## Zed", "## Parked"),
        "two Now items": _SOUND.replace("## Waiting", "- [N2] 2026-10-01 another\n## Waiting"),
        "a row with no date": _SOUND.replace("[X1] 2026-10-01", "[X1]"),
        "an unreal date": _SOUND.replace("[X1] 2026-10-01", "[X1] 2026-02-30"),
        "a date after Updated": _SOUND.replace("[X1] 2026-10-01", "[X1] 2026-10-02"),
        "a wrong letter": _SOUND.replace("[X1] 2026-10-01 doctor", "[D9] 2026-10-01 doctor"),
        "a duplicate id": _SOUND.replace("[P1] 2026-09-30", "[D1] 2026-09-30"),
        "a waiting row with no unblocks": _SOUND.replace(" unblocks: X1, N1", ""),
        "an unblocks that is no row": _SOUND.replace("X1, N1", "X1, X9"),
        "a done row with no reference": _SOUND.replace(" (commit abc1234)", ""),
        "a decision with no quotation": _SOUND.replace('"Skip the portfolio management"', "skip the portfolio management"),
        "a link with no where": _SOUND.replace("repository: the demo repository", "repository"),
    }
    for what, text in plants.items():
        if not check_file(text):
            out.append(f"the roadmap rule misses: {what}")
    return out


def check_roadmaps(root: Path | str | None = None) -> bool:
    """self_test() + check_roadmap() as kit.testing.check lines."""
    ok = report("roadmap: every rule catches its planted break", self_test())
    return report("roadmap: the project's ROADMAP.md follows the format", check_roadmap(root)) and ok
