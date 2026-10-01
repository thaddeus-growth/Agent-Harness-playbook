#!/usr/bin/env python3
"""The roadmap format (kit/guards/roadmap.py).

  [0] the rules on planted input (self_test) all hold
  [1] the shipped template passes its own check, so a project that copies it
      starts green
  [2] a project's file: found at the configured path, a missing file and a
      broken one are reported with the file name
  [3] edge cases: CRLF, a byte order mark, a wrapped item, a Waiting row that
      unblocks several rows, blank sections
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402,F401
from kit.guards import roadmap  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).resolve().parent / "fake_harness"


def main() -> None:
    check("[0] self_test: every rule catches its plant", roadmap.self_test() == [], roadmap.self_test())
    tpl = (REPO / "templates" / "ROADMAP.md").read_text(encoding="utf-8")
    check("[1] the template passes its own check", roadmap.check_file(tpl) == [], roadmap.check_file(tpl))

    root = Path(tmp_dir()) / "h"
    (root / "docs").mkdir(parents=True)
    (root / "harness.toml").write_text((FAKE / "harness.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "verbs.py").write_text((FAKE / "scripts" / "verbs.py").read_text(encoding="utf-8"), encoding="utf-8")
    check("[2] no roadmap is reported", any("ROADMAP.md is missing" in x for x in roadmap.check_roadmap(root)))
    (root / "ROADMAP.md").write_text(tpl, encoding="utf-8")
    check("[2] the default path ROADMAP.md is read and clean", roadmap.check_roadmap(root) == [], roadmap.check_roadmap(root))
    (root / "ROADMAP.md").write_text(tpl.replace("## Parked", "## Later"), encoding="utf-8")
    got = roadmap.check_roadmap(root)
    check("[2] a broken file is reported with its name", got and got[0].startswith("ROADMAP.md: the sections"), got)
    (root / "harness.toml").write_text((root / "harness.toml").read_text(encoding="utf-8") + '\n[guards.roadmap]\nfile = "docs/ROADMAP.md"\n', encoding="utf-8")
    (root / "docs" / "ROADMAP.md").write_text(tpl, encoding="utf-8")
    check("[2] [guards.roadmap].file is honoured", roadmap.check_roadmap(root) == [], roadmap.check_roadmap(root))

    check("[3] CRLF and a byte order mark are read", roadmap.check_file("﻿" + tpl.replace("\n", "\r\n")) == [])
    wrapped = tpl.replace("## Done", "- [X2] 2026-10-01 a long item that wraps\n  onto a second line\n## Done", 1)
    check("[3] a wrapped item is one item", roadmap.check_file(wrapped) == [], roadmap.check_file(wrapped))
    check("[3] blank sections are fine", roadmap.check_file("# P roadmap\nUpdated: 2026-10-01\n## Now\n## Waiting on owner\n## Next\n## Done\n## Parked\n## Decisions\n## Links\n") == [])
    check("[3] a Waiting row may unblock several rows", roadmap.check_file(tpl.replace("unblocks: X1", "unblocks: X1, N1")) == [])


if __name__ == "__main__":
    main()
    raise SystemExit(finish())
