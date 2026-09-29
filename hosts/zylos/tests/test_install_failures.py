"""The hooks' hard and soft failures: the zylos-core version gate, the time
budget, the pinned uv installer, a scheduler that fails, and the console's
Caddy and Host warnings.

Run: python3 hosts/zylos/tests/test_install_failures.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import FAKE_UV, Host, example  # noqa: E402

KEYS = {"SHOP_API_KEY": "k"}


def test_an_old_zylos_core_is_refused_before_any_install_step():
    cases = [("0.7.0", False, "older than 0.7.1"), ("0.6.12", False, "older than 0.7.1"),
             ("0.7.1", True, "zylos-core 0.7.1"), ("v0.8.1\n", True, "zylos-core 0.8.1"),
             ("1.0.0", True, "zylos-core 1.0.0"), (None, True, "could not read the zylos-core version"),
             ("unknown", True, "could not read the zylos-core version")]
    for version, ok, says in cases:
        with Host(zylos_version=version) as host:
            host.configure(KEYS)
            r = host.hook("post-install.js")
            out = r.stdout + r.stderr
            if ok:
                assert r.returncode == 0 and says in out and host.tasks(), (version, out)
            else:
                assert r.returncode != 0 and says in r.stderr and "zylos upgrade --self" in r.stderr, (version, out)
                assert not host.uv_calls() and not host.tasks(), "nothing installed, nothing registered"
                r = host.hook("post-upgrade.js")
                assert r.returncode != 0 and says in r.stderr, "post-upgrade holds the same gate"


def test_the_time_budget_cuts_soft_steps_and_fails_hard_ones():
    with Host() as host:
        host.hook("post-install.js")      # warm up: a new script's first exec can be slow (macOS scans it)
        started = time.time()
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_BUDGET_MS="2500", FAKE_UV_SLEEP_ON="doctor")
        took = time.time() - started
        assert r.returncode == 0 and took < 5 and "skipped:" in r.stdout \
            and "doctor (time budget)" in r.stdout and "post-install.js" in r.stdout, f"{took:.1f}s {r.stdout}"
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_BUDGET_MS="2500",
                      FAKE_UV_NO_PYTHON="1", FAKE_UV_SLEEP_ON="python install")
        assert r.returncode != 0 and "budget" in r.stderr and "post-install.js" in r.stderr, r.stderr


def test_the_uv_installer_is_pinned_and_checked():
    with Host(uv=None) as host:
        marker = os.path.join(host.home, "installer-ran")
        installer = os.path.join(host.home, "install.sh")
        host.write(installer, f"""#!/bin/sh
echo "UV_NO_MODIFY_PATH=$UV_NO_MODIFY_PATH" > '{marker}'
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/uv" <<'EOF'
{FAKE_UV}EOF
chmod 755 "$HOME/.local/bin/uv"
""")
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_UV_INSTALLER=installer)
        assert r.returncode != 0 and "sha256" in r.stderr and not os.path.exists(marker), r.stderr
        sha = hashlib.sha256(open(installer, "rb").read()).hexdigest()
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_UV_INSTALLER=installer,
                      SHOP_ZYLOS_HOOK_UV_INSTALLER_SHA256=sha)
        assert r.returncode == 0 and open(marker).read().strip() == "UV_NO_MODIFY_PATH=1" \
            and f"uv: {os.path.join(host.home, '.local', 'bin', 'uv')}" in r.stdout, r.stdout + r.stderr
        got = host.lib("console.log(JSON.stringify([h.UV_INSTALLER, h.UV_SHA256, h.ENDPOINTS]))",
                       SHOP_ZYLOS_HOOK_TEST="0", SHOP_ZYLOS_HOOK_UV_INSTALLER=installer,
                       SHOP_ZYLOS_HOOK_UV_INSTALLER_SHA256=sha)
        m = example()
        assert got == [m["runtime"]["uv_installer_url"], m["runtime"]["uv_installer_sha256"], m["endpoints"]], \
            "without <PREFIX>_ZYLOS_HOOK_TEST=1 the overrides are ignored"
    with Host(uv=None) as host:
        r = host.hook("post-install.js")
        assert r.returncode != 0 and "FAILED" in r.stderr and "fix by hand" in r.stderr \
            and "install.sh" in r.stderr and not host.tasks(), r.stderr


def test_python_is_installed_only_when_missing():
    with Host() as host:
        host.hook("post-install.js")
        assert not any(c[3].startswith("python install") for c in host.uv_calls())
        host.reset_uv_log()
        r = host.hook("post-install.js", FAKE_UV_NO_PYTHON="1")
        assert r.returncode == 0 and any(c[3] == "python install 3.12" for c in host.uv_calls()), r.stdout
        r = host.hook("post-install.js", FAKE_UV_NO_PYTHON="1", FAKE_UV_FAIL_ON="python install")
        assert r.returncode != 0 and "uv python install 3.12 exited 1" in r.stderr, r.stderr
        r = host.hook("post-install.js", FAKE_UV_FAIL_ON="--help")
        assert r.returncode != 0 and "scripts/shop.py --help exited 1" in r.stderr, r.stderr


def test_scheduler_failures_are_notes_or_named_exits():
    with Host() as host:
        host.configure(KEYS)
        r = host.hook("post-install.js", FAKE_ADD_FAILS="1")
        assert r.returncode == 0 and "did not report a created task" in r.stdout and not host.tasks(), r.stdout
        r = host.hook("post-install.js", FAKE_LIST_FAILS="1")
        assert r.returncode == 0 and "list --json` failed" in r.stdout and not host.tasks(), r.stdout
        other = {"id": "task-other", "name": "something-else"}
        host.seed([other, {**other, "id": "task-stuck", "name": "shop-daily-sync", "sticky": True}])
        r = host.hook("pre-uninstall.js")
        assert r.returncode == 1 and "could not remove task-stuck" in r.stderr, r.stdout + r.stderr
        r = host.hook("pre-uninstall.js", FAKE_LIST_FAILS="1")
        assert r.returncode == 1 and "list --json" in r.stderr, r.stderr
        assert os.path.isfile(os.path.join(host.data, ".env")), "the data dir is still there"
    with Host(scheduler=False) as host:
        host.configure(KEYS)
        r = host.hook("post-install.js")
        assert r.returncode == 0 and "no scheduler skill" in r.stdout, r.stdout
        assert not os.path.exists(os.path.join(host.data, ".zylos-tasks.json"))
        r = host.hook("pre-uninstall.js")
        assert r.returncode == 0 and "no scheduler skill" in r.stdout, r.stdout


def test_skip_uv_is_test_only():
    with Host() as host:
        host.configure(KEYS)
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_SKIP_UV="1")
        assert r.returncode == 0 and not host.uv_calls() and len(host.tasks()) == 3, r.stdout + r.stderr
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_SKIP_UV="1", SHOP_ZYLOS_HOOK_TEST="0")
        assert host.uv_calls(), "ignored without SHOP_ZYLOS_HOOK_TEST=1"


def test_console_warnings_caddy_block_and_public_host():
    marker = "# BEGIN zylos-component:shop-harness"
    cases = [("no users file", None, "", None, False, False),
             ("users, block present, host set", "alice $2a$hash\n", f"x {{\n    {marker}\n}}\n", "shop.example.com", False, False),
             ("users, block missing", "alice $2a$hash\n", "x {\n}\n", "shop.example.com", True, False),
             ("users, no Caddyfile", "alice $2a$hash\n", None, "shop.example.com", False, False),
             ("users, no public host", "alice $2a$hash\n", f"x {{\n    {marker}\n}}\n", None, False, True),
             ("empty users file", "\n", "x {\n}\n", None, False, False)]
    for label, users, caddy, public, warns_caddy, warns_host in cases:
        with Host(scheduler=False) as host:
            os.makedirs(host.data, exist_ok=True)
            if users is not None:
                host.write(os.path.join(host.data, "console.users"), users)
            if caddy is not None:
                host.write(os.path.join(host.zylos, "http", "Caddyfile"), caddy)
            if public:
                host.configure({"SHOP_CONSOLE_ALLOW_HOST": public})
            r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_SKIP_UV="1")
            said_caddy = "no route" in r.stdout and "lib.js' caddy" in r.stdout and "pm2 reload caddy" in r.stdout
            said_host = "answers no public Host" in r.stdout and "SHOP_CONSOLE_ALLOW_HOST=" in r.stdout
            assert r.returncode == 0 and said_caddy == warns_caddy and said_host == warns_host, (label, r.stdout)


def test_a_missing_console_entry_is_a_note():
    with Host() as host:
        os.remove(os.path.join(host.skill, "console", "serve.py"))
        r = host.hook("post-install.js", SHOP_ZYLOS_HOOK_SKIP_UV="1")
        assert r.returncode == 0 and "zylos-shop-harness) will not start" in r.stdout, r.stdout


if __name__ == "__main__":
    _t.main(globals())
