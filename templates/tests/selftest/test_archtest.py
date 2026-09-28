#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""archtest.py, the engine under the layering rules, on small trees written
for each case: module names, eager and lazy imports, relative imports,
re-exports, reach, both kinds of rule, and the text rules' self-check.
"""

import os
import re
import sys
from pathlib import Path

SELF = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SELF))          # the kit

import archtest  # noqa: E402
from _check import check, finish, tmp_dir  # noqa: E402

TREE = {
    "top.py": "import pkg.mid\n",
    "lazy.py": "def f():\n    import leaf\n    return leaf\n",
    "cls.py": "class C:\n    import leaf\n",
    "lam.py": "g = lambda: __import__('leaf')\n",
    "leaf.py": "import json\nimport os.path\n",
    "chain.py": "import lazy\n",
    "reex.py": "from pkg import Thing\n",
    "pkg/__init__.py": "from .impl import Thing\n",
    "pkg/impl.py": "import leaf\nfrom . import mid\nThing = 1\n",
    "pkg/mid.py": "from .sub.deep import x\nimport pkg.missing\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/deep.py": "from ..impl import *\nimport subprocess\nx = 1\n",
    "cache/__pycache__/junk.py": "import nothing\n",
}


def tree(files: dict[str, str]) -> Path:
    root = Path(tmp_dir(prefix="tree-"))
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def main() -> int:
    root = tree(TREE)
    paths = archtest.modules_in(root)
    g = archtest.Graph(paths)

    print("module names")
    check("dotted from the folder; a package's __init__ is the package; __pycache__ skipped",
          sorted(paths) == ["chain", "cls", "lam", "lazy", "leaf", "pkg", "pkg.impl",
                            "pkg.mid", "pkg.sub", "pkg.sub.deep", "reex", "top"], sorted(paths))
    check("a package prefix names them from it",
          "app.pkg.impl" in archtest.modules_in(root, "app"))

    print("eager and lazy imports")
    check("module level is eager, and loads the parent packages too",
          g.direct("top", lazy=False) == {"pkg", "pkg.mid"}, g.direct("top", lazy=False))
    check("inside a function is lazy", g.direct("lazy", lazy=False) == set()
          and g.direct("lazy") == {"leaf"}, g.direct("lazy"))
    check("a class body is eager", g.direct("cls", lazy=False) == {"leaf"})
    check("modules not ours are leaves, as named",
          g.direct("leaf") == {"json", "os", "os.path"}, g.direct("leaf"))

    print("relative imports and re-exports")
    check("`from . import mid` in pkg/impl.py is pkg.mid, and its package",
          g.direct("pkg.impl") == {"leaf", "pkg", "pkg.mid"}, g.direct("pkg.impl"))
    check("`from .impl import` in an __init__ counts from the package itself",
          g.direct("pkg") == {"pkg.impl"}, g.direct("pkg"))
    check("`from ..impl import *` in pkg/sub/deep.py is pkg.impl",
          g.direct("pkg.sub.deep") == {"pkg", "pkg.impl", "subprocess"}, g.direct("pkg.sub.deep"))
    check("`from pkg import Thing` also loads the module pkg re-exports it from",
          g.direct("reex") == {"pkg", "pkg.impl"}, g.direct("reex"))
    check("an import of ours that does not exist is dropped, not taken for a library",
          g.direct("pkg.mid") == {"pkg", "pkg.sub", "pkg.sub.deep"}, g.direct("pkg.mid"))

    print("reach")
    check("through any chain of eager imports, externals included",
          {"pkg.sub.deep", "subprocess", "pkg.impl", "leaf", "json"} <= g.reach("top"), g.reach("top"))
    check("a module's own lazy import counts, with what it loads eagerly",
          g.reach("lazy") == {"leaf", "json", "os", "os.path"}, g.reach("lazy"))
    check("...unless lazy=False", g.reach("lazy", lazy=False) == set())
    check("a lazy import further down the chain does not count",
          g.reach("chain") == {"lazy"}, g.reach("chain"))

    print("rules")
    R = archtest.Rule
    res = dict(archtest.evaluate(g, [
        R("x", "top reaches no subprocess", layer=("top",), bad=("subprocess",)),
        R("y", "no lazy leaf", layer=("l*", "!leaf"), bad=("leaf",), lazy=False),
        R("z", "nothing matches", layer=("gone_*",), bad=("leaf",)),
        R("w", "only pkg.mid imports pkg.sub.deep", layer=("pkg.sub.deep",),
          importers=frozenset({"pkg.mid"})),
        R("v", "only top imports pkg.mid", layer=("pkg.mid",), importers=frozenset({"top"})),
        R("u", "only chain imports the renamed", layer=("renamed",), importers=frozenset({"chain"})),
    ]))
    check("a forbidden module reached through a chain is named",
          res["x: top: top reaches no subprocess"] == ["subprocess"], res)
    check("`!glob` leaves a module out; lazy=False allows a lazy import",
          "y: leaf: no lazy leaf" not in res and res["y: lazy: no lazy leaf"] == []
          and res["y: lam: no lazy leaf"] == [], res)
    check("a layer that matches no module fails the rule",
          res["z: the layer gone_* has modules"] == ["no module matches"], res)
    check("importers: the exact set holds",
          res["w: only pkg.mid imports pkg.sub.deep"] == [], res)
    check("importers: one more importer fails, and is named",
          "pkg.impl" in res["v: only top imports pkg.mid"][0], res)
    check("importers: a renamed target fails twice, never passes empty",
          res["u: the layer renamed has modules"] and res["u: only chain imports the renamed"], res)

    print("text rules")
    src = tree({"core/a.py": "x = 1\n# see webconsole/pages.py\n", "core/b.md": "fine\n"})
    S = archtest.Scan
    pat = re.compile(r"\bwebconsole\b")
    res = dict(archtest.scan(src, [
        S("t", "names it", ("core",), pat, hits=("webconsole/x",), misses=("console",)),
        S("b", "broken", ("core",), re.compile(r"\bwebkonsole\b"), hits=("webconsole/x",)),
        S("o", "over-wide", ("core",), re.compile(r"console"), hits=("webconsole",), misses=("console",)),
        S("m", "required", ("nothere",), pat, hits=("webconsole",)),
        S("p", "armed", ("nothere",), pat, hits=("webconsole",), optional=True),
        S("e", "no samples", ("core",), pat, hits=()),
    ]))
    check("a hit is named as file:line", res["t: names it"] == ["core/a.py:2"], res)
    check("a pattern that misses its sample fails before it can pass on nothing",
          res["b: the pattern catches its samples and only them"] == ["misses 'webconsole/x'"], res)
    check("a pattern that catches what it must leave alone fails",
          res["o: the pattern catches its samples and only them"] == ["catches 'console'"], res)
    check("a rule with no sample fails", res["e: the pattern catches its samples and only them"], res)
    check("an absent folder fails a required rule",
          res["m: nothere holds files to read"] == ["nothing to read"], res)
    check("...and passes an optional one, armed for when it lands",
          "p: nothere holds files to read" not in res and res["p: armed"] == [], res)

    print("verbs(): shell text and argv")
    w = archtest.verbs("tool", "facts set|init;queue reject", "pull|execute")
    for text in ("tool facts set k 1", "uv run src/tool.py pull ads", "tool execute",
                 "run([T, 'queue', 'reject', q])", '("facts", "init")', '[T, "pull", "ads"]',
                 '[T, "execute"]'):
        check(f"catches {text}", bool(w.search(text)))
    for text in ("tool facts list --json", "tool facts settle", '("facts", "get")',
                 "the tool pulls data", '{"execute": 1}'):
        check(f"leaves {text} alone", not w.search(text))
    return finish()


if __name__ == "__main__":
    sys.exit(main())
