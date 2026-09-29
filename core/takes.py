"""Takes: paid generative calls (video, image, voice, a batch of LLM calls),
planned, capped, approved by a person, and kept. Stdlib only.

A take is one call to a generator, keyed by the request that makes it:

    <root>/<key>/request.json     what was asked; key = sha256 of it, canonical
    <root>/<key>/result.json      what came back; its presence = done
    <root>/<key>/…                the files the generator wrote (media, logs)
    <ledger>                      one JSON line per paid take: when, key, cost, who approved

The rules, each the media form of a rule the playbook already had:

  - Raw only grows (core/raw.py): a take is written once. `put` on a key that
    has a result is refused, and nothing here deletes a take. A re-roll is a
    new request (add `take: 2`), so an approved take can never be lost or
    silently replaced.
  - The key includes everything that changes the output, including the
    generator's own version: bump `impl` when the same request would now give
    different media, or the old take keeps being served.
  - Never cache a broken take: `put(check=…)` runs the caller's sanity check on
    the result before result.json is written. *Paid for:* a TTS voice that
    was not installed wrote a 0.01 s file and exited 0; the empty take was
    cached and every later build reused it.
  - The money path (README, "Trust lives in the data"): `approve()` refuses
    without a declared cap, refuses a plan with an unpriced paid item, refuses
    a plan over what is left of the cap, then asks a person through
    core/gate.py with the exact plan as the subject: items (key -> cost) and
    the ledger length as the version, so an approval covers this plan once.
  - Download what you are given at once: generator URLs expire. The generator
    function writes bytes into the take folder; a result that only holds a URL
    is a broken take.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import dates, gate
from .messages import msg


class TakeRefused(Exception):
    """A take or a plan refused: nothing was called or cached. args[0] is a Msg."""


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def key(request: dict) -> str:
    """The take's key: sha256 of the canonical request. Put only what changes
    the output in the request (not absolute paths: moving a project must not
    re-pay for every take)."""
    return hashlib.sha256(canon(request).encode()).hexdigest()


@dataclass
class Item:
    key: str
    cost: float | None      # None = unpriced
    paid: bool
    cached: bool


class TakeStore:
    def __init__(self, root: Path, ledger: Path):
        self.root, self.ledger = Path(root), Path(ledger)

    def dir(self, k: str) -> Path:
        return self.root / k

    def get(self, k: str) -> dict | None:
        f = self.dir(k) / "result.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None

    def put(self, request: dict, make: Callable[[Path], dict], *,
            check: Callable[[dict], str | None] | None = None) -> tuple[str, dict, bool]:
        """(key, result, made): the cached result, or `make(folder)` run once and
        kept. `check(result)` returns why the result is broken, or None; a broken
        result is refused and not cached (the folder keeps what it wrote, for
        a look, and the next put retries)."""
        k = key(request)
        hit = self.get(k)
        if hit is not None:
            return k, hit, False
        d = self.dir(k)
        d.mkdir(parents=True, exist_ok=True)
        _write(d / "request.json", request)
        result = make(d)
        why = check(result) if check else None
        if why:
            raise TakeRefused(msg("take_rejected", f"take {k[:12]} was not kept: {why}", take=k[:12], why=why))
        result = {**result, "made_at": dates.utc_stamp()}
        if (d / "result.json").exists():      # another run finished first: its take wins, ours is dropped
            return k, self.get(k), False
        _write(d / "result.json", result)
        return k, result, True

    def charge(self, k: str, cost: float, **audit) -> None:
        """One ledger line for a paid take that was made."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger, "a", encoding="utf-8") as f:
            f.write(canon({"at": dates.utc_stamp(), "key": k, "cost": cost, **audit}) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def lines(self) -> list[dict]:
        if not self.ledger.is_file():
            return []
        return [json.loads(l) for l in self.ledger.read_text(encoding="utf-8").splitlines() if l.strip()]

    def spent(self) -> float:
        return sum(float(l.get("cost") or 0) for l in self.lines())

    def plan(self, requests: list[tuple[dict, bool, float | None]]) -> list[Item]:
        """[(request, paid, cost)] -> items with their cache state; a cached take costs 0."""
        out = []
        for req, paid, cost in requests:
            k = key(req)
            cached = self.get(k) is not None
            out.append(Item(k, 0.0 if cached else cost, paid and not cached, cached))
        return out

    def approve(self, items: list[Item], *, cap: float | None, scope: str, reason: str | None,
                currency: str = "CNY", code=None, relay_user=None, relay_at=None) -> dict | None:
        """The money path for a plan: {approved_by, reason} to write on each
        ledger line, or None when nothing in it is paid."""
        paid = [i for i in items if i.paid]
        if not paid:
            return None
        if cap is None:
            raise TakeRefused(msg("spend_cap_missing", "no spend cap is declared: the owner declares one "
                                  "before any paid call. Nothing was generated."))
        unpriced = sum(1 for i in paid if i.cost is None)
        if unpriced:
            raise TakeRefused(msg("price_unknown", f"{unpriced} paid takes have no price; the owner adds "
                                  "one first. Nothing was generated.", count=unpriced))
        total = round(sum(i.cost for i in paid), 2)
        left = round(float(cap) - self.spent(), 2)
        if total > left:
            raise TakeRefused(msg("spend_cap_exceeded", f"this plan costs {total} {currency} but only {left} "
                                  f"{currency} of the cap is left. Nothing was generated.",
                                  cost=total, left=left, currency=currency))
        why = gate.why(reason)
        passed = gate.confirm("approve spend", f"{len(paid)} paid takes, {total} {currency} "
                              f"({left} {currency} left of the cap)", "approve",
                              subj=gate.subject("approve_spend", scope, "takes",
                                                {i.key: i.cost for i in paid}, version=len(self.lines())),
                              code=code, relay_user=relay_user, relay_at=relay_at)
        return {"approved_by": passed.changed_by, "reason": why + passed.audit}


def _write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)
