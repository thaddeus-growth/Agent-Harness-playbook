#!/usr/bin/env python3
"""kit/tools/manifest.py: a vendored copy's fingerprint; drift is detected.

  * MANIFEST.sha256 = `<sha256>  <relpath>` per file, sorted, leaving out
    the top-level tests/, __pycache__, *.pyc, .DS_Store and itself;
  * check() is clean right after write(), and names a changed file, a
    missing file, an added file and an edited VERSION;
  * the CLI: write, and --check exiting 1 on drift, 0 when clean;
  * write() is idempotent; the real kit/ manifest covers the kit, not its
    tests;
  * the playbook's kit/MANIFEST.sha256 is fresh (this test fails while it
    is stale), and a vendored copy's manifest equals it.
"""

import hashlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit.testing.check import capture, check, finish, tmp_dir  # noqa: E402
from kit.tools import manifest, vendor  # noqa: E402


def tree() -> Path:
    d = Path(tmp_dir("vendored-"))
    for rel, body in {"VERSION": "0.1.0\n", "a.py": "A = 1\n",
                      "sub/b.py": "B = 2\n", "tests/t.py": "T\n",
                      "sub/tests/kept.txt": "kept\n",
                      "__pycache__/a.cpython-312.pyc": "x",
                      "sub/c.pyc": "x", ".DS_Store": "x"}.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    return d


def main() -> int:
    print("[1] what the manifest covers")
    d = tree()
    path = manifest.write(d)
    lines = path.read_text().splitlines()
    rels = [ln.split("  ", 1)[1] for ln in lines]
    check("sorted '<sha256>  <relpath>' lines",
          rels == sorted(rels) and all(len(ln.split("  ")[0]) == 64
                                       for ln in lines), lines)
    check("covers VERSION and code, nested tests/ dirs included",
          rels == ["VERSION", "a.py", "sub/b.py", "sub/tests/kept.txt"], rels)
    check("the sha256 is the file's",
          lines[1] == hashlib.sha256(b"A = 1\n").hexdigest() + "  a.py")

    print("\n[2] drift is detected")
    check("clean right after write()", manifest.check(d) == [])
    mtime = path.stat().st_mtime_ns
    manifest.write(d)
    check("write() again with no change leaves the file alone",
          path.stat().st_mtime_ns == mtime)
    (d / "a.py").write_text("A = 2  # a local edit\n")
    check("a local edit: changed", manifest.check(d) == ["changed: a.py"],
          manifest.check(d))
    (d / "a.py").write_text("A = 1\n")
    (d / "VERSION").write_text("0.1.1\n")
    check("an edited VERSION: changed",
          manifest.check(d) == ["changed: VERSION"])
    (d / "VERSION").write_text("0.1.0\n")
    (d / "sub" / "b.py").unlink()
    (d / "new.py").write_text("N = 1\n")
    check("a removed file and an added file are both named",
          manifest.check(d) == ["missing: sub/b.py",
                                "not in MANIFEST.sha256: new.py"],
          manifest.check(d))
    check("tests/, __pycache__ and .pyc changes are not drift",
          (d / "tests" / "t.py").write_text("changed") and
          [p for p in manifest.check(d) if "tests/t.py" in p or "pyc" in p]
          == [])
    empty = Path(tmp_dir("none-"))
    check("no manifest at all is drift",
          manifest.check(empty) == [f"{empty}: no MANIFEST.sha256"])
    (empty / "MANIFEST.sha256").write_text("not a manifest line\n")
    check("a malformed manifest is drift",
          "not '<sha256>  <path>'" in manifest.check(empty)[0])

    print("\n[3] the CLI")
    rc, out, _ = capture(manifest.main, ["--check", str(d)])
    check("--check on drift: exit 1, each difference printed",
          rc == 1 and "missing: sub/b.py" in out and "drift" in out, out)
    rc, out, _ = capture(manifest.main, [str(d)])
    check("write mode rewrites it", rc == 0 and manifest.check(d) == [])
    rc, out, _ = capture(manifest.main, ["--check", str(d)])
    check("--check clean: exit 0", rc == 0 and "matches" in out, out)
    rc, _, err = capture(manifest.main, [])
    check("no DIR: usage, exit 2", rc == 2 and "usage" in err)

    print("\n[4] the real kit/")
    covered = manifest.files(_shop.KIT)
    check("covers VERSION, messages.py, the registry; not tests/",
          {"VERSION", "messages.py", "message_codes.tsv",
           "tools/manifest.py"} <= set(covered)
          and not any(r.startswith("tests/") for r in covered)
          and not any("__pycache__" in r for r in covered))
    problems = manifest.check(_shop.KIT)
    check("the playbook's kit/MANIFEST.sha256 is fresh: it names exactly "
          "this version's files (regenerate: python3 kit/tools/manifest.py "
          "kit)", problems == [], problems)
    body = (_shop.KIT / manifest.NAME).read_text(encoding="utf-8")
    check("…and covers the README and VERSION",
          "  README.md\n" in body and "  VERSION\n" in body)
    copy = Path(tmp_dir("vendored-kit-")) / "kit"
    vendor.sync(_shop.KIT, copy, {})
    check("a vendored copy's manifest equals the playbook's",
          (copy / manifest.NAME).read_bytes()
          == (_shop.KIT / manifest.NAME).read_bytes())
    stale = Path(tmp_dir("stale-kit-")) / "kit"
    vendor.sync(_shop.KIT, stale, {})
    (stale / "README.md").write_text("edited\n", encoding="utf-8")
    check("the freshness check fails once a covered file changes",
          manifest.check(stale) == ["changed: README.md"],
          manifest.check(stale))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
