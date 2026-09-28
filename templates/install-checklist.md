# Host install checklist

The remote host agent installs a release only after the owner types `confirm vX` in the group chat, outside scheduled-job windows. It ends every install with this report, as one table.

## The install message

Written from the version the host actually runs, not the previous tag: tags are cheap and hosts skip versions. Paste-ready, posted to the host agent (or parked in the owner queue while the host agent is being replaced).

- From `vA` (installed) to `vB`; wait for the owner's `confirm vB`
- Every step of the skipped versions, in order: schema change (add-only or not), ingests to run once, backfill pulls, new scheduled tasks
- Deadlines, such as a push queue's retention while consumption is paused

## Steps

1. Back up: the database (with an integrity check), the web-server config, the user list for the console.
2. Fetch the tag with a read-only deploy token; `git archive` it into a fresh release folder; delete the clone.
3. Uninstall the old version without purging data; install the new release folder.
4. Restore any web-server block the reinstall dropped; validate and reload the config.
5. Run the post-install doctor; re-register the scheduled tasks.

## Report

| Check | Expected |
| --- | --- |
| Version | The installed manifest says `vX` |
| Internal files | Agent instructions, tests, evals and CI files are absent |
| Doctor | OK, no warnings |
| Schema | The database version stamp equals the release's |
| Scheduled tasks | Each task re-registered, next run time listed |
| Pages | Local 200, public 401 (login required) |
| The change itself | The fix or feature this release carries, seen live |
| Writes | Still off unless the owner enabled them in writing |

Rules: no confirm, approve or apply on the owner's behalf; no paid data calls; report failures verbatim.

## Scheduled tasks and long jobs

- The source project's host scheduler sent the agent a prompt, not a command. Write it closed: absolute paths, "run only these commands", what success looks like, "on success report nothing", "on failure send the owner the error lines", a verified reply channel.
- Find tasks by name (names are not unique), keep exactly one, act on each by its id, and judge each call by its output, not its exit code.
- Agent tool calls stop at about 10 minutes. A longer job runs detached: it writes a log ending `EXIT <code>` and a job file with its pid and start time; the agent polls the log. Pulls are read-only and resumable, so a killed job is simply rerun.

## New host, or a replaced host agent: what can it reach?

Ask these read-only questions; answers name keys, never values. Re-ask them when the host agent is replaced.

1. Which OS user runs the agent, and who owns the harness folder and the data folder?
2. Does that user have sudo?
3. The agent's permission mode and deny rules: can the agent itself edit them?
4. The secrets file's mode and key names: can the agent read it?
5. Do the host's own connectors expose write actions on the client's platform account, and can the harness's read credential also write? Keep credentials behind one seam (env, or a host command that prints a short-lived token) so the harness stays the only writer.

If the agent can read the code and the keys, only a barrier it cannot edit protects them. Options, cheapest first: ship only runtime files; audit outputs and tracebacks; a read sandbox in settings the agent can't change; a second OS user behind one narrow command; the core off the box.
