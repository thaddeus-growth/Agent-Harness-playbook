#!/usr/bin/env node
'use strict';
// Run a long command detached from the agent (Linux and macOS; no setsid
// needed), so it survives the 10-minute foreground cap and an agent session
// restart:
//
//   node zylos/bin/detach.js <name> -- <alias> pull orders --days 30
//
// The child runs in its own session and process group (spawn `detached`),
// cwd = the data dir, <PREFIX>_DATA_DIR = the data dir unless already set,
// with stdout+stderr in <data dir>/logs/<name>.log. When it ends, the log's
// last line is `<detach.exit_marker> <status>`. <data dir>/logs/<name>.pid is
// a JSON job file {pid, start, cmd}; `start` is the OS's start time for that
// pid, so a pid reused after a reboot is not taken for a running job. A start
// takes an exclusive lock (<name>.lock, O_EXCL) and refuses while <name>
// runs. A leading <alias> (manifest detach.alias) is the harness CLI
// (bin/cli.js); a leading `uv` is resolved like the hooks resolve it.
// Stdlib only.

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const h = require('../lib.js');

const { alias, exit_marker: MARKER } = h.M.detach;
const USAGE = `usage: node zylos/bin/detach.js <name> -- <command> [args…]   (a leading \`${alias}\` is the harness CLI)`;
const STALE_LOCK_MS = 60_000;
const [name, sep, ...cmd] = process.argv.slice(2);
if (!name || !/^[A-Za-z0-9_.-]+$/.test(name) || sep !== '--' || !cmd.length) {
  console.error(USAGE);
  process.exit(2);
}
if (cmd[0] === alias) cmd.splice(0, 1, process.execPath, h.CLI_JS);
else if (cmd[0] === 'uv') cmd[0] = h.findUv() || 'uv';

const logs = h.LOGS_DIR;
const jobFile = path.join(logs, `${name}.pid`);
const logFile = path.join(logs, `${name}.log`);
const lockFile = path.join(logs, `${name}.lock`);
fs.mkdirSync(logs, { recursive: true, mode: 0o700 });

let lock;
try {
  lock = fs.openSync(lockFile, 'wx', 0o600);
} catch (e) {
  if (e.code !== 'EEXIST') throw e;
  const age = Date.now() - fs.statSync(lockFile).mtimeMs;
  if (age < STALE_LOCK_MS) {
    console.error(`${name} is being started by another call; try again in a minute`);
    process.exit(1);
  }
  fs.rmSync(lockFile, { force: true }); // left by a start that crashed
  lock = fs.openSync(lockFile, 'wx', 0o600);
}
const unlock = () => { fs.closeSync(lock); fs.rmSync(lockFile, { force: true }); };

const old = h.readJob(jobFile);
if (h.jobAlive(old)) {
  unlock();
  console.error(`${name} is already running (pid ${old.pid}); log: ${logFile}`);
  process.exit(1);
}

const fd = fs.openSync(logFile, 'w', 0o600);
// sh appends the exit status as the done marker; "$@" keeps args verbatim.
// The marker is a validated plain token (lib.js), safe unquoted.
const child = spawn('sh', ['-c', `"$@"; echo "${MARKER} $?"`, 'sh', ...cmd], {
  cwd: h.DATA_DIR,
  env: h.harnessEnv(),
  detached: true,
  stdio: ['ignore', fd, fd],
});
fs.closeSync(fd);
if (!child.pid) {
  unlock();
  console.error(`could not start ${cmd[0]} (see ${logFile})`);
  process.exit(1);
}
child.on('error', () => {});
child.unref();
fs.writeFileSync(jobFile, `${JSON.stringify({ pid: child.pid, start: h.procStart(child.pid), cmd })}\n`);
unlock();
console.log(`pid ${child.pid} (${jobFile})`);
console.log(`log ${logFile}`);
