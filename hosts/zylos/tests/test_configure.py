"""hooks/configure.js: stdin JSON → <data dir>/.env, 0600, merge-only, silent;
and lib.js's env chain, which must read a .env exactly like the kit's
kit/env.py parse() (the twin the harness itself uses).

Run: python3 hosts/zylos/tests/test_configure.py
"""

from __future__ import annotations

import copy
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t  # noqa: E402
from _host import PLAYBOOK, Host, example  # noqa: E402

sys.path.insert(0, PLAYBOOK)
from kit.env import parse as kit_parse  # noqa: E402  the harness's own .env reader


def js_parse(host, path):
    return host.lib(f"console.log(JSON.stringify(h.readEnvFile({json.dumps(path)})))")


def env_file(host):
    return os.path.join(host.data, ".env")


def test_writes_0600_into_a_0700_data_dir_and_prints_no_value():
    with Host() as host:
        os.makedirs(host.data, mode=0o755)     # core creates it 0755
        os.chmod(host.data, 0o755)
        host.write(os.path.join(host.data, ".env.tmp-999"), "half-written")
        r = host.configure({"SHOP_API_KEY": "api-SECRET-1", "SHOP_REPLY_CHANNEL": "lark", "EMPTY": ""})
        assert r.returncode == 0, r.stderr
        assert stat.S_IMODE(os.stat(env_file(host)).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(host.data).st_mode) == 0o700
        assert not os.path.exists(os.path.join(host.data, ".env.tmp-999")), "stale tmp from an interrupted run"
        body = open(env_file(host)).read()
        assert "SHOP_API_KEY=api-SECRET-1\n" in body and "EMPTY" not in body, body
        assert "SECRET" not in r.stdout + r.stderr and "lark" not in r.stdout + r.stderr, r.stdout
        assert "added SHOP_API_KEY, SHOP_REPLY_CHANNEL" in r.stdout, r.stdout
        assert not os.path.exists(os.path.join(host.zylos, ".env")), "the Zylos root .env is never written"


def test_merge_only():
    with Host() as host:
        host.configure({"SHOP_API_KEY": "one", "SHOP_REPLY_CHANNEL": "lark"})
        with open(env_file(host), "a") as fh:
            fh.write("# owner note\nexport OTHER=keep\n")
        r = host.configure({"SHOP_API_KEY": "two", "SHOP_CONSOLE_PORT": 9123, "OTHER": "changed"})
        lines = open(env_file(host)).read().splitlines()
        assert lines == ["SHOP_API_KEY=two", "SHOP_REPLY_CHANNEL=lark", "# owner note",
                         "OTHER=changed", "SHOP_CONSOLE_PORT=9123"], lines
        assert "updated SHOP_API_KEY, OTHER; added SHOP_CONSOLE_PORT" in r.stdout, r.stdout
        host.configure({"SHOP_API_KEY": "", "SHOP_REPLY_CHANNEL": None})
        assert open(env_file(host)).read().splitlines() == lines, "an empty or null value keeps the stored one"
        host.configure({})
        assert open(env_file(host)).read().splitlines() == lines, "a key not given is kept"


def test_refuses_bad_input_whole():
    with Host() as host:
        host.configure({"A": "1"})
        before = open(env_file(host)).read()
        for bad, says in [({"GOOD": "x", "BAD": {"nested": 1}}, "BAD (an object"),
                          ({"GOOD": "x", "LIST": [1]}, "LIST (an object or array"),
                          ({"GOOD": "x", "bad key": "v"}, "not a KEY name"),
                          ({"GOOD": "x", "ML": "a\nb"}, "ML (multi-line"),
                          ({"GOOD": "x", "LS": "a b"}, "LS (multi-line")]:
            r = host.configure(bad)
            assert r.returncode != 0 and says in r.stderr and "nothing written" in r.stderr, r.stderr
            assert open(env_file(host)).read() == before
        for stdin in ["[1, 2]", "not json", "null"]:
            r = host.hook("configure.js", stdin, ZYLOS_DATA_DIR=host.data)
            assert r.returncode != 0 and "fix by hand" in r.stderr, (stdin, r.stderr)


def test_values_read_back_exactly_through_the_kit_parser_and_lib():
    tricky = {"T_SPACES": "  padded  ", "T_DQ": '"quoted"', "T_SQ": "'single'", "T_MIXED": 'a "b" c',
              "T_EQ": "k=v|w$x", "T_HASH": "#not-a-comment", "T_BOOL": True, "T_NUM": 1.5}
    want = {**{k: v for k, v in tricky.items() if isinstance(v, str)}, "T_BOOL": "true", "T_NUM": "1.5"}
    with Host() as host:
        r = host.configure(tricky)
        assert r.returncode == 0, r.stderr
        got = kit_parse(env_file(host))
        assert {k: got.get(k) for k in want} == want, got
        assert js_parse(host, env_file(host)) == got


def test_lib_reads_a_env_file_like_kit_env_parse():
    vectors = ("A=1\n  B =  two words  \nexport C=exp\nexport  D = spaced\nE=\"dq\"\nF='sq'\n"
               "G=\"mismatch'\nH==eq=\n# comment\n   # indented\nNOEQUALS\n=novalue\nI=a|b$c\n"
               "J=\"\"\nK='\nL=x # kept\nM=\"  in quotes  \"\nCRLF=v\r\nN=last")
    with Host() as host:
        p = os.path.join(host.home, "vectors.env")
        with open(p, "w", newline="") as fh:
            fh.write(vectors)
        want = kit_parse(p)
        assert want["CRLF"] == "v" and want["C"] == "exp" and want["K"] == "'", want   # the vectors bite
        assert js_parse(host, p) == want
        assert js_parse(host, os.path.join(host.home, "missing.env")) == kit_parse(
            os.path.join(host.home, "missing.env")) == {}


def test_env_chain_order_and_the_auth_paths_override():
    m = example()
    with Host() as host:
        home_file = os.path.join(host.home, ".shop.env")      # manifest env.files: ~/.shop.env
        host.write(home_file, "K1=home\nK2=home\n")
        host.configure({"K2": "data", "K3": "data"})
        got = host.lib("console.log(JSON.stringify(['K1','K2','K3','K4'].map(h.envValue)))", K3="process")
        assert got == ["home", "data", "process", ""], "process env, then env.files, then <data>/.env (later wins)"
        got = host.lib("console.log(JSON.stringify(['K1','K2'].map(h.envValue)))", SHOP_AUTH_ENV_PATHS="none")
        assert got == ["", ""], "<PREFIX>_AUTH_ENV_PATHS=none: process env only"
        got = host.lib("console.log(JSON.stringify(h.envValue('K1')))", SHOP_AUTH_ENV_PATHS=f"relative.env,{home_file}")
        assert got == "home", "the override replaces the chain; a relative entry is dropped"
        assert m["env"]["files"] == ["~/.shop.env"]


def test_declared_defaults_are_written_when_their_group_is_in_use():
    m = copy.deepcopy(example())
    m["defaults"] = {"SHOP_REGION": {"value": "us-east-1", "when": ["SHOP_QUEUE_URL", "SHOP_QUEUE_KEY"]}}
    with Host(m) as host:
        r = host.configure({"SHOP_API_KEY": "x"})
        assert "SHOP_REGION" not in kit_parse(env_file(host)), "no key of its group in use"
        r = host.configure({"SHOP_QUEUE_URL": "https://q.example.com"})
        assert kit_parse(env_file(host))["SHOP_REGION"] == "us-east-1" and "defaulted SHOP_REGION=us-east-1" in r.stdout
        host.configure({"SHOP_REGION": "eu-west-1"})
        host.configure({"SHOP_QUEUE_KEY": "k"})
        assert kit_parse(env_file(host))["SHOP_REGION"] == "eu-west-1", "a value set is never overwritten by its default"


def test_data_dir_derivation():
    with Host() as host:
        alt = os.path.join(host.home, "alt zylos")
        r = host.hook("configure.js", json.dumps({"K": "v"}), ZYLOS_DIR=alt)
        assert os.path.isfile(os.path.join(alt, "components", host.name, ".env")), r.stdout + r.stderr
        r = host.hook("configure.js", json.dumps({"K": "v"}), ZYLOS_DIR=None)
        assert os.path.isfile(os.path.join(host.home, "zylos", "components", host.name, ".env")), \
            "without ZYLOS_DIR: $HOME/zylos/components/<name>"
        other = os.path.join(host.home, "other data")
        host.hook("configure.js", json.dumps({"K": "v"}), ZYLOS_DATA_DIR=other, ZYLOS_COMPONENT="someone-else")
        assert not os.path.exists(other), "another component's ZYLOS_DATA_DIR is not ours"
        host.hook("configure.js", json.dumps({"K": "v"}), ZYLOS_DATA_DIR=other, ZYLOS_COMPONENT=host.name)
        assert os.path.isfile(os.path.join(other, ".env"))


if __name__ == "__main__":
    _t.main(globals())
