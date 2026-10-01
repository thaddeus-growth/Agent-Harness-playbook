"""The playbook's own ROADMAP.md follows the one format every project's roadmap does
(kit.guards.roadmap), so a session on a new machine starts from the file. Each check
is tried on a broken input first, so it cannot pass by looking at nothing."""

from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)
import _t  # noqa: E402
from kit.guards import roadmap  # noqa: E402


def read() -> str:
    with open(os.path.join(REPO, "ROADMAP.md"), encoding="utf-8") as f:
        return f.read()


def test_the_checker_refuses_a_roadmap_that_breaks_the_format():
    text = read()
    assert roadmap.check_file(text.replace("## Next\n", "## Soon\n"))        # a section renamed
    assert roadmap.check_file(text.replace("unblocks: X3", "unblocks: X99"))  # a row that unblocks nothing
    assert roadmap.check_file(text.replace("(PR #18)", ""))                   # a Done row with no reference


def test_the_playbook_roadmap_follows_the_format():
    assert roadmap.check_file(read()) == [], roadmap.check_file(read())


if __name__ == "__main__":
    _t.main(globals())
