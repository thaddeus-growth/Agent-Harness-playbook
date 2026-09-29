#!/usr/bin/env python3
"""kit.atomic and kit.single_instance.

  * a write that fails mid-way leaves the target untouched and no temp
    file; a write that succeeds replaces it whole; JSON keeps non-ASCII;
  * hold(): the first holder gets True, a second (another open file, or
    another process) gets False without waiting; the lock goes with its
    holder; the lock file stays.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import atomic, single_instance  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402


def main() -> int:
    d = Path(tmp_dir("atomic-"))
    target = d / "raw.json"

    print("[1] atomic writes")
    atomic.write_json_atomic(target, {"a": "价格", "n": [1, 2]})
    check("write_json_atomic: the object, non-ASCII kept",
          json.loads(target.read_text("utf-8")) == {"a": "价格", "n": [1, 2]}
          and "价格" in target.read_text("utf-8"))

    def crash():
        with atomic.open_atomic(target) as f:
            f.write('{"half": ')
            raise OSError("disk full")
    check("a failure mid-write propagates", raises(crash, OSError) is not None)
    check("… the target is untouched and no temp file is left",
          json.loads(target.read_text("utf-8"))["a"] == "价格"
          and sorted(os.listdir(d)) == ["raw.json"], os.listdir(d))
    atomic.write_bytes_atomic(target, b"\x00bytes")
    check("write_bytes_atomic replaces it whole",
          target.read_bytes() == b"\x00bytes" and os.listdir(d) == ["raw.json"])
    with atomic.open_atomic(str(d / "t.txt")) as f:
        f.write("text")
    check("open_atomic takes a str path too", (d / "t.txt").read_text() == "text")
    check("two temp names for one path differ",
          atomic._tmp_for(target) != atomic._tmp_for(target)
          and atomic.is_temp(atomic._tmp_for(target)))
    mode = (d / "t.txt").stat().st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    check("the file gets the umask's mode, like a plain open()",
          mode == 0o666 & ~umask, oct(mode))

    print("\n[2] single_instance.hold")
    lock = d / "locks" / "run.lock"
    with single_instance.hold(lock) as first:
        with single_instance.hold(str(lock)) as second:
            check("first holder True, a second open False (no wait)",
                  first is True and second is False)
        r = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, %r)\n"
             "from kit import single_instance\n"
             "with single_instance.hold(%r) as ok: print(ok)"
             % (str(_shop.PLAYBOOK), str(lock))],
            capture_output=True, text=True)
        check("another process gets False while we hold it",
              r.stdout.strip() == "False", r)
    with single_instance.hold(lock) as again:
        check("released on exit: the next holder gets True", again is True)
    check("the lock file stays (and its parent dir was created)",
          lock.is_file())
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
