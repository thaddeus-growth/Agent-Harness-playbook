#!/usr/bin/env python3
"""kit.auth: the token source (`<P>_TOKEN_SOURCE` = env | command).

Driven by a FAKE token command (kit/tests/fixtures/fake_token_command.py)
and fake env-mode fetchers: no credentials, no network.

  * the source var follows the bound harness; an unknown value is refused;
  * env mode: the harness's fetcher (static_env_token reads the env chain,
    parsed, never sourced); the token command is never run; env mode with
    no fetcher is refused;
  * command mode: the command's one JSON object is parsed; the token is
    cached until 60 s before its expiry (a patched kit.dates.now crosses
    that line), `force` fetches again, a near-expiry token is fetched
    again on the next call; expiry formats: epoch s, epoch ms, numeric
    string, ISO 8601 with Z / offset / naive (= UTC);
  * a token with no expiry is refused only when the call requires one,
    and then on every call; a static key may omit it;
  * failures are coded AuthErrors naming the var and never echoing
    stdout: missing var, empty / unparsable command line, not found,
    non-zero exit, bad JSON, non-object, no access_token, already
    expired, bad expires_at, timeout;
  * no shell: `$VAR`, globs and `;` reach the command as plain words;
    stdin is closed (a pipe on the test's fd 0 never reaches it);
  * command_fields win over `<prefix>_<FIELD>`, which is the fallback;
    other keys are ignored; setting_source names where each came from;
  * one fetch under concurrent callers; repr never shows a token;
  * auth.source() is configured from harness.toml [auth] and reset by
    config.use.
"""

import contextlib
import json
import os
import shlex
import shutil
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import auth, config, dates  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.env import EnvError  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_token_command.py"
TMP = Path(tmp_dir("auth-"))
P = "SHOP_X"                      # the API's credential prefix in these tests
CVAR = f"{P}_TOKEN_COMMAND"
_n = 0


def fake(*args: str) -> tuple[str, Path]:
    """(command line, counter file) of a fresh fake token command."""
    global _n
    _n += 1
    counter = TMP / f"count-{_n}"
    words = [sys.executable, str(FAKE), str(counter)]
    return " ".join(shlex.quote(w) for w in words) + (
        " " + " ".join(args) if args else ""), counter


def fake_q(*args: str) -> tuple[str, Path]:
    """fake() with every extra arg shell-quoted (a JSON value, a space)."""
    return fake(*(shlex.quote(a) for a in args))


def runs(counter: Path) -> int:
    try:
        return int(counter.read_text())
    except OSError:
        return 0


DATA: Path


@contextlib.contextmanager
def env(**kv: str):
    """A hermetic process env (no env files unless asked) + fresh cache."""
    base = {"PATH": os.environ.get("PATH", ""), "HOME": str(TMP),
            "PYTHONDONTWRITEBYTECODE": "1",
            "KIT_HARNESS_ROOT": os.environ.get("KIT_HARNESS_ROOT", ""),
            "SHOP_DATA_DIR": str(DATA), "SHOP_AUTH_ENV_PATHS": "none"}
    base.update(kv)                   # a None value drops that var
    with mock.patch.dict(os.environ, {k: v for k, v in base.items()
                                      if v is not None}, clear=True):
        yield


def failed(fn) -> tuple[str | None, str, list[str]]:
    """(code, text, next) of the HarnessError fn() raised; (None, '', [])
    when it returned."""
    e = raises(fn, HarnessError)
    if e is None:
        return None, "", []
    return getattr(e.message, "code", None), str(e), e.next


@contextlib.contextmanager
def clock(seconds: float):
    """kit.dates.now moved `seconds` ahead."""
    real = dates.now
    dates.now = lambda: real() + timedelta(seconds=seconds)
    try:
        yield
    finally:
        dates.now = real


def counting(fetch):
    calls = []

    def f(prefix, now):
        calls.append(prefix)
        return fetch(prefix, now)
    return f, calls


def never(prefix, now):
    raise AssertionError("the env-mode fetcher ran in command mode")


def test_mode(ts):
    print("[1] the source var follows the bound harness")
    check("source var = <P>_TOKEN_SOURCE", ts.source_var == "SHOP_TOKEN_SOURCE",
          ts.source_var)
    check("command var = <prefix>_TOKEN_COMMAND", ts.command_var(P) == CVAR)
    with env():
        check("unset = env", ts.mode() == "env")
    with env(SHOP_TOKEN_SOURCE=" Command "):
        check("case and spaces ignored", ts.mode() == "command")
    with env(SHOP_TOKEN_SOURCE="bogus"):
        c, text, nxt = failed(ts.mode)
    check("unknown value refused, naming the var",
          c == "auth_bad_token_source" and "SHOP_TOKEN_SOURCE" in text
          and nxt == ["shop doctor"], (c, text, nxt))


def test_env_mode(ts):
    print("\n[2] env mode: the harness's fetcher; the command never runs")
    cmd, counter = fake()
    exp = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    fetch, calls = counting(auth.static_env_token())
    with env(**{f"{P}_ACCESS_TOKEN": "env-tok", f"{P}_TOKEN_EXPIRES_AT": exp,
                CVAR: cmd}):
        t1 = ts.token(P, fetch=fetch, require_expiry=True)
        t2 = ts.token(P, fetch=fetch, require_expiry=True)
    check("static_env_token: <prefix>_ACCESS_TOKEN, expiry parsed",
          t1 == t2 == "env-tok", (t1, t2))
    check("cached: the fetcher ran once", calls == [P], calls)
    check("env mode never runs the token command", runs(counter) == 0)

    ts.clear_cache()
    (DATA / ".env").write_text(f"{P}_ACCESS_TOKEN=a|b$c'd\n", encoding="utf-8")
    with env(SHOP_AUTH_ENV_PATHS=None):
        t = ts.token(P, fetch=auth.static_env_token(), require_expiry=False)
    check("read from <DATA_DIR>/.env through the chain, never sourced",
          t == "a|b$c'd", t)
    (DATA / ".env").unlink()

    ts.clear_cache()
    with env():
        e = raises(lambda: ts.token(P, fetch=auth.static_env_token(),
                                    require_expiry=False), HarnessError)
    check("no token var: EnvError env_required naming it",
          isinstance(e, EnvError) and e.message.code == "env_required"
          and f"{P}_ACCESS_TOKEN" in str(e), e)

    ts.clear_cache()
    with env(**{f"{P}_ACCESS_TOKEN": "static"}):
        c, text, _ = failed(lambda: ts.token(
            P, fetch=auth.static_env_token(), require_expiry=True))
        again, _, _ = failed(lambda: ts.token(
            P, fetch=auth.static_env_token(), require_expiry=True))
        kept = ts.token(P, fetch=auth.static_env_token(), require_expiry=False)
    check("no expiry + require_expiry: refused, naming the var, not the token",
          c == again == "auth_token_no_expiry" and f"{P}_ACCESS_TOKEN" in text
          and "static" not in text, (c, text))
    check("no expiry, not required: served", kept == "static", kept)

    ts.clear_cache()
    with env(**{f"{P}_ACCESS_TOKEN": "t", f"{P}_TOKEN_EXPIRES_AT": "soon"}):
        c, text, _ = failed(lambda: ts.token(
            P, fetch=auth.static_env_token(), require_expiry=True))
    check("a bad <prefix>_TOKEN_EXPIRES_AT: auth_bad_expiry naming it",
          c == "auth_bad_expiry" and f"{P}_TOKEN_EXPIRES_AT" in text, text)

    ts.clear_cache()
    with env():
        c, text, _ = failed(lambda: ts.token(P, require_expiry=True))
    check("env mode, no fetcher: refused, naming the command setting",
          c == "auth_no_env_source" and CVAR in text
          and "SHOP_TOKEN_SOURCE=command" in text, text)

    def soon(prefix, now):
        return {"token": "short", "expires_at": now + 30}
    fetch, calls = counting(soon)
    ts.clear_cache()
    with env():
        ts.token(P, fetch=fetch, require_expiry=True)
        ts.token(P, fetch=fetch, require_expiry=True)
    check("env mode: a token inside the 60 s skew is fetched again",
          len(calls) == 2, calls)
    for label, got, want in (
            ("expired", {"token": "t", "expires_at": 1.0}, "auth_token_expired"),
            ("no token", {"expires_at": None}, "auth_no_access_token"),
            ("not a dict", "tok", "auth_no_access_token")):
        ts.clear_cache()
        with env():
            c, _, _ = failed(lambda: ts.token(
                P, fetch=lambda p, n: got, require_expiry=False))
        check(f"env fetcher output {label}: {want}", c == want, c)


def test_command_cache(ts):
    print("\n[3] command mode: parsed, cached until expiry - 60 s")
    cmd, counter = fake()
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
        t1 = ts.token(P, fetch=never, require_expiry=True)
        t2 = ts.token(P, fetch=never, require_expiry=True)
        check("the command's access_token", t1 == "fake-tok-1", t1)
        check("cached: the second call runs nothing",
              t2 == t1 and runs(counter) == 1, (t2, runs(counter)))
        check("force=True runs the command again",
              ts.token(P, require_expiry=True, force=True) == "fake-tok-2")
        with clock(3600 - 60 - 5):
            t3 = ts.token(P, require_expiry=True)
        check("still cached 65 s before expiry", t3 == "fake-tok-2"
              and runs(counter) == 2, (t3, runs(counter)))
        with clock(3600 - 60 + 5):
            t4 = ts.token(P, require_expiry=True)
        check("fetched again once inside the last 60 s",
              t4 == "fake-tok-3" and runs(counter) == 3, (t4, runs(counter)))

    cmd, counter = fake("--expires-in", "30")
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
        ts.clear_cache()
        ts.token(P, require_expiry=True)
        ts.token(P, require_expiry=True)
    check("a token inside the skew is fetched again", runs(counter) == 2,
          runs(counter))

    now = datetime.now(timezone.utc) + timedelta(hours=1)
    for label, val in (
            ("ISO 8601 with Z", json.dumps(now.isoformat().replace("+00:00", "Z"))),
            ("ISO 8601 with an offset", json.dumps(
                now.astimezone(timezone(timedelta(hours=8))).isoformat())),
            ("naive ISO 8601 (= UTC)", json.dumps(
                now.replace(tzinfo=None).isoformat())),
            ("epoch ms", str(int(now.timestamp() * 1000))),
            ("epoch seconds as a string", json.dumps(str(now.timestamp()))),
            ("epoch seconds", str(now.timestamp()))):
        cmd, counter = fake_q("--expires-at", val)
        with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
            ts.clear_cache()
            ts.token(P, require_expiry=True)
            ts.token(P, require_expiry=True)
        check(f"{label}: parsed and cached", runs(counter) == 1, runs(counter))


def test_parse_expiry():
    print("\n[4] parse_expiry")
    t = 1_900_000_000
    check("epoch s", auth.parse_expiry(t, "V") == t)
    check("epoch ms -> s", auth.parse_expiry(t * 1000, "V") == t)
    check("numeric string", auth.parse_expiry(str(t), "V") == t)
    iso = datetime.fromtimestamp(t, timezone.utc)
    check("ISO Z", auth.parse_expiry(iso.isoformat().replace("+00:00", "Z"),
                                     "V") == t)
    check("naive ISO = UTC",
          auth.parse_expiry(iso.replace(tzinfo=None).isoformat(), "V") == t)
    check("None = no known expiry", auth.parse_expiry(None, "V") is None)
    for bad in (True, "soon", "", float("nan"), "inf", [1], {}):
        c, text, _ = failed(lambda: auth.parse_expiry(bad, "V_EXP"))
        check(f"{bad!r}: auth_bad_expiry naming the var",
              c == "auth_bad_expiry" and "V_EXP" in text, (c, text))


def test_no_expiry(ts):
    print("\n[5] no expiry: refused only when required, then every time")
    for label, args in (("null expires_at", ("--expires-at", "null")),
                        ("absent expires_at",
                         ("--raw", '{"access_token": "secret-xyz"}'))):
        cmd, counter = fake_q(*args)
        with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
            ts.clear_cache()
            c, text, nxt = failed(lambda: ts.token(P, require_expiry=True))
            c2, text2, _ = failed(lambda: ts.token(P, require_expiry=True))
            served = ts.token(P, require_expiry=False)
            key = ts.api_key(P, f"{P}_KEY")
        check(f"{label}: refused, naming the var, never the token",
              c == "auth_token_no_expiry" and CVAR in text
              and "secret-xyz" not in text and "fake-tok" not in text
              and nxt == ["shop doctor"], (c, text))
        check(f"{label}: refused on a later call too", c2 == c and text2 == text)
        check(f"{label}: an API that needs no expiry is served (one run)",
              served and served == key and runs(counter) == 1,
              (served, key, runs(counter)))


def test_command_failures(ts):
    print("\n[6] command failures: coded, naming the var, never stdout")
    with env(SHOP_TOKEN_SOURCE="command"):
        ts.clear_cache()
        c, text, _ = failed(lambda: ts.token(P, require_expiry=True))
    check("missing command var", c == "auth_command_missing" and CVAR in text
          and "process env" in text, text)
    cases = (
        ("non-zero exit (stderr quoted)", fake("--exit", "3")[0],
         "auth_command_exit", "connector refused"),
        ("bad JSON", fake_q("--raw", "oops secret-xyz")[0],
         "auth_command_not_json", "one JSON object"),
        ("JSON that is not an object", fake_q("--raw", '["secret-xyz"]')[0],
         "auth_command_not_json", "one JSON object"),
        ("two JSON documents", fake_q("--raw", '{"a":1} {"b":2}')[0],
         "auth_command_not_json", "one JSON object"),
        ("no access_token", fake_q("--raw", '{"expires_at": 1, "x": '
                                   '"secret-xyz"}')[0],
         "auth_no_access_token", "no access_token"),
        ("an empty access_token", fake_q("--raw", '{"access_token": ""}')[0],
         "auth_no_access_token", "no access_token"),
        ("already expired", fake("--expires-in", "-5")[0],
         "auth_token_expired", "already expired"),
        ("bad expires_at", fake_q("--expires-at", '"soon"')[0],
         "auth_bad_expiry", "soon"),
        ("whitespace only", "   ", "auth_command_invalid", "not a valid"),
        ("unbalanced quote", "'unterminated", "auth_command_invalid",
         "not a valid"),
        ("command not found", str(TMP / "no-such-token-cmd"),
         "auth_command_not_started", "could not start"))
    for label, cmd, want, words in cases:
        with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
            ts.clear_cache()
            c, text, nxt = failed(lambda: ts.token(P, require_expiry=False))
        check(label, c == want and CVAR in text and words in text
              and "secret-xyz" not in text and "fake-tok" not in text
              and nxt == ["shop doctor"], (c, text))
    slow = auth.TokenSource(command_timeout=1)
    cmd, _ = fake("--sleep", "5")
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
        c, text, _ = failed(lambda: slow.token(P, require_expiry=False))
    check("timeout", c == "auth_command_timeout" and CVAR in text, text)
    bad, _ = fake("--exit", "3")
    good, counter = fake()
    ts.clear_cache()
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: bad}):
        first, _, _ = failed(lambda: ts.token(P, require_expiry=True))
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: good}):
        tok = ts.token(P, require_expiry=True)
    check("a failure is not cached: the next call runs the command again",
          first == "auth_command_exit" and tok == "fake-tok-1"
          and runs(counter) == 1, (first, tok))


def test_no_shell(ts):
    print("\n[7] no shell, stdin closed")
    pwned = TMP / "pwned"
    cmd, _ = fake(";", "touch", str(pwned))
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
        ts.clear_cache()
        c, text, _ = failed(lambda: ts.token(P, require_expiry=True))
    check("`; touch …` reaches the command as words (it rejects them), "
          "never runs", c == "auth_command_exit" and "; touch" in text
          and not pwned.exists(), (c, text, pwned.exists()))
    words = auth.TokenSource(command_fields=("home", "glob", "stdin"))
    cmd, counter = fake("--field", "home=$HOME", "--field", "glob=*",
                        "--stdin-field", "stdin")
    r, w = os.pipe()
    os.write(w, b"leaked-from-parent\n")
    os.close(w)
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
            home = words.setting(P, "home")
            glob = words.setting(P, "glob")
            stdin = words.setting(P, "stdin")
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(r)
    check("$HOME and * reach the command unexpanded",
          home == "$HOME" and glob == "*", (home, glob))
    check("stdin is closed: the parent's stdin never reaches the command",
          stdin is None and runs(counter) == 1, stdin)


def test_settings(ts):
    print("\n[8] command_fields: the JSON wins, config falls back")
    cmd, counter = fake("--field", "account_id=json-acct", "--field",
                        "ignored_key=x")
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd},
             **{f"{P}_ACCOUNT_ID": "cfg-acct", f"{P}_ENDPOINT": "https://cfg.example.com",
                f"{P}_IGNORED_KEY": "cfg-ignored"}):
        ts.clear_cache()
        check("JSON account_id wins over <prefix>_ACCOUNT_ID",
              ts.setting_source(P, "account_id") == ("json-acct", CVAR),
              ts.setting_source(P, "account_id"))
        check("absent from the JSON: the config var",
              ts.setting_source(P, "endpoint") == ("https://cfg.example.com",
                                                   f"{P}_ENDPOINT"))
        check("a key not in command_fields is ignored",
              ts.setting(P, "ignored_key") == "cfg-ignored")
        check("an explicit env_var name is the fallback",
              ts.setting_source(P, "region", env_var="SHOP_REGION")
              == (None, None))
        ts.token(P, require_expiry=True)
        check("one command run serves the token and every setting",
              runs(counter) == 1, runs(counter))
    cmd, counter = fake("--field", "account_id=json-acct")
    with env(**{CVAR: cmd, f"{P}_ACCOUNT_ID": "cfg-acct",
                f"{P}_KEY": "env-key"}):
        ts.clear_cache()
        check("env mode: config only, the command never runs",
              ts.setting(P, "account_id") == "cfg-acct"
              and ts.api_key(P, f"{P}_KEY") == "env-key"
              and runs(counter) == 0)


def test_threads_and_repr(ts):
    print("\n[9] one fetch under concurrent callers; repr hides tokens")
    cmd, counter = fake("--sleep", "0.3")
    got = []
    with env(SHOP_TOKEN_SOURCE="command", **{CVAR: cmd}):
        ts.clear_cache()
        threads = [threading.Thread(target=lambda: got.append(
            ts.token(P, require_expiry=True))) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    check("six threads, one command run, one token",
          runs(counter) == 1 and got == ["fake-tok-1"] * 6, (runs(counter), got))
    check("repr never shows a cached token", "fake-tok" not in repr(ts),
          repr(ts))
    ts.clear_cache(P)
    check("clear_cache(prefix) drops that prefix", P not in ts._cache)


def test_singleton():
    print("\n[10] auth.source(): configured from harness.toml, reset by use()")
    s1 = auth.source()
    check("default: no command_fields, 30 s", s1.command_fields == ()
          and s1.command_timeout == 30.0 and auth.source() is s1)
    other = TMP / "harness-auth"
    shutil.copytree(_shop.SHOP, other)
    with open(other / "harness.toml", "a", encoding="utf-8") as f:
        f.write('\n[auth]\ncommand_fields = ["account_id", "endpoint"]\n'
                'command_timeout = 5\n')
    config.use(other)
    try:
        s2 = auth.source()
        check("[auth] read; the cache was reset by config.use",
              s2 is not s1 and s2.command_fields == ("account_id", "endpoint")
              and s2.command_timeout == 5.0, repr(s2))
    finally:
        _shop.use()
    check("back on the shop harness: a fresh default source",
          auth.source() is not s2 and auth.source().command_fields == ())


def main() -> int:
    global DATA
    _shop.use()
    DATA = _shop.data_dir()
    ts = auth.TokenSource(command_fields=("account_id", "endpoint"))
    test_mode(ts)
    test_env_mode(ts)
    test_command_cache(ts)
    test_parse_expiry()
    test_no_expiry(ts)
    test_command_failures(ts)
    test_no_shell(ts)
    test_settings(ts)
    test_threads_and_repr(ts)
    test_singleton()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
