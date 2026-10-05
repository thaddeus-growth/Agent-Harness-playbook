# Invariants

What every growth harness built from this playbook keeps, however it is written. A harness may vendor the [kit](../kit/README.md) or have its own tables; it conforms when these hold. Each row says who showed it and whether [`check.py`](check.py) can test it from outside.

A rule is listed only when two harnesses reached it on their own. Evidence was read on 2026-10-05 from the checkouts named; nothing in them was changed.

| # | Invariant | Shown by | Check |
| --- | --- | --- | --- |
| I1 | **Pull is read-only and lands in raw files** that are written once. | seoh (`open(..., "x")`, manifest, hash), kit `raw` (KOL, RedNote) | not yet: needs a pull fixture per harness |
| I2 | **Ingest rebuilds the cache from raw and is idempotent**: ingest twice, same rows. | seoh (`ingest_twice_gives_same_rows`), ppc (upsert), kit `db` | not yet: needs a raw fixture per harness |
| I3 | **An agent write never changes a value a human confirmed.** Only a human verb confirms; it carries a reference to the decision. | seoh (`h_fact`), kit `facts` | **C1** |
| I4 | **Human data is a separate table family** that ingest never drops. | seoh (`h_` prefix), ppc (`client_facts`), kit stack | not yet: the check is a schema read, which differs per harness |
| I5 | **Registries are TSV files in `ssot/`**; a number lives once. | all three | not yet: structure only, covered by each harness's own `test_ssot` |
| I6 | **`doctor` runs before work and every warning names its fix.** | all three | **C2** |
| I7 | **One CLI facade; one `RESULT: N passed` line** from the whole suite, and a suite that prints none fails. | all three | not yet: needs the harness's test run |
| I8 | **At most 10 open asks per owner**, and a read-only report page built from one data object. | seoh, adcut (kit `queue`) | not yet: ppc has neither on its current branch |

## How to read a result

`python3 conformance/check.py conformance/adapters/<name>.toml --root <harness checkout>` prints PASS, FAIL or SKIP per check. A SKIP is a check the adapter does not cover, not a pass. The runner exits 1 on any failure and when nothing passed.

First run, 2026-10-05:

| Harness | C1 | C2 |
| --- | --- | --- |
| seoh | pass | pass |
| ppc (amazon-ads-harness, branch `fix-pr-a-sqlite-utils`) | **fail**: `facts set` on a confirmed fact replaces the value and the fact stays confirmed (`is_assumption` stays 0), so an agent can change what the owner signed | pass |

## Adding an invariant or a check

1. A second harness must show it, in a path named in the row.
2. A check is an adapter-driven scenario in `check.py` plus a test in `tests/test_conformance.py` that runs it on a harness that breaks the rule (each check is tried on a broken input first).
3. Never put an adapter in a harness. The adapter is the playbook's statement of how to drive it.
