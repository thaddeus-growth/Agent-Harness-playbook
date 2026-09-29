"""Takes: paid generative calls (an image, a video, a voice, a batch of
model calls), planned, capped, approved by a person, charged and kept.

Ported from the playbook's media-harness core/takes.py. A take is one call
to a generator, keyed by the request that makes it:

    <root>/<key>/request.json   what was asked; key = sha256 of it, canonical
    <root>/<key>/result.json    what came back; its presence = done
    <root>/<key>/...            the files the generator wrote (media, logs)
    <ledger>                    one JSON line per paid take: when, key, cost,
                                who approved it and why

What it guards:

  * A take is written once. `put` on a key that has a result returns the
    kept one and calls nothing; nothing here overwrites or deletes a take.
    A re-roll is a new request (add `take: 2`), so an approved take is
    never lost or silently replaced. The key covers everything that changes
    the output, the generator's own version included: bump `impl` when the
    same request would now give different media, or the old take keeps
    being served. request.json and result.json are written atomically
    (kit.atomic); a key that is not a sha256 is refused, so a key can never
    name a folder outside the store.
  * A broken result is never cached: `put(check=...)` runs the caller's
    sanity check on the result before result.json is written; a refused
    result leaves no result.json (the folder keeps what the generator wrote,
    for a look) and the next `put` calls the generator again.
  * The money path. `approve()` refuses, in this order: a paid plan with no
    declared cap; a plan with an unpriced paid item (an unknown price is
    never taken as free); a plan that costs more than what is left of the
    cap. Then a person passes the human gate (kit.human.confirm): they
    retype the plan's total at the terminal, or a relayed one-time code
    comes back that is bound to the subject {key: cost} of exactly the
    plan's paid items, with the ledger's length as the version. A charge
    moves that version, so a code approves this plan once. --reason is
    required and a relayed approval carries its relay audit.
  * The standing allowance (optional, `allowance=`): the owner may declare
    ahead of time that plans keeping the ledger's total at or under an
    amount pass without the retype. It only replaces the person at the
    gate; the cap, the prices and --reason are checked first, exactly as
    without it, and a plan that would take the ledger above the allowance
    goes to the gate. The approval names it ("owner, standing allowance
    ..."), so the ledger shows a plan passed by allowance, not by a person
    at a terminal. The owner sets, raises and removes it; an agent never
    writes it.
  * An unconfirmed price never rides the allowance: an item may say its
    price is not confirmed by a person (`confirmed` False, e.g. from
    kit.prices.estimate); a plan with any such paid item goes to the gate
    whatever the allowance, and the gate's prompt and the approval's
    reason name how many prices are unconfirmed.
  * Spend is charged in an append-only ledger (one fsynced JSON line per
    paid take made, with who approved it); `spent()` sums it. A ledger line
    that cannot be read is refused, never skipped: skipping would undercount
    what was spent.
  * Download what you are given at once: generator URLs expire. The make
    function writes bytes into the take folder; a result that only holds a
    URL is a broken take (say so in `check`).

Paid for: a voice take whose voice was not installed wrote a 0.01 s file
and exited 0; the empty take was cached and every later build reused it
(so `check`). A re-roll that replaced a take would lose one the owner had
already approved and paid for (so a re-roll is a new request, and nothing
here overwrites or deletes). Every price was pending while the allowance
approved spend against them (so `confirmed`, and kit.prices).

Deviations from the source: refusals are kit.contract.HarnessError
(TakeRefused) carrying coded messages, renamed `take_*` so a harness still
holding the old registry can load the kit; the gate is kit.human, where the
person retypes the total rather than a fixed word; `charge` needs
`approved_by`; the currency is always named by the caller, never assumed;
amounts are compared unrounded, so a plan of many sub-cent calls is not
rounded to free.

Test: kit/tests/test_takes.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kit import atomic, dates, human
from kit.contract import HarnessError
from kit.messages import msg

KEY_RE = re.compile(r"[0-9a-f]{64}")
EPSILON = 1e-9          # float sums of prices: a plan exactly at the cap fits


class TakeRefused(HarnessError):
    """A take or a plan refused: nothing was called, cached or charged."""


def canon(obj) -> str:
    """The one canonical JSON form (sorted keys, no spaces, utf-8 kept)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def key(request: dict) -> str:
    """The take's key: sha256 of the canonical request. Put only what
    changes the output in the request (not absolute paths: moving a project
    must not pay again for every take)."""
    return hashlib.sha256(canon(request).encode()).hexdigest()


def _amount(x: float) -> float:
    """An amount for a message: rounded for reading, never for comparing."""
    return round(x, 6)


def _shown(x: float) -> str:
    """An amount as a person retypes it: 30, 12.5, 0.0003 (no exponent)."""
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


@dataclass(frozen=True)
class Item:
    """One take of a plan: its key, its cost (None = unpriced; 0 when it is
    cached), whether it will be paid for, whether it is kept already, and
    whether a person confirmed its price (True unless the caller says)."""
    key: str
    cost: float | None
    paid: bool
    cached: bool
    confirmed: bool = True


class TakeStore:
    """The takes under `root` and their spend ledger at `ledger`."""

    def __init__(self, root: Path | str, ledger: Path | str):
        self.root, self.ledger = Path(root), Path(ledger)

    def dir(self, k: str) -> Path:
        """A take's folder; refused unless `k` is a sha256 key."""
        if not isinstance(k, str) or not KEY_RE.fullmatch(k):
            raise TakeRefused(msg(
                "take_key_malformed",
                f"{k!r} is not a take key (the sha256 of a request). "
                f"Nothing was read or written.", key=str(k)))
        return self.root / k

    def get(self, k: str) -> dict | None:
        """The kept result of take `k`, None when it was not made yet."""
        f = self.dir(k) / "result.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None

    def put(self, request: dict, make: Callable[[Path], dict], *,
            check: Callable[[dict], str | None] | None = None
            ) -> tuple[str, dict, bool]:
        """(key, result, made): the kept result, or `make(folder)` run once
        and kept. `check(result)` returns why the result is broken, or None;
        a broken result is refused (TakeRefused) and not kept, and the next
        put calls `make` again."""
        k = key(request)
        hit = self.get(k)
        if hit is not None:
            return k, hit, False
        d = self.dir(k)
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "request.json").exists():
            atomic.write_json_atomic(d / "request.json", request, indent=1)
        result = make(d)
        why = check(result) if check else None
        if why:
            raise TakeRefused(msg(
                "take_not_kept",
                f"take {k[:12]} was not kept: {why}. Nothing was cached; "
                f"the next run calls the generator again.",
                take=k[:12], why=why))
        result = {**result, "made_at": dates.utc_stamp()}
        hit = self.get(k)
        if hit is not None:     # another run finished first: its take is kept
            return k, hit, False
        atomic.write_json_atomic(d / "result.json", result, indent=1)
        return k, result, True

    def charge(self, k: str, cost: float, *, approved_by: str,
               **audit) -> None:
        """One ledger line for a paid take that was made, with who approved
        it (an approve() result spreads in: approved_by, reason)."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        line = canon({"at": dates.utc_stamp(), "key": k, "cost": cost,
                      "approved_by": approved_by, **audit})
        with open(self.ledger, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def lines(self) -> list[dict]:
        """Every ledger line, in order; an unreadable one is refused."""
        if not self.ledger.is_file():
            return []
        out = []
        for n, raw in enumerate(
                self.ledger.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                float(row.get("cost") or 0)
            except (ValueError, TypeError, AttributeError):
                raise TakeRefused(msg(
                    "take_ledger_unreadable",
                    f"spend ledger {self.ledger} line {n} cannot be read; "
                    f"what was spent is unknown until a person repairs it. "
                    f"Nothing was generated.",
                    path=str(self.ledger), line=n)) from None
            out.append(row)
        return out

    def spent(self) -> float:
        """The sum of every charged cost."""
        return sum(float(r.get("cost") or 0) for r in self.lines())

    def plan(self, requests: list[tuple]) -> list[Item]:
        """[(request, paid, cost[, confirmed])] -> items with their cache
        state; a kept take costs 0 and is not paid for again. `confirmed`
        (default True) says a person confirmed the price."""
        out = []
        for req, paid, cost, *rest in requests:
            k = key(req)
            cached = self.get(k) is not None
            out.append(Item(k, 0.0 if cached else cost, paid and not cached,
                            cached, bool(rest[0]) if rest else True))
        return out

    def approve(self, items: list[Item], *, cap: float | None, scope: str,
                reason: str | None, currency: str,
                allowance: float | None = None,
                allowance_note: str | None = None, code: str | None = None,
                relay_user: str | None = None, relay_at: str | None = None
                ) -> dict | None:
        """The money path for a plan: {approved_by, reason} to spread on
        each ledger line (charge), or None when nothing in it is paid.
        `scope` names what the plan is for (a project); it is the subject's
        market slot. `allowance` is the owner's standing allowance, if they
        declared one; it never covers a plan with an unconfirmed price."""
        paid = [i for i in items if i.paid]
        if not paid:
            return None
        if cap is None:
            raise TakeRefused(msg(
                "take_cap_missing",
                "no spend cap is declared: the owner declares one before any "
                "paid call. Nothing was generated."))
        unpriced = sum(1 for i in paid if i.cost is None)
        if unpriced:
            raise TakeRefused(msg(
                "take_price_missing",
                f"{unpriced} paid takes have no price; the owner adds one "
                f"first. Nothing was generated.", count=unpriced))
        total = sum(float(i.cost) for i in paid)
        spent = self.spent()
        left = float(cap) - spent
        if total > left + EPSILON:
            raise TakeRefused(msg(
                "take_cap_exceeded",
                f"this plan costs {_amount(total)} {currency} but only "
                f"{_amount(left)} {currency} of the cap is left. Nothing was "
                f"generated.", cost=_amount(total), left=_amount(left),
                currency=currency))
        why = human.why(reason)
        after = spent + total
        unconfirmed = sum(1 for i in paid if not i.confirmed)
        if (allowance is not None and not unconfirmed
                and after <= float(allowance) + EPSILON):
            note = f" ({allowance_note})" if allowance_note else ""
            return {"approved_by": f"owner, standing allowance "
                                   f"{_amount(float(allowance))} {currency}"
                                   f"{note}",
                    "reason": f"{why} [plan {_amount(total)} {currency}, "
                              f"ledger after {_amount(after)} {currency}]"}
        warn = ""
        if unconfirmed:
            off = ("; the standing allowance does not cover them"
                   if allowance is not None else "")
            warn = (f" {unconfirmed} of these prices are unconfirmed (no "
                    f"person confirmed them{off}).")
        expected = _shown(total)
        subj = human.subject("approve spend", scope, "takes",
                             {i.key: i.cost for i in paid},
                             version=len(self.lines()))
        channel = human.confirm(
            "approve spend",
            f"{len(paid)} paid takes for {scope}: {expected} {currency} "
            f"({_amount(left)} {currency} left of the cap).{warn} Retype the "
            f"total to approve: ", expected, subj=subj, code=code)
        audit = human.relay_audit(channel, relay_user, relay_at)
        note = f" [{unconfirmed} prices unconfirmed]" if unconfirmed else ""
        return {"approved_by": human.changed_by(channel),
                "reason": why + audit + note}
