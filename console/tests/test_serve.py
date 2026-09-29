"""serve.py, run for real: a subprocess on a free port and a folder of its own,
driven with urllib (raw sockets only where urllib would tidy the request up). A 303
is looked at, not followed: `Srv.follow` does what a browser would do with it.

Every safety item is a pair: the bad request is refused AND the good one is
accepted, so a test cannot pass by refusing everything. The forms a person
submits are read out of the HTML pages.py actually drew and sent the way a
browser would, so a drift between pages.py and serve.py fails here. Gated
answers go through tests/fake_harness.py.
"""

from __future__ import annotations

import contextlib
import email.message
import email.parser
import hmac
import html
import http.client
import io
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import _t  # noqa: E402
import core  # noqa: E402
import fake_harness  # noqa: E402
import i18n  # noqa: E402
import relay  # noqa: E402
import serve  # noqa: E402

SERVE = os.path.join(ROOT, "serve.py")
FAKE = os.path.join(HERE, "fake_harness.py")
FORM = "application/x-www-form-urlencoded"
CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
for _k in ("CONSOLE_DIR", "CONSOLE_SECRET"):        # the tests' own store must not see the developer's
    os.environ.pop(_k, None)


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                     # a 303 is an answer to look at, then to follow by hand


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Stay)   # never through a proxy


# ------------------------------------------------------------ a tiny client --

class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    @property
    def location(self):
        return self.headers.get("Location")


def fetch(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=15) as r:
            return Resp(r.status, r.headers, r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.headers, e.read())


def bytes_back(port, payload: bytes) -> bytes:
    """Everything the server sends to these bytes, read to the end (it closes): nothing, for a request it drops."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(payload)
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks)


def raw(port, payload: bytes) -> Resp:
    """One request as bytes, the response read to the end (the server closes)."""
    head, _, body = bytes_back(port, payload).partition(b"\r\n\r\n")
    status_line, _, rest = head.decode("latin-1").partition("\r\n")
    return Resp(int(status_line.split()[1]), email.parser.Parser().parsestr(rest), body)


def flat(markup: str) -> str:
    """The visible words of a page: tags gone, entities decoded, spaces squeezed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def say(key, lang="en", **p):
    return i18n.t(key, lang, **p)


def results(body: str):
    return [(m.group(1), flat(m.group(2))) for m in re.finditer(r'<li class="res (ok|bad)">(.*?)</li>', body, re.S)]


# ------------------------------------------------ forms, as a browser sends --

class Form:
    def __init__(self, attrs):
        self.action, self.method, self.items = attrs.get("action"), attrs.get("method"), []

    def value(self, name):
        return next(v for n, _k, v, _c in self.items if n == name)

    def data(self, **over):
        """What a browser sends: every named control (a radio only if checked),
        with `over` replacing all of a name's values."""
        out = [(n, v) for n, kind, v, checked in self.items
               if n not in over and (checked or kind not in ("radio", "checkbox"))]
        for n, v in over.items():
            out += [(n, x) for x in ([v] if isinstance(v, str) else v)]
        return out


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self.cur, self.area = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.cur = Form(a)
            self.forms.append(self.cur)
        elif self.cur is not None and tag == "input" and a.get("name"):
            self.cur.items.append([a["name"], a.get("type", "text"), a.get("value", ""), "checked" in a])
        elif self.cur is not None and tag == "textarea" and a.get("name"):
            self.area = [a["name"], "textarea", "", True]
            self.cur.items.append(self.area)

    def handle_data(self, data):
        if self.area is not None:
            self.area[2] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None
        elif tag == "textarea":
            self.area = None


def forms_of(markup: str) -> list[Form]:
    p = _Forms()
    p.feed(markup)
    p.close()
    return p.forms


def form_for(markup, action, id=None) -> Form:
    """The form that posts to `action` (for one ask, the one holding its id)."""
    for f in forms_of(markup):
        if f.action == action and (id is None or ("id", "hidden", id, False) in map(tuple, f.items)):
            return f
    raise AssertionError(f"no {action!r} form" + (f" for {id!r}" if id else ""))


# ------------------------------------------------------------ a running console --

class Srv:
    """serve.py as a subprocess on a free port, its folder and log under `root`."""

    def __init__(self, root, *args, env=None, user="alice", flag=True, folder=None, bind=None):
        self.root, self.folder = root, folder or os.path.join(root, "data")
        self.store = core.Store(self.folder)
        argv = [sys.executable, SERVE, "--port", "0"] + (["--host", bind] if bind else [])
        argv += ["--dir", self.folder] if flag else []
        argv += ["--user", user] if user else []
        self.args = argv + list(args)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CONSOLE_DIR", "CONSOLE_SECRET")}
        self.env.update({"PYTHONDONTWRITEBYTECODE": "1", **(env or {})})

    def __enter__(self):
        self.log_file = open(os.path.join(self.root, "serve.log"), "w")
        self.proc = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=self.log_file, env=self.env, text=True)
        dog = threading.Timer(20, self.proc.kill)
        dog.start()
        try:
            first = self.proc.stdout.readline()
            m = re.fullmatch(r"Console: (http://(\[[0-9a-f:]+\]|[A-Za-z0-9.]+):(\d+)/)\n", first)
            assert m, (first, self.log())
            self.printed, self.port = first.strip(), int(m.group(3))
            self.url = m.group(1).replace("//0.0.0.0:", "//127.0.0.1:")       # where a client of "all interfaces" connects
            self.relay_line = self.proc.stdout.readline().strip() if "--relay-cmd" in self.args else None
        except BaseException:
            self.__exit__()
            raise
        finally:
            dog.cancel()
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.log_file.close()

    def log(self):
        with open(os.path.join(self.root, "serve.log"), encoding="utf-8", errors="replace") as f:
            return f.read()

    # -- a browser's requests --
    def get(self, path="", **headers):
        return fetch(self.url + path, headers=headers)

    def token(self, **headers):
        return forms_of(self.get(**headers).text)[-1].value("token")

    def post(self, path, fields, *, headers=None, token=True, raw_body=None):
        if token is True:
            token = self.token()
        fields = list(fields.items() if isinstance(fields, dict) else fields)
        if token:
            fields = [("token", token)] + fields
        body = raw_body if raw_body is not None else urllib.parse.urlencode(fields).encode()
        h = {"Content-Type": FORM, "Origin": self.url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
        h.update(headers or {})
        return fetch(self.url + path, "POST", body, {k: v for k, v in h.items() if v is not None})

    def submit(self, form, page="", headers=None, **over):
        """Send a form of `page` the way its browser would (the action is relative to the page)."""
        target = urllib.parse.urljoin(self.url + page, form.action)
        h = {"Content-Type": FORM, "Origin": self.url.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
        h.update(headers or {})
        return fetch(target, "POST", urllib.parse.urlencode(form.data(**over)).encode(), h)

    def follow(self, resp, posted):
        """What a browser does with a 303 to `Location` after a POST to `posted`."""
        return fetch(urllib.parse.urljoin(self.url + posted, resp.location))

    # -- what is in the log --
    def events(self, type=None):
        return [e for e in self.store.events() if type in (None, e["type"])]

    def agent(self, *asks):
        return self.store.post("bot", list(asks))

    def secret(self):
        return self.store.secret()


def run_serve(*args, env=None, cwd=None):
    """serve.py that is expected to stop by itself: (exit code, stdout, stderr)."""
    e = {k: v for k, v in os.environ.items() if k not in ("CONSOLE_DIR", "CONSOLE_SECRET")}
    e.update({"PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    p = subprocess.run([sys.executable, SERVE, *args], capture_output=True, text=True, timeout=20, env=e, cwd=cwd,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ fixtures --

def raw_ask(step, id, **over):
    a = {"id": id, "step": step, "kind": "test", "group": "Words", "title": f"Title of {id}",
         "why": f"Why for {id}.", "evidence": [{"label": "Fact", "value": "42", "source": "Ledger"}],
         "if_no": f"Nothing changes for {id}."}
    if step in ("confirm", "approve"):
        a["recommend"] = {"value": "yes", "because": f"Because of {id}."}
    if step == "approve":
        a["effect"] = f"Effect of {id}."
    if step == "choose":
        a["options"] = [{"value": "a", "label": "Option A"}, {"value": "b", "label": "Option B"},
                        {"value": "c", "label": "Option C"}]
        a["recommend"] = {"value": "b", "because": f"Because of {id}."}
    if step == "provide":
        a["input"] = {"type": "number", "min": 0, "max": 50, "unit": "USD"}
        a["recommend"] = {"value": "4.2", "because": f"Because of {id}."}
    a.update(over)
    return {k: v for k, v in a.items() if v is not None}


GATE = {"verb": ["facts", "confirm", "unit_cost"], "value_arg": True, "expect": {"items": {"unit_cost": "$value"}}}


def gated(id="k1", **over):
    return raw_ask("provide", id, gate=GATE, **over)


def gate_on(id, verb, step="confirm", **over):
    """A gated ask on `verb`, its payload the one fake_harness binds a code to (a word after the second is a
    key; a payload may not be empty, so a verb of two words or fewer names itself)."""
    put = "$value" if step == "provide" else "yes"
    expect = {"verb": " ".join(verb[:2]), **({"items": {w: put for w in verb[2:]}} if verb[2:] else {})}
    return raw_ask(step, id, gate={"verb": verb, "expect": expect, **({"value_arg": True} if step == "provide" else {})},
                   **over)


def gated_srv(root, mode="ok", verbs="facts confirm,queue approve", args=(), env=None, **kw):
    """A console whose gates run tests/fake_harness.py; its calls are logged under `root`."""
    e = {"FAKE_HARNESS_STATE": os.path.join(root, "fake-state.json"), "FAKE_HARNESS_LOG": os.path.join(root, "fake.log"),
         "FAKE_HARNESS_MODE": mode, **(env or {})}
    return Srv(root, "--relay-cmd", shlex.join([sys.executable, FAKE]), "--relay-verbs", verbs, *args, env=e, **kw)


def fake_calls(root):
    try:
        with open(os.path.join(root, "fake.log")) as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def harness_started(root, secs=5.0):
    """Wait until the harness has been called (its log has a line), so a test acts while it runs."""
    end = time.monotonic() + secs
    while not fake_calls(root) and time.monotonic() < end:
        time.sleep(0.02)
    assert fake_calls(root), "the harness was never started"


def refused(resp, status, key, **p):
    assert resp.status == status, (resp.status, flat(resp.text)[:300])
    assert "res bad" in resp.text and say(key, **p) in flat(resp.text), flat(resp.text)[:400]


def saved(resp):
    assert resp.status == 200 and "res ok" in resp.text, (resp.status, flat(resp.text)[:400])


def see_other(resp, to):
    """A saved note or reopen: 303 to the waiting page, which says what happened, and no page of its own."""
    assert resp.status == 303 and resp.location == to and resp.body == b"", (resp.status, resp.location, flat(resp.text)[:300])


def noted(resp):
    see_other(resp, "./?done=noted")


def reopened(resp):
    see_other(resp, "./?done=reopened")


@contextmanager
def fake_env(root, **extra):
    """The environment of a harness run by an in-process console (it inherits ours), restored afterwards."""
    env = {"FAKE_HARNESS_MODE": "ok", "FAKE_HARNESS_STATE": os.path.join(root, "fake-state.json"),
           "FAKE_HARNESS_LOG": os.path.join(root, "fake.log"), **extra}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def captured_log(level=logging.DEBUG):
    """What the console logs, as (level, message, has a traceback) rows."""
    rows = []

    class Grab(logging.Handler):
        def emit(self, record):
            rows.append((record.levelno, record.getMessage(), record.exc_info is not None))
    grab, lg = Grab(), logging.getLogger("console.serve")
    old = lg.level
    lg.addHandler(grab)
    lg.setLevel(level)
    try:
        yield rows
    finally:
        lg.removeHandler(grab)
        lg.setLevel(old)


@contextmanager
def inproc(root, **over):
    """The same Server and Console in this process, for what a subprocess cannot be told to do."""
    store = core.Store(os.path.join(root, "data"))
    secret = store.ensure_secret()
    srv = serve.Server("127.0.0.1", 0)
    kw = dict(port=srv.server_address[1], user="alice", user_header=None, allow=[], title="Northwind Console",
              lang="en", cmd=[], verbs=[], reason="console")
    kw.update(over)
    app = serve.Console(store, secret, **kw)
    srv.app = app
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield types.SimpleNamespace(app=app, store=store, port=srv.server_address[1],
                                    url=f"http://127.0.0.1:{srv.server_address[1]}/")
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- starting up --

def test_the_console_listens_on_loopback_by_default_and_says_where():
    d = serve._parser().parse_args(["--dir", "x"])
    assert (d.host, d.port, d.lang, d.default_reason, d.log_level) == ("127.0.0.1", 8770, "en", "console", "info")
    with _t.tmpdir() as root, Srv(root) as srv:
        assert srv.url.startswith("http://127.0.0.1:") and srv.get().status == 200
        assert srv.get("history").status == 200


def test_starting_makes_the_secret_or_uses_the_one_it_is_given():
    with _t.tmpdir() as root, Srv(root) as srv:
        path = os.path.join(srv.folder, "secret")
        assert os.stat(path).st_mode & 0o777 == 0o600 and len(srv.secret()) >= 32
    with _t.tmpdir() as root, Srv(root, env={"CONSOLE_SECRET": "e" * 32}) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        page = srv.get().text
        assert srv.submit(form_for(page, "answer", "c1")).status == 200
        assert not os.path.exists(os.path.join(srv.folder, "secret"))          # the given one is used, none is made
        assert core.verify(b"e" * 32, srv.events("answer")[0]) is True


def test_no_folder_is_exit_2_with_one_line_and_never_guessed_from_the_cwd():
    with _t.tmpdir() as cwd:
        with open(os.path.join(cwd, "events.jsonl"), "w") as f:
            f.write("")
        code, out, err = run_serve("--port", "0", cwd=cwd)
        assert code == 2 and out == "" and len(err.strip().splitlines()) == 1, (code, out, err)
        assert sorted(os.listdir(cwd)) == ["events.jsonl"]                        # nothing was made in the cwd
    with _t.tmpdir() as root:
        env_dir = os.path.join(root, "from-env")
        with Srv(root, env={"CONSOLE_DIR": env_dir}, flag=False, folder=env_dir) as srv:
            srv.agent(raw_ask("confirm", "c1"))
            assert os.path.exists(os.path.join(env_dir, "secret")) and "Title of c1" in srv.get().text
        with Srv(root, env={"CONSOLE_DIR": os.path.join(root, "elsewhere")}) as srv:   # the flag wins
            assert os.path.exists(os.path.join(srv.folder, "secret"))
            assert not os.path.exists(os.path.join(root, "elsewhere"))


def test_options_that_make_no_sense_stop_it_with_one_line():
    with _t.tmpdir() as root:
        d = os.path.join(root, "d")
        bad = [("--user=-x",), ("--user", "a b"), ("--user", "bob", "--user-header", "X-User"),
               ("--allow-host", "bad host"), ("--relay-cmd", "echo"), ("--relay-verbs", "facts confirm"),
               ("--relay-cmd", "'unbalanced", "--relay-verbs", "a b"), ("--relay-cmd", "echo", "--relay-verbs", ",")]
        for extra in bad:
            code, out, err = run_serve("--dir", d, "--port", "0", *extra)
            assert code == 2 and out == "" and len(err.strip().splitlines()) == 1, (extra, code, out, err)
        code, out, err = run_serve("--dir", d, "--port", "0", env={"USER": ""})       # no --user and no $USER
        assert code == 2 and len(err.strip().splitlines()) == 1, (code, err)
        assert not os.path.exists(d)                                                # nothing was made by a refused start
    with _t.tmpdir() as root, Srv(root, user="bob") as srv:                       # the good counterpart
        assert say("foot.user", user="bob") in flat(srv.get().text)
    with _t.tmpdir() as root, Srv(root, user=None, env={"USER": "carol"}) as srv:       # no --user: the login name
        assert say("foot.user", user="carol") in flat(srv.get().text)


def ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as sk:
            sk.bind(("::1", 0))
        return True
    except OSError:
        return False


def test_a_host_that_is_not_loopback_is_refused_without_a_user_header_before_anything_is_made():
    with _t.tmpdir() as root:
        d = os.path.join(root, "d")
        for host in ("0.0.0.0", "::", "192.0.2.10", "10.1.2.3", "example.com", "127.0.0.2", "::ffff:127.0.0.1", "localhost."):
            for extra in ((), ("--user", "bob")):                                   # naming the one user does not make it safe
                code, out, err = run_serve("--dir", d, "--port", "0", "--host", host, *extra)
                assert code == 2 and out == "" and len(err.strip().splitlines()) == 1, (host, code, out, err)
                assert "--user-header" in err and "Traceback" not in err, (host, err)
        assert not os.path.exists(d)                              # refused before the folder, the secret or a socket
        code, out, err = run_serve("--dir", d, "--port", "0", "--host", "0.0.0.0", "--user", "bob", "--user-header", "X-User")
        assert code == 2 and "exclude each other" in err          # and the other option check still holds beside it
        for extra in ((), ("--user-header", "X-User"), ("--user", "bob")):        # an empty host would mean every interface
            code, out, err = run_serve("--dir", d, "--port", "0", "--host", "", *extra)
            assert code == 2 and out == "" and "is an address" in err and "Traceback" not in err, (extra, err)
        assert not os.path.exists(d)


def test_a_host_that_is_not_loopback_starts_with_a_user_header_and_only_believes_it_from_a_loopback_peer():
    with _t.tmpdir() as root, Srv(root, "--user-header", "X-User", user=None, bind="0.0.0.0") as srv:
        assert srv.printed == f"Console: http://0.0.0.0:{srv.port}/"
        srv.agent(raw_ask("confirm", "c1"))
        as_bob = {"X-User": "bob"}
        assert say("foot.user", user="bob") in flat(srv.get(**as_bob).text)             # the proxy (here: this test) is on loopback
        assert say("foot.ro") in flat(srv.get().text) and not forms_of(srv.get().text)       # nobody named: read-only
        saved(srv.submit(form_for(srv.get(**as_bob).text, "answer", "c1"), headers=as_bob))
        assert srv.events("answer")[0]["by"] == "web:bob"
        refused(srv.post("note", {"text": "x"}, token=srv.token(**as_bob)), 403, "err.forbidden")     # no header, no one


def test_loopback_hosts_start_with_no_user_header_and_one_person_is_the_console():
    spots = [("127.0.0.1", "127.0.0.1"), ("localhost", "localhost"), ("LOCALHOST", "LOCALHOST")]
    if ipv6_loopback():
        spots.append(("::1", "[::1]"))
    for host, shown in spots:
        with _t.tmpdir() as root, Srv(root, bind=host, user="carol") as srv:
            assert srv.printed == f"Console: http://{shown}:{srv.port}/", (host, srv.printed)
            srv.agent(raw_ask("confirm", "c1"))
            assert say("foot.user", user="carol") in flat(srv.get().text), host
            saved(srv.submit(form_for(srv.get().text, "answer", "c1")))
            assert srv.events("answer")[0]["by"] == "web:carol"


def stops(*args, env=None) -> str:
    """serve.py that must refuse to start: exit 2, nothing on stdout, one line on stderr and no traceback."""
    code, out, err = run_serve(*args, env=env)
    assert code == 2 and out == "" and len(err.strip().splitlines()) == 1 and "Traceback" not in err, (args, code, out, err)
    return err


def test_a_port_that_is_taken_or_out_of_range_or_an_address_that_cannot_be_bound_stops_it_with_one_line():
    with _t.tmpdir() as root:
        d = os.path.join(root, "d")
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            assert f"port {port}" in stops("--dir", d, "--user", "bob", "--port", str(port))
        for bad in ("-1", "65536", "70000", "99999999999999999999"):
            assert "--port" in stops("--dir", d, "--user", "bob", "--port", bad), bad
        for host in ("192.0.2.10", "[::1]", "a" * 70, "no.such.host.invalid"):        # not ours, not an address, a label too long, not a name
            assert repr(host) in stops("--dir", d, "--port", "0", "--host", host, "--user-header", "X-User"), host
        assert not os.path.exists(d)                                                   # a start that failed made no folder and no secret
        with Srv(root, folder=d) as srv:                                               # the good counterpart: the same command, "any free port"
            assert srv.port > 0 and srv.get().status == 200
        assert os.path.exists(os.path.join(d, "secret"))


def test_a_console_secret_that_is_too_short_stops_the_console_and_one_of_16_characters_starts_it():
    with _t.tmpdir() as root:
        d = os.path.join(root, "d")
        for bad in ("s", "x" * 15, " " * 3):
            err = stops("--dir", d, "--user", "bob", "--port", "0", env={"CONSOLE_SECRET": bad})
            assert "CONSOLE_SECRET" in err and "at least 16 characters" in err, (bad, err)
            assert not os.path.exists(d), bad                                          # no folder, no secret file, no socket kept
        with Srv(root, folder=d, env={"CONSOLE_SECRET": "x" * 16}) as srv:               # the shortest that is a secret
            srv.agent(raw_ask("confirm", "c1"))
            saved(srv.submit(form_for(srv.get().text, "answer", "c1")))
            assert core.verify(b"x" * 16, srv.events("answer")[0]) is True
            assert not os.path.exists(os.path.join(d, "secret"))
        with Srv(root, folder=os.path.join(root, "e"), env={"CONSOLE_SECRET": ""}) as srv:   # set but empty is not set: the folder's own
            assert os.path.exists(os.path.join(srv.folder, "secret")) and srv.get().status == 200


# ---------------------------------------------------------------- the safety --

def test_a_request_for_another_host_is_421_and_our_own_names_are_fine():
    with _t.tmpdir() as root, Srv(root, "--allow-host", "console.example.com", "--allow-host", "proxy.example:8080") as srv:
        tok, p = srv.token(), srv.port
        good = [f"localhost:{p}", f"LOCALHOST:{p}", "127.0.0.1", f"127.0.0.1:{p}", f"[::1]:{p}", "localhost",
                "console.example.com", "console.example.com:31337", "proxy.example:8080"]
        bad = ["evil.example", f"evil.example:{p}", "localhost:1", "127.0.0.1:1", f"localhost.evil.example:{p}",
               f"localhost@evil.example", f"127.0.0.1:{p}:{p}", "proxy.example:9090", "proxy.example", "0.0.0.0", " ",
               f"127.0.0.2:{p}"]
        for h in good:
            assert srv.get(Host=h).status == 200, h
            assert srv.get("poll", Host=h).status == 200, h
        for h in bad:
            r = srv.get(Host=h)
            assert r.status == 421 and say("err.bad_host") in flat(r.text), h
            assert srv.get("poll", Host=h).status == 421 and srv.get("static/console.css", Host=h).status == 421, h
        before = len(srv.events())
        for h, want in ((f"127.0.0.1:{p}", 303), ("evil.example", 421)):       # a POST is checked too
            r = srv.post("note", {"text": "hello"}, token=tok, headers={"Host": h, "Origin": f"http://{h}"})
            assert r.status == want, (h, r.status)
        assert len(srv.events()) == before + 1


def test_a_post_needs_the_consoles_token():
    with _t.tmpdir() as root, Srv(root) as srv:
        tok = srv.token()
        assert len(tok) == 32 and srv.token() == tok                     # one per console, on every page
        refused(srv.post("note", {"text": "hi"}, token=False), 403, "err.forbidden")
        refused(srv.post("note", {"text": "hi"}, token=""), 403, "err.forbidden")
        refused(srv.post("note", {"text": "hi"}, token=tok[:-1]), 403, "err.forbidden")
        refused(srv.post("note", {"text": "hi"}, token=tok + "x"), 403, "err.forbidden")
        refused(srv.post("note", {"text": "hi"}, token="é" * 24), 403, "err.forbidden")        # non-ASCII is a refusal, not a crash
        refused(srv.post("note", {"text": "hi"}, token="A" * len(tok)), 403, "err.forbidden")
        refused(srv.post("note?token=" + tok, {"text": "hi"}, token=False), 403, "err.forbidden")   # not from the URL
        assert srv.events() == []
        noted(srv.post("note", {"text": "hi"}, token=tok))
        assert [e["text"] for e in srv.events("note")] == ["hi"]
    with _t.tmpdir() as root, Srv(root) as other:                        # another folder, another token
        assert other.token() != tok
    src = open(SERVE).read()
    assert "hmac.compare_digest" in src                                  # constant time, by construction


def page_token(secret: bytes) -> str:
    return hmac.new(secret, b"console-page-token", "sha256").hexdigest()[:32]


def test_the_page_token_comes_from_the_secret_so_a_page_survives_a_restart_and_only_that_folders_console_accepts_it():
    with _t.tmpdir() as root:
        with Srv(root) as first:
            first.agent(raw_ask("confirm", "c1"), raw_ask("confirm", "c2"))
            page, tok = first.get().text, first.token()
            assert re.fullmatch(r"[0-9a-f]{32}", tok) and tok == page_token(first.secret())
        with Srv(root) as again:                                                  # the same folder, a new process
            assert again.token() == tok
            saved(again.submit(form_for(page, "answer", "c1")))                   # the page fetched before the restart answers
            noted(again.post("note", {"text": "from the old page"}, token=tok))
            assert [e["by"] for e in again.events()][-2:] == ["web:alice", "web:alice"]
        with Srv(root, folder=os.path.join(root, "other")) as other:              # another folder's console: its own secret
            assert other.token() != tok and other.token() == page_token(other.secret())
            refused(other.submit(form_for(page, "answer", "c2")), 403, "err.forbidden")
            refused(other.post("note", {"text": "x"}, token=tok), 403, "err.forbidden")
            assert other.events() == []
            noted(other.post("note", {"text": "x"}))                              # its own token works
    with _t.tmpdir() as root, Srv(root, env={"CONSOLE_SECRET": "e" * 32}) as given:      # a secret from the environment too
        assert given.token() == page_token(b"e" * 32)
    with _t.tmpdir() as root, Srv(root, env={"CONSOLE_SECRET": "f" * 32}) as other:
        assert other.token() == page_token(b"f" * 32) != page_token(b"e" * 32)


def test_a_post_that_says_it_is_cross_site_is_403_and_one_that_says_nothing_is_a_plain_client():
    with _t.tmpdir() as root, Srv(root) as srv:
        p, tok = srv.port, srv.token()
        same = f"http://127.0.0.1:{p}"
        cases = [  # (Origin, Sec-Fetch-Site, expected)
            (None, None, 303), (same, None, 303), (None, "same-origin", 303), (same, "same-origin", 303),
            ("https://evil.example", None, 403), (f"http://evil.example:{p}", None, 403), ("null", None, 403),
            (f"http://localhost:{p}", None, 403), (f"http://127.0.0.1:{p}@evil.example", None, 403),
            (f"ftp://127.0.0.1:{p}", None, 403), ("", None, 403),
            (None, "cross-site", 403), (None, "same-site", 403), (None, "none", 403), (None, "", 403),
            (same, "cross-site", 403), ("https://evil.example", "same-origin", 403),
            # `Origin: null` (a proxy's no-referrer policy) says nothing about where: only same-origin fetch metadata vouches for it
            ("null", "same-origin", 303), ("null", "cross-site", 403), ("null", "same-site", 403),
            ("null", "none", 403), ("null", "", 403), ("NULL", "same-origin", 403)]
        for origin, site, want in cases:
            n = len(srv.events())
            r = srv.post("note", {"text": f"from {origin!r} {site!r}"}, token=tok, headers={"Origin": origin, "Sec-Fetch-Site": site})
            assert r.status == want, (origin, site, r.status)
            assert len(srv.events()) == n + (want == 303), (origin, site)
            if want == 403:
                assert say("err.forbidden") in flat(r.text)
        # a browser on the name it was opened by: Host and Origin agree
        noted(srv.post("note", {"text": "by name"}, token=tok, headers={"Host": f"localhost:{p}", "Origin": f"http://localhost:{p}"}))


def test_behind_a_proxy_the_origin_is_compared_with_the_public_host_the_proxy_passes_through():
    with _t.tmpdir() as root, Srv(root, "--allow-host", "console.example.com") as srv:
        tok, p = srv.token(), srv.port
        for origin in ("https://console.example.com", "http://console.example.com"):
            noted(srv.post("note", {"text": origin}, token=tok, headers={"Host": "console.example.com", "Origin": origin}))
        noted(srv.post("note", {"text": "same-origin"}, token=tok, headers={
            "Host": "console.example.com", "Origin": "https://console.example.com", "Sec-Fetch-Site": "same-origin"}))
        noted(srv.post("note", {"text": "proxy with no-referrer"}, token=tok, headers={     # the browser says `null`, and that it is same-origin
            "Host": "console.example.com", "Origin": "null", "Sec-Fetch-Site": "same-origin"}))
        n = len(srv.events())
        for host, origin in ((f"127.0.0.1:{p}", "https://console.example.com"),      # a proxy that kept its own Host
                             ("console.example.com", "https://evil.example"),
                             ("console.example.com", "https://console.example.com:8443"),
                             ("console.example.com", "https://console.example.com.evil.example")):
            refused(srv.post("note", {"text": "x"}, token=tok, headers={"Host": host, "Origin": origin}), 403, "err.forbidden")
        for site in (None, "cross-site"):                                       # `null` that a page could send from anywhere
            refused(srv.post("note", {"text": "x"}, token=tok, headers={
                "Host": "console.example.com", "Origin": "null", "Sec-Fetch-Site": site}), 403, "err.forbidden")
        assert len(srv.events()) == n


def test_a_body_over_64_kb_is_413_and_the_limit_itself_is_fine():
    assert serve.MAX_BODY == 64 * 1024
    with _t.tmpdir() as root, Srv(root) as srv:
        tok = srv.token()

        def body(total):
            head = urllib.parse.urlencode({"token": tok, "text": "sized"}) + "&pad="
            return (head + "x" * (total - len(head))).encode()
        assert len(body(65536)) == 65536
        noted(srv.post("note", {}, token=False, raw_body=body(65536)))
        for n in (65537, 70000, 200000, 1_000_000):                         # the answer reaches a client that sent it all
            r = srv.post("note", {}, token=False, raw_body=body(n))
            assert r.status == 413 and say("err.too_large") in flat(r.text), n
        assert [e["text"] for e in srv.events("note")] == ["sized"]
        assert srv.get().status == 200                                    # and it still serves


def test_a_client_that_promises_a_big_body_and_sends_none_still_gets_its_413_and_stalled_ones_do_not_block_others():
    with _t.tmpdir() as root, Srv(root) as srv:
        head = (f"POST /note HTTP/1.0\r\nHost: 127.0.0.1:{srv.port}\r\nContent-Type: {FORM}\r\n"
                f"Content-Length: 900000\r\n\r\ntoken=x").encode()
        t0 = time.monotonic()
        r = raw(srv.port, head)                                     # the connection is left open by the client, not closed
        assert r.status == 413 and time.monotonic() - t0 < 5, (r.status, time.monotonic() - t0)
        stalled = [socket.create_connection(("127.0.0.1", srv.port)) for _ in range(3)]
        stalled[0].sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1")   # never finishes its request
        t0 = time.monotonic()
        assert srv.get().status == 200 and srv.get("poll").status == 200
        assert time.monotonic() - t0 < 2, "a stalled client blocked the others"
        [s.close() for s in stalled]


def test_a_header_or_an_address_that_is_too_long_gets_our_page_and_our_headers():
    with _t.tmpdir() as root, Srv(root) as srv:
        host = f"Host: 127.0.0.1:{srv.port}\r\n"
        r = raw(srv.port, f"GET / HTTP/1.0\r\n{host}X-Long: {'a' * 70000}\r\n\r\n".encode())
        assert r.status == 431 and r.headers["Content-Security-Policy"] == CSP and say("err.too_large") in flat(r.text)
        r = raw(srv.port, f"GET /{'a' * 70000} HTTP/1.0\r\n{host}\r\n".encode())
        assert r.status == 414 and r.headers["Content-Security-Policy"] == CSP and say("err.too_large") in flat(r.text)
        assert srv.get().status == 200


def test_a_request_line_that_is_not_http_gets_no_traceback_in_the_log_and_the_console_goes_on():
    with _t.tmpdir() as root, Srv(root) as srv:                                        # at the default log level
        wants = {                                                                      # what each is answered with: our page, never a trace
            b"GET\r\n\r\n": "err.bad_request", b"\x00\x01\x02\r\n\r\n": "err.bad_request",
            b"GET / HTTP/x.y\r\n\r\n": "err.bad_request", b"GET / HTTP/1.0 extra\r\n\r\n": "err.bad_request",
            b"POST /\r\n\r\n": "err.bad_request", b"GET / HTTP/2.0\r\n\r\n": "err.bad_request",
            b"GET /\r\n\r\n": "err.bad_host"}                                           # HTTP/0.9 has no Host: not ours
        for payload, key in wants.items():
            back = bytes_back(srv.port, payload)          # an old client's answer has no status line, only a body
            assert back.startswith((b"HTTP/1.0 4", b"HTTP/1.0 5", b"<!doctype html>")), (payload, back[:60])
            assert say(key) in flat(back.decode("utf-8", "replace")), (payload, back[:200])
            if back.startswith(b"HTTP/"):
                assert CSP in back.decode("latin-1"), payload                              # and our headers, as for any refusal
        assert bytes_back(srv.port, b"\r\n\r\n") == b""                                        # an empty request line is dropped
        assert srv.get().status == 200 and srv.get("history").status == 200            # the good counterpart, still served
        log = srv.log()
        assert "Traceback" not in log and "Exception occurred" not in log and "AttributeError" not in log, log


def test_a_failure_that_escapes_a_request_is_a_debug_line_and_never_a_traceback_on_stderr():
    err, real = io.StringIO(), serve.pages.render_error

    def boom(*a, **k):
        raise RuntimeError("render-detail-5521")
    with _t.tmpdir() as root, captured_log() as seen, inproc(root) as s:
        serve.pages.render_error = boom                                                # an error page that cannot be drawn
        try:
            with contextlib.redirect_stderr(err):
                back = bytes_back(s.port, b"GET /nope HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        finally:
            serve.pages.render_error = real
        assert back == b"" and fetch(s.url + "nope").status == 404                     # dropped; and the console answers the next one
    assert err.getvalue() == "", err.getvalue()
    assert any(lvl == logging.DEBUG and msg == "request failed" and trace for lvl, msg, trace in seen), seen
    assert not any(lvl >= logging.INFO and "render-detail" in msg for lvl, msg, _ in seen), seen


def test_only_a_urlencoded_form_of_known_length_is_read():
    with _t.tmpdir() as root, Srv(root) as srv:
        tok = srv.token()
        body = urllib.parse.urlencode({"token": tok, "text": "typed"}).encode()
        for ctype in ("text/plain", "application/json", "multipart/form-data; boundary=x", "text/html", "x"):
            r = srv.post("note", {}, token=False, raw_body=body, headers={"Content-Type": ctype})
            assert r.status == 415, (ctype, r.status)
        assert srv.events() == []
        for ctype in (FORM, FORM + "; charset=UTF-8", FORM.upper(), " " + FORM):
            noted(srv.post("note", {}, token=False, raw_body=body, headers={"Content-Type": ctype}))
        assert len(srv.events("note")) == 4
        host = f"Host: 127.0.0.1:{srv.port}\r\n".encode()
        base = b"POST /note HTTP/1.0\r\n" + host + f"Origin: http://127.0.0.1:{srv.port}\r\n".encode()
        cases = {                                                            # what a client can get wrong
            "no content type": base + b"Content-Length: %d\r\n\r\n" % len(body) + body,
            "no length": base + b"Content-Type: " + FORM.encode() + b"\r\n\r\n" + body,
            "chunked": base + b"Content-Type: " + FORM.encode() + b"\r\nTransfer-Encoding: chunked\r\n\r\n"
            + b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n",
            "chunked and a length": base + b"Content-Type: " + FORM.encode() + b"\r\nTransfer-Encoding: chunked\r\n"
            + b"Content-Length: %d\r\n\r\n" % len(body) + body,
            "length not a number": base + b"Content-Type: " + FORM.encode() + b"\r\nContent-Length: abc\r\n\r\n" + body,
            "negative length": base + b"Content-Type: " + FORM.encode() + b"\r\nContent-Length: -5\r\n\r\n" + body}
        want = {"no content type": 415, "no length": 400, "chunked": 400, "chunked and a length": 400,
                "length not a number": 400, "negative length": 400}
        for name, payload in cases.items():
            assert raw(srv.port, payload).status == want[name], name
        bad_utf8 = raw(srv.port, base + b"Content-Type: " + FORM.encode() + b"\r\nContent-Length: 8\r\n\r\ntoken=\xff\xfe")
        assert bad_utf8.status == 400
        assert len(srv.events("note")) == 4                                  # none of them wrote
        good = base + b"Content-Type: " + FORM.encode() + b"\r\nContent-Length: %d\r\n\r\n" % len(body) + body
        assert raw(srv.port, good).status == 303 and len(srv.events("note")) == 5


def test_every_response_carries_the_security_headers_and_pages_are_never_cached():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        tok = srv.token()
        big = urllib.parse.urlencode({"token": tok, "pad": "x" * 70000}).encode()
        pages_and_errors = {
            "inbox": srv.get(), "history": srv.get("history"), "poll": srv.get("poll"), "404": srv.get("nope"),
            "405": fetch(srv.url, "PUT"), "405 get on a post route": srv.get("answer"), "421": srv.get(Host="evil.example"),
            "403": srv.post("note", {"text": "x"}, token=False), "413": srv.post("note", {}, token=False, raw_body=big),
            "415": srv.post("note", {"text": "x"}, headers={"Content-Type": "text/plain"}),
            "303": srv.post("note", {"text": "x"}),
            "result": srv.submit(form_for(srv.get().text, "answer", "c1")),
            "unknown method": raw(srv.port, b"FOO / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")}
        static = {"css": srv.get("static/console.css"), "js": srv.get("static/console.js"), "static 404": srv.get("static/nope.css")}
        for name, r in {**pages_and_errors, **static}.items():
            h = r.headers
            assert h["Content-Security-Policy"] == CSP, name
            assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff", name
            # not "no-referrer": under it a browser sends `Origin: null` on a same-origin POST, and one that
            # sends no fetch metadata would have every answer click refused
            assert h["Referrer-Policy"] == "same-origin", name
            assert "python" not in (h.get("Server") or "").lower(), name
        for name, r in pages_and_errors.items():
            assert r.headers["Cache-Control"] == "no-store", name
        assert pages_and_errors["303"].status == 303 and pages_and_errors["result"].status == 200
        assert pages_and_errors["poll"].headers["Content-Type"] == "application/json"
        assert pages_and_errors["inbox"].headers["Content-Type"] == "text/html; charset=utf-8"


def test_static_files_are_served_by_exact_name_and_nothing_else_is():
    with _t.tmpdir() as root, Srv(root) as srv:
        for name, ctype in (("console.css", "text/css"), ("console.js", "text/javascript")):
            r = srv.get("static/" + name)
            with open(os.path.join(ROOT, "static", name), "rb") as f:
                assert r.status == 200 and r.body == f.read(), name
            assert r.headers["Content-Type"].startswith(ctype), name
        core_src = open(os.path.join(ROOT, "core.py"), "rb").read()[:200]
        for path in ("static/../core.py", "static/%2e%2e/core.py", "static/..%2fcore.py", "static/%2e%2e%2fcore.py",
                     "static/./console.css", "static//console.css", "static/console.css/", "static/console.css%00.js",
                     "static/", "static", "static/nope.css", "static/CONSOLE.CSS", "core.py", "serve.py", "i18n.json",
                     "secret", "events.jsonl", "../secret", "%2e%2e/secret", "tests/test_serve.py"):
            r = srv.get(path)
            assert r.status == 404 and say("err.not_found") in flat(r.text), path
            assert core_src not in r.body and srv.secret() not in r.body, path
        # and as raw bytes, where nothing tidies the path first
        for path in (b"/static/../core.py", b"/static/../../etc/passwd", b"/static/%2e%2e/core.py"):
            r = raw(srv.port, b"GET " + path + b" HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            assert r.status == 404 and core_src not in r.body, path


def test_unknown_paths_are_404_and_other_methods_are_405():
    with _t.tmpdir() as root, Srv(root) as srv:
        tok = srv.token()
        for path in ("nope", "history/", "index.html", "answers", "poll/", "note/"):
            assert srv.get(path).status == 404, path
        assert srv.post("nope", {"a": "b"}).status == 404 and srv.post("answer/", {}).status == 404
        for path in ("answer", "answer_all", "reopen", "note"):
            r = srv.get(path)
            assert r.status == 405 and r.headers["Allow"] == "GET, POST", path
            assert say("err.method_not_allowed") in flat(r.text)
        for path in ("", "history", "poll", "static/console.css"):
            assert srv.post(path, {"text": "x"}, token=tok).status == 405, path
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"):
            r = fetch(srv.url, method, headers={"Content-Type": FORM} if method in ("PUT", "PATCH") else None)
            assert r.status == 405, method
        head = raw(srv.port, f"HEAD / HTTP/1.0\r\nHost: 127.0.0.1:{srv.port}\r\n\r\n".encode())
        assert head.status == 405 and head.body == b""                            # a HEAD answer has no body
        assert raw(srv.port, b"FOO / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").status == 405
        assert srv.events() == [] and srv.get().status == 200


def test_a_failure_of_ours_is_an_error_page_never_a_stack_trace():
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append((record.levelno, record.getMessage()))
    grab, lg = Grab(), logging.getLogger("console.serve")
    old = lg.level
    lg.addHandler(grab)
    lg.setLevel(logging.DEBUG)
    try:
        with _t.tmpdir() as root, inproc(root) as s:
            s.store.post("bot", [raw_ask("confirm", "c1")])
            page = fetch(s.url).text
            form = form_for(page, "answer", "c1")
            s.app.store.answer = lambda *a, **k: (_ for _ in ()).throw(PermissionError("disk-detail-9917"))
            r = fetch(s.url + "answer", "POST", urllib.parse.urlencode(form.data()).encode(), {"Content-Type": FORM})
            assert r.status == 500 and say("err.server_error") in flat(r.text), flat(r.text)
            assert core.Store(os.path.join(root, "data")).events()[-1]["type"] == "ask"       # nothing was written
            s.app.state = lambda: 1 / 0
            for path in ("", "history", "poll"):
                r = fetch(s.url + path)
                assert r.status == 500 and r.headers["Content-Security-Policy"] == CSP, path
                assert "Traceback" not in r.text and "ZeroDivision" not in r.text and "disk-detail" not in r.text, path
            assert say("err.server_error") in flat(fetch(s.url).text)
    finally:
        lg.removeHandler(grab)
        lg.setLevel(old)
    assert any(lvl == logging.WARNING and "ZeroDivisionError" in msg for lvl, msg in seen), seen
    assert not any("disk-detail" in msg for lvl, msg in seen if lvl >= logging.INFO), seen     # details only at debug


def test_a_corrupt_log_is_a_500_page_nothing_is_written_and_a_repaired_log_is_served():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        tok = srv.token()
        good = open(srv.store.path, "rb").read()
        assert srv.get().status == 200
        with open(srv.store.path, "ab") as f:
            f.write(b"this is not json\n")
        corrupt = open(srv.store.path, "rb").read()
        for path in ("", "history"):
            r = srv.get(path)
            assert r.status == 500 and say("err.corrupt_log") in flat(r.text), path
            assert r.headers["Content-Security-Policy"] == CSP
        r = srv.get("poll")
        assert r.status == 500 and json.loads(r.text) == {"error": "corrupt_log"}
        r = srv.post("note", {"text": "hello"}, token=tok)
        assert r.status == 500 and say("err.corrupt_log") in flat(r.text)
        assert open(srv.store.path, "rb").read() == corrupt                        # refused, not written
        with open(srv.store.path, "wb") as f:
            f.write(good)
        assert srv.get().status == 200 and "Title of c1" in srv.get().text
        noted(srv.post("note", {"text": "hello"}, token=tok))


def test_the_log_is_read_again_only_when_its_size_or_time_changed():
    with _t.tmpdir() as root, inproc(root) as s:
        s.store.post("bot", [raw_ask("confirm", "c1")])
        calls = []
        real = s.app.store.state
        s.app.store.state = lambda: (calls.append(1), real())[1]
        first = s.app.state()
        assert s.app.state() is first and len(calls) == 1                            # unchanged: not read again
        path = s.store.path
        st = os.stat(path)
        s.store.post("bot", [raw_ask("confirm", "c2")])
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))                          # same time, new size
        assert len(s.app.state()["order"]) == 2 and len(calls) == 2, "a size change must re-read"
        s.app.state()
        assert len(calls) == 2
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))          # same size, new time
        s.app.state()
        assert len(calls) == 3, "a time change must re-read"
        os.remove(path)
        assert s.app.state()["order"] == [] and s.app.state()["seq"] == 0            # no file yet is an empty inbox


def test_poll_follows_the_log_and_matches_the_page():
    with _t.tmpdir() as root, Srv(root) as srv:
        def poll():
            r = srv.get("poll")
            assert r.status == 200 and r.headers["Content-Type"] == "application/json"
            return json.loads(r.text)
        assert poll() == {"seq": 0, "open": 0}                                       # no file yet
        srv.agent(raw_ask("confirm", "c1"))
        assert poll() == {"seq": 1, "open": 1}
        srv.agent(raw_ask("confirm", "c2"))                                          # the agent posts through the Store
        assert poll() == {"seq": 2, "open": 2}
        page = srv.get().text
        assert f'data-seq="{poll()["seq"]}"' in page and 'data-open="2"' in page and 'data-poll="poll"' in page
        saved(srv.submit(form_for(page, "answer", "c1")))
        assert poll() == {"seq": 3, "open": 1}
        srv.store.say("bot", "hello")
        assert poll() == {"seq": 4, "open": 1}


# ------------------------------------------------------------- the four writes --

def test_a_full_answer_through_the_page_is_signed_and_moves_to_history():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("choose", "o1"), raw_ask("provide", "p1"), raw_ask("approve", "a1"))
        page = srv.get().text
        shown = {i: form_for(page, "answer", i).value("hash") for i in ("c1", "o1", "p1", "a1")}
        r = srv.submit(form_for(page, "answer", "c1"))                                # the suggestion, untouched
        saved(r)
        assert results(r.text) == [("ok", "Title of c1 " + say("result.line_ok", value="Yes"))], results(r.text)
        assert r.headers["Cache-Control"] == "no-store"
        ev = srv.events("answer")[0]
        assert (ev["id"], ev["value"], ev["by"], ev["suggested"], ev["subject"]) == ("c1", "yes", "web:alice", True, shown["c1"])
        assert "gate" not in ev and "comment" not in ev and core.verify(srv.secret(), ev) is True
        saved(srv.submit(form_for(page, "answer", "o1"), value="a", comment="I prefer A"))
        saved(srv.submit(form_for(page, "answer", "p1"), value="7"))
        r = srv.submit(form_for(page, "answer", "a1"), value="no")
        saved(r)
        got = {e["id"]: e for e in srv.events("answer")}
        assert (got["o1"]["value"], got["o1"]["suggested"], got["o1"]["comment"]) == ("a", False, "I prefer A")
        assert (got["p1"]["value"], got["p1"]["suggested"]) == ("7", False) and got["a1"]["value"] == "no"
        assert all(core.verify(srv.secret(), e) is True for e in got.values())
        inbox = srv.get().text
        assert "Title of c1" not in inbox and say("empty.title") in flat(inbox)
        hist = flat(srv.get("history").text)
        for title in ("Title of c1", "Title of o1", "Title of p1", "Title of a1"):
            assert title in hist, title
        assert say("state.waiting") in hist and "I prefer A" in hist


def test_reopen_from_history_puts_the_ask_back_and_an_applied_answer_cannot_be_reopened():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        saved(srv.submit(form_for(srv.get().text, "answer", "c1")))
        hist = srv.get("history").text
        reopen = form_for(hist, "reopen", "c1")
        assert reopen.value("answer_seq") == str(srv.events("answer")[0]["seq"])
        r = srv.submit(reopen, page="history")                                       # ./reopen from /history is /reopen
        reopened(r)
        assert srv.store.state()["asks"]["c1"]["status"] == "open" and "Title of c1" in srv.get().text
        saved(srv.submit(form_for(srv.get().text, "answer", "c1"), value="no"))
        stale, fresh = reopen, form_for(srv.get("history").text, "reopen", "c1")
        refused(srv.submit(stale, page="history"), 409, "err.changed")                # an old page's seq
        srv.store.applied("bot", ["c1"], "the plan")
        refused(srv.submit(fresh, page="history"), 409, "err.already_applied")
        hist = srv.get("history").text
        assert "Applied · the plan" in flat(hist) and not [f for f in forms_of(hist) if f.action == "reopen"]
        srv.agent(raw_ask("confirm", "c2"))
        refused(srv.post("reopen", {"id": "c2", "answer_seq": "1"}), 409, "err.not_answered")
        refused(srv.post("reopen", {"id": "nobody", "answer_seq": "1"}), 409, "err.unknown_id")
        refused(srv.post("reopen", {"id": "c2", "answer_seq": "abc"}), 400, "err.bad_request")
        refused(srv.post("reopen", {"id": "c2"}), 400, "err.bad_request")


def test_a_note_goes_to_the_agent_signed_and_html_in_it_is_only_text():
    with _t.tmpdir() as root, Srv(root) as srv:
        form = form_for(srv.get().text, "note")
        noted(srv.submit(form, text="Please <b>wait</b> & <script>alert(1)</script>"))
        ev = srv.events("note")[0]
        assert ev["by"] == "web:alice" and core.verify(srv.secret(), ev) is True
        page = srv.get().text
        assert "&lt;b&gt;wait&lt;/b&gt;" in page and "<script>alert" not in page
        assert len(re.findall(r"<script", page)) == 1                                # only console.js
        for bad in ("", "   ", "x" * 1001):
            refused(srv.submit(form, text=bad), 400, "err.bad_request")
        refused(srv.post("note", [("text", "a"), ("text", "b")]), 400, "err.bad_request")   # one text, not two
        refused(srv.post("note", {}), 400, "err.bad_request")
        assert len(srv.events("note")) == 1


def test_a_saved_note_or_reopen_is_a_303_to_the_waiting_page_that_says_so_and_a_reload_writes_nothing_more():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        r = srv.submit(form_for(srv.get().text, "note"), text="first note")
        noted(r)
        assert r.headers["Cache-Control"] == "no-store" and r.headers["Content-Security-Policy"] == CSP
        n = len(srv.events())
        for _ in range(3):                                                           # the browser follows it, and a reload is a GET
            landed = srv.follow(r, "note")
            assert landed.status == 200 and "first note" in landed.text and "res ok" not in landed.text
            assert flashed(landed.text) == say("flash.noted")
        assert len(srv.events()) == n and len(srv.events("note")) == 1
        saved(srv.submit(form_for(landed.text, "answer", "c1")))                       # an answer is still a page of its own
        hist = srv.get("history").text
        r = srv.submit(form_for(hist, "reopen", "c1"), page="history")
        reopened(r)
        n = len(srv.events())
        for _ in range(3):                                                           # it waits again, on the page it lands on
            landed = srv.follow(r, "reopen")
            assert landed.status == 200 and "Title of c1" in landed.text and form_for(landed.text, "answer", "c1")
            assert flashed(landed.text) == say("flash.reopened")
        assert len(srv.events()) == n and len(srv.events("reopen")) == 1
        assert srv.store.state()["asks"]["c1"]["status"] == "open"


def flashed(markup: str):
    """The words of the banner at the top of the waiting page, or None."""
    m = re.search(r'<p class="flash ok" role="status">(.*?)</p>', markup, re.S)
    return html.unescape(m.group(1)) if m else None


def test_the_waiting_page_shows_the_banner_of_a_finished_redirect_and_only_for_the_two_words_it_knows():
    with _t.tmpdir() as root, Srv(root, "--lang", "zh") as srv:
        srv.agent(raw_ask("confirm", "c1"))
        assert flashed(srv.get().text) is None                                         # a plain visit says nothing
        for done, key in (("noted", "flash.noted"), ("reopened", "flash.reopened")):
            page = srv.get(f"?done={done}")
            assert flashed(page.text) == say(key, "zh") and page.status == 200         # in the console's language
            assert flashed(srv.get(f"?done={done}&lang=en").text) == say(key, "en")
            assert flashed(srv.get(f"?done={done}", Cookie=f"console_lang_{srv.port}=en").text) == say(key, "en")
        for junk in ("", "nope", "NOTED", "noted%20", "noted%26done=reopened", "%3Cscript%3Ealert(1)%3C/script%3E", "flash.noted",
                     "history", "../", "noted%00", "0"):
            page = srv.get(f"?done={junk}")
            assert page.status == 200 and flashed(page.text) is None and "flash" not in page.text, junk
            assert "<script>alert" not in page.text
        assert flashed(srv.get("?done=noted&done=nope").text) == say("flash.noted", "zh")           # the first one counts
        assert flashed(srv.get("?done=nope&done=noted").text) is None


def test_a_refused_note_or_reopen_and_every_answer_is_still_a_page_never_a_redirect():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("confirm", "c2"), raw_ask("confirm", "c3"))
        page = srv.get().text
        for r in (srv.post("note", {"text": "  "}), srv.post("note", {"text": "x" * 1001}), srv.post("note", {})):
            assert r.status == 400 and r.location is None and "res bad" in r.text
        for r in (srv.post("reopen", {"id": "c1", "answer_seq": "1"}), srv.post("reopen", {"id": "nobody", "answer_seq": "1"}),
                  srv.post("reopen", {"id": "c1", "answer_seq": "x"})):
            assert r.status in (400, 409) and r.location is None and "res bad" in r.text
        for r in (srv.submit(form_for(page, "answer", "c1")), srv.submit(form_for(page, "answer_all"))):
            saved(r)
            assert r.location is None
        refused(srv.submit(form_for(page, "answer", "c1")), 409, "err.not_open")            # and a refused answer
        assert srv.post("note", {"text": "  "}, token=False).status == 403                   # a request turned away is no redirect either
        assert len(srv.events("note")) == 0 and len(srv.events("reopen")) == 0


def test_a_bad_value_is_refused_with_its_own_sentence_and_nothing_is_written():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("choose", "o1"), raw_ask("provide", "p1"),
                  raw_ask("provide", "d1", input={"type": "date"}, recommend=None))
        page = srv.get().text
        f = {i: form_for(page, "answer", i) for i in ("c1", "o1", "p1", "d1")}
        cases = [("c1", {"value": "maybe"}, "bad_value.yes_no"), ("c1", {"value": []}, "bad_value.yes_no"),
                 ("o1", {"value": "zzz"}, "bad_value.choice"), ("p1", {"value": "99"}, "bad_value.range"),
                 ("p1", {"value": "abc"}, "bad_value.number"), ("p1", {"value": "  "}, "bad_value.empty"),
                 ("p1", {"value": "9" * 501}, "bad_value.too_long"), ("d1", {"value": "2026-13-45"}, "bad_value.date")]
        for id, over, key in cases:
            refused(srv.submit(f[id], **over), 400, key)
        assert srv.events("answer") == []
        for id, over in (("c1", {}), ("o1", {"value": "c"}), ("p1", {"value": "25"}), ("d1", {"value": "2026-03-02"})):
            saved(srv.submit(f[id], **over))                                            # the good counterparts
        assert {e["id"]: e["value"] for e in srv.events("answer")} == {"c1": "yes", "o1": "c", "p1": "25", "d1": "2026-03-02"}


def test_a_malformed_answer_form_is_a_bad_request_not_a_crash():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        good = form_for(srv.get().text, "answer", "c1")
        h, i = good.value("hash"), "c1"
        refused(srv.post("answer", {"hash": h, "value": "yes"}), 400, "err.bad_request")                    # no id
        refused(srv.post("answer", {"id": i, "value": "yes"}), 400, "err.bad_request")                      # no hash
        refused(srv.post("answer", [("id", i), ("id", i), ("hash", h), ("value", "yes")]), 400, "err.bad_request")
        refused(srv.post("answer", [("id", i), ("hash", h), ("value", "yes"), ("value", "no")]), 400, "err.bad_request")
        refused(srv.post("answer", {"id": "nobody", "hash": h, "value": "yes"}), 409, "err.unknown_id")
        refused(srv.post("answer", {"id": i, "hash": "0" * 16, "value": "yes"}), 409, "err.changed")
        refused(srv.post("answer", {"id": i, "hash": h, "value": "yes", "comment": "c" * 501}), 400, "err.bad_request")
        assert srv.events("answer") == []
        saved(srv.submit(good, comment="fine"))
        refused(srv.submit(good), 409, "err.not_open")                                                       # once


def kept(comment: str) -> str:
    """The sentence that gives a comment back to the person who typed it (spaces squeezed, as `flat` does)."""
    return " ".join(say("result.comment", comment=comment).split())


def test_a_refused_answer_gives_the_comment_back_and_a_saved_one_does_not():
    typed = "I checked the invoice <b>twice</b> & it says 4.5\nsecond line"
    marker = "zx-refused-comment-3306"
    with _t.tmpdir() as root, gated_srv(root, "fail") as srv:
        srv.agent(raw_ask("provide", "p1"), raw_ask("confirm", "c1"), raw_ask("confirm", "c2"), gated("k1"))
        page = srv.get().text
        refusals = [                                                                   # (form, what is sent, status, words)
            (form_for(page, "answer", "p1"), {"value": "99"}, 400, "bad_value.range"),         # a bad value
            (form_for(page, "answer", "p1"), {"value": "abc"}, 400, "bad_value.number"),
            (form_for(page, "answer", "k1"), {"value": "4.5"}, 409, "err.gate_refused")]       # the harness's refusal
        for form, over, status, key in refusals:
            r = srv.submit(form, comment=typed, **over)
            refused(r, status, key)
            assert kept(typed) in flat(r.text), key                                   # as typed, line break and all
            assert "&lt;b&gt;twice&lt;/b&gt;" in r.text and "<b>twice" not in r.text  # only ever as text
        refused(srv.submit(form_for(page, "answer", "p1"), value="4", comment="c" * 501), 400, "err.bad_request")
        assert kept("c" * 501) in flat(srv.submit(form_for(page, "answer", "p1"), value="4", comment="c" * 501).text)   # too long: still given back
        old = form_for(page, "answer", "c1")
        srv.agent(raw_ask("confirm", "c1", why="The agent found out more."))
        r = srv.submit(old, comment=typed)                                            # a page that went stale
        refused(r, 409, "err.changed")
        assert kept(typed) in flat(r.text)
        saved(srv.submit(form_for(page, "answer", "c2"), comment=marker))
        r = srv.submit(form_for(page, "answer", "c2"), comment=typed)                 # already answered
        refused(r, 409, "err.not_open")
        assert kept(typed) in flat(r.text)
        r = srv.submit(form_for(page, "answer", "p1"), value="99")                    # nothing typed: nothing given back
        refused(r, 400, "bad_value.range")
        assert kept("").strip() not in flat(r.text)
        r = srv.submit(form_for(page, "answer", "p1"), value="99", comment="   ")
        assert kept("").strip() not in flat(r.text)
        # a saved answer says nothing about the comment, and a refusal's is not written to the log
        srv.agent(raw_ask("confirm", "c3"))
        r = srv.submit(form_for(srv.get().text, "answer", "c3"), comment=marker)
        saved(r)
        assert marker not in r.text and kept("").strip() not in flat(r.text)
        assert typed.split("\n")[0] not in srv.log() and marker not in srv.log()
        assert [e["id"] for e in srv.events("answer")] == ["c2", "c3"]


def test_a_page_that_went_stale_is_changed_not_answered_and_that_beats_a_bad_value():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("choose", "o1"))
        old = srv.get().text
        srv.agent(raw_ask("confirm", "c1", why="The agent found out something new."))       # revised while the page was open
        r = srv.submit(form_for(old, "answer", "c1"))
        refused(r, 409, "err.changed")
        assert srv.events("answer") == []
        two = raw_ask("choose", "o1")
        two["options"] = two["options"][:2]                                                # the agent drops option c
        srv.agent(two)
        r = srv.submit(form_for(old, "answer", "o1"), value="c")
        refused(r, 409, "err.changed")                                                     # changed, not "pick one of the listed"
        new = srv.get().text
        saved(srv.submit(form_for(new, "answer", "c1")))
        saved(srv.submit(form_for(new, "answer", "o1"), value="a"))
        assert {e["id"]: e["value"] for e in srv.events("answer")} == {"c1": "yes", "o1": "a"}


def test_an_ask_the_agent_withdrew_is_not_open_to_a_page_that_still_shows_it():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("confirm", "c2"))
        page = srv.get().text
        srv.store.withdraw("bot", ["c1"], "No longer needed.")
        refused(srv.submit(form_for(page, "answer", "c1")), 409, "err.not_open")
        assert srv.events("answer") == []
        saved(srv.submit(form_for(page, "answer", "c2")))


def test_answer_all_gives_each_ask_its_own_suggestion_and_reports_each_line():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("confirm", "c2", recommend={"value": "no", "because": "Not now."}),
                  raw_ask("choose", "o1"), raw_ask("provide", "z1", group="Elsewhere", recommend=None))
        page = srv.get().text
        group = [f for f in forms_of(page) if f.action == "answer_all"]
        assert len(group) == 1                                                            # the group of 3 with suggestions
        r = srv.submit(group[0])
        saved(r)
        assert [k for k, _ in results(r.text)] == ["ok", "ok", "ok"] and "Title of c1" in results(r.text)[0][1]
        got = {e["id"]: e for e in srv.events("answer")}
        assert {i: e["value"] for i, e in got.items()} == {"c1": "yes", "c2": "no", "o1": "b"}
        assert all(e["suggested"] is True and e["by"] == "web:alice" and core.verify(srv.secret(), e) for e in got.values())
        assert srv.store.state()["asks"]["z1"]["status"] == "open"
        # one of the pairs went stale, one is for nobody, one has no hash: the others still go through
        srv.agent(raw_ask("confirm", "d1"), raw_ask("confirm", "d2"), raw_ask("confirm", "d3"))
        old = form_for(srv.get().text, "answer_all")
        srv.agent(raw_ask("confirm", "d2", why="Revised."))
        pairs = old.data()[1:] + [("pair", "nobody:0000"), ("pair", "d3")]
        r = srv.post("answer_all", pairs, token=old.value("token"))
        assert r.status == 200                                                            # some were saved
        assert [k for k, _ in results(r.text)] == ["ok", "bad", "ok", "bad", "bad"], results(r.text)
        assert all(say(k) in flat(r.text) for k in ("err.changed", "err.unknown_id", "err.not_open"))
        assert {e["id"] for e in srv.events("answer")} == {"c1", "c2", "o1", "d1", "d3"}
        state = srv.store.state()["asks"]
        assert (state["d1"]["status"], state["d2"]["status"]) == ("answered", "open")
        refused(srv.post("answer_all", {}), 400, "err.bad_request")
        refused(srv.post("answer_all", [("pair", "d2:0000000000000000")]), 409, "err.changed")


def test_two_answers_to_one_ask_at_once_one_wins_and_the_other_finds_it_not_open():
    with _t.tmpdir() as root, Srv(root) as srv:
        for n in range(6):
            srv.agent(raw_ask("confirm", f"r{n}"))
            form = form_for(srv.get().text, "answer", f"r{n}")
            barrier, out = threading.Barrier(2), []

            def click(value):
                barrier.wait()
                out.append(srv.submit(form, value=value))
            ts = [threading.Thread(target=click, args=(v,)) for v in ("yes", "no")]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
            assert sorted(r.status for r in out) == [200, 409], [r.status for r in out]
            loser = next(r for r in out if r.status == 409)
            assert say("err.not_open") in flat(loser.text) and "res bad" in loser.text
            assert len([e for e in srv.events("answer") if e["id"] == f"r{n}"]) == 1, n


# ---------------------------------------------------------- answers with a gate --

def test_a_gated_answer_goes_through_the_harness_and_its_word_is_recorded():
    with _t.tmpdir() as root:
        with gated_srv(root, args=("--default-reason", "from the console page"), env={"CONSOLE_SECRET": "g" * 32}) as srv:
            assert srv.relay_line == "Relay verbs: facts confirm, queue approve"
            srv.agent(gated("k1"), gated("k2"))
            page = srv.get().text
            assert "facts confirm unit_cost" in page and say("gate.tech") in page      # what will be written is shown first
            r = srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="Invoice 17 confirms")
            saved(r)
            assert say("result.wrote", message="Confirmed unit_cost.") in flat(r.text)
            ev = srv.events("answer")[0]
            assert (ev["value"], ev["suggested"], ev["comment"]) == ("4.5", False, "Invoice 17 confirms")
            assert ev["gate"] == {"ok": True, "verb": ["facts", "confirm", "unit_cost"], "message": "Confirmed unit_cost."}
            assert core.verify(b"g" * 32, ev) is True
            saved(srv.submit(form_for(page, "answer", "k2")))                          # no comment: the default reason
            calls = fake_calls(root)
            assert [c["run"] for c in calls] == [1, 2, 1, 2]
            first, second = calls[0]["argv"], calls[1]["argv"]
            assert first == ["facts", "confirm", "unit_cost", "--value=4.5", "--reason=Invoice 17 confirms", "--json"]
            assert second[:6] == first and len(second) == 9, second                                 # the console adds exactly three
            assert second[6] == "--code=***" and second[7] == "--relay-user=web:alice", second      # each one element, the = form
            assert re.fullmatch(r"--relay-at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", second[8]), second
            assert calls[2]["argv"][3:5] == ["--value=4.2", "--reason=from the console page"]
            assert all(c["stdin_devnull"] and not c["tty"] and not c["console_secret"] for c in calls), calls
            hist = flat(srv.get("history").text)
            assert "Confirmed unit_cost." in hist


def test_the_example_gated_ask_is_answerable_through_the_fake_harness():
    with _t.tmpdir() as root, gated_srv(root) as srv:
        with open(os.path.join(ROOT, "examples", "provide-number.json")) as f:
            srv.agent(json.load(f))
        r = srv.submit(form_for(srv.get().text, "answer", "cost-sencha-tin"))
        saved(r)
        assert srv.events("answer")[0]["gate"]["ok"] is True and srv.events("answer")[0]["value"] == "4.20"


def test_a_harness_refusal_writes_nothing_and_shows_the_harness_own_words():
    with _t.tmpdir() as root, gated_srv(root, "fail") as srv:
        srv.agent(gated("k1"))
        r = srv.submit(form_for(srv.get().text, "answer", "k1"), value="4.5")
        refused(r, 409, "err.gate_refused")
        assert say("result.refused", message=fake_harness.REFUSED) in flat(r.text)
        assert srv.events("answer") == [] and srv.store.state()["asks"]["k1"]["status"] == "open"
        assert [c["run"] for c in fake_calls(root)] == [1, 2]
        refused(srv.submit(form_for(srv.get().text, "answer", "k1"), value="4.5"), 409, "err.gate_refused")   # not stuck as "taken"
        assert [c["run"] for c in fake_calls(root)] == [1, 2, 1, 2]
    with _t.tmpdir() as root, gated_srv(root, "no_secret") as srv:
        srv.agent(gated("k1"))
        r = srv.submit(form_for(srv.get().text, "answer", "k1"))
        refused(r, 409, "err.gate_refused")
        assert say("result.refused", message=fake_harness.NO_SECRET) in flat(r.text) and srv.events("answer") == []


def test_a_payload_that_is_not_what_was_shown_is_never_confirmed():
    with _t.tmpdir() as root, gated_srv(root, "changed") as srv:
        srv.agent(gated("k1"))
        r = srv.submit(form_for(srv.get().text, "answer", "k1"), value="4.5")
        refused(r, 409, "err.gate_changed")
        assert say("result.refused", message=core.CODES["gate_changed"]) in flat(r.text)     # under the console's own sentence
        assert srv.events("answer") == []
        assert [c["run"] for c in fake_calls(root)] == [1]                             # the code was never sent back
        with open(os.path.join(root, "fake-state.json")) as f:
            assert all(not v["used"] for v in json.load(f)["codes"].values())


def test_the_pages_are_told_the_verbs_a_gate_may_run_or_that_there_is_no_relay():
    def ctx_of(cmd, verbs):
        with _t.tmpdir() as root, inproc(root, cmd=cmd, verbs=verbs) as s:
            return s.app.ctx("en", "alice")["relay"]
    assert ctx_of([], []) is None
    assert ctx_of(["h"], [["facts", "confirm"], ["queue", "approve"]]) == [["facts", "confirm"], ["queue", "approve"]]
    assert ctx_of([], [["facts", "confirm"]]) is None and ctx_of(["h"], []) is None       # half a relay runs nothing: promise nothing
    with _t.tmpdir() as root, gated_srv(root, verbs=" facts   confirm , queue approve ,") as srv:
        assert srv.relay_line == "Relay verbs: facts confirm, queue approve"                # the option, parsed as it is used


def test_a_gate_the_console_cannot_run_draws_no_form_and_a_click_made_anyway_is_refused_before_any_harness():
    with _t.tmpdir() as root, Srv(root, env={"FAKE_HARNESS_LOG": os.path.join(root, "fake.log")}) as srv:     # no relay at all
        srv.agent(gated("k1"), raw_ask("confirm", "c1"), raw_ask("confirm", "q1", gate={
            "verb": ["queue", "approve", "spend-1"], "expect": {"items": {"spend-1": "yes"}}}))
        page = srv.get().text
        assert say("gate.norelay") in flat(page) and not [f for f in forms_of(page) if f.action == "answer"
                                                          and ("id", "hidden", "k1", False) in map(tuple, f.items)]
        assert [f for f in forms_of(page) if f.action == "answer"], "the ask with no gate still has its form"
        assert not [f for f in forms_of(page) if f.action == "answer_all"]                 # and no group button over a gate it cannot run
        r = srv.post("answer", {"id": "k1", "hash": srv.store.state()["asks"]["k1"]["hash"], "value": "4.5"})
        refused(r, 503, "err.gate_unavailable")
        assert srv.events("answer") == [] and fake_calls(root) == []
        hash_q1 = srv.store.state()["asks"]["q1"]["hash"]                                 # a "no" confirms nothing: it needs no harness
        refused(srv.post("answer", {"id": "q1", "hash": hash_q1, "value": "yes"}), 503, "err.gate_unavailable")
        saved(srv.post("answer", {"id": "q1", "hash": hash_q1, "value": "no"}))
        assert [e["value"] for e in srv.events("answer")] == ["no"] and fake_calls(root) == []
    with _t.tmpdir() as root, gated_srv(root, verbs="queue approve") as srv:          # a relay, but not for this verb
        srv.agent(gated("k1"), raw_ask("confirm", "q1", gate={"verb": ["queue", "approve", "spend-1"],
                                                            "expect": {"items": {"spend-1": "yes"}}}))
        page = srv.get().text
        assert not [f for f in forms_of(page) if f.action == "answer" and ("id", "hidden", "k1", False) in map(tuple, f.items)]
        assert flat(page).count(say("gate.norelay")) == 1                                   # k1 says so, before any click
        hash_k1 = srv.store.state()["asks"]["k1"]["hash"]
        refused(srv.post("answer", {"id": "k1", "hash": hash_k1, "value": "4.5"}), 503, "err.gate_unavailable")
        assert srv.events("answer") == [] and fake_calls(root) == []
        saved(srv.submit(form_for(page, "answer", "q1")))                                # an allowed verb goes through
        assert [c["run"] for c in fake_calls(root)] == [1, 2] and len(srv.events("answer")) == 1


def test_a_gated_ask_has_its_form_only_for_a_verb_that_starts_with_an_allowed_prefix_word_for_word():
    ok = {"k-long": ["facts", "confirm", "x"], "k-exact": ["queue", "approve"]}
    no = {"k-other": ["facts", "confirmed", "x"], "k-short": ["facts"], "k-wrong": ["queue", "reject", "x"],
          "k-late": ["do", "facts", "confirm", "x"]}
    with _t.tmpdir() as root, gated_srv(root) as srv:                                # facts confirm, queue approve
        srv.agent(*[gate_on(i, v, group=i) for i, v in {**ok, **no}.items()])
        page = srv.get().text
        def answers_of(i):                        # the values one ask's form offers
            return {tuple(x)[2] for f in forms_of(page) if f.action == "answer"
                    and ("id", "hidden", i, False) in map(tuple, f.items) for x in f.items if x[0] == "value"}
        drawn = {i for i in {**ok, **no} if "yes" in answers_of(i)}
        assert drawn == set(ok), drawn
        assert all(answers_of(i) == {"no"} for i in no), {i: answers_of(i) for i in no}    # a refusal needs no harness
        assert flat(page).count(say("gate.norelay")) == len(no)
        for id in no:                                                                # the relay agrees with the page: it runs none of these
            h = srv.store.state()["asks"][id]["hash"]
            refused(srv.post("answer", {"id": id, "hash": h, "value": "yes"}), 503, "err.gate_unavailable")
        assert fake_calls(root) == [] and srv.events("answer") == []
        for id in ok:
            saved(srv.submit(form_for(page, "answer", id)))
        assert [c["run"] for c in fake_calls(root)] == [1, 2, 1, 2] and len(srv.events("answer")) == 2
        h = srv.store.state()["asks"]["k-other"]["hash"]                              # a "no" still goes through, with no harness
        saved(srv.post("answer", {"id": "k-other", "hash": h, "value": "no"}))
        assert [c["run"] for c in fake_calls(root)] == [1, 2, 1, 2] and srv.events("answer")[-1]["value"] == "no"
        assert "gate" not in srv.events("answer")[-1]


def test_the_group_button_is_not_drawn_for_an_approval_or_for_a_gate_that_cannot_run_here():
    with _t.tmpdir() as root, gated_srv(root) as srv:
        srv.agent(raw_ask("confirm", "g1", group="Fine"), gate_on("g2", ["queue", "approve", "spend-1"], group="Fine"),
                  raw_ask("confirm", "a1", group="Spend"), raw_ask("approve", "a2", group="Spend"),
                  raw_ask("confirm", "n1", group="Stuck"), gate_on("n2", ["facts", "reject", "x"], group="Stuck"))
        page = srv.get().text
        buttons = [f for f in forms_of(page) if f.action == "answer_all"]
        assert len(buttons) == 1, len(buttons)                                       # Spend has an approval, Stuck a gate that cannot run
        assert sorted(v.split(":")[0] for n, _k, v, _c in buttons[0].items if n == "pair") == ["g1", "g2"]
        r = srv.submit(buttons[0])                                                   # the one that can: the gated ask goes through its harness
        saved(r)
        got = {e["id"]: e for e in srv.events("answer")}
        assert sorted(got) == ["g1", "g2"] and "gate" not in got["g1"] and got["g2"]["gate"]["ok"] is True
        assert [c["run"] for c in fake_calls(root)] == [1, 2]


def test_a_no_a_bad_value_a_stale_page_or_a_long_comment_never_reach_the_harness():
    with _t.tmpdir() as root, gated_srv(root) as srv:
        srv.agent(gated("k1"), raw_ask("confirm", "q1", gate={"verb": ["facts", "confirm", "flag"], "expect": {"items": {"flag": "yes"}}}))
        page = srv.get().text
        refused(srv.submit(form_for(page, "answer", "k1"), value="99"), 400, "bad_value.range")
        refused(srv.submit(form_for(page, "answer", "k1"), value="abc"), 400, "bad_value.number")
        refused(srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="c" * 501), 400, "err.bad_request")
        old = form_for(page, "answer", "k1")
        srv.agent(gated("k1", why="Revised by the agent."))
        refused(srv.submit(old, value="4.5"), 409, "err.changed")
        r = srv.submit(form_for(page, "answer", "q1"), value="no")                         # "no" confirms nothing
        saved(r)
        assert fake_calls(root) == []
        ev = srv.events("answer")[0]
        assert ev["value"] == "no" and "gate" not in ev
        saved(srv.submit(form_for(srv.get().text, "answer", "k1"), value="4.5"))          # the good counterpart
        assert [c["run"] for c in fake_calls(root)] == [1, 2]


def test_a_comment_is_checked_before_the_harness_runs_and_the_harness_gets_it_as_it_is_stored():
    with _t.tmpdir() as root, gated_srv(root) as srv:
        srv.agent(gated("k1"), gated("k2"), gated("k3"))
        page = srv.get().text
        refused(srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="c" * 501), 400, "err.bad_request")
        assert fake_calls(root) == [] and srv.events("answer") == []                    # the harness was never called
        refused(srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="c" * 500 + "é" * 1), 400, "err.bad_request")
        assert fake_calls(root) == [] and srv.events("answer") == []
        # the limit is on the comment as core.check_comment cleans it: 30 control characters do not count
        saved(srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="c" * 500 + "\x00" * 30))
        assert srv.events("answer")[0]["comment"] == "c" * 500
        # what the harness is given as the reason is that stored text, not the raw one
        saved(srv.submit(form_for(page, "answer", "k2"), value="4.5", comment="Invoice\x0017 ok\x07"))
        # a comment with nothing left after cleaning is no comment: the default reason
        saved(srv.submit(form_for(page, "answer", "k3"), value="4.5", comment="\x00\x07 \t"))
        by_id = {e["id"]: e for e in srv.events("answer")}
        assert by_id["k2"]["comment"] == "Invoice17 ok" and "comment" not in by_id["k3"]
        reasons = [c["argv"][4] for c in fake_calls(root) if c["run"] == 1]
        assert reasons == ["--reason=" + "c" * 200, "--reason=Invoice17 ok", "--reason=console"], reasons


def test_an_answer_is_checked_in_order_and_the_harness_is_reached_only_after_every_check():
    """open, then the page's hash, then the value, then the comment, then the gate, then the write."""
    with _t.tmpdir() as root, gated_srv(root) as srv:
        srv.agent(gated("k1"), gated("k0"))
        old = form_for(srv.get().text, "answer", "k1")
        srv.agent(gated("k1", why="The agent found out more."))                      # the page is now stale
        fresh = form_for(srv.get().text, "answer", "k1")
        srv.store.withdraw("bot", ["k0"], "Not needed.")

        def untouched():
            assert fake_calls(root) == [] and srv.events("answer") == []
        bad, long = "99", "c" * 501
        gone = old.data(id="nobody", value=bad, comment=long)
        refused(srv.post("answer", gone, token=False), 409, "err.unknown_id")                              # 1 open: before the hash
        untouched()
        refused(srv.post("answer", old.data(id="k0", value=bad, comment=long), token=False), 409, "err.not_open")
        untouched()
        refused(srv.submit(old, value=bad, comment=long), 409, "err.changed")                                # 2 hash: before the value
        untouched()
        refused(srv.submit(fresh, value=bad, comment=long), 400, "bad_value.range")                          # 3 value: before the comment
        untouched()
        refused(srv.submit(fresh, value="4.5", comment=long), 400, "err.bad_request")                        # 4 comment: before the harness
        untouched()
        saved(srv.submit(fresh, value="4.5", comment="ok"))                                                  # 5 gate, then 6 the write
        assert [c["run"] for c in fake_calls(root)] == [1, 2]
        ev = srv.events("answer")[0]
        assert ev["gate"]["ok"] is True and ev["value"] == "4.5" and ev["subject"] == fresh.value("hash")


def test_a_refusal_after_the_harness_ran_is_reported_and_logged_not_hidden():
    with _t.tmpdir() as root, gated_srv(root, "slow", env={"FAKE_HARNESS_SLOW_SECS": "1.5"}) as srv:
        srv.agent(gated("k1"))
        form = form_for(srv.get().text, "answer", "k1")
        out = []
        click = threading.Thread(target=lambda: out.append(srv.submit(form, value="4.5")))
        click.start()
        harness_started(root)                                                        # the harness is running now
        srv.store.withdraw("bot", ["k1"], "Not needed any more.")                    # and the ask goes while it does
        click.join(30)
        refused(out[0], 409, "err.not_recorded")                                     # the person is told to check, not "not saved"
        assert say("result.check") in flat(out[0].text) and say("result.none") not in flat(out[0].text)
        assert srv.events("answer") == [] and [c["run"] for c in fake_calls(root)] == [1, 2]
        assert "the harness was written for ask k1" in srv.log()                     # and the operator can see that the harness ran


def test_a_failure_to_record_after_the_harness_ran_says_check_before_you_go_on_whatever_the_failure_was():
    with _t.tmpdir() as root, fake_env(root), captured_log() as seen:
        with inproc(root, cmd=[sys.executable, FAKE], verbs=[["facts", "confirm"]]) as s:
            s.store.post("bot", [gated("k1"), gated("k2")])
            page = fetch(s.url).text
            real = s.app.store.answer

            def broken(*a, **k):
                raise PermissionError("disk-detail-3310")
            s.app.store.answer = broken
            form = form_for(page, "answer", "k1")
            r = fetch(s.url + "answer", "POST", urllib.parse.urlencode(form.data(value="4.5", comment="mine")).encode(),
                      {"Content-Type": FORM})
            refused(r, 409, "err.not_recorded")                                        # not the 500 "nothing was saved"
            assert say("result.check") in flat(r.text) and say("err.server_error") not in flat(r.text)
            assert kept("mine") in flat(r.text)
            assert [e["type"] for e in s.store.events()] == ["ask", "ask"]              # no answer was recorded
            assert [c["run"] for c in fake_calls(root)] == [1, 2]                       # the harness was written
            s.app.store.answer = real                                                   # the disk is back: the other ask is recorded
            saved(fetch(s.url + "answer", "POST", urllib.parse.urlencode(form_for(page, "answer", "k2").data()).encode(),
                        {"Content-Type": FORM}))
    assert any(lvl == logging.WARNING and "the harness was written for ask k1" in msg and "PermissionError" in msg
               for lvl, msg, _ in seen), seen
    assert not any("disk-detail" in msg for lvl, msg, _ in seen if lvl >= logging.INFO), seen


def test_a_log_that_cannot_be_written_after_the_harness_ran_is_check_before_you_go_on():
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        print("SKIP the file mode does not stop this user")
        return
    with _t.tmpdir() as root, gated_srv(root) as srv:
        srv.agent(gated("k1"), gated("k2"))
        page = srv.get().text
        os.chmod(srv.store.path, 0o444)
        try:
            r = srv.submit(form_for(page, "answer", "k1"), value="4.5", comment="kept")
            assert srv.get().status == 200                                              # it can still be read
        finally:
            os.chmod(srv.store.path, 0o600)
        refused(r, 409, "err.not_recorded")
        assert kept("kept") in flat(r.text) and [c["run"] for c in fake_calls(root)] == [1, 2]
        assert srv.events("answer") == [] and "the harness was written for ask k1" in srv.log()
        saved(srv.submit(form_for(page, "answer", "k2")))                               # writable again


def test_a_write_that_fails_on_the_disk_is_reported_for_that_ask_alone_and_the_group_goes_on():
    with _t.tmpdir() as root, captured_log() as seen, inproc(root) as s:
        s.store.post("bot", [raw_ask("confirm", f"c{n}") for n in range(1, 5)])
        page = fetch(s.url).text
        real, tried = s.app.store.answer, []

        def flaky(*a, **k):
            tried.append(a[1])
            if a[1] == "c3":
                raise OSError(28, "No space left on device")
            return real(*a, **k)
        s.app.store.answer = flaky
        group = form_for(page, "answer_all")
        r = fetch(s.url + "answer_all", "POST", urllib.parse.urlencode(group.data()).encode(), {"Content-Type": FORM})
        assert r.status == 200 and say("result.some") in flat(r.text), (r.status, flat(r.text)[:300])
        assert [k for k, _ in results(r.text)] == ["ok", "ok", "bad", "ok"] and say("err.server_error") in results(r.text)[2][1]
        assert tried == ["c1", "c2", "c3", "c4"]                                        # the ones after it were still tried
        assert sorted(e["id"] for e in s.store.events() if e["type"] == "answer") == ["c1", "c2", "c4"]
        r = fetch(s.url + "answer", "POST", urllib.parse.urlencode(form_for(page, "answer", "c3").data(comment="mine")).encode(),
                  {"Content-Type": FORM})
        refused(r, 500, "err.server_error")                                              # one ask: nothing was saved, and it says so
        assert kept("mine") in flat(r.text) and say("result.check") not in flat(r.text)
        s.app.store.answer = real
        saved(fetch(s.url + "answer", "POST", urllib.parse.urlencode(form_for(page, "answer", "c3").data()).encode(),
                    {"Content-Type": FORM}))
    assert sum(1 for lvl, msg, _ in seen if lvl == logging.WARNING and "the answer to ask c3 could not be written" in msg) == 2
    assert not any("No space left" in msg for lvl, msg, _ in seen if lvl >= logging.INFO), seen


def test_the_one_time_code_and_what_people_typed_are_not_logged():
    marker, value = "zx-comment-4471", "31.25"
    with _t.tmpdir() as root, gated_srv(root, args=("--log-level", "debug")) as srv:
        srv.agent(gated("k1"))
        saved(srv.submit(form_for(srv.get().text, "answer", "k1"), value=value, comment=marker))
        srv.post("note", {"text": "zx-note-7788"})
        log = srv.log()
        with open(os.path.join(root, "fake-state.json")) as f:
            codes = list(json.load(f)["codes"])
        assert codes and "relay call 2" in log and "--code=***" in log                 # the log was on, and hid the code
        assert not any(c in log for c in codes) and srv.secret().decode() not in log
        assert "zx-note-7788" not in log
    with _t.tmpdir() as root, gated_srv(root, args=("--log-level", "info")) as srv:
        srv.agent(gated("k1"))
        saved(srv.submit(form_for(srv.get().text, "answer", "k1"), value=value, comment=marker))
        srv.post("note", {"text": "zx-note-7788"})
        log = srv.log()
        assert "POST /answer" in log and "POST /note" in log
        for secret in (marker, value, "zx-note-7788", "--value", srv.secret().decode()):
            assert secret not in log, secret
        with open(os.path.join(root, "fake-state.json")) as f:
            assert not any(c in log for c in json.load(f)["codes"])


def test_a_poll_is_not_an_access_log_line_at_info_but_is_at_debug():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.get("poll")
        srv.get()
        log = srv.log()
        assert "GET / HTTP" in log and "GET /poll" not in log
    with _t.tmpdir() as root, Srv(root, "--log-level", "debug") as srv:
        srv.get("poll")
        assert "GET /poll" in srv.log()


def test_the_lock_is_not_held_while_the_harness_runs_and_a_second_click_finds_the_ask_taken():
    with _t.tmpdir() as root, gated_srv(root, "slow", env={"FAKE_HARNESS_SLOW_SECS": "1.5"}) as srv:
        srv.agent(gated("s1"), gated("s2"))
        page = srv.get().text
        out, t0 = {}, time.monotonic()

        def click(id):
            out[id] = srv.submit(form_for(page, "answer", id), value="4.5")
        a, b = threading.Thread(target=click, args=("s1",)), threading.Thread(target=click, args=("s2",))
        a.start()
        time.sleep(0.4)                                                                  # s1 is now waiting on the harness
        b.start()

        def quick(request):
            t = time.monotonic()
            r = request()
            assert time.monotonic() - t < 0.6, "the harness's wait blocked another request"
            return r
        noted(quick(lambda: srv.post("note", {"text": "while the harness runs"})))
        assert quick(srv.get).status == 200 and quick(lambda: srv.get("poll")).status == 200
        refused(quick(lambda: srv.submit(form_for(page, "answer", "s1"), value="4.5")), 409, "err.not_open")
        a.join(30)
        b.join(30)
        saved(out["s1"])
        saved(out["s2"])
        assert time.monotonic() - t0 < 2.9, "two asks waited on the harness one after the other"
        assert sorted(e["id"] for e in srv.events("answer")) == ["s1", "s2"]
        assert [c["run"] for c in fake_calls(root)].count(1) == 2                          # the second click ran nothing


def test_an_ask_revised_while_the_harness_runs_is_recorded_as_revised_for_what_was_shown():
    with _t.tmpdir() as root, gated_srv(root, "slow", env={"FAKE_HARNESS_SLOW_SECS": "1.2"}) as srv:
        srv.agent(gated("k1"))
        form = form_for(srv.get().text, "answer", "k1")
        out = []
        click = threading.Thread(target=lambda: out.append(srv.submit(form, value="4.5")))
        click.start()
        time.sleep(0.5)                                                   # the harness is running now
        srv.agent(gated("k1", why="The agent found out more."))
        click.join(30)
        saved(out[0])
        ev = srv.events("answer")[0]                                      # the harness was written, so the answer is kept
        assert ev["subject"] == form.value("hash") and ev["revised"] is True and ev["gate"]["ok"] is True
        assert say("history.revised") in flat(srv.get("history").text)


def test_a_harness_that_does_not_answer_in_time_is_a_gate_timeout_the_console_records_nothing_and_says_what_it_can():
    keys = ("FAKE_HARNESS_MODE", "FAKE_HARNESS_SLOW_SECS", "FAKE_HARNESS_SLOW_RUN", "FAKE_HARNESS_STATE")
    saved_env = {k: os.environ.get(k) for k in keys}
    old_timeout = serve.RELAY_TIMEOUT
    try:
        # the first call hangs: nothing was asked yet; the second hangs: the harness may have written before it stopped
        for which, words in (("1", core.CODES["gate_timeout"]), ("2", relay.LATE)):
            with _t.tmpdir() as root:
                os.environ.update({"FAKE_HARNESS_MODE": "slow", "FAKE_HARNESS_SLOW_SECS": "30", "FAKE_HARNESS_SLOW_RUN": which,
                                   "FAKE_HARNESS_STATE": os.path.join(root, "state.json")})
                serve.RELAY_TIMEOUT = 1.0
                with inproc(root, cmd=[sys.executable, FAKE], verbs=[["facts", "confirm"]]) as s:
                    s.store.post("bot", [gated("k1")])
                    form = form_for(fetch(s.url).text, "answer", "k1")
                    t0 = time.monotonic()
                    r = fetch(s.url + "answer", "POST", urllib.parse.urlencode(form.data()).encode(), {"Content-Type": FORM})
                    refused(r, 504, "err.gate_timeout")
                    assert say("result.refused", message=words) in flat(r.text), (which, flat(r.text))
                    assert 0.9 < time.monotonic() - t0 < 8
                    assert s.store.state()["asks"]["k1"]["status"] == "open" and s.store.events()[-1]["type"] == "ask"
    finally:
        serve.RELAY_TIMEOUT = old_timeout
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ------------------------------------------------------ who is answering, in what language --

def test_behind_a_proxy_the_user_is_the_header_and_without_one_the_page_is_read_only():
    with _t.tmpdir() as root, Srv(root, "--user-header", "X-Forwarded-User", user=None) as srv:
        srv.agent(raw_ask("confirm", "c1"), raw_ask("confirm", "c2"))
        as_bob = {"X-Forwarded-User": "bob"}
        page = srv.get(**as_bob).text
        assert say("foot.user", user="bob") in flat(page)
        tok = srv.token(**as_bob)
        saved(srv.submit(form_for(page, "answer", "c1"), headers=as_bob))
        assert srv.events("answer")[0]["by"] == "web:bob"
        anon = srv.get().text                                                       # nobody logged in
        assert say("ro.banner") in flat(anon) and say("foot.ro") in flat(anon) and not forms_of(anon)
        for who in (None, "", "-x", "a b", "-", "x" * 81):
            n = len(srv.events())
            refused(srv.post("answer", {"id": "c2", "hash": "0" * 16, "value": "yes"}, token=tok,
                             headers={"X-Forwarded-User": who}), 403, "err.forbidden")
            assert len(srv.events()) == n, who
        refused(srv.post("note", {"text": "hi"}, token=tok, headers={"X-Forwarded-User": None}), 403, "err.forbidden")
        # two copies of the header are not one user
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        body = urllib.parse.urlencode({"token": tok, "text": "twice"})
        conn.putrequest("POST", "/note")
        for k, v in (("Content-Type", FORM), ("Content-Length", str(len(body))), ("X-Forwarded-User", "bob"),
                     ("X-Forwarded-User", "mallory")):
            conn.putheader(k, v)
        conn.endheaders(body.encode())
        assert conn.getresponse().status == 403
        conn.close()
        assert srv.events("note") == []
        noted(srv.post("note", {"text": "hi"}, token=tok, headers=as_bob))            # the good counterpart
        assert srv.events("note")[0]["by"] == "web:bob"


def test_without_a_user_header_option_a_sent_header_is_ignored():
    with _t.tmpdir() as root, Srv(root, user="alice") as srv:
        srv.agent(raw_ask("confirm", "c1"))
        page = srv.get(**{"X-Forwarded-User": "mallory"}).text
        assert say("foot.user", user="alice") in flat(page) and "mallory" not in page
        saved(srv.submit(form_for(page, "answer", "c1"), headers={"X-Forwarded-User": "mallory"}))
        assert srv.events("answer")[0]["by"] == "web:alice"


def test_the_user_header_is_believed_only_from_a_loopback_peer_and_only_if_it_is_a_plain_name():
    def user_of(peer, value):
        h = object.__new__(serve.Handler)
        h.client_address = (peer, 5555)
        h.headers = email.message.Message()
        if value is not None:
            h.headers["X-User"] = value
        h.server = types.SimpleNamespace(app=types.SimpleNamespace(user_header="X-User", user="fallback"))
        return h._user()
    for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        assert user_of(peer, "bob") == "bob", peer
    for peer in ("10.0.0.5", "::ffff:10.0.0.5", "203.0.113.7", "192.168.1.9", "fe80::1", "2001:db8::1"):
        assert user_of(peer, "bob") is None, peer                                      # a direct client cannot name itself
    for value in ("-x", "a b", "bob\n", "", "a" * 81, "bob;x", " bob"):
        assert user_of("127.0.0.1", value) is None, repr(value)
    assert user_of("127.0.0.1", None) is None                                           # no header, nobody (no fallback)
    assert user_of("127.0.0.1", "bo:b/x@y.z-1") == "bo:b/x@y.z-1"


def test_the_language_a_person_picks_is_kept_in_the_cookie_named_for_the_port_and_the_operators_is_the_default():
    with _t.tmpdir() as root, Srv(root) as srv:
        jar = f"console_lang_{srv.port}"
        srv.agent(raw_ask("confirm", "c1"))
        en = srv.get()
        assert '<html lang="en">' in en.text and "Set-Cookie" not in en.headers
        zh = srv.get("?lang=zh")
        assert '<html lang="zh">' in zh.text and say("page.waiting", "zh") in zh.text
        assert len(zh.headers.get_all("Set-Cookie")) == 1
        attrs = [x.strip() for x in zh.headers["Set-Cookie"].split(";")]
        assert attrs[0] == f"{jar}=zh" and "SameSite=Lax" in attrs and "HttpOnly" in attrs, attrs
        assert not [a for a in attrs if a.lower().split("=")[0] in ("path", "domain")], attrs   # scoped by the browser to where it is served
        # a later request, with no ?lang, uses it, on every page
        assert say("nav.history", "zh") in srv.get("history", Cookie=f"{jar}=zh").text          # the internal link has no ?lang
        assert say("nav.history", "en") in srv.get("history").text
        assert '<html lang="zh">' in srv.get("history", Cookie=f"a=b; {jar}=zh; c=d").text
        assert '<html lang="zh">' in srv.get("history", Cookie=f"a=b;{jar}=zh").text
        two = raw(srv.port, f"GET /history HTTP/1.0\r\nHost: 127.0.0.1:{srv.port}\r\nCookie: a=b\r\nCookie: {jar}=zh\r\n\r\n".encode())
        assert '<html lang="zh">' in two.text                                                    # a proxy may send the jar in two lines
        assert "Set-Cookie" not in srv.get("history", Cookie=f"{jar}=zh").headers              # kept, not set again
        # what is not one of ours is ignored: another value, another cookie that ends the same
        for junk in ("lang=zh", f"{jar}=fr", f"{jar}=", f"{jar}=zhx", f"x{jar}=zh", f"{jar}=ZH",
                     f"{jar}=zh-CN", f'{jar}="zh"', f"{jar}=zh_", f"my_{jar}=zh", "zh"):
            assert '<html lang="en">' in srv.get("history", Cookie=junk).text, junk
        assert '<html lang="zh">' in srv.get("history", Cookie=f"{jar}=fr; {jar}=zh").text     # a junk one is skipped
        assert '<html lang="en">' in srv.get("history", Cookie=f"{jar}=en; {jar}=zh").text     # the first good one wins
        # ?lang wins over the cookie and becomes the cookie; one that is not ours changes nothing
        back = srv.get("?lang=en", Cookie=f"{jar}=zh")
        assert '<html lang="en">' in back.text and back.headers["Set-Cookie"].startswith(f"{jar}=en;")
        for bad in ("fr", "", "zhx", "ZH", "zh%00", "zh,en"):
            r = srv.get("?lang=" + bad, Cookie=f"{jar}=zh")
            assert '<html lang="zh">' in r.text and "Set-Cookie" not in r.headers, bad
            assert '<html lang="en">' in srv.get("?lang=" + bad).text and "Set-Cookie" not in srv.get("?lang=" + bad).headers, bad
        assert srv.get("history?lang=zh").headers["Set-Cookie"].startswith(f"{jar}=zh;")        # on any page that is a page
        assert "Set-Cookie" not in srv.get("static/console.css?lang=zh").headers and "Set-Cookie" not in srv.get("nope?lang=zh").headers
        # the pages after a click and the errors speak it too
        r = srv.submit(form_for(srv.get("", Cookie=f"{jar}=zh").text, "answer", "c1"), headers={"Cookie": f"{jar}=zh"})
        assert say("result.line_ok", "zh", value=say("ans.yes", "zh")) in flat(r.text) and '<html lang="zh">' in r.text
        assert say("err.bad_host", "zh") in flat(srv.get("", Host="evil.example", Cookie=f"{jar}=zh").text)
    with _t.tmpdir() as root, Srv(root, "--lang", "zh") as srv:
        jar = f"console_lang_{srv.port}"
        assert '<html lang="zh">' in srv.get().text and '<html lang="en">' in srv.get("?lang=en").text
        assert '<html lang="en">' in srv.get(Cookie=f"{jar}=en").text                           # a person's pick beats the operator's
        assert '<html lang="zh">' in srv.get(Cookie="lang=en").text
    with _t.tmpdir() as root, Srv(root, "--title", "Northwind <Console>") as srv:                    # the operator's title is escaped
        assert "Northwind &lt;Console&gt;" in srv.get().text


def test_two_consoles_on_one_host_keep_their_own_language_because_cookies_ignore_ports():
    with _t.tmpdir() as a_root, _t.tmpdir() as b_root, Srv(a_root) as a, Srv(b_root) as b:
        assert a.port != b.port
        picked = a.get("?lang=zh").headers["Set-Cookie"].split(";")[0]                 # what the browser stores for the host
        assert picked == f"console_lang_{a.port}=zh" and picked != b.get("?lang=zh").headers["Set-Cookie"].split(";")[0]
        assert '<html lang="zh">' in a.get(Cookie=picked).text
        assert '<html lang="en">' in b.get(Cookie=picked).text                        # the other console's choice is not ours
        both = f"{picked}; console_lang_{b.port}=en"
        assert '<html lang="zh">' in a.get(Cookie=both).text and '<html lang="en">' in b.get(Cookie=both).text
        assert '<html lang="en">' in a.get(Cookie="console_lang=zh").text             # and the name without a port is nobody's


# ---------------------------------------------------- everyone logged in decides --

def test_everyone_logged_in_answers_and_notes_and_the_retired_team_review_is_gone():
    with _t.tmpdir() as root:
        d = os.path.join(root, "d")
        code, out, err = run_serve("--dir", d, "--port", "0", "--user", "alice", "--deciders", "alice")
        assert code == 2 and out == "" and "--deciders" in err and not os.path.exists(d)   # an unknown option, nothing made
    with _t.tmpdir() as root, Srv(root, "--user-header", "X-Forwarded-User", user=None) as srv:
        srv.agent(raw_ask("confirm", "c1"))
        for who in ("alice", "carol"):
            page = srv.get(**{"X-Forwarded-User": who}).text
            assert {f.action for f in forms_of(page)} >= {"answer", "note"} and form_for(page, "answer", "c1")
        tok = srv.token(**{"X-Forwarded-User": "carol"})
        r = srv.post("advise", {"id": "c1", "on_seq": "1", "stance": "agree"}, token=tok, headers={"X-Forwarded-User": "carol"})
        assert r.status == 404 and srv.events("advice") == []
        noted(srv.post("note", {"text": "hi"}, token=tok, headers={"X-Forwarded-User": "carol"}))


def test_what_a_person_types_is_shown_back_only_as_text():
    with _t.tmpdir() as root, Srv(root) as srv:
        srv.agent(raw_ask("provide", "t1", input={"type": "text"}, recommend=None))
        page = srv.get().text
        payload = '<script>alert("x")</script>'
        r = srv.submit(form_for(page, "answer", "t1"), value=payload, comment="<img src=x onerror=alert(1)>")
        saved(r)
        assert "&lt;script&gt;" in r.text and "<script>alert" not in r.text and len(re.findall(r"<script", r.text)) == 1
        hist = srv.get("history").text
        assert "<script>alert" not in hist and "<img" not in hist and "&lt;img" in hist
        assert srv.events("answer")[0]["value"] == payload                                 # stored as typed


if __name__ == "__main__":
    _t.main(globals())
