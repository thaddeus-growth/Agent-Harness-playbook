#!/usr/bin/env python3
"""kit.testing.suites: the nine day-one suites, on a harness the playbook's
scaffolder generates (scaffold/new_harness.py, run as a subprocess), and
each on a planted violation, so no suite can pass by looking at nothing.

  [1] every suite is green on the fresh harness (markets HK, TW)
  [2] run_tests: tests/run.py replaced by something else
  [3] ssot: an owner row with no agent sibling; a stage signed before the
      step it comes after
  [4] layering: a compute importing the writer
  [5] boundary: a consumer that runs a human write verb
  [6] json_contract: a read verb that prints an uncoded English sentence
  [7] human_tables: a SPEC without one of the kit's human tables
  [8] gate: a gated verb that offers --force
  [9] release: B8 signed while shipped files still hold fill markers; a
      stale .gitattributes
  [10] kit_drift: a local edit to the vendored kit
  [11] a harness without markets: the gate and the json contract hold
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

import _shop  # noqa: E402
from kit import db, schema_base  # noqa: E402
from kit.testing import suites  # noqa: E402
from kit.testing.check import check, finish, tmp_dir  # noqa: E402

SCAFFOLD = _shop.PLAYBOOK / "scaffold" / "new_harness.py"
HAS_GIT = shutil.which("git") is not None


def generate(*extra: str) -> Path:
    root = Path(tmp_dir("suites-harness-")).resolve() / "acme"
    env = {k: v for k, v in os.environ.items() if k != "KIT_HARNESS_ROOT"}
    r = subprocess.run([sys.executable, str(SCAFFOLD), "--dir", str(root),
                        "--name", "acme-harness", "--cli", "acme",
                        "--prefix", "ACME", "--owner", "@boss",
                        "--repo-home", "the team's git host", *extra],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr
    if HAS_GIT:
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "scaffold")
    return root


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=suite",
                    "-c", "user.email=suite@example.com",
                    "-c", "commit.gpgsign=false", *args],
                   capture_output=True, check=True, timeout=60)


def failing(results) -> list[str]:
    return [label for label, problems in results if problems]


def planted(root: Path, rel: str, text: str | None = None, *,
            append: str | None = None, replace: tuple[str, str] | None = None):
    """Change one file (and stage it, so git sees it); returns the undo."""
    p = root / rel
    old = p.read_bytes() if p.exists() else None
    if append is not None:
        text = (old or b"").decode() + append
    elif replace is not None:
        text = (old or b"").decode().replace(*replace)
        assert text != old.decode(), f"nothing to replace in {rel}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if HAS_GIT:
        git(root, "add", "-A")

    def undo() -> None:
        if old is None:
            p.unlink()
        else:
            p.write_bytes(old)
        if HAS_GIT:
            git(root, "add", "-A")
    return undo


def caught(label: str, results, needle: str) -> None:
    bad = failing(results)
    check(label, any(needle in b for b in bad),
          bad or "(every suite check passed)")


def spec_of(root: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "acme_schema", root / "scripts" / "_lib" / "schema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SPEC


ALL = ("run_tests", "ssot", "layering", "boundary", "json_contract",
       "human_tables", "gate", "release", "kit_drift")


def every(root: Path) -> dict:
    out = {}
    for name in ALL:
        fn = getattr(suites, name)
        out[name] = fn(root, spec_of(root)) if name == "human_tables" \
            else fn(root)
    return out


BAD_COMPUTE = """\"\"\"A read verb that talks prose.\"\"\"
import json
print(json.dumps({"meta": None, "message": "Everything looks fine today"}))
"""
FORCE = """\"\"\"Confirm, the quick way.\"\"\"
import argparse
p = argparse.ArgumentParser(prog="acme facts force-confirm")
p.add_argument("--force", action="store_true", help="skip the person")
p.parse_args()
"""


def main() -> int:
    print("[1] every suite on a fresh harness (markets HK, TW)")
    root = generate("--markets", "HK,TW")
    got = every(root)
    for name, results in got.items():
        check(f"{name}: green ({len(results)} check(s))",
              results and not failing(results),
              [(label, p) for label, p in results if p])
    check("the suites bound the generated harness",
          suites.bind(root).root == root)

    print("\n[2] run_tests")
    undo = planted(root, "tests/run.py", "print('RESULT: 1 passed')\n")
    caught("tests/run.py that is not the kit's runner",
           suites.run_tests(root), "tests/run.py is that runner")
    undo()

    print("\n[3] ssot")
    undo = planted(root, "ssot/glossary.tsv",
                   append="stock\tUnits on hand that can be sold today\n")
    caught("an owner row with no agent sibling", suites.ssot(root),
           "every owner row names its decided answer")
    undo()
    undo = planted(root, "ssot/stages.agent.tsv", replace=(
        "B1\tscaffold\tB0.6\tci\ttodo\t\t\t", "B1\tscaffold\tB0.6\tci\tsigned"
        "\t\t\tci:42"))
    caught("a stage signed before a step it comes after", suites.ssot(root),
           "no stage is signed before")
    undo()

    print("\n[4] layering")
    undo = planted(root, "scripts/compute_x.py", "from _lib import writer\n")
    caught("a compute importing the writer", suites.layering(root),
           "the layering rules of harness.toml hold")
    undo()

    print("\n[5] boundary")
    undo = planted(root, "console/nudge.py",
                   "import subprocess\nsubprocess.run(['acme', 'facts', "
                   "'set', 'k', '1'])\n")
    caught("a consumer that runs a human write verb", suites.boundary(root),
           "a consumer runs only read verbs")
    undo()

    print("\n[6] json_contract")
    u1 = planted(root, "scripts/compute_bad.py", BAD_COMPUTE)
    u2 = planted(root, "scripts/verbs.py", replace=(
        "VERBS = [\n", "VERBS = [\n    Verb((\"compute\", \"bad\"), "
        "\"compute_bad.py\", \"read\"),\n"))
    caught("a read verb that prints an uncoded sentence",
           suites.json_contract(root), "every read verb on the fixture")
    u2()
    u1()

    print("\n[7] human_tables")
    spec = spec_of(root)
    tables = {t: v for t, v in spec.tables.items() if t != "action_effects"}
    thin = db.SchemaSpec(
        tables=tables, version=spec.version,
        human_tables=tuple(t for t in spec.human_tables
                           if t != "action_effects"),
        append_only={t: v for t, v in spec.append_only.items()
                     if t != "action_effects"},
        frozen_columns={t: v for t, v in spec.frozen_columns.items()
                        if t != "action_effects"})
    caught("a SPEC without one of the kit's human tables",
           suites.human_tables(root, thin), "holds the kit's human tables")
    check("(the kit's human tables are six)",
          len(schema_base.HUMAN) == 6)

    print("\n[8] gate")
    u1 = planted(root, "scripts/force.py", FORCE)
    u2 = planted(root, "scripts/verbs.py", replace=(
        "VERBS = [\n", "VERBS = [\n    Verb((\"facts\", \"force-confirm\"), "
        "\"force.py\", \"gated\"),\n"))
    caught("a gated verb that offers --force", suites.gate(root),
           "no gated verb offers a bypass flag")
    u2()
    u1()

    print("\n[9] release")
    if HAS_GIT:
        undo = planted(root, "ssot/stages.agent.tsv", replace=(
            "B8\tagent docs and evals\tB6\tbuilder owner + ci\ttodo",
            "B8\tagent docs and evals\tB6\tbuilder owner + ci\tsigned"))
        caught("B8 signed while README.md, SKILL.md and "
               "references/workflows.md hold fill markers",
               suites.release(root), "no shipped file holds a fill marker")
        undo()
        undo = planted(root, ".gitattributes", replace=(
            "/tests export-ignore\n", ""))
        caught("a .gitattributes that ships tests/", suites.release(root),
               "git archive leaves out the internal files")
        undo()
    else:
        check("(git missing: the release suite skips, as the guard does)",
              not failing(suites.release(root)))

    print("\n[10] kit_drift")
    undo = planted(root, "scripts/kit/dates.py", append="# a local fix\n")
    caught("a local edit to the vendored kit", suites.kit_drift(root),
           "match their MANIFEST.sha256")
    undo()
    check("…and green again once it is undone",
          not failing(suites.kit_drift(root)))

    print("\n[11] a harness without markets")
    flat = generate()
    for name in ("gate", "json_contract", "human_tables", "ssot"):
        results = (suites.human_tables(flat, spec_of(flat))
                   if name == "human_tables" else getattr(suites, name)(flat))
        check(f"{name}: green with markets = []", not failing(results),
              [(label, p) for label, p in results if p])
    _shop.use()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
