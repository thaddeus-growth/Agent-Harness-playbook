#!/usr/bin/env python3
"""kit.raw: raw only grows.

  * write-once: a new name is written; the same name with identical bytes
    is a no-op (same inode, same mtime); with different bytes it is
    refused (raw_would_overwrite) and the file is untouched, also when a
    concurrent writer claims the name between the check and the write;
  * import_file() twice = one raw file under <stem>.<sha256[:12]><suffix>;
  * save_json is canonical: equal objects = equal bytes;
  * names are plain components; temp files never show and never linger.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import raw  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402


def refusal(fn) -> str | None:
    e = raises(fn, HarnessError)
    return getattr(e.message, "code", None) if e else None


def main() -> int:
    _shop.use()
    data = _shop.data_dir()

    print("[1] write-once")
    p = raw.save("orders", "2026-09-01.json", b'{"n":1}')
    check("saved under <DATA_DIR>/raw/<source>/<name>",
          p == data / "raw" / "orders" / "2026-09-01.json"
          and p.read_bytes() == b'{"n":1}')
    st = p.stat()
    again = raw.save("orders", "2026-09-01.json", b'{"n":1}')
    st2 = again.stat()
    check("identical re-save: a no-op (same path, inode, mtime)",
          again == p and (st.st_ino, st.st_mtime_ns) == (st2.st_ino,
                                                         st2.st_mtime_ns))
    e = raises(lambda: raw.save("orders", "2026-09-01.json", b'{"n":2}'),
               HarnessError)
    check("different bytes, same name: raw_would_overwrite",
          e is not None and e.message.code == "raw_would_overwrite"
          and e.message.params == {"source": "orders",
                                   "name": "2026-09-01.json"}, e)
    check("… and the file on disk is untouched", p.read_bytes() == b'{"n":1}')

    real_link = raw.os.link

    def racing_link(src, dst):
        Path(dst).write_bytes(b"the other writer won")
        return real_link(src, dst)
    raw.os.link = racing_link
    try:
        code = refusal(lambda: raw.save("orders", "race.json", b"mine"))
    finally:
        raw.os.link = real_link
    check("a writer that claims the name first wins; the second is refused",
          code == "raw_would_overwrite"
          and (data / "raw/orders/race.json").read_bytes()
          == b"the other writer won", code)
    raw.os.link = racing_link
    try:
        same = raw.save("orders", "race2.json", b"the other writer won")
    finally:
        raw.os.link = real_link
    check("… unless it wrote the very same bytes (then a no-op)",
          same.read_bytes() == b"the other writer won")
    check("no temp file lingers after any of that",
          sorted(x.name for x in (data / "raw" / "orders").iterdir())
          == ["2026-09-01.json", "race.json", "race2.json"],
          sorted(os.listdir(data / "raw" / "orders")))

    print("\n[2] import_file(): same file twice = one raw file")
    src = Path(tmp_dir("export-")) / "Orders Export.csv"
    src.write_bytes(b"sku,units\nA,2\n")
    a = raw.import_file("orders_csv", src)
    b = raw.import_file("orders_csv", src)
    digest = hashlib.sha256(b"sku,units\nA,2\n").hexdigest()[:12]
    check("named <stem>.<sha256[:12]><suffix>",
          a.name == f"Orders Export.{digest}.csv", a.name)
    check("importing it twice leaves one raw file",
          a == b and raw.listing("orders_csv") == [a])
    src.write_bytes(b"sku,units\nA,3\n")
    c = raw.import_file("orders_csv", src)
    check("a changed export is a second file; the first stays",
          c != a and len(raw.listing("orders_csv")) == 2
          and a.read_bytes() == b"sku,units\nA,2\n")
    check("a missing export: raw_import_missing",
          refusal(lambda: raw.import_file("orders_csv", src.parent / "nope"))
          == "raw_import_missing")
    dot = src.parent / ".hidden.csv"
    dot.write_bytes(b"x")
    check("a dot-file export gets a plain name",
          not raw.import_file("orders_csv", dot).name.startswith("."))

    print("\n[3] save_json() is canonical")
    j1 = raw.save_json("api", "page1.json", {"b": [1, 2], "a": "价格"})
    j2 = raw.save_json("api", "page1.json", {"a": "价格", "b": [1, 2]})
    check("equal objects, any key order: one file, a no-op re-save",
          j1 == j2 and json.loads(j1.read_bytes()) == {"a": "价格", "b": [1, 2]}
          and j1.read_bytes() == '{"a":"价格","b":[1,2]}'.encode())
    check("a different object under that name is refused",
          refusal(lambda: raw.save_json("api", "page1.json", {"a": 1}))
          == "raw_would_overwrite")

    print("\n[4] names and listing")
    for label, fn in (
            ("a parent path", lambda: raw.save("orders", "../x", b"")),
            ("a separator", lambda: raw.save("orders", "a/b", b"")),
            ("a leading dot", lambda: raw.save("orders", ".x", b"")),
            ("an empty name", lambda: raw.save("orders", "", b"")),
            ("'..'", lambda: raw.save("orders", "..", b"")),
            ("a source with a separator", lambda: raw.save("a/b", "x", b""))):
        check(f"refused: {label} (raw_bad_name)", refusal(fn) == "raw_bad_name")
    (data / "raw" / "orders" / ".leftover.tmp").write_bytes(b"x")
    check("listing: sorted, temp/hidden files never shown",
          [x.name for x in raw.listing("orders")]
          == ["2026-09-01.json", "race.json", "race2.json"])
    check("save() takes bytes only",
          raises(lambda: raw.save("orders", "s.txt", "text"), TypeError)
          is not None)

    print("\n[5] no data dir, no raw")
    del os.environ["SHOP_DATA_DIR"]
    check("unset data dir: data_dir_unset, nothing written in the cwd",
          refusal(lambda: raw.save("orders", "x", b"")) == "data_dir_unset"
          and not Path("raw").exists())
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
