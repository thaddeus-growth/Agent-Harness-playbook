'use strict';
// Zylos pre-uninstall hook, before the skill dir disappears:
//   1. stop detached jobs still running (bin/detach.js job files in
//      <data dir>/logs/*.pid, checked to be ours: same pid AND start time);
//   2. remove every scheduler task named in zylos/manifest.json, or managed
//      by an earlier release (<data dir>/.zylos-tasks.json).
// The data dir (the harness's state, .env, console log, logs) is never
// touched. Exits 1 with the reason on stderr when a job can't be stopped or
// a task can't be listed or removed.

const h = require('../lib.js');

const problems = [...h.stopJobs(), ...h.removeAllTasks()];
h.log(`data dir left as is: ${h.DATA_DIR}`);
if (problems.length) {
  for (const p of problems) console.error(`[${h.NAME}] FAILED: ${p}`);
  process.exit(1);
}
