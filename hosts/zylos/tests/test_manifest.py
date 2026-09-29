"""zylos/manifest.json: loaded once by lib.js, every key checked with a named
error; `node zylos/lib.js check` ties it to SKILL.md and the files it names.

Run: python3 hosts/zylos/tests/test_manifest.py
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import Host, example, skill_md  # noqa: E402

OPTIONAL = {"defaults", "console.allow_host_key"}


def key_paths(obj, base=""):
    """Every key path of the example ("cli.bin", "tasks[0].cron", …)."""
    out = []
    for k, v in obj.items():
        if k == "_comment":
            continue
        p = f"{base}.{k}" if base else k
        out.append(p)
        if isinstance(v, dict):
            out += key_paths(v, p)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            out += key_paths(v[0], f"{p}[0]")
    return out


def without(m, dotted):
    """A copy of manifest `m` with the key at `dotted` removed."""
    m = copy.deepcopy(m)
    parts = dotted.replace("[0]", ".0").split(".")
    node = m
    for p in parts[:-1]:
        node = node[int(p)] if p.isdigit() else node[p]
    del node[parts[-1]]
    return m


def check(host):
    return host.node(os.path.join(host.adapter, "lib.js"), "check")


def test_the_example_is_valid_and_checks_clean():
    with Host() as host:
        r = check(host)
        assert r.returncode == 0 and "check: ok" in r.stdout, r.stdout + r.stderr


def test_every_missing_key_is_named():
    m = example()
    paths = [p for p in key_paths(m) if p not in OPTIONAL]
    assert len(paths) > 35, paths      # the whole schema, not a sample
    with Host() as host:
        for p in paths:
            host.write_manifest(without(m, p))
            r = check(host)
            assert r.returncode == 1 and f"zylos/manifest.json: {p}: missing" in r.stderr, (p, r.stderr)
        for p in OPTIONAL - {"defaults"}:
            host.write_manifest(without(m, p))
            host.write(os.path.join(host.skill, "SKILL.md"), skill_md(without(m, p)))
            r = check(host)
            assert r.returncode == 0, (p, r.stdout + r.stderr)


def test_bad_values_are_named():
    m = example()

    def bad(change, key, words):
        mm = copy.deepcopy(m)
        change(mm)
        host.write_manifest(mm)
        r = check(host)
        assert r.returncode == 1 and f"zylos/manifest.json: {key}:" in r.stderr and words in r.stderr, \
            (key, r.stderr)

    with Host() as host:
        bad(lambda x: x.update(nmae="typo"), "nmae", "unknown key")
        bad(lambda x: x["console"].update(prot=1), "console.prot", "unknown key")
        bad(lambda x: x.update(name="Shop Harness"), "name", "component name")
        bad(lambda x: x["cli"].update(bin="shop"), "cli.bin", "<brand>-<cli>")
        bad(lambda x: x["cli"].update(entry="../x.py"), "cli.entry", "inside the skill dir")
        bad(lambda x: x["env"].update(data_dir="DATA_DIR"), "env.data_dir", "SHOP_")
        bad(lambda x: x["env"].update(files=[".shop.env"]), "env.files[0]", "absolute")
        bad(lambda x: x["runtime"].update(uv_installer_sha256="abc"), "runtime.uv_installer_sha256", "sha256")
        bad(lambda x: x.update(endpoints=["http://plain.example.com"]), "endpoints[0]", "https://")
        bad(lambda x: x["runtime"].update(uv_installer_url="https://uv.example.com/latest/install.sh"),
            "runtime.uv_installer_url", "must name uv_version")
        bad(lambda x: x["runtime"].update(uv_installer_url="install.sh"), "runtime.uv_installer_url", "https://")
        bad(lambda x: x["scope"].update(list=[]), "scope.list", "must not be empty")
        bad(lambda x: x["console"].update(port=80), "console.port", "1024 to 65535")
        bad(lambda x: x["console"].update(args=["--port", "9"]), "console.args[0]", "set by zylos/bin/console.js")
        bad(lambda x: x["console"].update(deciders_key="SHOP_CONSOLE_DECIDERS"), "console.deciders_key", "unknown key")   # retired
        bad(lambda x: x["detach"].update(alias="uv"), "detach.alias", "not uv")
        bad(lambda x: x["detach"].update(exit_marker="done"), "detach.exit_marker", "upper-case")
        bad(lambda x: x["tasks"][1].update(cron="daily"), "tasks[1].cron", "five-field")
        bad(lambda x: x["tasks"][1].update(name=x["tasks"][0]["name"]), "tasks[1].name", "used twice")
        bad(lambda x: x["tasks"][0].update(miss_threshold="4h"), "tasks[0].miss_threshold", "whole number")
        bad(lambda x: x["tasks"][0].update(requires=["api key"]), "tasks[0].requires[0]", "env var name")
        bad(lambda x: x["tasks"][2].update(prompt="run {cli_path} now"), "tasks[2].prompt", "{cli_path}")
        bad(lambda x: x["tasks"][2].update(prompt="tail {log}"), "tasks[2].prompt", "unknown placeholder {log}")
        bad(lambda x: x.update(defaults={"REGION": {"value": "us"}}), "defaults.REGION.when", "missing")
        bad(lambda x: x.update(tasks={}), "tasks", "JSON list")


def test_a_hook_refuses_a_broken_manifest_with_one_named_line():
    with Host() as host:
        host.write_manifest(without(example(), "tasks"))
        r = host.configure({"SHOP_API_KEY": "k"})
        assert r.returncode == 1, r.stderr
        assert "FAILED: zylos/manifest.json: tasks: missing" in r.stderr, r.stderr
        assert "Traceback" not in r.stderr and "    at " not in r.stderr, r.stderr   # no stack
        assert not os.path.exists(os.path.join(host.data, ".env"))
        r = host.hook("post-install.js")
        assert r.returncode == 1 and "tasks: missing" in r.stderr and not host.calls("add"), r.stderr
        host.write_manifest("{not json")
        r = host.hook("pre-uninstall.js")
        assert r.returncode == 1 and "(file): not JSON" in r.stderr, r.stderr
        os.remove(os.path.join(host.adapter, "manifest.json"))
        r = host.hook("post-upgrade.js")
        assert r.returncode == 1 and "(file): cannot read" in r.stderr and "manifest.example.json" in r.stderr, r.stderr
        r = host.node(os.path.join(host.adapter, "bin", "cli.js"), "doctor")
        assert r.returncode == 1 and "(file): cannot read" in r.stderr, r.stderr


def test_required_from_elsewhere_it_throws_and_boot_stays_safe():
    with Host() as host:
        host.write_manifest(without(example(), "cli.bin"))
        r = host.node("-e", "try { require(process.argv[1]) } catch (e) { console.log(JSON.stringify([e.name, e.key])) }",
                      os.path.join(host.adapter, "lib.js"))
        assert json.loads(r.stdout) == ["ManifestError", "cli.bin"], r.stdout + r.stderr
        # core's pm2 ecosystem loads the root file at boot: a bad manifest
        # must not take the other components' services down with it
        r = host.node("-e", "console.log(JSON.stringify(require(process.argv[1])))",
                      os.path.join(host.skill, "ecosystem.config.cjs"))
        assert r.returncode == 0 and json.loads(r.stdout) == {"apps": []}, r.stdout + r.stderr
        assert "cli.bin: missing" in r.stderr, r.stderr


def test_the_manifest_is_frozen_after_load():
    with Host() as host:
        got = host.lib("try { h.M.tasks.push({}); } catch {} console.log(JSON.stringify(h.M.tasks.length))")
        assert got == len(example()["tasks"])


def test_check_ties_skill_md_to_the_manifest():
    m = example()
    good = skill_md(m)
    cases = [
        ("an http_routes block", good.replace("config:\n", "http_routes:\n  - path: /shop\nconfig:\n"), "http_routes"),
        ("an upgrade block", good.replace("config:\n", "upgrade:\n  mode: x\nconfig:\n"), "upgrade block"),
        ("another bin name", good.replace("northwind-shop: zylos", "shop: zylos"), "SKILL.md bin must be exactly"),
        ("a second bin", good.replace("lifecycle:\n", "  other-cli: zylos/bin/cli.js\nlifecycle:\n"), "SKILL.md bin"),
        ("a wrong service name", good.replace("name: zylos-shop-harness", "name: shop"), "service.name must be zylos-shop-harness"),
        ("a lifecycle key core does not read", good.replace("  npm: false\n", "  npm: false\n  cron: x\n"), "lifecycle.cron"),
        ("a hook elsewhere", good.replace("configure: zylos/hooks/configure.js", "configure: hooks/c.js"), "hooks.configure"),
        ("an undeclared config key", good.replace("    - name: SHOP_REPLY_CHANNEL\n", "    - name: OTHER\n"), "declare SHOP_REPLY_CHANNEL"),
        ("a secret not sensitive", good.replace("      sensitive: true\n", ""), "SHOP_API_KEY looks secret"),
        ("next-steps without the extras", good.replace("10-minute", "long"), "next-steps must mention \"10-minute\""),
        ("a version that is not semver", good.replace("version: 0.1.0", "version: v0.1"), "plain X.Y.Z"),
        ("no frontmatter", "# no frontmatter\n", "no --- frontmatter"),
    ]
    with Host() as host:
        for label, text, says in cases:
            host.write(os.path.join(host.skill, "SKILL.md"), text)
            r = check(host)
            assert r.returncode == 1 and says in r.stderr, (label, r.stderr)
        host.write(os.path.join(host.skill, "SKILL.md"), good)
        os.remove(os.path.join(host.skill, "ecosystem.config.cjs"))
        os.remove(os.path.join(host.skill, m["console"]["entry"]))
        r = check(host)
        assert r.returncode == 1 and "ecosystem.config.cjs at the skill root" in r.stderr \
            and "console/serve.py is missing" in r.stderr, r.stderr
        # a declared default must be the manifest's
        mm = copy.deepcopy(m)
        mm["defaults"] = {"SHOP_REGION": {"value": "us", "when": ["SHOP_API_KEY"]}}
        host.write_manifest(mm)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                               "ecosystem.config.cjs.template"), encoding="utf-8") as fh:
            host.write(os.path.join(host.skill, "ecosystem.config.cjs"), fh.read())
        host.write(os.path.join(host.skill, m["console"]["entry"]), "#\n")
        host.write(os.path.join(host.skill, "SKILL.md"), skill_md(mm).replace("default: us", "default: eu"))
        r = check(host)
        assert r.returncode == 1 and "SHOP_REGION default must be us" in r.stderr, r.stderr
        host.write(os.path.join(host.skill, "SKILL.md"), skill_md(mm))
        r = check(host)
        assert r.returncode == 0, r.stdout + r.stderr


if __name__ == "__main__":
    _t.main(globals())
