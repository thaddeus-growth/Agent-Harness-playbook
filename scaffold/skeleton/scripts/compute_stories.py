"""Each story's check (ssot/story_checks.tsv) run on this client's data,
read verbs only: pass, fail, or skip for missing data (kit.stories)."""

import sys

from kit import stories

if __name__ == "__main__":
    raise SystemExit(stories.main(sys.argv[1:]))
