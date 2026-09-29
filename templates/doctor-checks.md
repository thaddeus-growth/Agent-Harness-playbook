# Doctor checks

The doctor is the one command an operator, a host agent or an install runs before trusting a harness. It reads: it never writes, pulls, or creates the database. Build it on day one with the rows below, then add a row each time a bug teaches you a new one.

## How it behaves

- **Every warning names its fix.** A line without a fix is information, never a warning.
- **Exit codes:** 0 by default, even with warnings. `--strict` makes any warning exit 1, so `doctor --strict && ingest …` never runs on a broken setup. A rejected live credential exits 1 without `--strict`.
- **Silence is earned.** An unset optional key is one information line, never a warning; a half-set optional group (some keys of it set) warns. *Paid for:* a check that asked every platform for a key only one platform reads showed a false MISSING on every correct setup, and people learned to ignore the doctor.
- **Each value names its source:** process env, or the env file it came from.
- **The database is opened read-only, and only if it exists.** A newer or unreadable file is reported and the doctor carries on, but it claims nothing it could not read.
- **Live checks tell UNREACHABLE (no answer) from REJECTED (answered no).** Only REJECTED fails the run.

## The checks

| Check | Warns when | The fix it names | The bug it caught |
| --- | --- | --- | --- |
| Data root set | The data root is unset, or its value came from a home-level env file | Set it in the process env and turn the env-file chain off | A home-level env file filled every variable a process left unset, so a client with no data root of its own landed in another client's data |
| Data outside the checkout | Any resolved path (data root, database, raw) lies inside the harness checkout | Move the data root out of the repository | Agent shells reset their working directory, and credentials, the database and raw files were scattered across folders |
| Env files private | An env file in the chain is readable by group or others | `chmod 600 <file>` | An env file holding secrets was readable by other users |
| Credentials present | A key the configured platform needs is unset, or an optional group is half-set | Set the named keys, or unset the group | The check required one platform's key of every platform: a false MISSING on every correct setup |
| Credentials work (`--live`) | One read-only token exchange per platform is refused; no answer is UNREACHABLE, a warning only | Re-authorize, or check the network | A network error was reported as a rejected key |
| Refresh token lifetime *(untried)* | A refresh token expires within the platform's warning window | Re-authorize before then | Not built in the source project. How long a refresh token lives, and whether it does at all, differs per platform and per app status: verify it in the first week |
| Database version | The file is stamped by a newer schema than this code knows | Update the code checkout | Switching between an older and a newer checkout rebuilt the same tables each way, cutting history to the last pull on every switch |
| Stale tables | A cache table's shape differs from the schema | Run the ingest that fills it (it refuses, naming the backfill, if raw lacks a day the table holds) | The doctor said to fix this before running the ingest, while the ingest is the fix |
| Unknown tables | The file holds tables the schema does not know | Stop reading them; check the database path | Tables older code had left, never refreshed, sat in a live database beside the current ones |
| Human data lost | A human table has fewer rows than the newest snapshot | Restore from that snapshot; run setup again only if there is none | A client's confirmed facts were found wiped in a live database, with every test green |
| Values under renamed keys | A human value is stored under a key that was renamed or merged | Move the value to the new key; a person confirms it | None yet: built with the first threshold renames, because a value left under an old name is read by nothing and nothing else says so |
| Scope declared | No scope is declared yet (not set up) | Run the init command | The check assumed one default scope for every client, so every other client got a permanent warning naming facts it had just set |
| Required inputs | A declared scope lacks a key its rules need | Set the key (pending); a person confirms it | Every profit verdict rested on an assumed break-even until the client's unit cost was set |
| Access gaps | The last pull recorded a named access gap (an optional report refused) | Information only: request the access if the client wants that report | None yet: a client without an optional access is a normal client, so this stays information and `--strict` passes |

The database rows map to [`core/store.py`](../core/store.py): `SchemaTooNew`, `stale_tables()`, `unknown_tables()`, `shrunk_since_snapshot()`.

## A warning line

```
  WARNING: DATA_ROOT came from ~/.harness.env (home-level: any client that leaves it unset lands here)
    -> export DATA_ROOT in the process env and set ENV_FILES=none
3 warning(s): each names its fix above.
```
