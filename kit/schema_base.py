"""The kit's human tables, as data: what a human (or the gate) decided,
which no pull can ever rebuild.

`HUMAN` holds the six tables every harness gets (kit.db.with_human merges
them with the harness's own cache tables):

  * client_facts / client_facts_history   facts about the client, per market
  * decisions / decisions_history         decisions about one entity
  * action_queue                          proposed external writes and
                                          the human's yes/no on each
  * action_effects                        what each executed write did

Every table has a `market` column (a harness without markets writes the
pseudo-market "_"); every history table has `action`. Column order is the
physical order: kit.db's drift guard compares it, so a change here is a
schema change (kit/tests/test_db.py pins schema_hash of these tables:
changing them means every harness bumps its schema version).

`APPEND_ONLY` and `FROZEN_COLUMNS` become triggers in the file itself
(kit.db): history is never rewritten or deleted, a fact or a decision is
never deleted, a queued action never changes except its decision
columns, an effect row is never changed. Trust lives in the data:
`is_assumption` = 1 and status 'pending' are the defaults, so a row a
writer forgot to mark is never trusted.

Deviation (SPEC §db): SPEC lists action_effects without `market`, but
also says every human table has one; it has one here (after queue_id).

Data only, stdlib only. Test: kit/tests/test_db.py.
"""

from __future__ import annotations

DECISION_STATUSES = ("pending", "confirmed", "withdrawn")
QUEUE_STATUSES = ("pending", "approved", "rejected", "superseded", "expired",
                  "executed", "failed", "unknown")
EFFECT_STATES = ("pending", "sent", "confirmed", "failed", "unknown")

_T, _I = "TEXT", "INTEGER"

HUMAN: dict[str, dict] = {
    "client_facts": {
        "columns": {"market": _T, "key": _T, "value": _T, "is_assumption": _I,
                    "source": _T, "updated_at": _T, "changed_by": _T},
        "pk": ("market", "key"),
        "not_null": ("is_assumption", "source", "updated_at", "changed_by"),
        "defaults": {"is_assumption": 1},
        "doc": "Facts about the client, current values only, one row per "
               "market and key. is_assumption = 1 is pending (an agent or an "
               "unconfirmed human said so); 0 is confirmed through the human "
               "gate. Only the facts verb writes it; rows are never deleted.",
        "grain": "market × key",
    },
    "client_facts_history": {
        "columns": {"id": _I, "market": _T, "key": _T, "action": _T,
                    "old_value": _T, "new_value": _T, "is_assumption": _I,
                    "source": _T, "reason": _T, "changed_by": _T, "at": _T},
        "pk": ("id",),
        "not_null": ("market", "key", "action", "reason", "changed_by", "at"),
        "doc": "Append-only log of client_facts: one row per change, with "
               "the action, the stated reason and who made it (changed_by). "
               "Never updated or deleted (triggers).",
        "grain": "append-only row",
    },
    "decisions": {
        "columns": {"market": _T, "entity_type": _T, "entity_id": _T,
                    "key": _T, "value": _T, "status": _T,
                    "confirmed_value": _T, "source": _T, "updated_at": _T,
                    "changed_by": _T},
        "pk": ("market", "entity_type", "entity_id", "key"),
        "not_null": ("value", "status", "source", "updated_at",
                     "changed_by"),
        "defaults": {"status": "pending"},
        "doc": "Human decisions about one entity, one row per market, "
               "entity and key. status pending | confirmed | withdrawn; "
               "confirmed_value is the value in force until a pending one "
               "is confirmed. Only the decisions verb writes it; rows are "
               "never deleted.",
        "grain": "market × entity_type × entity_id × key",
    },
    "decisions_history": {
        "columns": {"id": _I, "market": _T, "entity_type": _T, "entity_id": _T,
                    "key": _T, "action": _T, "old_value": _T, "new_value": _T,
                    "status": _T, "reason": _T, "changed_by": _T, "at": _T},
        "pk": ("id",),
        "not_null": ("market", "entity_type", "entity_id", "key", "action",
                     "reason", "changed_by", "at"),
        "doc": "Append-only log of decisions: action, stated reason, who. "
               "Never updated or deleted (triggers).",
        "grain": "append-only row",
    },
    "action_queue": {
        "columns": {"id": _I, "market": _T, "action_id": _T, "kind": _T,
                    "target_ref": _T, "payload": _T, "evidence": _T,
                    "basis": _T, "expected": _T, "status": _T,
                    "created_at": _T,
                    "decided_by": _T, "decided_at": _T, "reason": _T,
                    "reason_code": _T},
        "pk": ("id",),
        "unique": (("action_id",),),
        "not_null": ("market", "action_id", "kind", "target_ref", "payload",
                     "basis", "status", "created_at"),
        "defaults": {"status": "pending"},
        "doc": "Proposed external writes waiting on a human. action_id = "
               "content hash of the proposal; payload, evidence and "
               "expected are JSON; basis = hash of the rows the rule read. "
               "status pending | approved | rejected | superseded | expired "
               "| executed | failed | unknown. The proposal is frozen: only "
               "status, decided_by, decided_at, reason and reason_code "
               "change (trigger); rows are never deleted.",
        "grain": "queued action",
    },
    "action_effects": {
        "columns": {"id": _I, "queue_id": _I, "market": _T, "effect_id": _T,
                    "state": _T, "request": _T, "response": _T, "at": _T,
                    "changed_by": _T},
        "pk": ("id",),
        "not_null": ("queue_id", "market", "effect_id", "state", "at",
                     "changed_by"),
        "doc": "Append-only log of executed writes: one row per state of "
               "one effect (effect_id = the queue row's action_id): pending "
               "| sent | confirmed | failed | unknown, with the request and "
               "response as JSON. Never updated or deleted (triggers).",
        "grain": "append-only row",
    },
}

# table -> the operations a trigger refuses on it.
APPEND_ONLY: dict[str, tuple[str, ...]] = {
    "client_facts": ("DELETE",),
    "client_facts_history": ("UPDATE", "DELETE"),
    "decisions": ("DELETE",),
    "decisions_history": ("UPDATE", "DELETE"),
    "action_queue": ("DELETE",),
    "action_effects": ("UPDATE", "DELETE"),
}

# table -> the ONLY columns an UPDATE may change (a trigger refuses the rest).
FROZEN_COLUMNS: dict[str, tuple[str, ...]] = {
    "action_queue": ("status", "decided_by", "decided_at", "reason",
                     "reason_code"),
}
