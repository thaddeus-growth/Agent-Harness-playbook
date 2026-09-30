#!/usr/bin/env python3
"""A paid request body is checked offline against the vendor's published
schema (kit/preflight.py), and refused with every problem coded.

  1. A body that fits passes (problems == [], check returns None); extra
     schema keys (node ids, defaults) are ignored.
  2. Each rule: unknown field, required missing (None = missing), enum not
     a string / not an option, boolean, integer vs number (bool, 5.0, nan),
     min / max, string length, media URL / data: URI / accept_types, a
     type the kit does not know, a body that is not an object.
  3. The two refusals it was paid for: a wav typed audio/x-wav, and 0.0
     for an enum whose one option is "0".
  4. check() raises ONE coded HarnessError listing every problem, "nothing
     was sent", with its `next`.
  5. preflight.py's msg() calls are closed over its fragment.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import messages, preflight  # noqa: E402
from kit.contract import HarnessError  # noqa: E402
from kit.testing.check import check, finish  # noqa: E402

SCHEMA = {
    "prompt": {"type": "prompt", "required": True, "min_length": 1,
               "max_length": 20, "node_id": "173"},
    "title": {"type": "string", "max_length": 5},
    "duration": {"type": "integer", "min": 1, "max": 15, "default": 5},
    "strength": {"type": "number", "min": 0, "max": 1.4},
    "loop": {"type": "boolean"},
    "resolution": {"type": "enum", "options": [{"label": "480p"},
                                               {"label": "768p"}]},
    "mode": {"type": "enum", "options": [{"label": "0"}]},
    "ref_image": {"type": "image", "required": True,
                  "accept_types": ["image/jpeg", "image/png"]},
    "ref_audio": {"type": "audio",
                  "accept_types": ["audio/mpeg", "audio/wav", "audio/flac"]},
    "ref_video": {"type": "video"},
}
GOOD = {"prompt": "a quiet street", "duration": 5, "strength": 0.5,
        "loop": False, "resolution": "768p", "mode": "0",
        "ref_image": "https://example.com/a.png",
        "ref_audio": "data:audio/wav;base64,UklGRg=="}


def codes(body, schema=SCHEMA) -> list[str]:
    return [m.code for m in preflight.problems(schema, body)]


def one(label, body, code, schema=SCHEMA) -> None:
    got = codes(body, schema)
    check(label, got == [code], got)


def test_passes() -> None:
    print("[1] a body that fits passes")
    check("the good body has no problems", preflight.problems(SCHEMA, GOOD)
          == [], preflight.problems(SCHEMA, GOOD))
    check("check() returns None", preflight.check(SCHEMA, GOOD, what="w")
          is None)
    check("an int passes a number field, bounds inclusive",
          codes({**GOOD, "strength": 0, "duration": 15}) == [])
    check("an enum with no options takes any string",
          codes({"x": "any"}, {"x": {"type": "enum"}}) == [])
    check("a data: URI with no accept_types passes",
          codes({**GOOD, "ref_video": "data:video/mp4;base64,AAAA"}) == [])


def test_rules() -> None:
    print("[2] each rule")
    one("a field the schema does not name", {**GOOD, "seed": 1},
        "preflight_field_unknown")
    one("a required field missing", {k: v for k, v in GOOD.items()
                                     if k != "ref_image"},
        "preflight_field_required")
    one("None counts as missing", {**GOOD, "prompt": None},
        "preflight_field_required")
    check("an optional field may be absent or None",
          codes({**GOOD, "duration": None, "loop": None}) == [])
    one("an enum sent as a number", {**GOOD, "resolution": 768},
        "preflight_enum_not_string")
    one("an enum label not among the options", {**GOOD, "resolution": "720p"},
        "preflight_enum_not_option")
    for bad in (1, "true", 0):
        one(f"boolean refuses {bad!r}", {**GOOD, "loop": bad},
            "preflight_not_boolean")
    for bad in (5.0, True, "5"):
        one(f"integer refuses {bad!r}", {**GOOD, "duration": bad},
            "preflight_not_integer")
    for bad in (True, "0.5", float("nan"), float("inf")):
        one(f"number refuses {bad!r}", {**GOOD, "strength": bad},
            "preflight_not_number")
    one("below min", {**GOOD, "duration": 0}, "preflight_out_of_range")
    one("above max", {**GOOD, "strength": 1.5}, "preflight_out_of_range")
    m = preflight.problems(SCHEMA, {**GOOD, "duration": 16})[0]
    check("out of range carries its bounds", m.params == {
        "field": "duration", "got": "16", "min": "1", "max": "15"}, m.params)
    one("a string field refuses a number", {**GOOD, "title": 5},
        "preflight_not_string")
    one("a prompt shorter than min_length", {**GOOD, "prompt": ""},
        "preflight_length_out_of_range")
    one("a string longer than max_length", {**GOOD, "title": "abcdef"},
        "preflight_length_out_of_range")
    check("length counts characters, not bytes",
          codes({**GOOD, "title": "五个汉字啊"}) == [])
    for bad in ("/local/a.png", "ftp://h/a.png", "https://", 7, ""):
        one(f"media refuses {bad!r}", {**GOOD, "ref_image": bad},
            "preflight_media_not_uri")
    one("a data: URI whose type is not accepted",
        {**GOOD, "ref_image": "data:image/gif;base64,R0lG"},
        "preflight_media_type_not_accepted")
    check("the media type is compared case aside",
          codes({**GOOD, "ref_image": "data:IMAGE/PNG;base64,iVBO"}) == [])
    one("a data: URI with no type", {**GOOD, "ref_image": "data:,abc"},
        "preflight_media_type_not_accepted")
    one("a schema type the kit does not know",
        {"x": 1}, "preflight_type_unknown", {"x": {"type": "tensor"}})
    one("a body that is not an object", [GOOD], "preflight_body_not_object")
    got = codes({"seed": 1, "loop": 1})
    check("every problem is listed, unknown fields first",
          got == ["preflight_field_unknown", "preflight_field_required",
                  "preflight_not_boolean", "preflight_field_required"], got)


def test_paid_for() -> None:
    print("[3] the two refusals it was paid for")
    wav = {**GOOD, "ref_audio": "data:audio/x-wav;base64,UklGRg=="}
    ms = preflight.problems(SCHEMA, wav)
    check("a wav typed audio/x-wav is refused (the schema says audio/wav)",
          [m.code for m in ms] == ["preflight_media_type_not_accepted"]
          and ms[0].params["mime"] == "audio/x-wav"
          and "audio/wav" in ms[0].params["accept"], [m.params for m in ms])
    for bad in (0.0, 0):
        ms = preflight.problems(SCHEMA, {**GOOD, "mode": bad})
        check(f"{bad!r} for an enum whose one option is \"0\" is refused",
              [m.code for m in ms] == ["preflight_enum_not_string"],
              [m.code for m in ms])
    check("the string \"0\" passes", codes({**GOOD, "mode": "0"}) == [])


def test_check_raises() -> None:
    print("[4] check() raises one coded refusal")
    body = {**GOOD, "mode": 0.0,
            "ref_audio": "data:audio/x-wav;base64,UklGRg=="}
    try:
        preflight.check(SCHEMA, body, what="clip_render",
                        next=["shop catalog refresh"])
        e = None
    except HarnessError as err:
        e = err
    check("a HarnessError is raised", e is not None)
    if e is None:
        return
    m = e.message
    check("coded preflight_refused with what and count",
          m.code == "preflight_refused" and m.params["what"] == "clip_render"
          and m.params["count"] == 2, getattr(m, "params", m))
    check("it lists both problems as nested codes",
          [c["code"] for c in messages.code(m)["params"]["problems"]]
          == ["preflight_enum_not_string",
              "preflight_media_type_not_accepted"],
          messages.code(m))
    check("the text says nothing was sent and names each field",
          "nothing was sent" in m and "mode:" in m and "ref_audio:" in m, m)
    check("next is kept", e.next == ["shop catalog refresh"], e.next)
    check("failure() gives the same code",
          messages.failure(e)["code"] == "preflight_refused")


def test_closure() -> None:
    print("[5] preflight.py's codes are closed over its fragment")
    reg = messages.registry()
    own = {c: r for c, r in reg.items()
           if Path(r["file"]).name == "preflight.tsv"}
    check("the fragment holds preflight_* codes only",
          own and all(c.startswith("preflight_") for c in own), sorted(own))
    probs = messages.check_registry_closed(_shop.KIT, ["preflight.py"], own,
                                           strict_kit=True)
    check("every msg() in preflight.py is literal, registered with exact "
          "params, and every preflight.tsv code is emitted", probs == [],
          probs)


def main() -> int:
    _shop.use()
    for fn in (test_passes, test_rules, test_paid_for, test_check_raises,
               test_closure):
        fn()
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
