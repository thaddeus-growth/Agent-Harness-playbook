"""Check a paid request body offline, against the vendor's own published
input schema, before it is sent.

A paid call that the vendor refuses for a shape its own schema already
rules out costs a round trip, a queue slot and sometimes the charge. The
schema is published; read it first. What it guards:

  * problems(schema, body) -> [Msg], empty when the body passes: a field
    the schema does not name, a required field missing (None counts as
    missing), and per field type:
      enum      a string, and one of the options' labels (a number is
                refused even when its text equals a label: 0 is not "0")
      boolean   true or false, nothing else (not 0/1, not "true")
      integer   an int, not a bool, not a float (5.0 is refused)
      number    an int or a finite float, not a bool
      string, prompt
                a string, of min_length..max_length characters
      image, audio, video
                an http(s) URL, or a data: URI whose media type is one of
                accept_types (compared exactly, case aside: audio/x-wav is
                not audio/wav); a URL's type cannot be seen offline
    min / max bound integer and number. A type the kit does not know is a
    problem itself, so a mapping mistake never passes unchecked.
  * check(schema, body, what=…) raises one coded HarnessError listing
    every problem (preflight_refused: "nothing was sent"); it returns
    None when the body passes. A harness calls it on the line before the
    request.

The schema is the kit's neutral format; a harness maps its vendor's
published schema into it (and refreshes it from the vendor, never by
hand):

    {"<field>": {"type": "enum" | "boolean" | "integer" | "number" |
                         "string" | "prompt" | "image" | "audio" | "video",
                 "required": bool,                     # default false
                 "options": [{"label": str, …}, …],    # enum
                 "min": number, "max": number,         # integer, number
                 "min_length": int, "max_length": int, # string, prompt
                 "accept_types": ["image/png", …]},    # image/audio/video
     …}

Other keys in a field (node ids, defaults, the vendor's own values) are
ignored.

Paid for: two live refusals in one day, both visible in the vendor's
published schema before either call: a wav sent as a data: URI typed
audio/x-wav when the schema accepts audio/wav, and a number (0.0) sent for
an enum whose one option is the string "0".

Test: kit/tests/test_preflight.py.
"""

from __future__ import annotations

import math
from typing import Iterable

from kit.contract import HarnessError
from kit.messages import Msg, msg

TYPES = ("enum", "boolean", "integer", "number", "string", "prompt",
         "image", "audio", "video")
MEDIA = ("image", "audio", "video")


def _got(v) -> str:
    return f"{type(v).__name__} {v!r}"[:120]


def _bound(v) -> str:
    return "" if v is None else str(v)


def _field(k: str, r: dict, v) -> list[Msg]:
    t = r.get("type")
    if t not in TYPES:
        return [msg("preflight_type_unknown",
                    f"{k}: the schema's type {t!r} is not one the kit checks "
                    f"({', '.join(TYPES)}); map it before sending",
                    field=k, type=str(t))]
    if t == "enum":
        labels = [o.get("label") for o in r.get("options") or []
                  if isinstance(o, dict)]
        if not isinstance(v, str):
            return [msg("preflight_enum_not_string",
                        f"{k}: an enum takes a string label, got {_got(v)}",
                        field=k, got=_got(v))]
        if labels and v not in labels:
            return [msg("preflight_enum_not_option",
                        f"{k}: {v!r} is not one of {labels}",
                        field=k, got=v, options=labels)]
        return []
    if t == "boolean":
        if not isinstance(v, bool):
            return [msg("preflight_not_boolean",
                        f"{k}: must be true or false, got {_got(v)}",
                        field=k, got=_got(v))]
        return []
    if t in ("integer", "number"):
        if t == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
            return [msg("preflight_not_integer",
                        f"{k}: must be an integer, got {_got(v)}",
                        field=k, got=_got(v))]
        if t == "number" and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v)):
            return [msg("preflight_not_number",
                        f"{k}: must be a finite number, got {_got(v)}",
                        field=k, got=_got(v))]
        lo, hi = r.get("min"), r.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return [msg("preflight_out_of_range",
                        f"{k}: {v} is outside {_bound(lo)}..{_bound(hi)}",
                        field=k, got=str(v), min=_bound(lo), max=_bound(hi))]
        return []
    if t in ("string", "prompt"):
        if not isinstance(v, str):
            return [msg("preflight_not_string",
                        f"{k}: must be a string, got {_got(v)}",
                        field=k, got=_got(v))]
        lo, hi = r.get("min_length"), r.get("max_length")
        if (lo is not None and len(v) < lo) or (hi is not None
                                                and len(v) > hi):
            return [msg("preflight_length_out_of_range",
                        f"{k}: {len(v)} characters, outside "
                        f"{_bound(lo)}..{_bound(hi)}",
                        field=k, length=len(v), min=_bound(lo),
                        max=_bound(hi))]
        return []
    # image, audio, video
    if isinstance(v, str) and v.startswith(("http://", "https://")) \
            and len(v) > len("https://"):
        return []
    if isinstance(v, str) and v[:5].lower() == "data:":
        mime = v[5:].split(",", 1)[0].split(";", 1)[0].strip().lower()
        accept = [str(a) for a in r.get("accept_types") or []]
        if accept and mime not in [a.lower() for a in accept]:
            return [msg("preflight_media_type_not_accepted",
                        f"{k}: data: URI typed {mime or '(none)'!s} is not "
                        f"one of {accept}",
                        field=k, mime=mime, accept=accept)]
        return []
    return [msg("preflight_media_not_uri",
                f"{k}: a {t} must be an http(s) URL or a data: URI, got "
                f"{_got(v)[:60]}", field=k, type=t, got=_got(v)[:60])]


def problems(schema: dict, body: dict) -> list[Msg]:
    """Why the vendor would refuse `body` by its own `schema` (the neutral
    format above); [] when it passes. Unknown fields first, in body
    order, then the schema's fields in schema order."""
    if not isinstance(body, dict):
        return [msg("preflight_body_not_object",
                    f"the request body must be an object, got "
                    f"{type(body).__name__}", got=type(body).__name__)]
    out = [msg("preflight_field_unknown",
               f"{k}: not an input of this request (known: "
               f"{', '.join(schema)})", field=k, known=list(schema))
           for k in body if k not in schema]
    for k, r in schema.items():
        v = body.get(k)
        if v is None:
            if (r or {}).get("required"):
                out.append(msg("preflight_field_required",
                               f"{k}: required, and missing", field=k))
            continue
        out += _field(k, r or {}, v)
    return out


def check(schema: dict, body: dict, *, what: str,
          next: Iterable[str] = ()) -> None:
    """Raise one HarnessError listing every problem of `body` (`what`
    names the request: the workflow, the endpoint); None when it
    passes."""
    found = problems(schema, body)
    if found:
        raise HarnessError(msg(
            "preflight_refused",
            f"{what}: {len(found)} problem(s) with the request body against "
            f"the vendor's published schema; nothing was sent: "
            + "; ".join(found),
            what=what, count=len(found), problems=found), next)
