#!/usr/bin/env python3
"""The kit's own rules (kit/README.md, "The rules"), on its source.

  [1] stdlib only: every import in a kit module (tests aside) is the
      standard library or the kit itself
  [2] no domain words: no platform, client or host name in kit/ outside
      tests/
  [3] the kit never imports the console, and the console never imports
      the kit
  [4] every module's docstring names its test (`Test: kit/tests/…`), and
      that test exists

Each rule is first tried on a planted source, so it cannot pass by
looking at nothing.
"""

import ast
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing.check import check, finish  # noqa: E402

KIT = _shop.KIT
CONSOLE = _shop.PLAYBOOK / "console"
STD = set(sys.stdlib_module_names) | {"__future__"}
BANNED = re.compile(r"\b(amazon|ppc|spapi|kol|emma|lark|zylos|meta_\w*)\b",
                    re.I)
# kit/tools/vendor.py falls back to `import manifest` (its sibling) when it
# runs as a script with kit/tools on sys.path
SIBLING = {("tools/vendor.py", "manifest")}


def modules(base: Path, skip_tests: bool = True) -> list[Path]:
    return sorted(p for p in base.rglob("*.py")
                  if "__pycache__" not in p.parts
                  and not (skip_tests and "tests" in p.relative_to(base).parts))


def imports(src: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append(n.module)
    return out


def non_std(rel: str, src: str) -> list[str]:
    return [m for m in imports(src) if m.split(".")[0] not in STD
            and m.split(".")[0] != "kit" and (rel, m) not in SIBLING]


def names_other(src: str, other: str) -> list[str]:
    """Imports of package `other`, or a sys.path entry pointing at it."""
    hits = [m for m in imports(src) if m.split(".")[0] == other]
    hits += re.findall(rf"sys\.path\.\w+\([^)]*['\"/]{other}['\"/)]", src)
    return hits


def main() -> int:
    print("[1] stdlib only")
    check("the rule sees a third-party import",
          non_std("x.py", "import requests\nfrom sqlite_utils import Db\n"
                          "import json\nfrom kit import db\n")
          == ["requests", "sqlite_utils"])
    mods = modules(KIT)
    check("the kit has its modules", len(mods) > 40, len(mods))
    bad = {p.relative_to(KIT).as_posix(): non_std(
        p.relative_to(KIT).as_posix(), p.read_text(encoding="utf-8"))
        for p in mods}
    bad = {k: v for k, v in bad.items() if v}
    check("every kit module imports only the stdlib and the kit", not bad, bad)

    print("\n[2] no domain words")
    check("the rule sees a domain word, and not a word that contains one",
          BANNED.search("the Amazon puller") and BANNED.search("meta_api")
          and not BANNED.search("a parakeet, a clarkia, the kolkhoz"))
    hits = []
    for p in sorted(KIT.rglob("*")):
        rel = p.relative_to(KIT)
        if not p.is_file() or rel.parts[0] == "tests" or "__pycache__" \
                in rel.parts or p.suffix == ".pyc":
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8",
                                             errors="replace").splitlines(), 1):
            if BANNED.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:80]}")
    check("no platform, client or host name in kit/ (tests aside)", not hits,
          hits)

    print("\n[3] the kit and the console never import each other")
    check("the rule sees an import and a sys.path entry",
          names_other("import console.relay\n", "console")
          and names_other("sys.path.insert(0, ROOT + '/console')\n", "console")
          and not names_other("# the console relays it\n", "console"))
    bad = [p.relative_to(KIT).as_posix() for p in modules(KIT)
           if names_other(p.read_text(encoding="utf-8"), "console")]
    check("no kit module imports the console", not bad, bad)
    bad = [p.relative_to(CONSOLE).as_posix() for p in modules(CONSOLE)
           if names_other(p.read_text(encoding="utf-8"), "kit")]
    check("no console module imports the kit", not bad, bad)

    print("\n[4] every module names its test")
    missing = []
    for p in mods:
        doc = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8"))) or ""
        said = re.split(r"\bTests?:", doc, maxsplit=1)
        tests = re.findall(r"kit/tests/(test_\w+\.py)", said[1]) \
            if len(said) == 2 else []
        if not tests:
            missing.append(f"{p.relative_to(KIT)}: no test named")
        missing += [f"{p.relative_to(KIT)}: {t} does not exist"
                    for t in tests if not (KIT / "tests" / t).is_file()]
    check("every kit module's docstring names a test that exists", not missing,
          missing)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
