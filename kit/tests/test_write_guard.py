#!/usr/bin/env python3
"""kit.write_guard: the one write path, behind hard guards.

A test allowlist (a made-up shop API) drives a fake transport that
records every call; nothing leaves the process.

  * the kit ships NO allowlist: Guard({}) refuses every call;
  * refusal order: the kill env beats everything (STOP, no opt-in, an
    unlisted endpoint, reads too); then the STOP file (even a dangling
    link; an unset or relative data dir refuses: STOP can't be checked);
    then the opt-in, exactly "1";
  * the allowlist: an unlisted method/path is refused (trailing slash,
    query string, other method); exactly one item; the field set must
    equal a shape's (extra or missing field refused); fixed values
    enforced by type and value; an OWN id must be in `created`; by_id
    bodies are exact; reads (declared read=True) pass any body;
    validators run last and a raising validator refuses;
  * `{name}` path segments match one plain id only (not `..`, `%2F`,
    `{name}` itself, an empty or extra segment);
  * a malformed allowlist (an endpoint with no shapes that is not
    declared a read, overlapping keys …) is a ValueError at Guard();
  * Writer: refusal -> check -> send; a transport exception becomes
    WriteFailed and is never retried (one transport call); refusal is
    re-checked on every call; refusals are coded HarnessErrors whose
    failure document names the code;
  * write_guard never imports kit.retry.
"""

import ast
import os
import sys
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import contract, write_guard as wg  # noqa: E402
from kit.testing.check import check, finish, raises  # noqa: E402
from kit.write_guard import Endpoint, Guard, Writer, shape  # noqa: E402

JSON = "application/json"
ALLOWED = {
    ("PUT", "/v1/prices"): Endpoint(JSON, "prices", (
        shape("sku", "price"),
        shape("sku", own="sku", state="PAUSED"))),
    ("PUT", "/v1/flags"): Endpoint(JSON, "flags", (shape("sku", active=False),)),
    ("POST", "/v1/listings"): Endpoint(JSON, "listings", (
        shape("sku", "title", state="DRAFT"),
        shape("sku", "title", "price", state="DRAFT"))),
    ("POST", "/v1/listings/delete"): Endpoint(JSON, "listings", (),
                                              by_id="listingIdFilter"),
    ("POST", "/v1/prices/list"): Endpoint(JSON, "prices", (), read=True),
    ("POST", "/v1/items/{item_id}"): Endpoint(JSON, None,
                                              (shape(status="PAUSED"),)),
}


def positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def boom(v):
    raise TypeError("validator bug")


VALIDATORS = {"price": positive, "title": boom}


class FakeTransport:
    def __init__(self, fail: BaseException | None = None):
        self.calls = []
        self.fail = fail

    def __call__(self, method, path, body, media):
        self.calls.append((method, path, body, media))
        if self.fail is not None:
            raise self.fail
        return 200, {"ok": True}


def code(fn) -> str | None:
    """The message code of the HarnessError fn() raised (None: returned)."""
    e = raises(fn, contract.HarnessError)
    return getattr(e.message, "code", None) if e else None


def price(sku="a", p=9.5):
    return {"prices": [{"sku": sku, "price": p}]}


def main() -> int:
    _shop.use()
    data = _shop.data_dir()
    ON = {"SHOP_ALLOW_WRITES": "1"}
    g = Guard(ALLOWED, validators={"price": positive})

    print("[1] the kit ships no allowlist")
    module_eps = [k for k, v in vars(wg).items()
                  if isinstance(v, (dict, tuple)) and any(
                      isinstance(x, Endpoint) for x in
                      (v.values() if isinstance(v, dict) else v))]
    check("no module-level table of endpoints in kit.write_guard",
          module_eps == [] and not hasattr(wg, "ALLOWED"), module_eps)
    empty = Writer(Guard({}), FakeTransport())
    with mock.patch.dict(os.environ, ON):
        c = code(lambda: empty.call("POST", "/v1/prices/list", {}))
    check("Guard({}) with writes on: every call refused, nothing sent",
          c == "write_not_allowed" and empty._send.calls == [], c)

    print("\n[2] refusal order: kill env, STOP file, opt-in")
    check("env names follow the bound harness",
          g.kill_env == "SHOP_KILL" and g.opt_in_env == "SHOP_ALLOW_WRITES")
    stop = data / "STOP"
    t = FakeTransport()
    w = Writer(g, t)
    stop.touch()
    with mock.patch.dict(os.environ, {"SHOP_KILL": "1"}):
        os.environ.pop("SHOP_ALLOW_WRITES", None)
        c1 = code(lambda: w.call("DELETE", "/v1/everything", {}))
        c2 = code(lambda: w.call("POST", "/v1/prices/list", {}))
    check("kill env beats STOP, no opt-in and an unlisted endpoint",
          c1 == "write_killed", c1)
    check("kill env stops reads too", c2 == "write_killed", c2)
    for v in ("1", "true", "yes", " 1 "):
        with mock.patch.dict(os.environ, {**ON, "SHOP_KILL": v}):
            check(f"SHOP_KILL={v!r} kills",
                  getattr(g.refusal(), "code", None) == "write_killed")
    with mock.patch.dict(os.environ, {**ON, "SHOP_KILL": "1"}):
        c = code(lambda: w.call("PUT", "/v1/prices", price()))
    check("kill env beats writes on and a valid call", c == "write_killed", c)
    with mock.patch.dict(os.environ, {"SHOP_KILL": "0"}):
        os.environ.pop("SHOP_ALLOW_WRITES", None)
        c = code(lambda: w.call("DELETE", "/v1/everything", {}))
    check("SHOP_KILL=0 is off; then the STOP file, before the opt-in and "
          "the allowlist", c == "write_stop_file", c)
    with mock.patch.dict(os.environ, ON):
        c = code(lambda: w.call("PUT", "/v1/prices", price()))
        doc = contract.failure_doc(raises(lambda: g.ensure_on()), ["shop", "x"])
    check("STOP beats writes on", c == "write_stop_file", c)
    check("its failure document is coded, next = doctor",
          doc["code"] == "write_stop_file" and str(stop) in doc["error"]
          and doc["next"] == ["shop doctor"], doc)
    stop.unlink()
    stop.symlink_to(data / "no-such-target")
    with mock.patch.dict(os.environ, ON):
        c = code(lambda: w.call("PUT", "/v1/prices", price()))
    check("a dangling STOP link still stops", c == "write_stop_file", c)
    stop.unlink()
    for label, val in (("unset", None), ("relative", "rel/dir")):
        with mock.patch.dict(os.environ, ON):
            if val is None:
                os.environ.pop("SHOP_DATA_DIR")
            else:
                os.environ["SHOP_DATA_DIR"] = val
            c = code(lambda: w.call("PUT", "/v1/prices", price()))
        check(f"data dir {label}: STOP can't be checked, refused",
              c == "write_stop_unchecked", c)
    check("the data dir came back", os.environ.get("SHOP_DATA_DIR") == str(data))
    for label, val in (("unset", None), ("'true'", "true"), ("'yes'", "yes"),
                       ("' 1'", " 1")):
        with mock.patch.dict(os.environ, {}):
            if val is None:
                os.environ.pop("SHOP_ALLOW_WRITES", None)
            else:
                os.environ["SHOP_ALLOW_WRITES"] = val
            c = code(lambda: w.call("DELETE", "/v1/everything", {}))
        check(f"opt-in {label}: writes off (only exactly '1'), before the "
              f"allowlist", c == "write_off", c)
    check("no refusal ever reached the transport", t.calls == [], t.calls)
    with mock.patch.dict(os.environ, ON):
        check("all clear: refusal() is None", g.refusal() is None)
    custom = Guard(ALLOWED, kill_env="OTHER_KILL", opt_in_env="OTHER_ON")
    with mock.patch.dict(os.environ, {"OTHER_ON": "1", **ON}):
        check("custom opt-in name honoured",
              getattr(custom.refusal(), "code", None) is None)
        os.environ.pop("OTHER_ON")
        check("custom opt-in unset: off even with SHOP_ALLOW_WRITES=1",
              getattr(custom.refusal(), "code", None) == "write_off")

    print("\n[3] the allowlist: exact shapes")
    ck = g.check
    check("allowed: one price item", ck("PUT", "/v1/prices", price()).key
          == "prices")
    for label, m, p, body in (
            ("another method", "POST", "/v1/prices", price()),
            ("an unlisted path", "PUT", "/v1/stock", price()),
            ("a trailing slash", "PUT", "/v1/prices/", price()),
            ("a query string", "PUT", "/v1/prices?force=1", price()),
            ("no leading slash", "PUT", "v1/prices", price())):
        check(f"refused: {label}", code(lambda: ck(m, p, body))
              == "write_not_allowed")
    for label, body, want in (
            ("an extra field", {"prices": [{"sku": "a", "price": 1,
                                            "note": "x"}]},
             "write_fields_not_allowed"),
            ("a missing field", {"prices": [{"price": 1}]},
             "write_fields_not_allowed"),
            ("two items", {"prices": [{"sku": "a", "price": 1},
                                      {"sku": "b", "price": 1}]},
             "write_bad_body"),
            ("no item", {"prices": []}, "write_bad_body"),
            ("an extra top-level key", {**price(), "dry": True},
             "write_bad_body"),
            ("items not a list", {"prices": {"sku": "a", "price": 1}},
             "write_bad_body"),
            ("an item that is not an object", {"prices": ["a"]},
             "write_bad_body"),
            ("a body that is not an object", ["x"], "write_bad_body"),
            ("a fixed value other than allowed",
             {"prices": [{"sku": "a", "state": "ENABLED"}]},
             "write_fixed_mismatch"),
            ("a bad price (0)", price(p=0), "write_value_refused"),
            ("a bad price (-1)", price(p=-1), "write_value_refused"),
            ("a bad price (a string)", price(p="9"), "write_value_refused"),
            ("a bad price (a bool)", price(p=True), "write_value_refused")):
        c = code(lambda: ck("PUT", "/v1/prices", body))
        check(f"refused: {label} ({want})", c == want, c)
    check("fixed values compare type: active=0 is not active=False",
          code(lambda: ck("PUT", "/v1/flags", {"flags": [
              {"sku": "a", "active": 0}]})) == "write_fixed_mismatch"
          and ck("PUT", "/v1/flags", {"flags": [
              {"sku": "a", "active": False}]}).key == "flags")
    pause = {"prices": [{"sku": "s1", "state": "PAUSED"}]}
    check("OWN: an id the harness did not create is refused",
          code(lambda: ck("PUT", "/v1/prices", pause)) == "write_not_own"
          and code(lambda: ck("PUT", "/v1/prices", pause,
                              frozenset({"s2"}))) == "write_not_own")
    check("OWN: an id in `created` passes",
          ck("PUT", "/v1/prices", pause, frozenset({"s1"})).key == "prices")
    check("OWN: an int id never matches a str in created",
          code(lambda: ck("PUT", "/v1/prices", {"prices": [
              {"sku": 7, "state": "PAUSED"}]}, frozenset({"7"})))
          == "write_not_own")
    dl = "/v1/listings/delete"
    check("by_id: exactly one created id passes", ck(
        "POST", dl, {"listingIdFilter": {"include": ["L1"]}},
        frozenset({"L1"})).by_id == "listingIdFilter")
    for label, body, want in (
            ("not created", {"listingIdFilter": {"include": ["L2"]}},
             "write_not_own"),
            ("two ids", {"listingIdFilter": {"include": ["L1", "L2"]}},
             "write_bad_body"),
            ("an int id", {"listingIdFilter": {"include": [1]}},
             "write_bad_body"),
            ("an extra filter key", {"listingIdFilter": {
                "include": ["L1"], "exclude": []}}, "write_bad_body"),
            ("an extra top-level key", {"listingIdFilter": {
                "include": ["L1"]}, "all": True}, "write_bad_body")):
        c = code(lambda: ck("POST", dl, body, frozenset({"L1", "L2"})
                            if label != "not created" else frozenset({"L1"})))
        check(f"by_id refused: {label}", c == want, c)
    check("a read endpoint (read=True) passes any body",
          ck("POST", "/v1/prices/list", {"skuFilter": {"include": ["a"]},
                                         "anything": 1}).read)
    with_boom = Guard(ALLOWED, validators=VALIDATORS)
    c = code(lambda: with_boom.check("POST", "/v1/listings", {"listings": [
        {"sku": "a", "title": "t", "state": "DRAFT"}]}))
    check("a validator that raises refuses", c == "write_value_refused", c)

    print("\n[4] {name} path segments")
    ok = {"status": "PAUSED"}
    check("POST /v1/items/123, flat body: allowed",
          ck("POST", "/v1/items/123", ok).key is None
          and ck("POST", "/v1/items/act_9.x-1", ok).key is None)
    for seg in ("..", ".", "a%2Fb", "{item_id}", "", "1/2", "a?b=1", "a b",
                "a#b"):
        c = code(lambda: ck("POST", f"/v1/items/{seg}", ok))
        check(f"refused segment {seg!r}", c == "write_not_allowed", c)
    for label, body, want in (
            ("an extra field", {"status": "PAUSED", "name": "x"},
             "write_fields_not_allowed"),
            ("another status", {"status": "ACTIVE"}, "write_fixed_mismatch"),
            ("not an object", "PAUSED", "write_bad_body")):
        c = code(lambda: ck("POST", "/v1/items/1", body))
        check(f"flat body refused: {label}", c == want, c)

    print("\n[5] a malformed allowlist fails at Guard()")
    bad = {
        "an endpoint with no shapes that is not declared a read":
            {("PUT", "/x"): Endpoint(JSON, "x", ())},
        "a read with shapes":
            {("PUT", "/x"): Endpoint(JSON, "x", (shape("a"),), read=True)},
        "by_id with shapes":
            {("PUT", "/x"): Endpoint(JSON, "x", (shape("a"),), by_id="f")},
        "an unknown method": {("FETCH", "/x"): Endpoint(JSON, None,
                                                        (shape("a"),))},
        "a path without a leading slash":
            {("PUT", "x"): Endpoint(JSON, None, (shape("a"),))},
        "an own field not in the shape":
            {("PUT", "/x"): Endpoint(JSON, None, (
                wg.Shape(frozenset({"a"}), {}, "b"),))},
        "a fixed field not in the shape":
            {("PUT", "/x"): Endpoint(JSON, None, (
                wg.Shape(frozenset({"a"}), {"b": 1}),))},
        "not an Endpoint": {("PUT", "/x"): ("json", "x", ())},
        "overlapping keys": {
            ("POST", "/v1/items/{id}"): Endpoint(JSON, None, (shape("a"),)),
            ("POST", "/v1/items/special"): Endpoint(JSON, None,
                                                    (shape("b"),))},
    }
    for label, table in bad.items():
        check(f"ValueError: {label}",
              raises(lambda: Guard(table), ValueError) is not None)
    check("the same pattern under another method is fine", Guard({
        ("POST", "/v1/items/{id}"): Endpoint(JSON, None, (shape("a"),)),
        ("DELETE", "/v1/items/{id}"): Endpoint(JSON, None, (shape("a"),))})
        is not None)

    print("\n[6] Writer: refusal -> check -> send, once")
    t = FakeTransport()
    w = Writer(g, t)
    with mock.patch.dict(os.environ, ON):
        got = w.call("PUT", "/v1/prices", price())
        check("sent once with the endpoint's media type",
              got == (200, {"ok": True})
              and t.calls == [("PUT", "/v1/prices", price(), JSON)], t.calls)
        c = code(lambda: w.call("PUT", "/v1/prices", price(p=-1)))
        check("a refused check sends nothing",
              c == "write_value_refused" and len(t.calls) == 1, c)
        stop.touch()
        c = code(lambda: w.call("PUT", "/v1/prices", price()))
        check("refusal is re-checked on every call (STOP mid-run)",
              c == "write_stop_file" and len(t.calls) == 1, c)
        stop.unlink()
        for exc in (TimeoutError("read timed out"),
                    ConnectionResetError("reset"), ValueError("bad json")):
            ft = FakeTransport(fail=exc)
            e = raises(lambda: Writer(g, ft).call("PUT", "/v1/prices",
                                                  price()))
            check(f"{type(exc).__name__} -> WriteFailed, one transport call, "
                  f"never retried",
                  isinstance(e, wg.WriteFailed)
                  and e.message.code == "write_failed"
                  and type(exc).__name__ in str(e) and len(ft.calls) == 1,
                  (e, ft.calls))
        doc = contract.failure_doc(e, ["shop", "execute", "apply"])
        check("WriteFailed's failure document is coded",
              doc["code"] == "write_failed"
              and doc["params"]["path"] == "/v1/prices", doc)

    tree = ast.parse(Path(wg.__file__).read_text(encoding="utf-8"))
    names = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
              for a in n.names]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
              for a in n.names]
    check("kit.write_guard never imports kit.retry",
          not any(x and "retry" in x for x in names), names)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
