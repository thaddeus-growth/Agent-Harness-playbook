"""The shop's one writer: the kit's guarded write path with an EMPTY
allowlist (nothing can go out until an allowlist entry is merged by the
owner), over a transport that never reaches a network."""

from kit.write_guard import Guard, Writer

ALLOWED: dict = {}


def transport(method, path, body, media):
    raise ConnectionError("the shop harness has no external API")


def writer() -> Writer:
    return Writer(Guard(ALLOWED), transport)
