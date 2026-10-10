# Changelog

What a host or an agent can see changed, newest first. Format: Keep a Changelog.
A change that touches the code, a registry, the skill or the console adds its line
under `## [Unreleased]` in the same merge request (CI job `changelog`); a change
with nothing visible says `[no changelog]` in its title. A removal or rename of a
`--json` key or a command is named under **Removed** or **Changed** as breaking.
At a release, `## [Unreleased]` becomes `## [X.Y.Z] - DATE` and the version in
`SKILL.md` moves with it.

Each entry names the story (`S..`) or rule (`P..`) it serves.

## [Unreleased]

### Added
- The harness skeleton, scaffolded from the playbook.
