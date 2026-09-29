#!/usr/bin/env python3
"""kit/tools/vendor.py: plain copies of kit/ and console/ in a harness.

  * kit/ lands at <harness>/<scripts_dir>/kit/ and console/ at
    <harness>/console/ (+ VERSION), both without tests/, each with a
    MANIFEST.sha256 that checks clean, and `import kit` works from the
    copy;
  * a second run changes nothing and says so (idempotent);
  * a local edit is drift until the next vendor run restores it; a stray
    file in the copy is removed; --kit / --console / --dry-run scope it;
  * the playbook itself is refused as a target.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
import kit  # noqa: E402
from kit.testing.check import capture, check, finish, tmp_dir  # noqa: E402
from kit.tools import manifest, vendor  # noqa: E402


def snapshot(d: Path) -> dict[str, int]:
    return {str(p.relative_to(d)): p.stat().st_mtime_ns
            for p in d.rglob("*") if p.is_file()}


def main() -> int:
    h = Path(tmp_dir("harness-"))
    (h / "harness.toml").write_text('[harness]\nname = "v"\ncli = "v"\n'
                                    'env_prefix = "V"\n')

    print("[1] a first vendor run")
    rc, out, err = capture(vendor.main, ["--harness", str(h)])
    kd, cd = h / "scripts" / "kit", h / "console"
    check("exit 0; kit and console reported as added",
          rc == 0 and "kit: " in out and "added" in out
          and "console: " in out, (rc, out, err))
    check("kit copied to scripts/kit, without tests/",
          (kd / "messages.py").is_file() and (kd / "VERSION").is_file()
          and not (kd / "tests").exists())
    check("console copied, without tests/, with the kit's VERSION",
          (cd / "relay.py").is_file() and not (cd / "tests").exists()
          and (cd / "VERSION").read_text().strip() == kit.__version__)
    check("both manifests written and clean",
          manifest.check(kd) == [] and manifest.check(cd) == [])
    check("no __pycache__ copied",
          not any("__pycache__" in str(p) for p in h.rglob("*")))
    r = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); import kit; "
         "print(kit.__version__, kit.KIT_DIR)" % str(h / "scripts")],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "KIT_HARNESS_ROOT"})
    check("`import kit` resolves to the vendored copy",
          r.stdout.startswith(kit.__version__ + " " + str(kd.resolve())), r)

    print("\n[2] idempotent")
    before = snapshot(h)
    rc, out, _ = capture(vendor.main, ["--harness", str(h)])
    check("a second run: 'up to date' for both, no file touched",
          rc == 0 and out.count("up to date") == 2 and snapshot(h) == before,
          out)

    print("\n[3] drift and repair")
    (kd / "dates.py").write_text("# a local edit\n")
    (kd / "stray.py").write_text("# not from the playbook\n")
    check("a local edit is drift",
          manifest.check(kd) == ["changed: dates.py",
                                 "not in MANIFEST.sha256: stray.py"],
          manifest.check(kd))
    rc, out, _ = capture(vendor.main, ["--harness", str(h), "--dry-run"])
    check("--dry-run reports and writes nothing",
          "updated  scripts/kit/dates.py" in out
          and "removed  scripts/kit/stray.py" in out
          and (kd / "stray.py").exists() and manifest.check(kd) != [], out)
    rc, out, _ = capture(vendor.main, ["--harness", str(h), "--kit"])
    check("--kit restores the file, removes the stray, console untouched",
          rc == 0 and "updated  scripts/kit/dates.py" in out
          and "removed  scripts/kit/stray.py" in out and "console" not in out
          and manifest.check(kd) == [] and not (kd / "stray.py").exists(), out)

    print("\n[4] scripts_dir and refusals")
    h2 = Path(tmp_dir("harness-"))
    (h2 / "harness.toml").write_text('[harness]\nname = "w"\ncli = "w"\n'
                                     'env_prefix = "W"\nscripts_dir = "lib"\n')
    rc, out, _ = capture(vendor.main, ["--harness", str(h2), "--kit"])
    check("the kit goes under the harness's scripts_dir; --kit only",
          rc == 0 and (h2 / "lib" / "kit" / "raw.py").is_file()
          and not (h2 / "console").exists(), out)
    rc, _, err = capture(vendor.main, ["--harness", str(_shop.PLAYBOOK)])
    check("the playbook itself is refused", rc == 2 and "playbook" in err, err)
    rc, _, err = capture(vendor.main, ["--harness", str(h / "nope")])
    check("a missing harness dir is refused", rc == 2, err)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
