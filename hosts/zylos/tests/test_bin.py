"""zylos/bin: cli.js (the linked harness CLI), detach.js (long jobs) and
console.js (the pm2 console service, run from the root ecosystem.config.cjs),
including one run of the real playbook console behind a Caddy-style proxy
that keeps the public Host.

Run: python3 hosts/zylos/tests/test_bin.py
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import FAKE_UV_ARGV, FAKE_UV_PYTHON, NODE, Host, example  # noqa: E402

M = example()


# --- cli.js ------------------------------------------------------------------------

def test_cli_runs_the_entry_with_the_data_dir():
    with Host(uv=FAKE_UV_ARGV, link=True) as host:
        path = f"{host.bin}:{os.path.dirname(NODE)}:/usr/bin:/bin"

        def cli(*args, **extra):
            r = subprocess.run([host.link, *args], capture_output=True, text=True, cwd=host.home,
                               timeout=30, env=host.env(PATH=path, **extra))
            return r, host.argv_seen()

        r, seen = cli("compute", "report", "--json", "a b", "it's")
        assert r.returncode == 0 and seen[2:] == [
            "ARG=run", "ARG=--no-project", "ARG=" + os.path.join(host.skill, "scripts", "shop.py"),
            "ARG=compute", "ARG=report", "ARG=--json", "ARG=a b", "ARG=it's"], r.stderr + repr(seen)
        assert seen[:2] == [f"DATA={host.data}", f"CACHE={os.path.join(host.data, '.pycache')}"], seen
        r, seen = cli("doctor", SHOP_DATA_DIR="/elsewhere")
        assert seen[0] == "DATA=/elsewhere", "a data dir the caller set is kept"
        r, _ = cli("doctor", FAKE_UV_EXIT="7")
        assert r.returncode == 7, "the CLI's exit status passes through"
        r, _ = cli("doctor", FAKE_UV_SIGNAL="TERM")
        assert r.returncode == 128 + 15, r.returncode
        os.remove(host.uv)
        r = host.node(host.link, "doctor", PATH="/usr/bin:/bin")
        assert r.returncode == 127 and "could not run uv" in r.stderr and "post-install.js" in r.stderr, r.stderr


# --- detach.js -----------------------------------------------------------------------

def running(pid):
    """Alive and not a zombie (exited, unreaped: an init that never reaps keeps them)."""
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:  # gone, or not Linux
        return subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0


def wait_marker(path, marker="SHOP-EXIT"):
    for _ in range(80):
        text = open(path).read() if os.path.exists(path) else ""
        if marker in text:
            return text
        time.sleep(0.1)
    return text


def test_detach_runs_a_job_in_its_own_session_with_a_job_file_and_lock():
    with Host() as host:
        detach = os.path.join(host.adapter, "bin", "detach.js")
        job_file = os.path.join(host.logs, "job.pid")
        log_file = os.path.join(host.logs, "job.log")

        def start(*cmd, **extra):
            return host.node(detach, "job", "--", *cmd, **extra)

        r = start("sh", "-c", "echo started; pwd; echo \"$SHOP_DATA_DIR\"; sleep 2; exit 3")
        job = json.load(open(job_file)) if os.path.exists(job_file) else {}
        pid = job.get("pid", 0)
        assert r.returncode == 0 and pid > 0 and job.get("start") and job_file in r.stdout \
            and log_file in r.stdout, r.stdout + r.stderr
        assert job.get("cmd") == ["sh", "-c", "echo started; pwd; echo \"$SHOP_DATA_DIR\"; sleep 2; exit 3"]
        assert os.getsid(pid) == pid != os.getsid(0), "its own session"
        r2 = start("true")
        assert r2.returncode != 0 and "already running" in r2.stderr, r2.stderr
        lines = wait_marker(log_file).strip().splitlines()
        assert lines[:3] == ["started", host.data, host.data] and lines[-1] == "SHOP-EXIT 3", lines

        # a pid alive but not ours (reused after a reboot): another start time
        with open(job_file, "w") as fh:
            json.dump({"pid": os.getpid(), "start": "proc:1", "cmd": ["x"]}, fh)
        r3 = start("true")
        assert r3.returncode == 0 and wait_marker(log_file).strip() == "SHOP-EXIT 0", r3.stderr
        # EPERM (another user's live process, e.g. pid 1) counts as alive
        start1 = host.lib("console.log(JSON.stringify(h.procStart(1)))")
        with open(job_file, "w") as fh:
            json.dump({"pid": 1, "start": start1, "cmd": ["x"]}, fh)
        r4 = start("true")
        assert r4.returncode != 0 and "already running" in r4.stderr, r4.stderr
        os.remove(job_file)

        lock = os.path.join(host.logs, "job.lock")
        open(lock, "w").close()
        r5 = start("true")
        assert r5.returncode != 0 and "being started" in r5.stderr, r5.stderr
        old = time.time() - 120
        os.utime(lock, (old, old))
        r6 = start("true")
        assert r6.returncode == 0 and not os.path.exists(lock), "a stale lock is cleared; the lock is released"
        wait_marker(log_file)
        r7 = host.node(detach, "job")
        assert r7.returncode == 2 and "usage" in r7.stderr and "`shop`" in r7.stderr, r7.stderr


def test_detach_alias_is_the_harness_cli_and_pre_uninstall_stops_jobs():
    with Host() as host:
        detach = os.path.join(host.adapter, "bin", "detach.js")
        r = host.node(detach, "pull-orders", "--", "shop", "pull", "orders", "--days", "30")
        assert r.returncode == 0, r.stderr
        log = wait_marker(os.path.join(host.logs, "pull-orders.log"))
        entry = os.path.join(host.skill, "scripts", "shop.py")
        assert log.strip().endswith("SHOP-EXIT 0") and host.uv_calls()[-1][3] == \
            f"run --no-project {entry} pull orders --days 30", (log, host.uv_calls())
        assert json.load(open(os.path.join(host.logs, "pull-orders.pid")))["cmd"][:2] == [
            host.lib("console.log(JSON.stringify(process.execPath))"), os.path.join(host.adapter, "bin", "cli.js")]

        r = host.node(detach, "long", "--", "sleep", "30")
        job = json.load(open(os.path.join(host.logs, "long.pid")))
        r2 = host.hook("pre-uninstall.js", ZYLOS_DATA_DIR=host.data)
        time.sleep(0.3)
        gone = not running(job["pid"])
        assert r.returncode == 0 and r2.returncode == 0 and gone and "stopped detached job long" in r2.stdout \
            and "stopped detached job pull-orders" not in r2.stdout, r2.stdout + r2.stderr


def test_a_zombie_job_is_not_alive():
    # Under an init that never reaps (a container without --init), a finished
    # job stays a zombie: it must neither block its re-run nor be "stopped".
    with Host() as host:
        child = subprocess.Popen(["true"])
        for _ in range(50):
            if not running(child.pid):
                break
            time.sleep(0.05)
        try:
            assert host.lib(f"console.log(JSON.stringify([h.pidAlive({os.getpid()}), h.pidAlive({child.pid})]))") \
                == [True, False]
        finally:
            child.wait()


# --- console.js and the ecosystem ------------------------------------------------------

def test_the_ecosystem_runs_console_js_as_the_service():
    with Host() as host:
        r = host.node("-e", "console.log(JSON.stringify(require(process.argv[1])))",
                      os.path.join(host.skill, "ecosystem.config.cjs"), cwd=host.home)
        apps = json.loads(r.stdout or "{}").get("apps") or [{}]
        (app,) = apps
        assert app["name"] == "zylos-shop-harness" and app["script"] == os.path.join(host.adapter, "bin", "console.js") \
            and app["cwd"] == host.data and app["out_file"] == os.path.join(host.logs, "console.log") \
            and app["autorestart"] is True, apps


def serve_argv(host, **extra):
    host.reset_uv_log()
    os.makedirs(host.data, exist_ok=True)
    r = host.node(os.path.join(host.adapter, "bin", "console.js"), cwd=host.data, **extra)
    return r, host.argv_seen()


def test_console_js_starts_the_playbook_console_with_the_manifest_flags():
    with Host(uv=FAKE_UV_ARGV) as host:
        r, seen = serve_argv(host)
        node = host.lib("console.log(JSON.stringify(process.execPath))")
        relay = f"'{node}' '{os.path.join(host.adapter, 'bin', 'cli.js')}'"
        assert r.returncode == 0 and seen[2:] == [
            "ARG=run", "ARG=--no-project", "ARG=" + os.path.join(host.skill, "console", "serve.py"),
            "ARG=--dir", f"ARG={os.path.join(host.data, 'console')}", "ARG=--host", "ARG=127.0.0.1",
            "ARG=--port", "ARG=8767", "ARG=--user-header", "ARG=X-Remote-User", "ARG=--title", "ARG=shop-harness",
            "ARG=--relay-cmd", f"ARG={relay}",
            "ARG=--relay-verbs", "ARG=facts confirm,decisions confirm", "ARG=--lang", "ARG=en"], r.stderr + repr(seen)
        assert seen[:2] == [f"DATA={host.data}", f"CACHE={os.path.join(host.data, '.pycache')}"], seen
        assert oct(os.stat(os.path.join(host.data, "console")).st_mode & 0o777) == "0o700"
        assert shlex.split(relay) == [node, os.path.join(host.adapter, "bin", "cli.js")], \
            "serve.py splits --relay-cmd like a shell"

        host.configure({"SHOP_CONSOLE_PORT": "9123", "SHOP_CONSOLE_TITLE": "Northwind shop",
                        "SHOP_CONSOLE_ALLOW_HOST": "agent.example.com, agent.example.com:8443"})
        r, seen = serve_argv(host)
        args = [s[4:] for s in seen[2:]]
        assert r.returncode == 0 and args[args.index("--port") + 1] == "9123" \
            and args[args.index("--title") + 1] == "Northwind shop" \
            and [args[i + 1] for i, a in enumerate(args) if a == "--allow-host"] == [
                "agent.example.com", "agent.example.com:8443"], args
        assert "--deciders" not in args and "--base-path" not in args and "--user" not in args, "the prefix is stripped by Caddy's handle_path"

        for bad, says in [({"SHOP_CONSOLE_PORT": "80"}, "SHOP_CONSOLE_PORT=80 is not a port"),
                          ({"SHOP_CONSOLE_PORT": "9x"}, "is not a port"),
                          ({"SHOP_CONSOLE_ALLOW_HOST": "https://x.example.com/"}, "is not a HOST or HOST:PORT")]:
            r, seen = serve_argv(host, **bad)
            assert r.returncode == 2 and says in r.stderr and not seen, (bad, r.stderr)
        r, _ = serve_argv(host, FAKE_UV_EXIT="3")
        assert r.returncode == 3, "exits with the console's status (pm2 restarts it)"


def test_console_js_without_relay_verbs_runs_read_only():
    m = json.loads(json.dumps(M))
    m["console"]["args"] = ["--lang", "zh"]
    with Host(m, uv=FAKE_UV_ARGV) as host:
        r, seen = serve_argv(host)
        args = [s[4:] for s in seen[2:]]
        assert r.returncode == 0 and "--relay-cmd" not in args and args[-2:] == ["--lang", "zh"], args
    with Host(uv=None) as host:
        r, _ = serve_argv(host, PATH="/usr/bin:/bin")
        assert r.returncode == 127 and "uv not found" in r.stderr, r.stderr


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, host_header, user="alice"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": host_header, "X-Remote-User": user})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_the_real_console_answers_the_public_host_caddy_passes_through():
    """console.js → the vendored playbook console/serve.py (through a uv that
    runs this Python). Behind Caddy's handle_path the console sees the
    public Host: it must answer it (--allow-host), and refuse any other."""
    with Host(uv=FAKE_UV_PYTHON, console="playbook") as host:
        port = free_port()
        host.configure({"SHOP_CONSOLE_PORT": str(port), "SHOP_CONSOLE_ALLOW_HOST": "agent.example.com"})
        os.makedirs(host.data, exist_ok=True)
        p = subprocess.Popen([NODE, os.path.join(host.adapter, "bin", "console.js")], cwd=host.data,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=host.env())
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    if p.poll() is not None:
                        break
                    time.sleep(0.1)
            assert p.poll() is None, p.stdout.read()
            assert get(port, "agent.example.com") == 200, "the public Host Caddy keeps"
            assert get(port, f"127.0.0.1:{port}") == 200, "a local check"
            assert get(port, "evil.example") == 421, "any other Host"
            assert os.path.isfile(os.path.join(host.data, "console", "secret")), "the log lives in <data>/console"
        finally:
            p.terminate()
            try:
                out, _ = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                out, _ = p.communicate()
        assert p.returncode in (0, 128 + 15, -15), (p.returncode, out)


if __name__ == "__main__":
    _t.main(globals())
