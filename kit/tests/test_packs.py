#!/usr/bin/env python3
"""kit/packs.tsv and kit/tools/packs.py: every module in one pack, packs closed.

  * every kit module has exactly one row and every row names a module;
  * base and testkit exist; a module imports only base and its own pack
    (lazy imports included), so a harness that takes base alone can import
    all of it;
  * the import reader tells a module from a name, and a package's names
    from its submodules;
  * `[kit] packs`: absent = every pack; base and testkit always; an unknown
    pack or a bad shape is refused.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.testing.check import check, finish, tmp_dir  # noqa: E402
from kit.tools import packs  # noqa: E402

KIT = Path(__file__).resolve().parents[1]


def main() -> int:
    table = packs.table(KIT)
    mods = set(packs.modules(KIT))

    print("[1] the table covers the kit")
    check("every module has a row", sorted(mods - set(table)) == [], sorted(mods - set(table)))
    check("every row names a module", sorted(set(table) - mods) == [], sorted(set(table) - mods))
    check("base and testkit exist", set(packs.ALWAYS) <= set(table.values()), set(table.values()))

    print("\n[2] a pack imports only base and itself")
    bad = []
    for m in sorted(mods):
        src = (KIT / (m.replace(".", "/") + ".py")).read_text("utf-8")
        for dep in sorted(packs.imports(src, mods)):
            if table[dep] not in ("base", table[m]):
                bad.append(f"{m} ({table[m]}) imports {dep} ({table[dep]})")
    check("no import crosses packs", bad == [], bad)

    print("\n[3] the import reader")
    known = {"__init__", "config", "guards.__init__", "guards.ssot", "tools.manifest", "takes"}
    src = ("import os\nfrom kit.config import config\nfrom kit.guards import ssot, report\n"
           "def f():\n    import kit.tools.manifest\n    from kit import __version__, takes\n"
           "from other.kit import x\n")
    check("modules, names, package names and lazy imports",
          packs.imports(src, known) == {"config", "guards.ssot", "guards.__init__",
                                        "tools.manifest", "__init__", "takes"},
          packs.imports(src, known))
    check("a file's module", (packs.module_of("facts.py", set()), packs.module_of("guards/ssot.py", set()),
                              packs.module_of("message_codes.d/takes.tsv", {"takes"}),
                              packs.module_of("message_codes.d/_README.txt", {"takes"}),
                              packs.module_of("VERSION", set()))
          == ("facts", "guards.ssot", "takes", None, None))

    print("\n[4] [kit] packs")
    h = Path(tmp_dir("packs-"))
    names = set(table.values())
    (h / "harness.toml").write_text('[harness]\nname = "p"\n')
    check("absent: every pack", packs.chosen(h, KIT) == names)
    (h / "harness.toml").write_text('[harness]\nname = "p"\n[kit]\npacks = []\n')
    check("empty: base and testkit", packs.chosen(h, KIT) == set(packs.ALWAYS))
    for body, words in (('packs = ["video"]', "unknown pack: video"),
                        ('packs = "data"', "must be a list")):
        (h / "harness.toml").write_text(f'[harness]\nname = "p"\n[kit]\n{body}\n')
        try:
            packs.chosen(h, KIT)
            err = ""
        except packs.PackError as e:
            err = str(e)
        check(f"refused: {body}", words in err, err)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
