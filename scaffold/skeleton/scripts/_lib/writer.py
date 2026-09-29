"""The one writer: every call to the client's external system goes through
the kit's guarded write path (kit.write_guard): the kill switch
{{env_prefix}}_KILL or a STOP file, the opt-in {{env_prefix}}_ALLOW_WRITES=1,
then an exact-shape allowlist, never a retry.

The allowlist is EMPTY: nothing can be written. An entry is its own merge
request, merged by the owner after they agreed in writing, with a cap
(BUILD.md B7). Only scripts/execute_actions.py imports this module
(tests/test_layering.py).
"""

from kit.write_guard import Guard, Writer

ALLOWED: dict = {}


def transport(method, path, body, media):
    raise ConnectionError("no external API is wired to this writer yet")


def writer() -> Writer:
    return Writer(Guard(ALLOWED), transport)
