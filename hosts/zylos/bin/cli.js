#!/usr/bin/env node
'use strict';
// The harness CLI for the agent: zylos-core links this file (SKILL.md `bin`,
// manifest cli.bin) as $ZYLOS_DIR/bin/<cli.bin>. That dir is not on the
// agent's PATH, so the hooks and prompts call it by its absolute path. It
// runs `uv run --no-project <skill dir>/<cli.entry> <args…>` in the caller's
// cwd, with <PREFIX>_DATA_DIR = the component data dir unless the caller set
// one, and bytecode caches under the data dir (nothing is written into the
// skill dir), and exits with the CLI's status (128+n when a signal killed
// it). Stdlib only.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

// Invoked through a symlink: find the real adapter dir before loading lib.js.
const here = path.dirname(fs.realpathSync(__filename));
const h = require(path.join(here, '..', 'lib.js'));

const r = spawnSync(h.findUv() || 'uv', h.cliArgs(...process.argv.slice(2)),
                    { stdio: 'inherit', env: h.harnessEnv() });
if (r.error) {
  console.error(`${h.BIN_NAME}: could not run uv (${r.error.code || r.error.message}); run node ${h.POST_INSTALL}`);
  process.exit(127);
}
process.exit(r.status ?? 128 + (os.constants.signals[r.signal] || 0));
