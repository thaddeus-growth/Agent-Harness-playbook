'use strict';
// The generic Zylos host adapter (the playbook's hosts/zylos/, README.md).
// A harness vendors this folder as <harness>/zylos/ and writes only three
// things: zylos/manifest.json, its SKILL.md frontmatter and the root
// ecosystem.config.cjs. Everything harness-specific (names, env prefix,
// pinned runtime, endpoints, scope check, console, scheduled tasks and their
// prompts) is read from the manifest, which is loaded and checked once, here.
//
// Node stdlib only: Zylos runs each hook as plain `node <hook>` with cwd = the
// skill dir and no npm install. Paths come from the ZYLOS_* env when Zylos
// passes it (configure, pre-uninstall) and fall back to its fixed layout
// otherwise (post-install and post-upgrade get no ZYLOS_* env; an agent may
// also run a hook by hand).
//
//   node zylos/lib.js check    the manifest, the SKILL.md frontmatter and the
//                              files they name agree (exit 1 with each problem)
//   node zylos/lib.js caddy    print the console's Caddy block for this host

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const https = require('node:https');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

const ADAPTER_DIR = __dirname;                        // <harness>/zylos
const SKILL_DIR = path.resolve(ADAPTER_DIR, '..');    // <harness>
const MANIFEST_FILE = path.join(ADAPTER_DIR, 'manifest.json');
// Each hook's total runtime, under the agent's 10-minute command cap.
const HOOK_BUDGET_MS = 9 * 60_000;
const HOOK_NAMES = ['configure', 'post-install', 'post-upgrade', 'pre-uninstall'];

// --- the manifest ------------------------------------------------------------

class ManifestError extends Error {
  constructor(key, why) {
    super(`zylos/manifest.json: ${key}: ${why}`);
    this.name = 'ManifestError';
    this.key = key;
  }
}

const RE = {
  name: /^[a-z0-9][a-z0-9-]*$/,
  bin: /^[a-z0-9]+(-[a-z0-9]+)+$/,            // <brand>-<cli>: core overwrites a clashing link
  semver: /^\d+\.\d+\.\d+$/,
  env: /^[A-Z][A-Z0-9_]*$/,
  prefix: /^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$/,
  python: /^3\.\d+$/,
  sha256: /^[0-9a-f]{64}$/,
  field: /^[A-Za-z_][A-Za-z0-9_]*$/,
  basePath: /^\/[a-z0-9][a-z0-9-]*$/,
  header: /^[A-Za-z][A-Za-z0-9-]*$/,
  file: /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/,
  marker: /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$/,     // e.g. ACME-EXIT; safe unquoted in sh and prose
  cron: /^\S+(\s+\S+){4}$/,
  host: /^[A-Za-z0-9.-]+(:\d{1,5})?$/,
};

// Prompt placeholders: resolved at install, paths shell-quoted.
const PLACEHOLDERS = ['cli', 'node', 'detach', 'data_dir', 'skill_dir', 'logs', 'exit', 'log:<name>'];
const TOKEN = /\{([a-z_]+)(?::([^{}\s]*))?\}/g;
// Flags console.js sets itself; the manifest's console.args may not.
const CONSOLE_OWNED = ['--dir', '--host', '--port', '--user', '--user-header', '--relay-cmd', '--title'];
const NOT_ALIASES = ['uv', 'node', 'sh', 'python', 'python3'];
// A config key whose name says it holds a secret must be `sensitive: true`.
const SECRET_KEY = /SECRET|TOKEN|PASSWORD|ACCESS_KEY|API_KEY|PRIVATE_KEY/;

function relPath(v) {
  return typeof v === 'string' && v !== '' && !path.isAbsolute(v) && /^[A-Za-z0-9_.\/-]+$/.test(v)
    && v.split('/').every((s) => s !== '' && s !== '.' && s !== '..');
}

function placeholderProblems(tpl) {
  const bad = [];
  for (const [all, name, arg] of tpl.matchAll(TOKEN)) {
    const known = name === 'log'
      ? arg !== undefined && RE.file.test(arg)
      : arg === undefined && PLACEHOLDERS.includes(name);
    if (!known) bad.push(all);
  }
  return bad;
}

function validate(m) {
  const fail = (key, why) => { throw new ManifestError(key, why); };
  const sub = (key, k) => (key ? `${key}.${k}` : k);
  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const shape = (v, key, required, optional = []) => {
    if (!isObj(v)) fail(key || '(top level)', 'must be a JSON object');
    for (const k of required) if (!(k in v)) fail(sub(key, k), 'missing');
    for (const k of Object.keys(v)) {
      if (k === '_comment' || required.includes(k) || optional.includes(k)) continue;
      fail(sub(key, k), `unknown key (known here: ${[...required, ...optional].join(', ')})`);
    }
  };
  const str = (v, key, ok, what) => {
    if (typeof v !== 'string' || !ok(v)) fail(key, `must be ${what}, got ${JSON.stringify(v)}`);
  };
  const re = (r) => (s) => r.test(s);
  const text = (s) => s.trim() !== '';
  const list = (v, key, each) => {
    if (!Array.isArray(v)) fail(key, 'must be a JSON list');
    v.forEach((x, i) => each(x, `${key}[${i}]`));
  };
  const int = (v, key, lo, hi) => {
    if (!Number.isInteger(v) || v < lo || v > hi) {
      fail(key, `must be a whole number from ${lo} to ${hi}, got ${JSON.stringify(v)}`);
    }
  };
  const envName = (v, key) => str(v, key, re(RE.env), 'an env var name (A-Z, 0-9, _)');
  const word = (v, key) => str(v, key, text, 'a non-empty string');

  shape(m, '', ['name', 'min_zylos', 'cli', 'env', 'runtime', 'endpoints', 'scope', 'reply',
                'console', 'detach', 'tasks'], ['defaults']);
  str(m.name, 'name', re(RE.name), 'the component name = SKILL.md name (a-z, 0-9, -)');
  str(m.min_zylos, 'min_zylos', re(RE.semver), 'a zylos-core version X.Y.Z');

  shape(m.cli, 'cli', ['bin', 'entry', 'doctor']);
  str(m.cli.bin, 'cli.bin', re(RE.bin), 'the SKILL.md bin name, <brand>-<cli> (a generic name clashes)');
  str(m.cli.entry, 'cli.entry', relPath, 'a path inside the skill dir, e.g. scripts/acme.py');
  list(m.cli.doctor, 'cli.doctor', word);

  shape(m.env, 'env', ['prefix', 'data_dir', 'files']);
  str(m.env.prefix, 'env.prefix', re(RE.prefix), 'an env prefix such as ACME');
  envName(m.env.data_dir, 'env.data_dir');
  if (!m.env.data_dir.startsWith(`${m.env.prefix}_`)) {
    fail('env.data_dir', `must start with the prefix ${m.env.prefix}_ (e.g. ${m.env.prefix}_DATA_DIR)`);
  }
  list(m.env.files, 'env.files', (v, key) => str(v, key, (s) => s.startsWith('~/') || path.isAbsolute(s),
                                                    'an absolute or ~/ path (a relative one would follow the cwd)'));

  shape(m.runtime, 'runtime', ['python', 'uv_version', 'uv_installer_url', 'uv_installer_sha256']);
  str(m.runtime.python, 'runtime.python', re(RE.python), 'a Python version such as 3.12');
  str(m.runtime.uv_version, 'runtime.uv_version', re(RE.semver), 'a uv version X.Y.Z');
  str(m.runtime.uv_installer_url, 'runtime.uv_installer_url', re(/^https:\/\/[^\s/]+\/\S*$/),
      "an https:// URL of uv's installer script");
  if (!m.runtime.uv_installer_url.includes(m.runtime.uv_version)) {
    fail('runtime.uv_installer_url', `must name uv_version ${m.runtime.uv_version} (bump the version, `
         + 'the installer URL and its sha256 together)');
  }
  str(m.runtime.uv_installer_sha256, 'runtime.uv_installer_sha256', re(RE.sha256),
      'the sha256 (64 hex) of the script at runtime.uv_installer_url');

  list(m.endpoints, 'endpoints', (v, key) => str(v, key, re(/^https:\/\/[^\s/]+/), 'an https:// URL'));

  shape(m.scope, 'scope', ['list', 'field', 'declare']);
  list(m.scope.list, 'scope.list', word);
  if (!m.scope.list.length) fail('scope.list', 'must not be empty (the CLI verb that lists the declared scope)');
  str(m.scope.field, 'scope.field', re(RE.field), 'the JSON field holding the declared scope');
  word(m.scope.declare, 'scope.declare');

  shape(m.reply, 'reply', ['channel', 'endpoint']);
  envName(m.reply.channel, 'reply.channel');
  envName(m.reply.endpoint, 'reply.endpoint');

  const C = m.console;
  shape(C, 'console', ['entry', 'port', 'port_key', 'title_key', 'base_path', 'user_header', 'users_file', 'args'],
        ['deciders_key', 'allow_host_key']);
  str(C.entry, 'console.entry', relPath, 'a path inside the skill dir, e.g. console/serve.py');
  int(C.port, 'console.port', 1024, 65535);
  envName(C.port_key, 'console.port_key');
  envName(C.title_key, 'console.title_key');
  str(C.base_path, 'console.base_path', re(RE.basePath), 'a one-segment URL path such as /acme');
  str(C.user_header, 'console.user_header', re(RE.header), 'an HTTP header name such as X-Remote-User');
  str(C.users_file, 'console.users_file', re(RE.file), 'a file name in the data dir, e.g. console.users');
  list(C.args, 'console.args', (v, key) => {
    word(v, key);
    const owned = CONSOLE_OWNED.find((f) => v === f || v.startsWith(`${f}=`));
    if (owned) fail(key, `${owned} is set by zylos/bin/console.js, not console.args`);
  });
  if ('deciders_key' in C) envName(C.deciders_key, 'console.deciders_key');
  if ('allow_host_key' in C) envName(C.allow_host_key, 'console.allow_host_key');
  if (C.deciders_key && C.args.some((a) => a === '--deciders' || a.startsWith('--deciders='))) {
    fail('console.args', '--deciders comes from console.deciders_key; do not set both');
  }

  shape(m.detach, 'detach', ['exit_marker', 'alias']);
  str(m.detach.exit_marker, 'detach.exit_marker', re(RE.marker), 'an upper-case marker such as ACME-EXIT');
  str(m.detach.alias, 'detach.alias', (s) => RE.name.test(s) && !NOT_ALIASES.includes(s),
      'the word a detached command starts with to mean the harness CLI (not uv, node or sh)');

  const seen = new Set();
  list(m.tasks, 'tasks', (t, key) => {
    shape(t, key, ['name', 'cron', 'miss_threshold', 'requires', 'prompt']);
    str(t.name, `${key}.name`, re(RE.name), 'a scheduler task name (a-z, 0-9, -)');
    if (seen.has(t.name)) fail(`${key}.name`, `${t.name} is used twice (task names identify tasks)`);
    seen.add(t.name);
    str(t.cron, `${key}.cron`, re(RE.cron), 'a five-field cron expression');
    int(t.miss_threshold, `${key}.miss_threshold`, 60, 7 * 86400);
    list(t.requires, `${key}.requires`, envName);
    word(t.prompt, `${key}.prompt`);
    const bad = placeholderProblems(t.prompt);
    if (bad.length) {
      fail(`${key}.prompt`, `unknown placeholder ${bad.join(', ')} (known: ${PLACEHOLDERS.map((p) => `{${p}}`).join(', ')})`);
    }
  });

  if ('defaults' in m) {
    if (!isObj(m.defaults)) fail('defaults', 'must be a JSON object {KEY: {value, when}}');
    for (const [k, d] of Object.entries(m.defaults)) {
      if (k === '_comment') continue;
      envName(k, `defaults.${k}`);
      shape(d, `defaults.${k}`, ['value', 'when']);
      word(d.value, `defaults.${k}.value`);
      list(d.when, `defaults.${k}.when`, envName);
      if (!d.when.length) fail(`defaults.${k}.when`, 'must name at least one key');
    }
  }
  return m;
}

function deepFreeze(v) {
  if (v && typeof v === 'object') { Object.values(v).forEach(deepFreeze); Object.freeze(v); }
  return v;
}

function loadManifest(file = MANIFEST_FILE) {
  let raw;
  try {
    raw = fs.readFileSync(file, 'utf8');
  } catch (e) {
    throw new ManifestError('(file)', `cannot read ${file} (${e.code || e.message}); `
      + 'copy manifest.example.json there and fill it in');
  }
  let m;
  try { m = JSON.parse(raw); } catch (e) { throw new ManifestError('(file)', `not JSON (${e.message})`); }
  return deepFreeze(validate(m));
}

let M;
try {
  M = loadManifest();
} catch (e) {
  // A hook or bin of this adapter: one clear line, exit 1. Anyone else (core's
  // pm2 ecosystem via the root ecosystem.config.cjs, a test): the error.
  const main = require.main && require.main.filename;
  if (!(e instanceof ManifestError) || !main || !(main === __filename || main.startsWith(ADAPTER_DIR + path.sep))) throw e;
  console.error(`[zylos adapter] FAILED: ${e.message}\n[zylos adapter] fix by hand: correct ${MANIFEST_FILE} `
                + `(zylos/README.md "The manifest"), then rerun: node ${process.argv[1]}`);
  process.exit(1);
}

// --- names and paths -----------------------------------------------------------

const NAME = M.name;
const BIN_NAME = M.cli.bin;
const MIN_ZYLOS = M.min_zylos;
const PREFIX = M.env.prefix;
const DATA_VAR = M.env.data_dir;
const SERVICE = `zylos-${NAME}`;              // = SKILL.md lifecycle.service.name = the pm2 app name
const PYTHON_VERSION = M.runtime.python;
const UV_VERSION = M.runtime.uv_version;

// Test-only overrides (<PREFIX>_ZYLOS_HOOK_*), read only when
// <PREFIX>_ZYLOS_HOOK_TEST=1; never set on a host.
const TEST_PREFIX = `${PREFIX}_ZYLOS_HOOK`;
const TEST_MODE = process.env[`${TEST_PREFIX}_TEST`] === '1';
const testKnob = (name) => (TEST_MODE ? process.env[`${TEST_PREFIX}_${name}`] : undefined);

const UV_INSTALLER = testKnob('UV_INSTALLER') || M.runtime.uv_installer_url;
const UV_SHA256 = testKnob('UV_INSTALLER_SHA256') || M.runtime.uv_installer_sha256;
const ENDPOINTS = testKnob('ENDPOINTS') !== undefined
  ? testKnob('ENDPOINTS').split(',').filter(Boolean) : [...M.endpoints];
const BUDGET_MS = Number(testKnob('BUDGET_MS')) || HOOK_BUDGET_MS;
const DEADLINE = Date.now() + BUDGET_MS;

// Data dir, as zylos-core derives it: ZYLOS_DATA_DIR (configure,
// pre-uninstall; ignored when ZYLOS_COMPONENT names another component) →
// $ZYLOS_DIR/components/<name> → $HOME/zylos/components/<name>.
const HOME = os.homedir();
const ZYLOS_DIR = process.env.ZYLOS_DIR || path.join(HOME, 'zylos');
const OWN_DATA_ENV = process.env.ZYLOS_DATA_DIR
  && (!process.env.ZYLOS_COMPONENT || process.env.ZYLOS_COMPONENT === NAME);
const DATA_DIR = OWN_DATA_ENV ? process.env.ZYLOS_DATA_DIR : path.join(ZYLOS_DIR, 'components', NAME);
const LOGS_DIR = path.join(DATA_DIR, 'logs');
const CONSOLE_DIR = path.join(DATA_DIR, 'console');
const ENTRY = path.join(SKILL_DIR, M.cli.entry);
const CONSOLE_ENTRY = path.join(SKILL_DIR, M.console.entry);
const CLI_JS = path.join(ADAPTER_DIR, 'bin', 'cli.js');
const DETACH_JS = path.join(ADAPTER_DIR, 'bin', 'detach.js');
const CONSOLE_JS = path.join(ADAPTER_DIR, 'bin', 'console.js');
const POST_INSTALL = path.join(ADAPTER_DIR, 'hooks', 'post-install.js');
const SCHEDULER_CLI = path.join(ZYLOS_DIR, '.claude', 'skills', 'scheduler', 'scripts', 'cli.js');
const BIN_LINK = path.join(ZYLOS_DIR, 'bin', BIN_NAME);
// The task names this adapter manages, so a task dropped from the manifest
// is removed by the next post-install / post-upgrade.
const TASK_STATE = path.join(DATA_DIR, '.zylos-tasks.json');
const USERS_FILE = path.join(DATA_DIR, M.console.users_file);
const CADDYFILE = path.join(ZYLOS_DIR, 'http', 'Caddyfile');
const CADDY_MARKER = `# BEGIN zylos-component:${NAME}`;
const CADDY_SNIPPET = path.join(ADAPTER_DIR, 'Caddyfile.snippet');

// --- output, budget, quoting ------------------------------------------------------

const log = (msg) => console.log(`[${NAME}] ${msg}`);

function fail(what, fix) {
  console.error(`[${NAME}] FAILED: ${what}\n[${NAME}] fix by hand: ${fix}`);
  process.exit(1);
}

const remaining = () => DEADLINE - Date.now();
const budgetMinutes = () => Math.round(BUDGET_MS / 60_000);

// POSIX single-quoting, so a path with spaces or `$` survives a shell.
const q = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`;

function tail(r, n = 5) {
  const out = `${r.stdout || ''}${r.stderr || ''}${r.error ? r.error.message : ''}`.trim();
  return out.split('\n').slice(-n).join(' | ');
}

// --- the env chain ---------------------------------------------------------------------

// KEY=VALUE lines, parsed exactly like the kit's kit/env.py parse():
// blank and # lines skipped, a leading `export ` dropped, one pair of
// matching outer quotes removed, a missing file is {}.
const LINES = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
function readEnvFile(file) {
  let raw;
  try { raw = fs.readFileSync(file, 'utf8'); } catch { return {}; }
  const out = {};
  for (const line of raw.split(LINES)) {
    if (!line.trim() || line.trimStart().startsWith('#') || !line.includes('=')) continue;
    const i = line.indexOf('=');
    let k = line.slice(0, i).trim();
    let v = line.slice(i + 1).trim();
    if (k.startsWith('export ')) k = k.slice('export '.length).trim();
    if (v.length >= 2 && `'"`.includes(v[0]) && v.at(-1) === v[0]) v = v.slice(1, -1);
    if (k) out[k] = v;
  }
  return out;
}

const expandHome = (p) => (p === '~' || p.startsWith('~/') ? path.join(HOME, p.slice(1)) : p);

// The files after the process env, in order (later wins), as the kit's
// EnvChain lists them: <PREFIX>_AUTH_ENV_PATHS replaces the chain (`none` or
// empty: process env only); else manifest env.files, then <data dir>/.env.
function envFiles() {
  const override = process.env[`${PREFIX}_AUTH_ENV_PATHS`];
  let files;
  if (override !== undefined) {
    if (['', 'none'].includes(override.trim().toLowerCase())) return [];
    files = override.split(',').map((s) => s.trim()).filter(Boolean);
  } else {
    files = [...M.env.files, path.join(process.env[DATA_VAR] || DATA_DIR, '.env')];
  }
  return files.map(expandHome).filter((p) => path.isAbsolute(p));
}

// A value as the harness would resolve it: process env, then the files.
// Values are never printed.
function envValue(key) {
  if (process.env[key]) return process.env[key];
  const merged = Object.assign({}, ...envFiles().map(readEnvFile));
  return merged[key] || '';
}

// The env every harness run gets: the data dir (unless the caller set one),
// and bytecode caches under it, so nothing is written into the skill dir
// (which `zylos upgrade` backs up and merges).
function harnessEnv(base = process.env) {
  const dataDir = base[DATA_VAR] || DATA_DIR;
  return { ...base, [DATA_VAR]: dataDir,
           PYTHONPYCACHEPREFIX: base.PYTHONPYCACHEPREFIX || path.join(dataDir, '.pycache') };
}

// --- runtime: uv, Python, zylos-core --------------------------------------------------

function isExec(p) {
  try { fs.accessSync(p, fs.constants.X_OK); return fs.statSync(p).isFile(); } catch { return false; }
}

// uv on PATH, else where Astral's installer puts it.
function findUv() {
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (dir && isExec(path.join(dir, 'uv'))) return path.join(dir, 'uv');
  }
  const dirs = [process.env.UV_INSTALL_DIR, process.env.XDG_BIN_HOME,
                process.env.XDG_DATA_HOME && path.join(process.env.XDG_DATA_HOME, '..', 'bin'),
                path.join(HOME, '.local', 'bin'), path.join(HOME, '.cargo', 'bin')];
  return dirs.filter(Boolean).map((d) => path.join(d, 'uv')).find(isExec) || null;
}

async function fetchInstaller() {
  if (!/^https?:/.test(UV_INSTALLER)) return fs.readFileSync(UV_INSTALLER); // test mode only
  const res = await fetch(UV_INSTALLER, { signal: AbortSignal.timeout(Math.max(1, Math.min(60_000, remaining()))) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

// uv's installer at the pinned URL (manifest runtime), checked against its
// pinned sha256 before it runs; UV_NO_MODIFY_PATH=1 keeps it out of shell rc files
// (findUv looks in ~/.local/bin anyway). No sudo.
async function ensureUv() {
  const found = findUv();
  if (found) return found;
  const manual = `download ${UV_INSTALLER}, check sha256 ${UV_SHA256}, run it with UV_NO_MODIFY_PATH=1 sh `
    + `(or install uv ${UV_VERSION} by hand as uv's installation docs say), then rerun: node ${process.argv[1]}`;
  log(`uv not found; installing uv ${UV_VERSION} with Astral's installer (sha256-checked, no sudo)`);
  let script;
  try {
    script = await fetchInstaller();
  } catch (e) {
    fail(`could not download the uv installer from ${UV_INSTALLER} (${e.cause?.code || e.cause?.errors?.[0]?.code
         || e.cause?.message || e.code || e.message})`, manual);
  }
  const sha = crypto.createHash('sha256').update(script).digest('hex');
  if (sha !== UV_SHA256) fail(`the uv installer's sha256 is ${sha}, expected ${UV_SHA256}; it was not run`, manual);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-install-'));
  const file = path.join(tmp, 'install.sh');
  fs.writeFileSync(file, script, { mode: 0o700 });
  const r = spawnSync('sh', [file], {
    encoding: 'utf8', timeout: Math.max(1, Math.min(300_000, remaining())),
    env: { ...process.env, UV_NO_MODIFY_PATH: '1' },
  });
  fs.rmSync(tmp, { recursive: true, force: true });
  if (r.status !== 0) fail(`the uv installer exited ${r.status ?? r.signal}: ${tail(r)}`, manual);
  const uv = findUv();
  if (!uv) fail('the uv installer finished but no uv binary was found', manual);
  return uv;
}

// A too-old core is a hard failure; a missing or unparsable `zylos
// --version` is only a warning (the version can't be told; the hooks may
// still work).
function checkZylosVersion() {
  const r = spawnSync('zylos', ['--version'], { encoding: 'utf8', timeout: 30_000 });
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(`${r.stdout || ''}`);
  if (r.error || !m) {
    log(`warning: could not read the zylos-core version (${r.error ? r.error.code || r.error.message
        : `\`zylos --version\` printed ${JSON.stringify((r.stdout || '').trim().slice(0, 40))}`}); `
        + `this adapter needs zylos-core >= ${MIN_ZYLOS}`);
    return null;
  }
  const have = m.slice(1).map(Number);
  const need = MIN_ZYLOS.split('.').map(Number);
  const cmp = have.map((x, i) => x - need[i]).find((d) => d !== 0) || 0;
  if (cmp < 0) {
    fail(`zylos-core ${m[0]} is older than ${MIN_ZYLOS}, the minimum this adapter supports`,
         `zylos upgrade --self, then rerun: node ${process.argv[1]}`);
  }
  return m[0];
}

// A command with cwd = the data dir (no project to discover), capped by the
// hook's time budget. `timedOut` is set when the budget, not the step, ran out.
function run(cmd, args, timeout = 600_000) {
  const left = remaining();
  if (left <= 0) return { status: null, timedOut: true, stdout: '', stderr: '' };
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const r = spawnSync(cmd, args, {
    cwd: DATA_DIR, encoding: 'utf8', timeout: Math.min(timeout, left),
    env: harnessEnv({ ...process.env, [DATA_VAR]: DATA_DIR }),
  });
  return { ...r, timedOut: r.error?.code === 'ETIMEDOUT' && remaining() <= 0 };
}

// `uv run` arguments for the harness CLI (a PEP 723 script: no project venv).
const cliArgs = (...args) => ['run', '--no-project', ENTRY, ...args];

// How a shell (the agent, a scheduled prompt) calls the harness CLI: the
// absolute path of the link zylos-core made in $ZYLOS_DIR/bin (not on the
// agent's PATH: core only adds it to shell rc files), or, without the link,
// node on this adapter's bin/cli.js. Quoted for a shell.
function cliCommand() {
  return fs.existsSync(BIN_LINK) ? q(BIN_LINK) : `node ${q(CLI_JS)}`;
}

// Any HTTP response counts as reachable; only a network error does not.
function reachable(url) {
  return new Promise((resolve) => {
    const req = https.request(url, { method: 'HEAD', timeout: 10_000 }, (res) => {
      res.resume();
      resolve(`reachable (HTTP ${res.statusCode})`);
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve(`UNREACHABLE (${e.code || e.message})`));
    req.end();
  });
}

// --- prompts ------------------------------------------------------------------------------

function placeholder(name, arg) {
  switch (name) {
    case 'cli': return arg === undefined ? cliCommand() : undefined;
    case 'node': return arg === undefined ? 'node' : undefined;
    case 'detach': return arg === undefined ? `node ${q(DETACH_JS)}` : undefined;
    case 'data_dir': return arg === undefined ? q(DATA_DIR) : undefined;
    case 'skill_dir': return arg === undefined ? q(SKILL_DIR) : undefined;
    case 'logs': return arg === undefined ? q(LOGS_DIR) : undefined;
    case 'exit': return arg === undefined ? M.detach.exit_marker : undefined;
    case 'log': return arg && RE.file.test(arg) ? q(path.join(LOGS_DIR, `${arg}.log`)) : undefined;
    default: return undefined;
  }
}

// What is left of the template's own {…} after resolution (the values put
// in are not looked at, so a path holding braces is not a false alarm).
function unresolved(tpl) {
  return [...tpl.replace(TOKEN, (all, name, arg) => (placeholder(name, arg) === undefined ? all : ''))
    .matchAll(TOKEN)].map((m) => m[0]);
}

function renderPrompt(tpl, where = 'prompt') {
  const left = unresolved(tpl);
  if (left.length) throw new ManifestError(where, `unresolved placeholder ${left.join(', ')}`);
  return tpl.replace(TOKEN, (all, name, arg) => placeholder(name, arg));
}

// --- scheduler CLI -------------------------------------------------------------------------
// Its exit codes don't say whether add/update/remove worked (a remove of a
// missing id exits 0), so callers read its output instead.

function scheduler(args) {
  const r = spawnSync(process.execPath, [SCHEDULER_CLI, ...args], { encoding: 'utf8', timeout: 30_000 });
  return { ...r, out: `${r.stdout || ''}${r.stderr || ''}`.trim() };
}

// Every task named `name` (names are not unique), or an error string.
function tasksNamed(name) {
  const r = scheduler(['list', '--json']);
  try {
    const all = JSON.parse(r.stdout);
    if (!Array.isArray(all)) throw new Error('not a list');
    return { tasks: all.filter((t) => t && t.name === name) };
  } catch {
    return { error: `\`cli.js list --json\` failed: ${r.out || r.error?.message || `exit ${r.status}`}` };
  }
}

function addTask(args) {
  const m = /Task created:\s*(\S+)/.exec(scheduler(['add', ...args]).out);
  return m ? m[1] : null;
}

function updateTask(id, args) {
  const r = scheduler(['update', id, ...args]);
  return { ok: r.out.includes(`Task updated: ${id}`), out: r.out };
}

function removeTask(id) {
  const r = scheduler(['remove', id]);
  return { ok: r.out.includes(`Removed task: ${id}`), out: r.out };
}

function readTaskState() {
  try {
    const names = JSON.parse(fs.readFileSync(TASK_STATE, 'utf8')).tasks;
    return Array.isArray(names) ? names.filter((n) => typeof n === 'string' && RE.name.test(n)) : [];
  } catch { return []; }
}

function writeTaskState(names) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const tmp = `${TASK_STATE}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify({ tasks: [...new Set(names)].sort() })}\n`, { mode: 0o600 });
  fs.renameSync(tmp, TASK_STATE);
}

const requiredKeys = (task) => [...task.requires];

// Exactly one task named task.name, matching what this install would add:
// add it if missing, update the one kept when it differs, remove extras.
// Registered only once the keys it needs resolve (without them it could
// only fail).
function reconcileTask(task) {
  const missing = requiredKeys(task).filter((k) => !envValue(k));
  if (missing.length) {
    log(`note: ${task.name} not registered: set ${missing.join(', ')} with configure `
        + `(zylos/README.md), then rerun: node ${POST_INSTALL}`);
    return;
  }
  const listed = tasksNamed(task.name);
  if (listed.error) {
    log(`note: ${task.name} not registered: ${listed.error} (rerun this hook later)`);
    return;
  }
  const reply = { channel: envValue(M.reply.channel), endpoint: envValue(M.reply.endpoint) };
  const want = { prompt: renderPrompt(task.prompt, `tasks[${M.tasks.indexOf(task)}].prompt`),
                 cron: task.cron, miss: task.miss_threshold };
  const replyArgs = reply.channel
    ? ['--reply-channel', reply.channel, ...(reply.endpoint ? ['--reply-endpoint', reply.endpoint] : [])]
    : [];
  // Keep the task already matching this release, else the oldest.
  const [keep, ...extra] = [...listed.tasks].sort((a, b) =>
    (b.prompt === want.prompt) - (a.prompt === want.prompt) || (a.created_at || 0) - (b.created_at || 0));
  let id = keep?.id;
  if (!keep) {
    id = addTask([want.prompt, '--cron', want.cron, '--miss-threshold', String(want.miss),
                  '--name', task.name, ...replyArgs]);
    log(id ? `scheduler task ${task.name} registered (${id}, ${want.cron})`
           : `note: scheduler add did not report a created task; ${task.name} not registered`);
    if (!id) return;
  } else {
    const args = [];
    if (keep.prompt !== want.prompt) args.push('--prompt', want.prompt);
    if (keep.cron_expression !== want.cron) args.push('--cron', want.cron);
    if (Number(keep.miss_threshold) !== want.miss) args.push('--miss-threshold', String(want.miss));
    // Reply fields only when configured: a reply set by hand is left alone.
    if (reply.channel && (keep.reply_channel !== reply.channel
                          || (keep.reply_endpoint || '') !== reply.endpoint)) args.push(...replyArgs);
    if (args.length) {
      const u = updateTask(id, args);
      log(u.ok ? `scheduler task ${task.name} updated (${id})` : `note: could not update ${id}: ${u.out || 'no output'}`);
    } else {
      log(`scheduler task ${task.name} already registered (${id}); up to date`);
    }
    for (const t of extra) {
      const rm = removeTask(t.id);
      log(rm.ok ? `removed duplicate ${task.name} task ${t.id}`
                : `note: could not remove duplicate ${t.id}: ${rm.out || 'no output'}`);
    }
  }
  if (!reply.channel && !keep?.reply_channel) {
    log(`note: ${task.name} has no reply channel, so its report reaches no one. `
        + `Set ${M.reply.channel} (e.g. lark) and ${M.reply.endpoint} (e.g. the owner's chat id) `
        + `with configure and rerun this hook, or: node ${q(SCHEDULER_CLI)} update ${id} `
        + '--reply-channel <channel> --reply-endpoint <endpoint>');
  }
}

// Every manifest task, then every task a previous release managed that the
// manifest no longer names is removed. A name whose removal failed stays
// in the state file, so the next run tries again.
function reconcileAll() {
  if (!fs.existsSync(SCHEDULER_CLI)) {
    log(`note: no scheduler skill at ${SCHEDULER_CLI}; scheduler tasks not registered`);
    return;
  }
  const want = M.tasks.map((t) => t.name);
  for (const task of M.tasks) reconcileTask(task);
  const retry = [];
  for (const name of readTaskState().filter((n) => !want.includes(n))) {
    const listed = tasksNamed(name);
    if (listed.error) {
      log(`note: ${name} is no longer in zylos/manifest.json but ${listed.error}; rerun this hook later`);
      retry.push(name);
      continue;
    }
    for (const t of listed.tasks) {
      const rm = removeTask(t.id);
      if (rm.ok) log(`removed scheduler task ${name} (${t.id}): no longer in zylos/manifest.json`);
      else { log(`note: could not remove ${name} task ${t.id}: ${rm.out || 'no output'}`); retry.push(name); }
    }
  }
  writeTaskState([...want, ...retry]);
}

// pre-uninstall: every task named in the manifest or managed before.
// Returns the problems (empty when all went).
function removeAllTasks() {
  if (!fs.existsSync(SCHEDULER_CLI)) {
    log(`note: no scheduler skill at ${SCHEDULER_CLI}; no task to remove`);
    return [];
  }
  const problems = [];
  for (const name of [...new Set([...M.tasks.map((t) => t.name), ...readTaskState()])]) {
    const listed = tasksNamed(name);
    if (listed.error) { problems.push(`${listed.error}; remove ${name} by hand`); continue; }
    for (const t of listed.tasks) {
      const r = removeTask(t.id);
      if (r.ok) log(`removed scheduler task ${name} (${t.id})`);
      else problems.push(`could not remove ${t.id}: ${r.out || 'no output'}`);
    }
  }
  return problems;
}

// --- detached jobs (bin/detach.js) ---------------------------------------------------------
// A job file holds {pid, start, cmd}: `start` is the process's start time as
// the OS reports it, so a pid reused after a reboot isn't taken for ours.

function procStart(pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8'); // Linux
    return `proc:${stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19]}`;
  } catch { /* not Linux, or gone */ }
  const r = spawnSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8' }); // macOS, BSD
  return r.status === 0 && r.stdout.trim() ? `ps:${r.stdout.trim()}` : null;
}

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; }
}

function readJob(file) {
  try {
    const job = JSON.parse(fs.readFileSync(file, 'utf8'));
    return Number.isInteger(job.pid) && job.pid > 0 ? job : null;
  } catch { return null; }
}

// Alive and still the process we started (same start time when the OS can
// tell; pid liveness alone when it can't).
function jobAlive(job) {
  if (!job || !pidAlive(job.pid)) return false;
  const now = procStart(job.pid);
  return !job.start || !now || now === job.start;
}

// pre-uninstall: stop every detached job still running (its whole process
// group: detach.js starts each as a group leader). Returns the problems.
function stopJobs() {
  const problems = [];
  let files = [];
  try { files = fs.readdirSync(LOGS_DIR).filter((f) => f.endsWith('.pid')); } catch { /* no logs */ }
  for (const f of files) {
    const job = readJob(path.join(LOGS_DIR, f));
    if (!jobAlive(job)) continue;
    try {
      try { process.kill(-job.pid, 'SIGTERM'); } catch { process.kill(job.pid, 'SIGTERM'); }
      log(`stopped detached job ${path.basename(f, '.pid')} (pid ${job.pid}: ${[].concat(job.cmd || []).join(' ')})`);
    } catch (e) {
      problems.push(`could not stop detached job ${f} (pid ${job.pid}): ${e.code || e.message}`);
    }
  }
  return problems;
}

// --- the console service (bin/console.js, the root ecosystem.config.cjs) -------------------

// The public Host names the console answers to behind the host's Caddy
// (which passes the Host through): console.allow_host_key's value, comma- or
// space-separated. The static --allow-host entries in console.args add to it.
function allowHosts() {
  const key = M.console.allow_host_key;
  const raw = key ? envValue(key) : '';
  return raw.split(/[\s,]+/).filter(Boolean);
}

// How the console's relay calls the harness: this node on bin/cli.js, which
// sets the data dir itself (no link or PATH needed under pm2). serve.py
// splits it like a shell does.
const relayCommand = () => `${q(process.execPath)} ${q(CLI_JS)}`;

// serve.py's argv, or {error} for a config that can't run.
function consoleCommand() {
  const C = M.console;
  const raw = envValue(C.port_key) || String(C.port);
  const port = Number(raw);
  if (!/^\d+$/.test(raw) || port < 1024 || port > 65535) {
    return { error: `${C.port_key}=${raw} is not a port (1024-65535)` };
  }
  const hosts = allowHosts();
  const badHost = hosts.find((h) => !RE.host.test(h));
  if (badHost) return { error: `${C.allow_host_key}: ${JSON.stringify(badHost)} is not a HOST or HOST:PORT` };
  const argv = ['--dir', CONSOLE_DIR, '--host', '127.0.0.1', '--port', String(port),
                '--user-header', C.user_header, '--title', envValue(C.title_key) || NAME];
  for (const h of hosts) argv.push('--allow-host', h);
  const deciders = C.deciders_key ? envValue(C.deciders_key) : '';
  if (deciders) argv.push('--deciders', deciders);
  // serve.py takes --relay-cmd and --relay-verbs together or neither.
  if (C.args.some((a) => a === '--relay-verbs' || a.startsWith('--relay-verbs='))) {
    argv.push('--relay-cmd', relayCommand());
  }
  argv.push(...C.args);
  return { port, entry: CONSOLE_ENTRY, argv };
}

// The pm2 app zylos-core starts from the root ecosystem.config.cjs.
function ecosystem() {
  const out = path.join(LOGS_DIR, 'console.log');
  return {
    apps: [{
      name: SERVICE,                 // = SKILL.md lifecycle.service.name
      script: CONSOLE_JS,
      cwd: DATA_DIR,
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      kill_timeout: 5000,
      out_file: out,
      error_file: out,
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    }],
  };
}

// Hooks never edit the Caddyfile (a bad one takes down every host site);
// they warn when people have logins but the block is gone, as after
// uninstall + add. Missing or unreadable files: no warning.
function hasLogins() {
  try { return fs.readFileSync(USERS_FILE, 'utf8').trim() !== ''; } catch { return false; }
}

function caddyBlockMissing() {
  if (!hasLogins()) return false;
  try {
    return !fs.readFileSync(CADDYFILE, 'utf8').split('\n').some((l) => l.trim() === CADDY_MARKER);
  } catch { return false; }
}

// People have logins but the console would refuse the public Host (421).
function allowHostMissing() {
  return hasLogins() && !allowHosts().length && !M.console.args.some((a) => a.startsWith('--allow-host'));
}

// Caddyfile.snippet with this host's values filled in.
function caddyBlock() {
  const raw = envValue(M.console.port_key);
  const port = /^\d+$/.test(raw) ? raw : String(M.console.port);
  const values = { '<NAME>': NAME, '<BASE_PATH>': M.console.base_path, '<PORT>': port,
                   '<USERS_FILE>': USERS_FILE, '<USER_HEADER>': M.console.user_header };
  return fs.readFileSync(CADDY_SNIPPET, 'utf8')
    .split('\n').filter((l) => !l.startsWith('##')).join('\n')
    .replace(/<[A-Z_]+>/g, (k) => values[k] ?? k).trimStart();
}

// --- `node zylos/lib.js check`: manifest ↔ SKILL.md frontmatter ↔ files ------------------
// Hooks can't parse YAML (stdlib only), so the frontmatter is checked line
// by line against the manifest rather than read: the subset core needs.

function frontmatterLines(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?(\n|$)/.exec(text);
  return m ? m[1].split(/\r?\n/) : null;
}

// A scalar's value: one pair of outer quotes removed, else a trailing
// ` # comment` dropped.
const unquote = (s) => {
  const v = s.trim();
  if (v.length >= 2 && `'"`.includes(v[0]) && v.at(-1) === v[0]) return v.slice(1, -1);
  return v.replace(/\s+#.*$/, '');
};

// The block under a top-level key: {key: value} of its lines, one level deep,
// plus nested blocks as "parent.child".
function yamlBlock(lines, top) {
  const out = {};
  const i = lines.findIndex((l) => new RegExp(`^${top}:\\s*$`).test(l));
  if (i < 0) return null;
  const stack = [];
  for (const l of lines.slice(i + 1)) {
    if (!l.trim() || l.trim().startsWith('#')) continue;
    if (!/^\s/.test(l)) break;
    const m = /^(\s+)([A-Za-z0-9_.-]+):\s*(.*)$/.exec(l);
    if (!m) continue;
    const depth = m[1].length;
    while (stack.length && stack.at(-1).depth >= depth) stack.pop();
    const key = [...stack.map((s) => s.key), m[2]].join('.');
    if (m[3].trim() === '') stack.push({ depth, key: m[2] });
    else out[key] = unquote(m[3]);
  }
  return out;
}

// A top-level key's value; a block scalar (`>`, `|`, `>-`, …) or a plain
// scalar continued on indented lines is joined with spaces.
function topValue(lines, key) {
  const i = lines.findIndex((x) => x.startsWith(`${key}:`));
  if (i < 0) return undefined;
  const head = lines[i].slice(key.length + 1).trim();
  const rest = [];
  for (const l of lines.slice(i + 1)) {
    if (l.trim() && !/^\s/.test(l)) break;
    if (l.trim()) rest.push(l.trim());
  }
  if (/^[>|][+-]?$/.test(head)) return rest.join(' ');
  return unquote([head, ...rest].join(' '));
}

// config.required / config.optional items: [{name, sensitive, default}].
// Keys in any order: an item starts at `- key: value`.
function configItems(lines) {
  const i = lines.findIndex((l) => /^config:\s*$/.test(l));
  if (i < 0) return [];
  const items = [];
  for (const l of lines.slice(i + 1)) {
    if (l.trim() && !/^\s/.test(l)) break;
    const start = /^\s+-\s+([A-Za-z_]+):\s*(.*)$/.exec(l);
    if (start) { items.push({ [start[1]]: unquote(start[2]) }); continue; }
    const kv = /^\s+([A-Za-z_]+):\s*(.+)$/.exec(l);
    if (kv && items.length) items.at(-1)[kv[1]] = unquote(kv[2]);
  }
  return items.filter((x) => x.name);
}

function skillProblems(text) {
  const lines = frontmatterLines(text);
  if (!lines) return ['SKILL.md has no --- frontmatter block'];
  const p = [];
  const want = (cond, msg) => { if (!cond) p.push(msg); };
  want(topValue(lines, 'name') === NAME, `SKILL.md name must be ${NAME} (manifest name)`);
  want(RE.semver.test(topValue(lines, 'version') || ''), 'SKILL.md version must be plain X.Y.Z (the release tag vX.Y.Z)');
  want(topValue(lines, 'type') === 'capability', 'SKILL.md type must be capability');
  const steps = topValue(lines, 'next-steps') || '';
  for (const w of ['10-minute', M.scope.declare, M.reply.channel]) {
    want(steps.includes(w), `SKILL.md next-steps must mention ${JSON.stringify(w)}`);
  }
  const bin = yamlBlock(lines, 'bin') || {};
  want(JSON.stringify(bin) === JSON.stringify({ [BIN_NAME]: 'zylos/bin/cli.js' }),
       `SKILL.md bin must be exactly {${BIN_NAME}: zylos/bin/cli.js} (manifest cli.bin)`);
  const life = yamlBlock(lines, 'lifecycle') || {};
  const expected = {
    npm: 'false',
    ...Object.fromEntries(HOOK_NAMES.map((h) => [`hooks.${h}`, `zylos/hooks/${h}.js`])),
    'service.name': SERVICE, 'service.entry': 'zylos/bin/console.js', 'service.type': 'pm2',
  };
  for (const [k, v] of Object.entries(expected)) want(life[k] === v, `SKILL.md lifecycle.${k} must be ${v}`);
  for (const k of Object.keys(life)) want(k in expected, `SKILL.md lifecycle.${k}: core reads only npm, hooks, service`);
  want(!lines.some((l) => /^http_routes:/.test(l)), 'SKILL.md must not declare http_routes (core\'s routes carry no login)');
  want(!lines.some((l) => /^upgrade:/.test(l)), 'SKILL.md must not declare an upgrade block');
  const items = configItems(lines);
  const names = new Set(items.map((i) => i.name));
  const C = M.console;
  const adapterKeys = [M.reply.channel, M.reply.endpoint, C.port_key, C.title_key,
                       C.deciders_key, C.allow_host_key, ...M.tasks.flatMap((t) => t.requires),
                       ...Object.keys(M.defaults || {}).filter((k) => k !== '_comment')].filter(Boolean);
  for (const k of new Set(adapterKeys)) want(names.has(k), `SKILL.md config must declare ${k} (read by zylos/)`);
  for (const i of items) {
    want(!SECRET_KEY.test(i.name) || i.sensitive === 'true',
         `SKILL.md config ${i.name} looks secret: mark it sensitive: true`);
    const d = M.defaults && M.defaults[i.name];
    if (d) want(i.default === d.value, `SKILL.md config ${i.name} default must be ${d.value} (manifest defaults)`);
  }
  return p;
}

function checkProblems() {
  const p = [];
  const exists = (rel, why) => { if (!fs.existsSync(path.join(SKILL_DIR, rel))) p.push(`${rel} is missing (${why})`); };
  exists(M.cli.entry, 'manifest cli.entry');
  exists(M.console.entry, 'manifest console.entry: vendor the playbook console');
  for (const h of HOOK_NAMES) exists(`zylos/hooks/${h}.js`, 'a vendored hook');
  for (const b of ['cli', 'detach', 'console']) exists(`zylos/bin/${b}.js`, 'a vendored bin');
  exists('zylos/Caddyfile.snippet', 'vendored with lib.js');
  const eco = path.join(SKILL_DIR, 'ecosystem.config.cjs');
  let ecoText = '';
  try { ecoText = fs.readFileSync(eco, 'utf8'); } catch { /* reported below */ }
  if (!ecoText.includes("require('./zylos/lib.js')")) {
    p.push('ecosystem.config.cjs at the skill root must load ./zylos/lib.js (copy zylos/ecosystem.config.cjs.template)');
  }
  let skill = null;
  try { skill = fs.readFileSync(path.join(SKILL_DIR, 'SKILL.md'), 'utf8'); } catch { p.push('SKILL.md is missing'); }
  if (skill !== null) p.push(...skillProblems(skill));
  M.tasks.forEach((t, i) => {
    const left = unresolved(t.prompt);
    if (left.length) p.push(`tasks[${i}].prompt: unresolved ${left.join(', ')}`);
  });
  return p;
}

module.exports = {
  ManifestError, loadManifest, validate, M, MANIFEST_FILE, PLACEHOLDERS, SECRET_KEY,
  NAME, BIN_NAME, MIN_ZYLOS, PREFIX, DATA_VAR, SERVICE, PYTHON_VERSION, UV_VERSION, UV_INSTALLER, UV_SHA256,
  ENDPOINTS, TEST_MODE, testKnob,
  ADAPTER_DIR, SKILL_DIR, ZYLOS_DIR, DATA_DIR, LOGS_DIR, CONSOLE_DIR, ENTRY, CONSOLE_ENTRY, CLI_JS, DETACH_JS,
  CONSOLE_JS, POST_INSTALL, SCHEDULER_CLI, BIN_LINK, TASK_STATE, USERS_FILE, CADDYFILE, CADDY_MARKER,
  log, fail, q, tail, remaining, budgetMinutes,
  readEnvFile, envFiles, envValue, harnessEnv,
  findUv, ensureUv, checkZylosVersion, run, cliArgs, cliCommand, reachable,
  renderPrompt, unresolved, tasksNamed, addTask, updateTask, removeTask, readTaskState, requiredKeys,
  reconcileTask, reconcileAll, removeAllTasks,
  procStart, pidAlive, readJob, jobAlive, stopJobs,
  allowHosts, relayCommand, consoleCommand, ecosystem, hasLogins, caddyBlockMissing, allowHostMissing, caddyBlock,
  skillProblems, checkProblems,
};

if (require.main === module) {
  const verb = process.argv[2];
  if (verb === 'check') {
    const problems = checkProblems();
    for (const x of problems) console.error(`[${NAME}] ${x}`);
    console.log(problems.length ? `[${NAME}] check: ${problems.length} problem(s)` : `[${NAME}] check: ok`);
    process.exit(problems.length ? 1 : 0);
  } else if (verb === 'caddy') {
    process.stdout.write(caddyBlock());
  } else {
    console.error('usage: node zylos/lib.js check | caddy');
    process.exit(2);
  }
}
