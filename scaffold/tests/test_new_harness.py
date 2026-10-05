#!/usr/bin/env python3
"""scaffold/new_harness.py: a new harness, green before its first feature.

  [1] --dry-run lists what it would write and writes nothing; a folder that
      is not empty, one inside the playbook (scaffold/out/, ignored, aside),
      a bad or taken name and bad languages are refused with one line,
      exit 2
  [2] the generated tree (markets HK, TW): no `{{` left; fill markers only
      in the files the build fills; exactly the ten tests templates/
      README.md names, plus tests/run.py; harness.toml and the ssot
      headers carry what the kit reads; the vendored copies match their
      manifests
  [3] `git init`, commit, and its own suite through its own CLI
      (`scripts/acme.py test`): RESULT with 0 failed
  [4] every CODEOWNERS path exists
  [5] every item of the day-one checklist (docs/stages.md) maps to a
      generated file (scaffold/new_harness.py DAY_ONE)
  [6] the CLI answers: --help lists every verb, verbs --json, doctor
  [7] --update-kit re-vendors and touches nothing else; a second run
      changes nothing
  [8] another shape: no markets, three languages: its ssot and json
      contract suites pass
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True
PLAYBOOK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLAYBOOK))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

from kit.testing.check import check, finish, tmp_dir  # noqa: E402

SCAFFOLD = PLAYBOOK / "scaffold" / "new_harness.py"
ENV = {k: v for k, v in os.environ.items()
       if k != "KIT_HARNESS_ROOT" and not k.startswith("ACME_")}

sys.path.insert(0, str(SCAFFOLD.parent))
import new_harness as nh  # noqa: E402


def scaffold(*argv: str) -> tuple[int, str, str]:
    r = subprocess.run([sys.executable, str(SCAFFOLD), *argv],
                       capture_output=True, text=True, timeout=300, env=ENV)
    return r.returncode, r.stdout, r.stderr


def args(d: Path, *extra: str) -> list[str]:
    return ["--dir", str(d), "--name", "acme-harness", "--cli", "acme",
            "--prefix", "ACME", *extra]


def git(root: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=scaffold",
                           "-c", "user.email=scaffold@example.com",
                           "-c", "commit.gpgsign=false", *a],
                          capture_output=True, text=True, check=True,
                          timeout=60).stdout


def own_files(root: Path) -> list[str]:
    """The files the scaffolder wrote itself (not the vendored copies)."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(root).parts
                  and not p.relative_to(root).as_posix().startswith(
                      ("scripts/kit/", "console/"))
                  and "__pycache__" not in p.parts)


def digest(root: Path, files: list[str]) -> dict[str, str]:
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest()
            for f in files}


def cli(root: Path, *argv: str) -> tuple[int, str, str]:
    r = subprocess.run([sys.executable, str(root / "scripts" / "acme.py"),
                        *argv], capture_output=True, text=True, timeout=900,
                       env={**ENV, "ACME_AUTH_ENV_PATHS": "none"},
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


# ------------------------------------------------------------------ [1]

def test_refusals() -> None:
    print("[1] --dry-run and refusals")
    d = Path(tmp_dir("scaffold-dry-")) / "acme"
    rc, out, err = scaffold(*args(d, "--dry-run"))
    check("--dry-run: the file list, nothing written",
          rc == 0 and "write    scripts/acme.py" in out
          and "write    tests/test_gate.py" in out and "nothing written" in out
          and not d.exists(), (rc, out[-300:], err))
    full = Path(tmp_dir("scaffold-full-"))
    (full / "x").write_text("x", encoding="utf-8")
    for label, argv in (
            ("a folder that is not empty", args(full)),
            ("a folder inside the playbook", args(PLAYBOOK / "kit" / "acme")),
            ("an upper-case CLI word", ["--dir", str(d), "--name", "a-h",
                                        "--cli", "Acme", "--prefix", "A1"]),
            ("a CLI word the skeleton uses", ["--dir", str(d), "--name", "a-h",
                                              "--cli", "facts", "--prefix",
                                              "AC"]),
            ("a lower-case prefix", ["--dir", str(d), "--name", "a-h",
                                     "--cli", "acme", "--prefix", "acme"]),
            ("languages without en first", args(d, "--langs", "zh,en")),
            ("a market twice", args(d, "--markets", "HK,HK")),
            ("an unknown pack", args(d, "--packs", "video")),
            ("base as a pack (it always comes)", args(d, "--packs", "base")),
            ("--update-kit on a folder with no harness.toml",
             ["--dir", str(full), "--update-kit"])):
        rc, out, err = scaffold(*argv)
        check(f"refused: {label} (exit 2, one line, nothing written)",
              rc == 2 and err.startswith("error: ")
              and len(err.strip().splitlines()) == 1 and not d.exists(),
              (rc, out, err))
    check("(nothing appeared inside the playbook)",
          not (PLAYBOOK / "kit" / "acme").exists())
    preview = PLAYBOOK / "scaffold" / "out" / "acme-preview"
    rc, out, err = scaffold(*args(preview, "--dry-run"))
    check("scaffold/out/ (ignored) is the one preview folder inside the "
          "playbook", rc == 0 and "nothing written" in out
          and not preview.exists(), (rc, err))
    ignored = subprocess.run(["git", "-C", str(PLAYBOOK), "check-ignore", "-q",
                              "scaffold/out/acme-preview/x"], timeout=60)
    check("…and git ignores it", ignored.returncode == 0)


# ------------------------------------------------------------------ [2]

def test_tree(root: Path) -> None:
    print("\n[2] the generated tree")
    files = own_files(root)
    leftovers = [f for f in files if "{{" in (root / f).read_text(
        encoding="utf-8", errors="replace")]
    check("no `{{` left in any file it wrote", not leftovers, leftovers)
    fills = sorted(f for f in files if "<<fill:" in (root / f).read_text(
        encoding="utf-8", errors="replace"))
    check("fill markers only in the files the build fills",
          set(fills) <= nh.FILLED and {"SKILL.md", "README.md"} <= set(fills),
          fills)
    table = (PLAYBOOK / "templates" / "README.md").read_text(encoding="utf-8")
    named = set(re.findall(r"^\| `tests/(test_\w+\.py)` \|", table, re.M))
    tests = {p.name for p in (root / "tests").glob("*.py")}
    check("exactly the ten tests templates/README.md names, plus run.py",
          tests == named | {"run.py"} and len(named) == 10
          and set(nh.GENERATED_TESTS) == named, sorted(tests ^ named))
    for t in sorted(named):
        text = (root / "tests" / t).read_text(encoding="utf-8")
        check(f"tests/{t}: a thin call into kit.testing.suites",
              "from kit.testing import suites" in text
              and "raise SystemExit(finish())" in text
              and len(text.splitlines()) < 30)
    cfg = tomllib.loads((root / "harness.toml").read_text(encoding="utf-8"))
    h = cfg["harness"]
    check("harness.toml: the names, the markets, the languages",
          (h["name"], h["cli"], h["env_prefix"], h["db_file"])
          == ("acme-harness", "acme", "ACME", "acme.db")
          and h["markets"] == ["HK", "TW"] and h["languages"] == ["en", "zh"]
          and "scripts/acme.py" in cfg["release"]["must_ship"])
    kd = root / "scripts" / "kit"
    check("no --packs: [kit] packs = [], the kit copy holds base and testkit only",
          cfg["kit"]["packs"] == [] and (kd / "facts.py").is_file()
          and (kd / "testing" / "suites.py").is_file()
          and not (kd / "takes.py").exists() and not (kd / "retry.py").exists())
    heads = {"constants.tsv": ["name", "default", "unit", "min", "max",
                               "group", "label_en", "label_zh", "explain",
                               "why"],
             "fact_keys.tsv": ["key", "group", "type", "unit", "min", "max",
                               "label_en", "label_zh", "why"],
             "decision_keys.tsv": ["entity_type", "key", "domain", "confirm",
                                   "label_en", "label_zh", "story", "why"],
             "message_codes.tsv": ["code", "params", "meaning_en",
                                   "meaning_zh"],
             "story_checks.tsv": ["story", "verb", "expect", "needs", "note"]}
    for name, head in heads.items():
        lines = (root / "ssot" / name).read_text(encoding="utf-8").splitlines()
        check(f"ssot/{name}: the columns the kit reads"
              + (", and the approval_ttl_hours row" if name == "constants.tsv"
                 else ", header only"),
              lines[0].split("\t") == head and (
                  [ln.split("\t")[0] for ln in lines[1:]]
                  == ["approval_ttl_hours"] if name == "constants.tsv"
                  else lines[1:] == []), lines[:3])
    index = [ln.split("\t")[0] for ln in (root / "ssot" / "index.tsv")
             .read_text(encoding="utf-8").splitlines()[1:]]
    on_disk = sorted(p.relative_to(root).as_posix()
                     for p in (root / "ssot").glob("*.tsv"))
    check("ssot/index.tsv lists exactly the ssot files written",
          sorted(index) == on_disk, sorted(set(index) ^ set(on_disk)))
    from kit.tools import manifest
    check("scripts/kit/ and console/ vendored, each matching its manifest",
          manifest.check(root / "scripts" / "kit") == []
          and manifest.check(root / "console") == []
          and (root / "scripts" / "kit" / "VERSION").read_text()
          == (root / "console" / "VERSION").read_text())
    check("no tests/ vendored with the kit",
          not (root / "scripts" / "kit" / "tests").exists())


# ------------------------------------------------------------------ [3]

def test_suite(root: Path) -> None:
    print("\n[3] its own suite, through its own CLI")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "S00 scaffold")
    rc, out, err = cli(root, "test")
    last = out.strip().splitlines()[-1] if out.strip() else ""
    check("`acme test`: exit 0 and RESULT with 0 failed",
          rc == 0 and re.fullmatch(r"RESULT: \d+ passed", last)
          and int(last.split()[1]) >= 25, (rc, out[-1500:], err[-500:]))
    check("every generated test file ran and passed",
          all(re.search(rf"^ok\s.*tests/{t}", out, re.M)
              for t in nh.GENERATED_TESTS), out[-1500:])
    check("the suite left the tree as it was (nothing untracked)",
          git(root, "status", "--porcelain") == "",
          git(root, "status", "--porcelain"))


# ------------------------------------------------------------------ [4]

def test_codeowners(root: Path) -> None:
    print("\n[4] CODEOWNERS")
    lines = [ln.split() for ln in (root / ".gitlab" / "CODEOWNERS").read_text(
        encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    paths = [p for p, *_ in lines]
    missing = [p for p in paths if not (root / p.strip("/")).exists()]
    check("every CODEOWNERS path exists in the new harness",
          len(paths) >= 12 and not missing, missing)
    check("every path is owned by the --owner handle",
          all(owner == ["@boss"] for _, *owner in lines), lines)
    check("the risky list is there: the kit, the console, the writer, "
          "execute, the schema, harness.toml",
          {"/scripts/kit/", "/console/", "/scripts/_lib/writer.py",
           "/scripts/execute_actions.py", "/scripts/_lib/schema.py",
           "/harness.toml"} <= set(paths), paths)


# ------------------------------------------------------------------ [5]

def test_day_one(root: Path) -> None:
    print("\n[5] the day-one checklist")
    readme = (PLAYBOOK / "docs" / "stages.md").read_text(encoding="utf-8")
    body = readme.split("## Day-one checklist", 1)[1].split("\n## ", 1)[0]
    items = re.findall(r"^- \[ \] (.+)$", body, re.M)
    check("the checklist has its items", len(items) >= 15, len(items))
    for item in items:
        keys = [k for k in nh.DAY_ONE if item.startswith(k)]
        paths = [p.format(cli="acme") for k in keys for p in nh.DAY_ONE[k]]
        missing = [p for p in paths if not (root / p).exists()]
        check(f"day one: {item[:60]}… -> {', '.join(paths) or 'NOTHING'}",
              len(keys) == 1 and paths and not missing, (keys, missing))
    stale = [k for k in nh.DAY_ONE if not any(i.startswith(k) for i in items)]
    check("every DAY_ONE key is still a checklist item", not stale, stale)


# ------------------------------------------------------------------ [6]

def test_cli(root: Path) -> None:
    print("\n[6] the CLI")
    rc, out, _ = cli(root, "--help")
    from kit.verbs import load
    verbs = load(root / "scripts" / "verbs.py")
    check("--help lists every verb of scripts/verbs.py",
          rc == 0 and all(f"acme {v.command}" in out for v in verbs), out)
    rc, out, _ = cli(root, "verbs", "--json")
    doc = json.loads(out)
    check("verbs --json: the table, with the gate verbs gated",
          rc == 0 and {tuple(r["words"]) for r in doc["verbs"]
                       if r["kind"] == "gated"}
          == {("facts", "confirm"), ("facts", "restore"),
              ("decisions", "confirm"), ("queue", "approve")})
    rc, out, err = cli(root, "facts", "list", "--json")
    check("a verb that needs the data dir refuses without it",
          rc == 2 and json.loads(out)["code"] == "data_dir_unset", out)
    rc, out, err = cli(root, "doctor", "--json")
    check("doctor runs (one document; writes off is healthy)",
          rc == 0 and any(c["message_code"]["code"] == "doctor_writes_off"
                          for c in json.loads(out)["checks"]), out[:300])


# ------------------------------------------------------------------ [7]

def test_update_kit(root: Path) -> None:
    print("\n[7] --update-kit")
    mine = [f for f in own_files(root)]
    before = digest(root, mine)
    (root / "scripts" / "kit" / "dates.py").write_text("# edited\n",
                                                       encoding="utf-8")
    rc, out, err = scaffold("--dir", str(root), "--update-kit")
    check("re-vendors a drifted kit", rc == 0
          and "updated  scripts/kit/dates.py" in out, (out, err))
    check("…and touches nothing else", digest(root, mine) == before)
    rc, out, err = scaffold("--dir", str(root), "--update-kit")
    check("a second run changes nothing", rc == 0
          and "kit: up to date" in out and "console: up to date" in out, out)


# ------------------------------------------------------------------ [8]

def test_other_shape() -> None:
    print("\n[8] no markets, three languages, two packs")
    root = Path(tmp_dir("scaffold-flat-")).resolve() / "acme"
    rc, out, err = scaffold(*args(root, "--langs", "en,zh,ja", "--packs", "data,compliance"))
    check("generated", rc == 0, err)
    cfg = tomllib.loads((root / "harness.toml").read_text(encoding="utf-8"))
    kd = root / "scripts" / "kit"
    check("[kit] packs as given; the copy holds them and not the others",
          cfg["kit"]["packs"] == ["data", "compliance"] and (kd / "retry.py").is_file()
          and (kd / "copylint.py").is_file() and not (kd / "takes.py").exists())
    check("markets = [], languages en, zh, ja",
          cfg["harness"]["markets"] == []
          and cfg["harness"]["languages"] == ["en", "zh", "ja"])
    head = (root / "ssot" / "message_codes.tsv").read_text(
        encoding="utf-8").split("\n")[0].split("\t")
    check("the message registry has a meaning column per language",
          head == ["code", "params", "meaning_en", "meaning_zh", "meaning_ja"])
    check("CLAUDE.md keeps a fill marker for the repository's home",
          "<<fill: where the repository lives>>" in (root / "CLAUDE.md")
          .read_text(encoding="utf-8"))
    r = subprocess.run([sys.executable, str(root / "tests" / "run.py"),
                        "ssot", "json", "gate"], capture_output=True,
                       text=True, timeout=900, env=ENV)
    check("its ssot, json contract and gate tests pass",
          r.returncode == 0 and re.search(r"^RESULT: \d+ passed$",
                                          r.stdout.strip(), re.M),
          r.stdout[-1500:])


def main() -> int:
    test_refusals()
    root = Path(tmp_dir("scaffold-")).resolve() / "acme"
    rc, out, err = scaffold(*args(root, "--markets", "HK,TW", "--owner",
                                  "@boss", "--repo-home",
                                  "the team's git host"))
    check("generated: exit 0, the next steps printed",
          rc == 0 and "next: cd " in out and "acme.py test" in out,
          (out[-500:], err[-500:]))
    test_tree(root)
    test_suite(root)
    test_codeowners(root)
    test_day_one(root)
    test_cli(root)
    test_update_kit(root)
    test_other_shape()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
