"""The adapter's boundary: generic code, harness words only in the manifest.

  * lib.js, the hooks, bin, the root-file template and the Caddy snippet name
    no harness (amazon, ppc, spapi, kol, …) and hold none of the example
    manifest's own values: everything harness-specific is read from
    zylos/manifest.json;
  * Node stdlib only (Zylos runs hooks with no npm install); the hooks and
    bin are thin wrappers that load ../lib.js and nothing else of the harness;
  * the bin files are executable node scripts (core links them);
  * the root ecosystem file is the 3-line template; VERSION is semver;
  * the Caddy snippet strips the prefix (handle_path), keeps the public Host,
    strips then sets the user header, and redirects to the trailing slash;
  * README.md documents every manifest key, every placeholder, what a
    harness writes, and the known limits.

Run: python3 hosts/zylos/tests/test_boundary.py
"""

from __future__ import annotations

import os
import re
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import ADAPTER, Host, example  # noqa: E402

CODE = ["lib.js", *(f"hooks/{h}.js" for h in ("configure", "post-install", "post-upgrade", "pre-uninstall")),
        *(f"bin/{b}.js" for b in ("cli", "detach", "console"))]
GENERIC = CODE + ["ecosystem.config.cjs.template", "Caddyfile.snippet"]
# Real harnesses the adapter was distilled from or is for; nowhere in this folder.
HARNESS_WORDS = ["amazon", "ppc", "spapi", "sp-api", "kol", "sellersprite", "sqs", "webconsole"]
# The example harness's words: only the example and the docs may use them
# (generic hints in lib.js say "acme").
EXAMPLE_WORDS = ["shop", "northwind"]


def read(rel):
    with open(os.path.join(ADAPTER, rel), encoding="utf-8") as fh:
        return fh.read()


def test_the_adapter_files_are_all_there():
    want = set(GENERIC) | {"manifest.example.json", "README.md", "VERSION"}
    have = {os.path.relpath(os.path.join(d, f), ADAPTER) for d, dirs, files in os.walk(ADAPTER)
            for f in files if "tests" not in os.path.relpath(d, ADAPTER).split(os.sep)
            and f != ".DS_Store"}
    assert want == have, (sorted(want - have), sorted(have - want))


def test_no_harness_names_in_the_generic_files():
    for rel in GENERIC + ["README.md", "manifest.example.json"]:
        low = read(rel).lower()
        words = HARNESS_WORDS + (EXAMPLE_WORDS if rel in GENERIC else [])
        hits = [w for w in words if w in low]
        assert not hits, (rel, hits)
    assert "ppc" in "run PPC_DATA_DIR".lower(), "the rule bites (case-insensitive)"


def test_no_public_host_or_machine_path_anywhere_in_the_folder():
    """The playbook publishes this folder: it links no host but loopback and
    example.com (console/tests/test_docs.py holds the repo-wide rule), and
    names no machine path. Sources are described in words."""
    linked = re.compile(r"https?://([A-Za-z0-9][A-Za-z0-9.-]*)")
    machine = re.compile(r"/Us" r"ers/|/home/[a-z]|/private/" r"tmp|/tmp/" r"claude|/var/" r"folders/")
    for top, dirs, files in os.walk(ADAPTER):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f == ".DS_Store":
                continue
            rel = os.path.relpath(os.path.join(top, f), ADAPTER)
            text = read(rel)
            hosts = [h for h in linked.findall(text)
                     if h not in ("localhost", "127.0.0.1") and h != "example.com" and not h.endswith(".example.com")]
            assert not hosts, (rel, hosts)
            assert not machine.search(text), (rel, machine.search(text).group(0))


def test_no_example_manifest_value_is_hard_coded():
    m = example()
    values = [m["name"], m["cli"]["bin"], m["env"]["data_dir"], m["runtime"]["uv_installer_sha256"],
              m["runtime"]["uv_version"], str(m["console"]["port"]), m["detach"]["exit_marker"],
              m["reply"]["channel"], m["console"]["port_key"], m["console"]["title_key"],
              m["console"]["allow_host_key"], *m["endpoints"],
              *(t["name"] for t in m["tasks"]), *(k for t in m["tasks"] for k in t["requires"])]
    for rel in GENERIC:
        text = read(rel)
        hits = [v for v in values if v in text]
        assert not hits, (rel, hits)


def test_node_stdlib_only_and_thin_wrappers():
    req = re.compile(r"(?<![\"'])require\(([^)]*)\)")   # a call, not a string that names one
    for rel in CODE:
        for arg in req.findall(read(rel)):
            ok = arg.startswith("'node:") or arg in ("'../lib.js'", "'./post-install.js'") \
                or arg == "path.join(here, '..', 'lib.js'"      # [^)]* stops at join's own )
            assert ok, (rel, arg)
    for rel in CODE[1:]:
        text = read(rel)
        loads = "lib.js" in text or "require('./post-install.js')" in text
        assert loads and "manifest.json" not in text.replace("zylos/manifest.json", ""), rel
        assert not re.search(r"\b[A-Z][A-Z0-9]*_(DATA_DIR|REPLY_CHANNEL|CONSOLE_PORT)\b", text), rel
    assert len(read("hooks/post-upgrade.js").strip().splitlines()) < 12


def test_bin_files_are_executable_node_scripts():
    for b in ("cli", "detach", "console"):
        p = os.path.join(ADAPTER, "bin", f"{b}.js")
        assert os.stat(p).st_mode & stat.S_IXUSR, f"{p} is not executable (git keeps the mode; keep it when vendoring)"
        assert read(f"bin/{b}.js").startswith("#!/usr/bin/env node\n"), b


def test_the_root_file_template_and_version():
    lines = read("ecosystem.config.cjs.template").rstrip("\n").split("\n")
    assert len(lines) == 3 and "require('./zylos/lib.js').ecosystem()" in lines[2], lines
    assert "apps: []" in lines[2], "a broken manifest must not break core's boot"
    v = read("VERSION")
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", v) and f"Version: `VERSION` ({v.strip()})" in read("README.md"), v


def test_the_caddy_snippet():
    body = [ln for ln in read("Caddyfile.snippet").splitlines() if not ln.startswith("##")]
    text = "\n".join(body)
    assert body[0] == "# BEGIN zylos-component:<NAME>" and body[-1] == "# END zylos-component:<NAME>", body
    assert "redir <BASE_PATH> <BASE_PATH>/ permanent" in text
    assert "handle_path <BASE_PATH>/* {" in text and "handle <BASE_PATH>" not in text
    assert "header_up Host" not in text, "the public Host is passed through, never rewritten"
    auth, strip, proxy, setu = (text.index(s) for s in (
        "basic_auth", "request_header -<USER_HEADER>", "reverse_proxy 127.0.0.1:<PORT>",
        "header_up <USER_HEADER> {http.auth.user.id}"))
    assert auth < strip < proxy < setu and "import <USERS_FILE>" in text
    with Host() as host:
        host.configure({"SHOP_CONSOLE_PORT": "9123"})
        r = host.node(os.path.join(host.adapter, "lib.js"), "caddy")
        out = r.stdout
        assert r.returncode == 0 and not re.search(r"<[A-Z_]+>", out) and "##" not in out, out
        assert f"import {os.path.join(host.data, 'console.users')}" in out and "127.0.0.1:9123" in out \
            and "handle_path /shop/* {" in out and "request_header -X-Remote-User" in out \
            and out.startswith("# BEGIN zylos-component:shop-harness\n"), out


def test_the_readme_documents_the_contract():
    text = read("README.md")

    def leaves(obj, out):
        for k, v in obj.items():
            if k != "_comment":
                out.add(k)
            if isinstance(v, dict):
                leaves(v, out)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                leaves(v[0], out)
        return out

    keys = leaves(example(), set()) | {"defaults", "allow_host_key", "value", "when"}
    missing = sorted(k for k in keys if f"`{k}`" not in text)
    assert not missing, f"README.md does not document manifest keys {missing}"
    for p in ("{cli}", "{node}", "{detach}", "{data_dir}", "{skill_dir}", "{logs}", "{exit}", "{log:NAME}"):
        assert f"`{p}`" in text, p
    for must in ("zylos/manifest.json", "SKILL.md", "ecosystem.config.cjs", "## Known limits",
                 "handle_path", "--allow-host", "node zylos/lib.js check", ".zylos-tasks.json"):
        assert must in text, must


if __name__ == "__main__":
    _t.main(globals())
