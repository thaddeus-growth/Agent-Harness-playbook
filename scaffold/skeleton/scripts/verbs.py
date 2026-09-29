"""{{name}}'s verb table (kit.verbs): every command `{{cli}}` answers, and
the kind of thing each does. A verb that is not listed here is refused.

Kinds: read (writes nothing), human (writes a human table: a pending value,
or lowers trust), gated (passes the human gate), external (calls an
external system), dev (developer tools). The boundary guard, the story
checks and the json contract read this table; add a verb here in the same
change as its script.
"""

from kit.verbs import Verb

VERBS = [
    Verb(("facts", "init"), "facts.py", "human", False,
         answers="Declare the scope and answer the onboarding questions "
                 "(all pending)"),
    Verb(("facts", "list"), "facts.py", "read",
         answers="Every fact of a scope (* = pending)"),
    Verb(("facts", "get"), "facts.py", "read", answers="One fact"),
    Verb(("facts", "history"), "facts.py", "read",
         answers="Who changed which fact, when and why"),
    Verb(("facts", "set"), "facts.py", "human",
         answers="Record a value, pending until a person confirms it"),
    Verb(("facts", "unconfirm"), "facts.py", "human",
         answers="Doubt a confirmed value (lowers trust)"),
    Verb(("facts", "rollback"), "facts.py", "human",
         answers="Propose an earlier value again, pending"),
    Verb(("facts", "confirm"), "facts.py", "gated",
         answers="A person confirms a pending value"),
    Verb(("facts", "restore"), "facts.py", "gated", False,
         answers="Replay a backup (a person at the terminal)"),
    Verb(("decisions", "list"), "decisions.py", "read",
         answers="Every decision (* = pending)"),
    Verb(("decisions", "get"), "decisions.py", "read",
         answers="One decision and the value in force"),
    Verb(("decisions", "history"), "decisions.py", "read",
         answers="Every change to a decision"),
    Verb(("decisions", "set"), "decisions.py", "human",
         answers="Propose a decision, pending"),
    Verb(("decisions", "withdraw"), "decisions.py", "human",
         answers="Take back a pending decision"),
    Verb(("decisions", "confirm"), "decisions.py", "gated",
         answers="A person puts a decision in force"),
    Verb(("pending",), "pending.py", "read",
         answers="What waits on a person, as ready console asks"),
    Verb(("compute", "stories"), "compute_stories.py", "read",
         answers="Is each story still true on this client's data?"),
    Verb(("queue", "list"), "queue_actions.py", "read",
         answers="Proposed, approved and executed actions"),
    Verb(("queue", "add"), "queue_actions.py", "human",
         answers="Queue proposals, pending a person's yes"),
    Verb(("queue", "reject"), "queue_actions.py", "human",
         answers="Say no to a proposal, with a reason code"),
    Verb(("queue", "approve"), "queue_actions.py", "gated",
         answers="A person approves a proposal"),
    Verb(("execute", "apply"), "execute_actions.py", "external",
         answers="Send approved actions (a dry run unless --apply)"),
    Verb(("execute", "reconcile"), "execute_actions.py", "external",
         answers="Read back an action whose outcome is unknown"),
    Verb(("test",), "../tests/run.py", "dev", False, False,
         answers="Run every test (each must print RESULT: N passed)"),
]
