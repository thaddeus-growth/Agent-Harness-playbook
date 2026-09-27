# Host install checklist

The remote host agent installs a release only after the owner types `confirm vX` in the group chat, outside scheduled-job windows. It ends every install with this report, as one table.

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
