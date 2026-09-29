#!/usr/bin/env python3
"""kit.paths: the data dir is required and never the cwd.

  * unset / blank <P>_DATA_DIR -> data_dir_unset, next naming the var;
  * a relative value -> data_dir_not_absolute (it would be cwd-relative);
  * `~` expands; <P>_DB overrides the DB (relative = under the data dir);
  * resolve() never raises; inside_repo() is the checkout and below only.
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import paths  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402


def main() -> int:
    cfg = _shop.use()
    data = _shop.data_dir()

    print("[1] the data dir")
    check("SHOP_DATA_DIR is the data dir", paths.data_dir() == data)
    for val in (None, "", "   "):
        if val is None:
            os.environ.pop("SHOP_DATA_DIR", None)
        else:
            os.environ["SHOP_DATA_DIR"] = val
        e = raises(paths.data_dir, HarnessError)
        check(f"SHOP_DATA_DIR={val!r}: data_dir_unset naming the var",
              e is not None and e.message.code == "data_dir_unset"
              and e.message.params == {"var": "SHOP_DATA_DIR"}
              and e.next[0].startswith("export SHOP_DATA_DIR=")
              and "shop doctor" in e.next, e)
    here = os.getcwd()
    os.chdir(tmp_dir("cwd-"))
    try:
        os.environ["SHOP_DATA_DIR"] = "client-data"
        e = raises(paths.data_dir, HarnessError)
        check("a relative data dir is refused, not resolved against the cwd",
              e is not None and e.message.code == "data_dir_not_absolute"
              and e.message.params == {"var": "SHOP_DATA_DIR",
                                       "value": "client-data"}, e)
    finally:
        os.chdir(here)
    home = Path(tmp_dir("home-"))
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    os.environ["SHOP_DATA_DIR"] = "~/clients/a"
    check("~ expands", paths.data_dir() == home / "clients" / "a")
    if saved_home is None:
        del os.environ["HOME"]
    else:
        os.environ["HOME"] = saved_home
    os.environ["SHOP_DATA_DIR"] = str(data)

    print("\n[2] the DB path")
    check("default: <DATA_DIR>/<db_file>",
          paths.db_path() == data / cfg.db_file == data / "shop.db")
    os.environ["SHOP_DB"] = str(data / "elsewhere.db")
    check("SHOP_DB absolute: used as is",
          paths.db_path() == data / "elsewhere.db")
    os.environ["SHOP_DB"] = "sub/x.db"
    check("SHOP_DB relative: under the data dir",
          paths.db_path() == data / "sub" / "x.db")
    del os.environ["SHOP_DB"]
    check("resolve(): both vars", paths.resolve() == {
        "SHOP_DATA_DIR": str(data), "SHOP_DB": str(data / "shop.db")})
    del os.environ["SHOP_DATA_DIR"]
    check("resolve() with nothing set: None, no exception",
          paths.resolve() == {"SHOP_DATA_DIR": None, "SHOP_DB": None})
    os.environ["SHOP_DATA_DIR"] = str(data)

    print("\n[3] inside_repo")
    root = cfg.root
    check("the checkout itself and anything below it",
          paths.inside_repo(root) and paths.inside_repo(root / "data" / "x"))
    check("a dir outside it", not paths.inside_repo(data))
    check("a sibling sharing the name prefix is outside",
          not paths.inside_repo(Path(str(root) + "-data")))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
