#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The human's side of the console: the pages, and the four things a click can
do. Stdlib only. The agent's side is ask.py; the two meet only in the event
log (core.py), and this file never calls the agent's verbs (post, withdraw,
applied, say), as ask.py never calls the human's (tests/test_boundary.py).

    CONSOLE_DIR=… uv run serve.py [--dir DIR] [--host 127.0.0.1] [--port 8770]
        [--user NAME | --user-header NAME] [--allow-host HOST[:PORT]]
        [--title TEXT] [--lang en|zh] [--relay-cmd "CMD ARGS"]
        [--relay-verbs "facts confirm,queue approve"] [--default-reason TEXT]
        [--log-level debug|info|warning]

There is no login. With --user, anyone who can reach the port is that person, so
a --host that is not loopback (127.0.0.1, ::1, localhost) is refused unless
--user-header says who is asking (a proxy sets it): a console that writes and
knows nobody is never bound to the network.

A request is turned away, before anything is read or written, unless:
  host_ok       its Host is a loopback name (our own port or none) or an --allow-host
                (421); an attacker's page that resolves to us is not ours
  same_origin   a POST that carries Origin or Sec-Fetch-Site says same-origin (403); Origin
                is compared with Host, so a proxy must pass the public Host through; an
                `Origin: null` (what a browser sends when a proxy's Referrer-Policy is
                no-referrer) counts only with Sec-Fetch-Site: same-origin, which a page
                cannot forge
  _form         a POST is a form of at most 64 KB (400 / 413 / 415)
  token_ok      a POST holds this console's page token (403, constant time): a keyed hash
                of the folder's secret, so a page opened before a restart still answers
                after it, and a page of another folder's console never does
  _user         and someone is logged in: --user, or with --user-header the
                header's value, believed only from a loopback peer (403)
Every response carries the CSP, frame, sniffing and referrer headers; pages and
/poll are never cached; static files are served by exact name from static/;
any other method is 405, any unknown path 404, and a failure is an error page,
never a stack trace, not even for a request line that is not HTTP at all. A form value
or a one-time code is never logged above debug (and the code never at all: relay.py
hides it). A console that cannot start (no folder, a secret that is too short, a port
that is taken or out of range, an address that cannot be bound) says so in one line on
stderr and exits 2.

A click writes one way. /answer checks, in this order and each before the next,
that the ask is open, is still the one the page showed (`changed`), that the value
is valid, that the comment is (core.check_comment), and only then, if the ask has a
gate, sends it through the operator's harness (relay.py) and records the
harness's word with the answer: nothing a harness must not see reaches it, and a
failure to record it after the harness ran (a refusal, or the disk) is reported as
"check before you go on", never as "nothing was saved". A refused answer gives the
typed comment back. One click per ask at a time (`Console.claim`), so a second click
never runs the harness twice; the lock is held only for the write, never while the
harness runs. A saved note or reopen answers 303 to the waiting page, which says so
(`?done=`: only the two known words are shown, nothing else), so a reload cannot
write twice; the answer and answer-all results are pages of their own. console.js sends
the forms itself and shows those same lines over the page it is on, so the person never
leaves it; the server has one way to answer and does not know which it is talking to. The language
a person picks with ?lang= is kept in the cookie `console_lang_<port>` (no Path, so
it stays with the console's own prefix; the port, so two consoles on one host keep
their own), because the pages' internal links carry none.
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import http.client
import http.server
import json
import logging
import os
import re
import shlex
import socket
import sys
import threading
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core  # noqa: E402
import i18n  # noqa: E402
import pages  # noqa: E402
import relay  # noqa: E402

log = logging.getLogger("console.serve")

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "[::1]"}       # a Host header
LOOPBACK_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}  # who may name the user through the header
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}         # what --host may be without a --user-header
LANG_COOKIE = "console_lang"                               # + "_<port>": cookies ignore ports, names do not
TOKEN_KEY = b"console-page-token"
HOST_RE = re.compile(r"(\[[0-9a-f:.]+\]|[a-z0-9._-]+)(?::([0-9]{1,5}))?")
MAX_BODY, DRAIN = 64 * 1024, 1 << 20     # a bigger body is refused; up to DRAIN of it is read so the refusal reaches the client
RELAY_TIMEOUT = 60.0                     # per call of the harness (relay makes two)
FORM = "application/x-www-form-urlencoded"
SECURITY = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
                               "img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
    # not "no-referrer": under it a browser sends `Origin: null` on every same-origin form POST, which
    # same_origin() accepts only with fetch metadata. same-origin leaks nothing cross-site.
    "Referrer-Policy": "same-origin"}
# the HTTP status of a code; anything not here is a conflict with the current state
STATUS = {"bad_request": 400, "bad_value": 400, "forbidden": 403, "forbidden_event": 403, "not_found": 404,
          "method_not_allowed": 405, "too_large": 413, "bad_host": 421, "corrupt_log": 500, "server_error": 500,
          "gate_unavailable": 503, "gate_timeout": 504}
STATIC = os.path.join(HERE, "static")
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
FILES = {f"/static/{n}": (os.path.join(STATIC, n), TYPES[os.path.splitext(n)[1]])
         for n in (sorted(os.listdir(STATIC)) if os.path.isdir(STATIC) else [])
         if os.path.splitext(n)[1] in TYPES}
PAGES = ("/", "/history", "/poll")
# a saved note or reopen goes to the waiting page and says what happened (relative, so a proxy's prefix holds)
AFTER = {"/note": "./?done=noted", "/reopen": "./?done=reopened"}


class Reject(Exception):
    """A request turned away before anything was read or written."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status or STATUS.get(code, 409)


# ------------------------------------------------------ the safety checks --

def split_host(header) -> tuple[str, int | None] | None:
    m = HOST_RE.fullmatch((header or "").strip().lower())
    return (m.group(1), int(m.group(2)) if m.group(2) else None) if m else None


def host_ok(header, port: int, allow: list) -> bool:
    """Is this Host one of ours? A loopback name may carry our own port or none;
    an --allow-host entry any port unless it names one."""
    h = split_host(header)
    if h is None:
        return False
    if h[0] in LOOPBACK_NAMES:
        return h[1] in (None, port)
    return any(h[0] == a[0] and a[1] in (None, h[1]) for a in allow)


def same_origin(headers) -> bool:
    """A POST that says where it came from must say: this very site. One that
    says nothing is not a browser page, and still needs the token. `Origin:
    null` says nothing about where, so only fetch metadata can vouch for it."""
    site, origin = headers.get("Sec-Fetch-Site"), headers.get("Origin")
    if site is not None and site != "same-origin":
        return False
    if origin is None:
        return True
    if origin == "null":
        return site == "same-origin"
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and u.netloc.lower() == (headers.get("Host") or "").strip().lower()


def token_ok(given, token: str) -> bool:
    return hmac.compare_digest((given or "").encode(), token.encode())


# ------------------------------------------------------------ the console --

class Console:
    """What one running console shares between requests."""

    def __init__(self, store, secret, *, port, user, user_header, allow, title, lang, cmd, verbs, reason):
        self.store, self.secret, self.port = store, secret, port
        self.user, self.user_header, self.allow = user, user_header, allow
        self.title, self.lang, self.cmd, self.verbs, self.reason = title, lang, cmd, verbs, reason
        self.token = hmac.new(secret, TOKEN_KEY, "sha256").hexdigest()[:32]
        self.lang_cookie = f"{LANG_COOKIE}_{port}"
        self.lock = threading.Lock()       # every human write, and `busy`
        self.busy: set[str] = set()        # asks whose click is being handled
        self._cache: tuple = (None, None)  # ((mtime_ns, size), state)

    def state(self) -> dict:
        """The folded log, re-read only when the file changed. Raises Refused(corrupt_log)."""
        try:
            s = os.stat(self.store.path)
        except FileNotFoundError:
            return core.fold([])
        key = (s.st_mtime_ns, s.st_size)
        if self._cache[0] != key:
            self._cache = (key, self.store.state())
        return self._cache[1]

    def ctx(self, lang: str, user) -> dict:
        """`relay` is the verb prefixes a gate may run here, or None: the page draws
        a gated ask's form only for a verb that starts with one of them."""
        return {"lang": lang, "title": self.title, "user": user, "token": self.token,
                "relay": self.verbs if self.cmd and self.verbs else None,
                "now": core.now(), "secret": self.secret}

    @contextmanager
    def claim(self, id: str):
        """One click per ask at a time: a second, while the first waits on the harness, finds it taken."""
        with self.lock:
            if id in self.busy:
                raise core.Refused("not_open", id=id, status="answering")
            self.busy.add(id)
        try:
            yield
        finally:
            with self.lock:
                self.busy.discard(id)


# --------------------------------------------------------- the four writes --

def _one(form: dict, name: str, default=None) -> str:
    v = form.get(name, [])
    if len(v) > 1 or (not v and default is None):
        raise core.Refused("bad_request", f"the form needs exactly one {name}")
    return v[0] if v else default


def _ask_of(c: Console, id: str):
    try:
        return c.state()["asks"].get(id)
    except core.Refused:
        return None


def _code(e) -> str:
    """The code of a refusal. Whatever else stopped a click (an OSError: the disk) is a failure of ours."""
    return getattr(e, "code", "server_error")


def _line(lang: str, cur=None, value=None, refused=None, message: str = "", comment: str = "") -> dict:
    """One row of a result page. Only a harness's own words travel as `message`; the
    comment a person typed comes back with a refusal, so nothing typed is lost."""
    ask = cur["ask"] if cur else None
    line = {"ok": refused is None, "id": cur["id"] if cur else None, "title": ask["title"] if ask else None,
            "value": pages.answer_label(lang, ask, value) if ask and value is not None and not refused else None,
            "code": _code(refused) if refused else None, "message": message}
    if refused and comment.strip():
        line["comment"] = comment.strip()
    if refused and _code(refused) == "bad_value":
        line["reason"] = refused.params.get("reason")
    return line


def _through_gate(c: Console, user: str, ask: dict, value: str, comment: str) -> dict:
    if not c.cmd:
        raise core.Refused("gate_unavailable")
    r = relay.run(ask, value, cmd=c.cmd, verbs=c.verbs, user=user, reason=comment or c.reason,
                  timeout=RELAY_TIMEOUT)
    if not r["ok"]:
        raise core.Refused(r["code"], r["message"])
    return {"ok": True, "verb": r["verb"], "message": r["message"]}


def _answer(c: Console, user: str, lang: str, id: str, shown: str, value, comment: str) -> dict:
    """One ask answered. `value` None means its suggestion. Everything that needs
    no harness is checked first, so a stale page or a bad value never reaches one."""
    gate, typed = None, comment
    try:
        with c.claim(id):
            cur = c.state()["asks"].get(id)
            if cur is None or cur["status"] != "open":
                raise core.Refused("unknown_id" if cur is None else "not_open", id=id)
            if cur["hash"] != shown:
                raise core.Refused("changed", id=id)
            ask = cur["ask"]
            value, err = core.check_value(ask, (ask.get("recommend") or {}).get("value") if value is None else value)
            if err:
                raise core.Refused("bad_value", err["message"], reason=err["reason"])
            comment = core.check_comment(comment)          # before the harness, not after; it gets the cleaned one
            if core.gate_runs(ask, value):
                gate = _through_gate(c, user, ask, value, comment)
            with c.lock:
                c.store.answer(user, id, value, shown=shown, comment=comment, gate=gate)
    except (core.Refused, OSError) as e:
        cause = getattr(e, "code", type(e).__name__)
        if gate:
            log.warning("the harness was written for ask %s but the console could not record it (%s)", id, cause)
            e = core.Refused("not_recorded", id=id, cause=cause)
        elif not isinstance(e, core.Refused):
            log.warning("the answer to ask %s could not be written (%s)", id, cause)
        return _line(lang, _ask_of(c, id), refused=e, message=str(e) if _code(e) in pages.HARNESS_SAYS else "",
                     comment=typed)
    return _line(lang, _ask_of(c, id), value, message=gate["message"] if gate else "")


def h_answer(c, user, lang, form):
    return [_answer(c, user, lang, _one(form, "id"), _one(form, "hash"), _one(form, "value", ""),
                    _one(form, "comment", ""))]


def h_answer_all(c, user, lang, form):
    pairs = form.get("pair", [])
    if not pairs:
        raise core.Refused("bad_request", "no asks were sent")
    return [_answer(c, user, lang, id, shown, None, "")
            for id, _, shown in (pair.partition(":") for pair in pairs)]


def h_reopen(c, user, lang, form):
    id, seq = _one(form, "id"), _one(form, "answer_seq")
    try:
        if not re.fullmatch(r"[0-9]{1,9}", seq):
            raise core.Refused("bad_request", "answer_seq is a number")
        with c.lock:
            c.store.reopen(user, id, int(seq))
    except core.Refused as e:
        return [_line(lang, _ask_of(c, id), refused=e)]
    return [_line(lang, _ask_of(c, id))]


def h_note(c, user, lang, form):
    with c.lock:
        c.store.note(user, _one(form, "text"))
    return [_line(lang)]


POSTS = {"/answer": h_answer, "/answer_all": h_answer_all, "/reopen": h_reopen, "/note": h_note}


# ------------------------------------------------------------- the server --

class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10                # a client that stalls does not hold a thread for ever
    headers = None              # until parse_request has read them (send_error may come first)
    _set_lang = None

    def parse_request(self):
        ok = super().parse_request()
        if not isinstance(self.headers, http.client.HTTPMessage):    # an HTTP/0.9 request has none (the stdlib leaves a dict)
            self.headers = http.client.HTTPMessage()
        return ok

    def version_string(self):
        return "console"

    def log_message(self, fmt, *args):     # the request line only (ascii(): it cannot write to a terminal); a poll is noise
        line = ascii(fmt % args)
        log.log(logging.DEBUG if "GET /poll" in line else logging.INFO, "%s", line)

    def send_error(self, code, message=None, explain=None):
        """The stdlib's own refusals (an unknown method, a bad request line) get our page and headers, not its own."""
        code = 405 if code == 501 else code
        name = "method_not_allowed" if code == 405 else "too_large" if code in (414, 431) else "bad_request"
        self._error(name, status=code)

    # -- one request --
    def do_GET(self):
        self._serve(self._get)

    def do_POST(self):
        self._serve(self._post)

    def _serve(self, handle):
        try:
            try:
                handle()
            except Reject as r:
                self._error(r.code, status=r.status)
            except core.Refused as e:
                self._error(e.code, str(e))
            except (ConnectionError, TimeoutError):
                raise
            except Exception as e:      # noqa: BLE001 - whatever it is, the person gets a page, not a trace
                log.warning("request failed: %s", type(e).__name__)
                log.debug("request failed", exc_info=True)
                self._error("server_error")
        except (ConnectionError, TimeoutError):
            log.debug("the client went away or stalled")

    def _get(self):
        c = self.server.app
        self._host()
        path, _, query = self.path.partition("?")
        if path in FILES:
            with open(FILES[path][0], "rb") as f:
                return self._send(200, f.read(), FILES[path][1], static=True)
        if path in POSTS:
            raise Reject("method_not_allowed")
        if path not in PAGES:
            raise Reject("not_found")
        picked = parse_qs(query).get("lang", [""])[0]
        self._set_lang = picked if picked in i18n.LANGS else None
        try:
            st = c.state()
        except core.Refused as e:
            if path != "/poll":
                raise
            return self._send(500, json.dumps({"error": e.code}).encode(), "application/json")
        if path == "/poll":
            return self._send(200, json.dumps({"seq": st["seq"], "open": len(core.open_asks(st))}).encode(),
                              "application/json")
        ctx = self._ctx(self._user())
        if path == "/":
            done = pages.flash_text(ctx["lang"], parse_qs(query).get("done", [""])[0])
            if done:
                ctx["flash"] = {"kind": "ok", "text": done}
        self._send(200, (pages.render_inbox if path == "/" else pages.render_history)(st, ctx).encode())

    def _post(self):
        c = self.server.app
        self._host()
        path = self.path.partition("?")[0]
        if path not in POSTS:
            raise Reject("method_not_allowed" if path in FILES or path in PAGES else "not_found")
        form = self._form()
        user = self._user()
        if user is None:
            raise Reject("forbidden")
        ctx = self._ctx(user)
        try:
            lines = POSTS[path](c, user, ctx["lang"], form)
        except core.Refused as e:
            lines = [_line(ctx["lang"], refused=e)]
        if path in AFTER and lines[0]["ok"]:
            return self._send(303, b"", location=AFTER[path])
        status = 200 if any(x["ok"] for x in lines) else STATUS.get(lines[0]["code"], 409)
        self._send(status, pages.render_result(ctx, lines).encode())

    # -- reading the request --
    def _host(self):
        c = self.server.app
        if not host_ok(self.headers.get("Host"), c.port, c.allow):
            raise Reject("bad_host")

    def _form(self) -> dict:
        """The POST's fields, once the request itself has passed every check."""
        h = self.headers
        n = h.get("Content-Length", "")
        if "Transfer-Encoding" in h or not re.fullmatch(r"[0-9]{1,9}", n):
            raise Reject("bad_request")
        n = int(n)
        if n > MAX_BODY:
            self.connection.settimeout(1)               # a client that promised more than it sends is not waited for
            with contextlib.suppress(OSError):          # (a timeout is an OSError)
                self.rfile.read(min(n, DRAIN))
            raise Reject("too_large")
        raw = self.rfile.read(n)
        if not same_origin(h):
            raise Reject("forbidden")
        if h.get("Content-Type", "").split(";")[0].strip().lower() != FORM:
            raise Reject("bad_request", 415)
        try:
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=200)
        except (UnicodeDecodeError, ValueError):
            raise Reject("bad_request") from None
        if not token_ok(_one(form, "token", ""), self.server.app.token):
            raise Reject("forbidden")
        return form

    def _user(self):
        c = self.server.app
        if not c.user_header:
            return c.user
        seen = self.headers.get_all(c.user_header) or []
        if self.client_address[0] in LOOPBACK_PEERS and len(seen) == 1 and core.TOKEN_RE.fullmatch(seen[0]):
            return seen[0]
        return None

    def _lang(self) -> str:
        """?lang= if given, else the cookie it set, else the operator's --lang. A
        value that is not one of ours is ignored, wherever it comes from."""
        jar = "; ".join(self.headers.get_all("Cookie") or []) if self.headers is not None else ""
        pairs = (part.strip().partition("=") for part in jar.split(";"))
        saved = next((v for name, _, v in pairs if name == self.server.app.lang_cookie and v in i18n.LANGS), None)
        return self._set_lang or saved or self.server.app.lang

    def _ctx(self, user) -> dict:
        return self.server.app.ctx(self._lang(), user)

    # -- answering --
    def _error(self, code: str, message: str = "", status: int | None = None):
        page = pages.render_error(self._ctx(None), code, message)
        self._send(status or STATUS.get(code, 409), page.encode())

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", *, static: bool = False,
              location: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY.items():
            self.send_header(k, v)
        if not static:
            self.send_header("Cache-Control", "no-store")
        if status == 405:
            self.send_header("Allow", "GET, POST")
        if location:
            self.send_header("Location", location)
        if self._set_lang:      # no Path: the browser scopes it to where the console is served
            cookie = f"{self.server.app.lang_cookie}={self._set_lang}"
            self.send_header("Set-Cookie", f"{cookie}; Max-Age=31536000; SameSite=Lax; HttpOnly")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class Server(http.server.ThreadingHTTPServer):
    def __init__(self, host: str, port: int):
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, port), Handler)

    def handle_error(self, request, client_address):
        """What escapes `_serve` is a client that went away, a request that is not HTTP, or an error
        page that could not be drawn; the stdlib would print a traceback to stderr for each."""
        log.debug("request failed", exc_info=True)


# ------------------------------------------------------------------- main --

def die(message: str):
    print(f"serve.py: {message}", file=sys.stderr)
    sys.exit(2)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="The console's pages, for the human.")
    add = p.add_argument
    add("--dir", help="the folder of the log (default $CONSOLE_DIR; never guessed)")
    add("--host", default="127.0.0.1", help="the address to listen on; anything but 127.0.0.1, ::1 or localhost needs --user-header")
    add("--port", type=int, default=8770)
    add("--user", help="who answers (default $USER); there is no login: anyone who can reach the port is this person")
    add("--user-header", help="take the user from this header, from a loopback peer only (a proxy sets it)")
    add("--allow-host", action="append", default=[], metavar="HOST[:PORT]", help="another Host to answer to")
    add("--title", default="Console")
    add("--lang", choices=i18n.LANGS, default="en")
    add("--relay-cmd", help="the harness command that answers a gate")
    add("--relay-verbs", help="comma-separated verb prefixes it may be asked to run, e.g. 'facts confirm,queue approve'")
    add("--default-reason", default="console", help="the reason given to a gate when the person wrote no comment")
    add("--log-level", choices=("debug", "info", "warning"), default="info")
    return p


def main(argv=None) -> int:
    a = _parser().parse_args(argv)
    folder = a.dir or os.environ.get("CONSOLE_DIR")
    if not folder:
        die("no folder: pass --dir or set CONSOLE_DIR")
    user = None
    if a.user_header and a.user:
        die("--user and --user-header exclude each other")
    if not a.host.strip():
        die("--host is an address to listen on (an empty one would mean every interface)")
    if a.host.lower() not in LOOPBACK_HOSTS and not a.user_header:
        die(f"--host {a.host!r} is not loopback (127.0.0.1, ::1, localhost), so it needs --user-header: "
            "a console with no login is never bound to the network")
    if not a.user_header:
        user = a.user or os.environ.get("USER", "")
        if not core.TOKEN_RE.fullmatch(user):
            die("--user must be a plain name (letters, digits, _ . : @ / -)")
    allow = [split_host(h) for h in a.allow_host]
    if None in allow:
        die("--allow-host is HOST or HOST:PORT")
    if not 0 <= a.port <= 65535:
        die(f"--port {a.port} is not a port (0 to 65535)")
    try:
        cmd = shlex.split(a.relay_cmd or "")
    except ValueError as e:
        die(f"--relay-cmd: {e}")
    verbs = [w.split() for w in (a.relay_verbs or "").split(",") if w.split()]
    if bool(cmd) != bool(verbs):
        die("--relay-cmd and --relay-verbs go together, and neither may be empty")
    logging.basicConfig(level=a.log_level.upper(), stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = core.Store(folder)
    try:                                   # the socket first: a start that fails leaves no folder or secret behind
        srv = Server(a.host, a.port)
    except (OSError, ValueError) as e:
        why = getattr(e, "strerror", None) or e
        die(f"cannot listen on {a.host!r} port {a.port}: {why}; pass another --port or --host")
    try:
        secret = store.ensure_secret()
    except core.Refused as e:
        srv.server_close()
        die(str(e))
    srv.app = Console(store, secret, port=srv.server_address[1], user=user, user_header=a.user_header, allow=allow,
                      title=a.title, lang=a.lang, cmd=cmd, verbs=verbs, reason=a.default_reason)
    print(f"Console: http://{f'[{a.host}]' if ':' in a.host else a.host}:{srv.server_address[1]}/", flush=True)
    if cmd:
        print("Relay verbs: " + ", ".join(" ".join(v) for v in verbs), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
