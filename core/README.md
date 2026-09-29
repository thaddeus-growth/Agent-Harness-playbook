# core/ moved into kit/

Round 3 added `core/` and this branch added `kit/`; both distilled the same
amazon-ads-harness modules. The owner chose one shared package (console
decision `core-vs-kit`, 2026-09-29): **`kit/`**. What `core/` held now lives there,
with its tests and its *Paid for* history (see kit/README.md, "What each guard paid for").

| core/ module | now |
| --- | --- |
| `gate.py` | [`kit/human.py`](../kit/human.py) (+ `kit/auth.py`): relay audit is now validated too |
| `store.py` | [`kit/db.py`](../kit/db.py): REPLACE trigger, `newest_backup`, `shrunk_since_backup` added |
| `raw.py` | [`kit/pull.py`](../kit/pull.py) (merge by key, chunks, gap ledger), [`kit/atomic.py`](../kit/atomic.py) (fsync), [`kit/single_instance.py`](../kit/single_instance.py) |
| retry policy in `raw.py` | [`kit/retry.py`](../kit/retry.py) (`RetryPolicy`, `call`, `GaveUp`) |
| `messages.py`, `contract.py` | [`kit/messages.py`](../kit/messages.py), [`kit/contract.py`](../kit/contract.py), `kit.guards.json_contract.check_verbs` |
| `runner.py`, `dates.py` | [`kit/runner.py`](../kit/runner.py), [`kit/dates.py`](../kit/dates.py) + [`kit/guards/clock.py`](../kit/guards/clock.py) |

A new harness does not copy this folder: `scaffold/new_harness.py` vendors `kit/`.
One difference is deliberate: kit's message registry header is `code, params, meaning_en, meaning_<lang>…`.
