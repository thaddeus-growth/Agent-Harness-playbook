#!/bin/bash
# Builds one eval run's workspace in the current (empty) directory. It runs
# outside the agent's sandbox, from a case's scaffold.sh:
#
#     exec bash "$(dirname "$0")/../fixture/build.sh" [--flag ...]
#
# Copy this file to evals/fixture/build.sh and set the four names below.
set -euo pipefail

CLI=shop                      # the harness's command name
PREFIX=SHOP                   # its env prefix (harness.toml [harness].env_prefix)
SCRIPTS=scripts               # its scripts dir (harness.toml [harness].scripts_dir)
FIXTURES=tests/fixtures       # committed text fixtures for the data dir (optional)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(pwd)"

# 1. Refuse a checkout that holds a file git does not track (client data, a
#    .env, a database), whether ignored or not. evals/, .venv/, __pycache__/ and
#    .DS_Store are the harness's own litter and are allowed. The agent under test
#    can read the checkout it runs from. A tracked file that is modified (an
#    ablation copy with a rule cut from SKILL.md) is fine.
stray="$(git -C "$REPO" status --porcelain --ignored --untracked-files=all \
  | grep -E '^(\?\?|!!) ' \
  | grep -Ev '^.. (evals/.*|(.*/)?(\.venv|__pycache__)/.*|(.*/)?\.DS_Store)$' \
  | sed -n 1p || true)"
if [ -n "$stray" ]; then
  echo "eval fixture: the checkout holds a file git does not track ($stray): run the evals from a clean clone" >&2
  exit 1
fi

# 2. The harness's code and its config (the kit looks for harness.toml at or
#    above scripts/kit), and the one entry the agent may run. The entry wipes
#    the environment: no credential is readable and the data dir is the
#    offline copy below.
mkdir -p harness client
cp -R "$REPO/$SCRIPTS" "$REPO/ssot" "$REPO/harness.toml" harness/
cat > "$CLI" <<ENTRY
#!/bin/bash
exec env -i HOME="$WORK" PATH="\$PATH" ${PREFIX}_DATA_DIR="$WORK/client" python3 "$WORK/harness/$SCRIPTS/$CLI.py" "\$@"
ENTRY
chmod +x "$CLI"

# 3. The data dir, from committed text fixtures if there are any (a scaffolded
#    harness has none until you write them; *.db is gitignored, so build the
#    database from text with the harness's own verbs, here, never commit it),
#    and the flags a case asks for.
if [ -d "$REPO/$FIXTURES" ]; then
  cp -R "$REPO/$FIXTURES/." client/
fi
for flag in "$@"; do
  case "$flag" in
    # --no-market) ... leave the market undeclared
    # --old-schema) ... leave one table in an older shape
    *) echo "eval fixture: unknown flag $flag" >&2; exit 2 ;;
  esac
done
