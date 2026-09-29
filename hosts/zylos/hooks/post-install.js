'use strict';
// Zylos post-install hook; post-upgrade.js runs it in its light mode. Both
// are idempotent. Hard steps exit non-zero with the manual fix; soft checks
// print and continue. The whole run is capped (HOOK_BUDGET_MS in lib.js);
// what the cap cuts is named with the command that finishes it. Writes
// nothing into the skill dir; state lives in the data dir.
//
//   hard: zylos-core >= manifest min_zylos; the data dir (0700); uv (pinned
//         installer, sha256-checked); Python runtime.python; the harness CLI
//         (manifest cli.entry) resolves its PEP 723 env (`--help`)
//   soft: endpoints reachable; `<cli> <cli.doctor>`; the declared scope
//         (manifest scope); the console entry and its Caddy block
//   then: the scheduler tasks, reconciled to exactly the manifest's

const fs = require('node:fs');
const path = require('node:path');
const h = require('../lib.js');

function budgetFail(step) {
  h.fail(`${step} did not finish within this hook's ${h.budgetMinutes()}-minute budget`,
         `run it again (it resumes where it stopped): node ${h.POST_INSTALL}`);
}

function consoleNotes() {
  if (!fs.existsSync(h.CONSOLE_ENTRY)) {
    h.log(`note: the console service (${h.SERVICE}) will not start: ${h.CONSOLE_ENTRY} is missing `
          + '(manifest console.entry; vendor the playbook console into the release)');
  }
  const C = h.M.console;
  if (h.caddyBlockMissing()) {
    h.log(`WARNING: ${h.USERS_FILE} has logins but ${h.CADDYFILE} has no "${h.CADDY_MARKER}" block, `
          + `so ${C.base_path} has no route (uninstall + add removes it). Print the block with `
          + `node ${h.q(path.join(h.ADAPTER_DIR, 'lib.js'))} caddy, paste it (zylos/README.md "The Caddy block"), then: `
          + `${h.ZYLOS_DIR}/bin/caddy validate --config ${h.CADDYFILE} --adapter caddyfile && pm2 reload caddy`);
  }
  if (h.allowHostMissing()) {
    h.log(`WARNING: ${h.USERS_FILE} has logins but the console answers no public Host, so every page `
          + `through Caddy is refused (421). ${C.allow_host_key
            ? `Set ${C.allow_host_key}=<the host's domain> with configure, then: pm2 restart ${h.SERVICE}`
            : 'Add console.allow_host_key (or --allow-host in console.args) to zylos/manifest.json'}`);
  }
}

async function main({ upgrade = false } = {}) {
  const skipped = [];
  // --- hard dependencies ---------------------------------------------------
  const core = h.checkZylosVersion();
  if (core) h.log(`zylos-core ${core} (minimum ${h.MIN_ZYLOS})`);
  try {
    fs.mkdirSync(h.LOGS_DIR, { recursive: true });
    fs.chmodSync(h.DATA_DIR, 0o700); // core creates it 0755
  } catch (e) {
    h.fail(`cannot create the data dir ${h.DATA_DIR} (${e.code || e.message})`,
           `mkdir -p ${h.q(h.LOGS_DIR)} && chmod 700 ${h.q(h.DATA_DIR)}`);
  }
  consoleNotes();
  if (h.testKnob('SKIP_UV') === '1') {
    h.log(`${h.PREFIX}_ZYLOS_HOOK_SKIP_UV=1 (test only): skipping uv, python, endpoint and CLI checks`);
    h.reconcileAll();
    return;
  }
  if (!fs.existsSync(h.ENTRY)) {
    h.fail(`the harness CLI ${h.ENTRY} (manifest cli.entry) is missing`,
           'install a complete release of the harness (zylos/README.md "Install")');
  }
  const uv = await h.ensureUv();
  h.log(`uv: ${uv}`);

  let r = h.run(uv, ['python', 'find', h.PYTHON_VERSION], 60_000);
  if (r.status !== 0) {
    r = h.run(uv, ['python', 'install', h.PYTHON_VERSION]);
    if (r.timedOut) budgetFail(`uv python install ${h.PYTHON_VERSION}`);
    if (r.status !== 0) {
      h.fail(`uv python install ${h.PYTHON_VERSION} exited ${r.status ?? r.signal}: ${h.tail(r)}`,
             `${h.q(uv)} python install ${h.PYTHON_VERSION}`);
    }
  }
  // The CLI is a PEP 723 script: `uv run` builds its env in uv's cache;
  // resolving it once proves Python + dependencies work.
  const entry = path.relative(h.SKILL_DIR, h.ENTRY);
  r = h.run(uv, h.cliArgs('--help'));
  if (r.timedOut) budgetFail(`resolving the harness CLI (uv run ${entry} --help)`);
  if (r.status !== 0) {
    h.fail(`uv run ${entry} --help exited ${r.status ?? r.signal}: ${h.tail(r)}`, `${h.cliCommand()} --help`);
  }
  h.log(`python ${h.PYTHON_VERSION} + ${h.BIN_NAME} ready; data dir: ${h.DATA_DIR}`);
  h.log(`run the harness as: ${h.cliCommand()} <verb> …`);

  // --- soft checks (install only: post-upgrade stays light) ----------------
  if (upgrade) skipped.push('endpoint, doctor and scope checks (post-upgrade stays light)');
  else await softChecks(uv, skipped);
  h.reconcileAll();
  if (skipped.length) h.log(`skipped: ${skipped.join('; ')}. For the full checks run: node ${h.POST_INSTALL}`);
}

async function softChecks(uv, skipped) {
  if (h.ENDPOINTS.length) {
    if (h.remaining() > 20_000) {
      const results = await Promise.all(h.ENDPOINTS.map(h.reachable));
      h.ENDPOINTS.forEach((url, i) => h.log(`${url}: ${results[i]}`));
    } else skipped.push('endpoint checks (time budget)');
  }

  const doctor = h.M.cli.doctor;
  if (doctor.length) {
    const r = h.run(uv, h.cliArgs(...doctor), 120_000);
    const shown = `${h.BIN_NAME} ${doctor.join(' ')}`;
    if (r.status === null) {
      skipped.push(`${shown} (time budget) — run: ${h.cliCommand()} ${doctor.join(' ')}`);
    } else {
      const report = path.join(h.LOGS_DIR, 'doctor-install.log');
      fs.writeFileSync(report, `${r.stdout || ''}${r.stderr || ''}`);
      const last = (r.stdout || '').trim().split('\n').at(-1) || h.tail(r, 1);
      h.log(`${shown} exited ${r.status}: ${last} (full report: ${report})`);
    }
  }

  const S = h.M.scope;
  const r = h.run(uv, h.cliArgs(...S.list), 120_000);
  if (r.status === null) {
    skipped.push(`scope check (time budget) — run: ${h.cliCommand()} ${S.list.join(' ')}`);
    return;
  }
  let scope = [];
  try { scope = JSON.parse(r.stdout)[S.field] || []; } catch { /* no data yet */ }
  if (!Array.isArray(scope) || !scope.length) {
    h.log(`no ${S.field} declared yet. Next, with the owner: ${h.cliCommand()} ${S.declare}`);
  } else {
    h.log(`${S.field}: ${scope.join(', ')}`);
  }
}

if (require.main === module) main();
module.exports = { main };
