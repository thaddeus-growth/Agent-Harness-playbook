#!/usr/bin/env python3
"""Build the rule-removed twin of every eval, and score the pairs.

    ablate.py build --cases evals/cases.tsv --skill SKILL.md --refs references --out DIR
    ablate.py score --verdicts DIR/verdicts.tsv

An eval counts only if it fails when its rule is removed (BUILD.md, B8). This
makes that test runnable: `build` writes DIR/full/ (the files as shipped) and
DIR/<id>/ (the same files with that case's `rule_text` deleted), and
DIR/plan.tsv (id, full dir, removed dir). You give a fresh agent (or a
simulated one, templates/evals.md) each directory plus the case's prompt, and
write what it did into DIR/verdicts.tsv: `id`, `full` (pass|fail), `removed`
(kept|broke). `score` says which evals count.

`rule_text` in cases.tsv is the exact words that carry the rule, one or more
snippets joined by ` || `. Guards:
  * a snippet that is in none of the files is an error, not a skip: the rule
    is not where the case says it is (it moved, or the case is stale);
  * an empty `rule_text` is an error (an eval names the words that carry its rule);
  * no case removes a snippet another case needs: each directory is built from
    the shipped files, never from another twin.

Stdlib only. Test: tests/test_evals_ablate.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SEP = " || "
COLUMNS = ("id", "rule", "rule_text", "pressure", "prompt", "keeps", "breaks")


def read_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    missing = [c for c in COLUMNS if not rows or c not in rows[0]]
    if missing:
        raise SystemExit(f"{path}: missing columns {missing}")
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)) or any(not i for i in ids):
        raise SystemExit(f"{path}: ids must be non-empty and unique")
    return rows


def read_files(skill: Path, refs: Path) -> dict[str, str]:
    files = {"SKILL.md": skill.read_text(encoding="utf-8")}
    if refs.is_dir():
        for p in sorted(refs.rglob("*.md")):
            files[f"references/{p.relative_to(refs).as_posix()}"] = p.read_text(encoding="utf-8")
    return files


def ablate(files: dict[str, str], rule_text: str, case: str) -> dict[str, str]:
    snippets = [s for s in rule_text.split(SEP) if s]
    if not snippets:
        raise SystemExit(f"{case}: rule_text is empty; an eval names the words that carry its rule")
    out = dict(files)
    for s in snippets:
        hit = [n for n, t in out.items() if s in t]
        if not hit:
            raise SystemExit(f"{case}: rule_text {s[:60]!r} is in none of {sorted(files)}: "
                             f"the rule moved or the case is stale")
        for n in hit:
            out[n] = out[n].replace(s, "")
    return out


def write(dirpath: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        p = dirpath / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def build(cases: Path, skill: Path, refs: Path, out: Path) -> list[tuple[str, str, str]]:
    rows, files = read_cases(cases), read_files(skill, refs)
    plan = []
    write(out / "full", files)
    for r in rows:
        write(out / r["id"], ablate(files, r["rule_text"], r["id"]))
        plan.append((r["id"], str(out / "full"), str(out / r["id"])))
    with (out / "plan.tsv").open("w", encoding="utf-8") as f:
        f.write("id\tfull_dir\tremoved_dir\n" + "".join("\t".join(p) + "\n" for p in plan))
    return plan


def score(verdicts: Path) -> tuple[list[tuple[str, str]], int]:
    """([(id, verdict)], number of full-rule failures). Counts iff the full
    run passed and the rule-removed run broke the rule."""
    with verdicts.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out, bad = [], 0
    for r in rows:
        full, removed = r["full"], r["removed"]
        if full not in ("pass", "fail") or removed not in ("kept", "broke"):
            raise SystemExit(f"{r['id']}: full must be pass|fail and removed kept|broke, got {full!r}/{removed!r}")
        if full == "fail":
            out.append((r["id"], "FAILS with the rule present: fix the skill or the case")); bad += 1
        elif removed == "kept":
            out.append((r["id"], "does not count: the agent kept the rule without its text"))
        else:
            out.append((r["id"], "counts: only the rule's text made the difference"))
    return out, bad


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--cases", type=Path, required=True)
    b.add_argument("--skill", type=Path, required=True)
    b.add_argument("--refs", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--verdicts", type=Path, required=True)
    a = p.parse_args(argv)
    if a.cmd == "build":
        for i, full, removed in build(a.cases, a.skill, a.refs, a.out):
            print(f"{i}\t{full}\t{removed}")
        return 0
    results, bad = score(a.verdicts)
    for i, v in results:
        print(f"{i}\t{v}")
    counting = sum(v.startswith("counts") for _, v in results)
    print(f"{counting} of {len(results)} count", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
