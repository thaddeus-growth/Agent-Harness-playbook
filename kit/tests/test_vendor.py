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

    print("\n[5] packs")
    check("with no [kit] packs every pack is vendored (as before packs)",
          (kd / "takes.py").is_file() and (kd / "retry.py").is_file()
          and (kd / "guards" / "ssot.py").is_file())
    (h / "harness.toml").write_text((h / "harness.toml").read_text()
                                    + '\n[kit]\npacks = ["data"]\n')
    rc, out, err = capture(vendor.main, ["--harness", str(h), "--kit"])
    check("[kit] packs = [\"data\"]: base, data and testkit stay; the others go",
          rc == 0 and "kit packs: base, data, testkit" in out
          and (kd / "facts.py").is_file() and (kd / "retry.py").is_file()
          and (kd / "testing" / "suites.py").is_file()
          and not (kd / "takes.py").exists() and not (kd / "copylint.py").exists(),
          (rc, out, err))
    check("a module's message codes go with it; the package's own files stay",
          not (kd / "message_codes.d" / "takes.tsv").exists()
          and (kd / "message_codes.d" / "facts.tsv").is_file()
          and (kd / "message_codes.tsv").is_file() and (kd / "packs.tsv").is_file())
    check("the smaller copy's manifest is clean", manifest.check(kd) == [])
    r = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r); from kit import facts, queue, "
         "execute, cli, doctor, stories, pending; from kit.testing import suites; "
         "from kit.guards import drift, evals; print('ok')" % str(h / "scripts")],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "KIT_HARNESS_ROOT"})
    check("base and testkit import without the packs left out",
          r.stdout.strip() == "ok", r.stderr)

    (h / "scripts" / "make_ad.py").write_text(
        "def run():\n    from kit import takes\n    from kit.copylint import lint\n")
    rc, _, err = capture(vendor.main, ["--harness", str(h), "--kit"])
    check("a harness file importing a left-out module is refused, named with its pack",
          rc == 2 and "scripts/make_ad.py: imports kit.copylint (pack compliance)" in err
          and "scripts/make_ad.py: imports kit.takes (pack generation)" in err, err)
    (h / "harness.toml").write_text((h / "harness.toml").read_text().replace(
        'packs = ["data"]', 'packs = ["data", "compliance", "generation"]'))
    rc, out, _ = capture(vendor.main, ["--harness", str(h), "--kit"])
    check("adding the packs it imports brings the modules back",
          rc == 0 and (kd / "takes.py").is_file() and (kd / "copylint.py").is_file()
          and manifest.check(kd) == [], out)
    (h / "harness.toml").write_text((h / "harness.toml").read_text().replace(
        '"generation"]', '"generation", "video"]'))
    rc, _, err = capture(vendor.main, ["--harness", str(h), "--kit"])
    check("an unknown pack is refused, naming the packs",
          rc == 2 and "unknown pack: video" in err and "compliance" in err, err)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
