"""post-install → post-upgrade → pre-uninstall on a fake host: exactly the
manifest's scheduler tasks, idempotent, healed on drift, a task dropped from
the manifest removed, prompts fully rendered and runnable as written.

Run: python3 hosts/zylos/tests/test_install.py
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import PLACEHOLDER_LEFT, Host, example, wait_for  # noqa: E402

M = example()
NAMES = [t["name"] for t in M["tasks"]]
STOCK, DAILY, STORIES = NAMES
KEYS = {"SHOP_API_KEY": "api-SECRET-1"}
REPLY = {"SHOP_REPLY_CHANNEL": "lark", "SHOP_REPLY_ENDPOINT": "oc_owner"}


def rendered(host):
    """{task name: prompt} as lib.js renders them on this host."""
    return host.lib("console.log(JSON.stringify(Object.fromEntries("
                    "h.M.tasks.map((t) => [t.name, h.renderPrompt(t.prompt)]))))")


def installed(host, **extra):
    host.configure({**KEYS, **REPLY})
    r = host.hook("post-install.js", **extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_post_install_registers_exactly_the_manifest_tasks():
    with Host(link=True) as host:
        r = installed(host)
        out = r.stdout + r.stderr
        tasks = host.tasks()
        assert sorted(t["name"] for t in tasks) == sorted(NAMES), tasks
        assert out.count("registered (task-") == len(NAMES), out
        want = rendered(host)
        for spec in M["tasks"]:
            (t,) = host.named(spec["name"])
            assert t["cron_expression"] == spec["cron"] and t["miss_threshold"] == spec["miss_threshold"], t
            assert t["reply_channel"] == "lark" and t["reply_endpoint"] == "oc_owner", t
            assert t["prompt"] == want[spec["name"]], t["prompt"]
            assert not re.search(PLACEHOLDER_LEFT, t["prompt"]), t["prompt"]
            assert f"'{host.link}'" in t["prompt"] or "{cli}" not in spec["prompt"], t["prompt"]
        assert "SHOP-EXIT" in host.named(DAILY)[0]["prompt"]
        assert f"'{os.path.join(host.data, 'logs', 'pull-orders.log')}'" in host.named(DAILY)[0]["prompt"]
        assert "no reply channel" not in out
        state = json.load(open(os.path.join(host.data, ".zylos-tasks.json")))
        assert state == {"tasks": sorted(NAMES)}, state
        assert "SECRET" not in out


def test_post_install_prepares_the_runtime_and_prints_the_next_steps():
    with Host(link=True) as host:
        r = installed(host)
        out = r.stdout + r.stderr
        calls = host.uv_calls()
        args = [c[3] for c in calls]
        entry = os.path.join(host.skill, "scripts", "shop.py")
        assert calls and all(c[0] == host.data and c[1] == os.path.join(host.data, ".pycache")
                              and c[2] == host.data for c in calls), calls
        assert "python find 3.12" in args and f"run --no-project {entry} --help" in args, args
        assert f"run --no-project {entry} doctor" in args and "0 warning(s)" in out, out
        assert "doctor-install.log" in out and os.path.isfile(os.path.join(host.logs, "doctor-install.log"))
        assert "UNREACHABLE" in out, "an unreachable endpoint is a soft note"
        assert f"no declared_markets declared yet. Next, with the owner: '{host.link}' facts init" in out, out
        assert f"run the harness as: '{host.link}' <verb>" in out, out
        assert "zylos-core 0.8.1 (minimum 0.7.1)" in out, out
        assert oct(os.stat(host.data).st_mode & 0o777) == "0o700"
        host.reset_uv_log()
        r = host.hook("post-install.js", FAKE_UV_SCOPES='"US", "CA"')
        assert "declared_markets: US, CA" in r.stdout and "facts init" not in r.stdout, r.stdout


def test_install_is_idempotent_and_upgrade_stays_light():
    with Host(link=True) as host:
        installed(host)
        before = host.tasks()
        n = {v: len(host.calls(v)) for v in ("add", "update", "remove")}
        r = host.hook("post-install.js")
        assert r.returncode == 0 and host.tasks() == before, r.stdout
        assert {v: len(host.calls(v)) for v in n} == n, "a second post-install changes nothing"
        assert r.stdout.count("up to date") == len(NAMES), r.stdout
        host.reset_uv_log()
        r = host.hook("post-upgrade.js")
        args = [c[3] for c in host.uv_calls()]
        assert r.returncode == 0 and host.tasks() == before and {v: len(host.calls(v)) for v in n} == n
        assert not any("doctor" in a or "facts" in a for a in args) and "UNREACHABLE" not in r.stdout, args
        assert "skipped: endpoint, doctor and scope checks" in r.stdout, r.stdout


def test_a_task_waits_for_the_keys_it_requires():
    with Host(link=True) as host:
        host.configure(REPLY)
        r = host.hook("post-install.js")
        assert [t["name"] for t in host.tasks()] == [STORIES], host.tasks()     # requires: []
        for name in (STOCK, DAILY):
            assert f"{name} not registered: set SHOP_API_KEY with configure" in r.stdout, r.stdout
        host.configure(KEYS)
        host.hook("post-install.js")
        assert sorted(t["name"] for t in host.tasks()) == sorted(NAMES)
        host.hook("post-install.js", SHOP_AUTH_ENV_PATHS="none")   # the key now resolves nowhere
        assert len(host.tasks()) == len(NAMES), "a registered task is not removed for a missing key"


def test_a_task_dropped_from_the_manifest_is_removed():
    with Host(link=True) as host:
        installed(host)
        foreign = {"id": "task-foreign", "name": "someone-elses-task", "prompt": "x"}
        host.seed(host.tasks() + [foreign])
        keep = {t["name"]: t["id"] for t in host.tasks()}
        m = copy.deepcopy(M)
        m["tasks"] = [t for t in m["tasks"] if t["name"] != STOCK]
        host.write_manifest(m)
        r = host.hook("post-upgrade.js")
        names = sorted(t["name"] for t in host.tasks())
        assert names == sorted([DAILY, STORIES, "someone-elses-task"]), names
        assert f"removed scheduler task {STOCK} ({keep[STOCK]}): no longer in zylos/manifest.json" in r.stdout, r.stdout
        assert all(host.named(n)[0]["id"] == keep[n] for n in (DAILY, STORIES)), "the others are kept as they were"
        assert json.load(open(os.path.join(host.data, ".zylos-tasks.json")))["tasks"] == sorted([DAILY, STORIES])
        n_remove = len(host.calls("remove"))
        host.hook("post-upgrade.js")
        assert len(host.calls("remove")) == n_remove, "nothing more to remove"


def test_a_failed_removal_of_a_dropped_task_is_retried():
    with Host(link=True) as host:
        installed(host)
        tasks = host.tasks()
        for t in tasks:
            if t["name"] == STOCK:
                t["sticky"] = True                    # the scheduler refuses to remove it
        host.seed(tasks)
        m = copy.deepcopy(M)
        m["tasks"] = [t for t in m["tasks"] if t["name"] != STOCK]
        host.write_manifest(m)
        r = host.hook("post-upgrade.js")
        assert r.returncode == 0 and f"could not remove {STOCK} task" in r.stdout, r.stdout
        assert STOCK in json.load(open(os.path.join(host.data, ".zylos-tasks.json")))["tasks"]
        host.seed([{**t, "sticky": False} for t in host.tasks()])
        r = host.hook("post-upgrade.js")
        assert not host.named(STOCK) and f"removed scheduler task {STOCK}" in r.stdout, r.stdout


def test_drift_and_duplicates_are_healed():
    with Host(link=True) as host:
        installed(host)
        (t,) = host.named(DAILY)
        old = {**t, "prompt": "old prompt", "cron_expression": "0 3 * * *", "miss_threshold": 300,
               "reply_channel": None, "reply_endpoint": None}
        others = [x for x in host.tasks() if x["name"] != DAILY]
        host.seed(others + [old, {**old, "id": "task-dup1"}, {**old, "id": "task-dup2"}])
        r = host.hook("post-upgrade.js")
        (k,) = host.named(DAILY)
        assert k["id"] == t["id"] and k["prompt"] == t["prompt"] and k["cron_expression"] == t["cron_expression"] \
            and k["miss_threshold"] == t["miss_threshold"] and k["reply_channel"] == "lark", k
        assert f"scheduler task {DAILY} updated" in r.stdout and r.stdout.count(f"removed duplicate {DAILY}") == 2
        assert [x for x in host.tasks() if x["name"] != DAILY] == others, "the other tasks untouched"
        n_update = len(host.calls("update"))
        host.hook("post-upgrade.js")
        assert len(host.calls("update")) == n_update, "a second run changes nothing"


def test_reply_channel_note_and_a_reply_set_by_hand():
    with Host(link=True) as host:
        host.configure(KEYS)
        r = host.hook("post-install.js")
        (t,) = host.named(STOCK)
        assert t["reply_channel"] is None
        assert f"{STOCK} has no reply channel" in r.stdout and "SHOP_REPLY_CHANNEL" in r.stdout \
            and f"update {t['id']} --reply-channel" in r.stdout, r.stdout
        host.seed([{**x, "reply_channel": "lark", "reply_endpoint": "oc_hand"} for x in host.tasks()])
        n_update = len(host.calls("update"))
        r = host.hook("post-upgrade.js")
        assert all(x["reply_endpoint"] == "oc_hand" for x in host.tasks()), "a reply set by hand is left alone"
        assert len(host.calls("update")) == n_update and "no reply channel" not in r.stdout, r.stdout


def test_prompts_run_as_written_from_the_agent_shell():
    with Host(link=True) as host:
        installed(host)
        entry = os.path.join(host.skill, "scripts", "shop.py")
        stock = host.named(STOCK)[0]["prompt"]
        host.reset_uv_log()
        first = stock.split("run ", 1)[1].split(", then ", 1)[0]
        r = host.sh(first)
        (call,) = host.uv_calls()
        assert r.returncode == 0 and call[3] == f"run --no-project {entry} pull stock", (first, call, r.stderr)
        assert call[0] == host.home and call[2] == host.data, "the caller's cwd; the data dir set by cli.js"

        daily = host.named(DAILY)[0]["prompt"]
        start = daily.split("Start the detached pull: ", 1)[1].split(". Then", 1)[0]
        r = host.sh(start)
        assert r.returncode == 0 and "pid " in r.stdout, (start, r.stderr)
        log = wait_for(os.path.join(host.logs, "pull-orders.log"), "SHOP-EXIT")
        assert log.strip().splitlines()[-1:] == ["SHOP-EXIT 0"], log
        assert any(c[3] == f"run --no-project {entry} pull orders" for c in host.uv_calls()), host.uv_calls()
        poll = daily.split("every 5 minutes run ", 1)[1].split(" until", 1)[0]
        r = host.sh(poll)
        assert r.returncode == 0 and "SHOP-EXIT 0" in r.stdout, (poll, r.stdout + r.stderr)
        ingest = daily.split("; then run ", 1)[1].split(". A log", 1)[0]
        host.reset_uv_log()
        r = host.sh(ingest)
        assert r.returncode == 0 and host.uv_calls()[-1][3] == f"run --no-project {entry} ingest orders"

        stories = host.named(STORIES)[0]["prompt"]
        listing = stories.split("run only these commands: ", 1)[1].split(", then for each", 1)[0]
        per = stories.split('"declared_markets": ', 1)[1].split(". Send", 1)[0]
        host.reset_uv_log()
        listed = host.sh(listing, FAKE_UV_SCOPES='"US", "CA"')
        for scope in json.loads(listed.stdout)["declared_markets"]:
            assert host.sh(per.replace("<M>", scope)).returncode == 0
        assert [c[3].split(f"{entry} ", 1)[1] for c in host.uv_calls()] == [
            "facts list --json", "compute stories --json --market US", "compute stories --json --market CA"]


def test_without_the_link_prompts_call_node_on_cli_js():
    with Host(link=False) as host:
        r = installed(host)
        cli = f"node '{os.path.join(host.adapter, 'bin', 'cli.js')}'"
        stock = host.named(STOCK)[0]["prompt"]
        assert stock.startswith(f"Hourly stock sync: run {cli} pull stock, then {cli} ingest stock."), stock
        assert f"Next, with the owner: {cli} facts init" in r.stdout, r.stdout
        host.reset_uv_log()
        r = host.sh(stock.split("run ", 1)[1].split(", then ", 1)[0])
        assert r.returncode == 0 and host.uv_calls()[-1][3].endswith("shop.py pull stock"), r.stderr
        # core links the bin later: the next post-upgrade moves every prompt to the link
        os.makedirs(os.path.dirname(host.link))
        os.symlink(os.path.join(host.adapter, "bin", "cli.js"), host.link)
        host.hook("post-upgrade.js")
        assert all(f"'{host.link}'" in t["prompt"] for t in host.tasks() if "{cli}" in
                   next(s["prompt"] for s in M["tasks"] if s["name"] == t["name"]))


def test_pre_uninstall_removes_every_managed_task_and_keeps_the_data():
    with Host(link=True) as host:
        installed(host)
        foreign = {"id": "task-foreign", "name": "someone-elses-task", "prompt": "x"}
        retired = {"id": "task-retired", "name": "shop-retired-task", "prompt": "y"}
        host.seed(host.tasks() + [foreign, retired])
        host.write(os.path.join(host.data, ".zylos-tasks.json"),
                   json.dumps({"tasks": NAMES + ["shop-retired-task"]}))
        r = host.hook("pre-uninstall.js", ZYLOS_COMPONENT=host.name, ZYLOS_SKILL_DIR=host.skill,
                      ZYLOS_DATA_DIR=host.data)
        assert r.returncode == 0 and host.tasks() == [foreign], r.stdout + r.stderr
        for n in NAMES + ["shop-retired-task"]:
            assert f"removed scheduler task {n}" in r.stdout, r.stdout
        assert os.path.isfile(os.path.join(host.data, ".env")) and "data dir left as is" in r.stdout


def test_every_placeholder_renders_shell_quoted():
    with Host(link=True) as host:            # the host's paths hold a space
        tpl = "{cli}|{node}|{detach}|{data_dir}|{skill_dir}|{logs}|{log:pull-a}|{exit}"
        got = host.lib(f"console.log(JSON.stringify(h.renderPrompt({json.dumps(tpl)})))").split("|")
        detach = os.path.join(host.adapter, "bin", "detach.js")
        assert got == [f"'{host.link}'", "node", f"node '{detach}'", f"'{host.data}'", f"'{host.skill}'",
                       f"'{host.logs}'", f"'{os.path.join(host.logs, 'pull-a.log')}'", "SHOP-EXIT"], got
        words = [host.link, host.data, host.skill, host.logs, os.path.join(host.logs, "pull-a.log")]
        r = host.sh("printf '%s\\n' " + " ".join(got[i] for i in (0, 3, 4, 5, 6)))
        assert r.stdout.splitlines() == words, "each value is one shell word, as written"
        r = host.node("-e", "const h=require(process.argv[1]);"
                      "try { h.renderPrompt('run {cli} {nope}', 'tasks[9].prompt') } "
                      "catch (e) { console.log(e.name + ': ' + e.message) }", os.path.join(host.adapter, "lib.js"))
        assert r.stdout.strip() == "ManifestError: zylos/manifest.json: tasks[9].prompt: unresolved placeholder {nope}", r.stdout
        left = host.lib("console.log(JSON.stringify(h.unresolved('a {cli} {x:y} {\"json\": 1} {log:ok}')))")
        assert left == ["{x:y}"], left


def test_a_missing_cli_entry_is_a_hard_failure():
    with Host(link=True) as host:
        host.configure(KEYS)
        os.remove(os.path.join(host.skill, "scripts", "shop.py"))
        r = host.hook("post-install.js")
        assert r.returncode == 1 and "manifest cli.entry" in r.stderr and "fix by hand" in r.stderr, r.stderr
        assert not host.uv_calls() and not host.tasks()


if __name__ == "__main__":
    _t.main(globals())
