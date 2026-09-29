"""The shop's one steering rule: once a market's unit cost is confirmed,
restock the house product by the reorder threshold. `basis()` is the hash
of what the rule read, so an approval goes stale when that moves."""

import hashlib
import json

from kit import facts
from kit.registry import Thresholds

TARGET = "SKU-A1"


def basis(con, market: str) -> str:
    cost = facts.confirmed(con, market, "unit_cost")
    blob = json.dumps({"market": market, "unit_cost": cost, "ingest": "v1"},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def proposals(con, market: str) -> list[dict]:
    cost = facts.confirmed(con, market, "unit_cost")
    if cost is None:
        return []
    units = int(Thresholds().get("reorder_units", con, market))
    return [{"kind": "restock", "market": market, "target_ref": TARGET,
             "payload": {"units": units},
             "evidence": {"unit_cost": cost},
             "expected": {"effect": f"{TARGET} gets {units} more units"},
             "basis": basis(con, market)}]
