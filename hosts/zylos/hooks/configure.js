'use strict';
// Zylos configure hook: the collected config arrives as one JSON object on
// stdin ({"KEY": "value", …}). It goes to the component's own
// <data dir>/.env (mode 0600, data dir 0700), which the harness's env chain
// reads as $<PREFIX>_DATA_DIR/.env; never to the Zylos root .env.
//
// Merge-only, so it can be re-run any time (zylos-core has no reconfigure
// command; the agent re-pipes new keys): a key in the file is updated in
// place, a new one appended, a key not given is kept, and an empty or null
// value leaves the stored one as is. It never deletes a key: remove its line
// from the .env by hand. Numbers and booleans are stored as their JSON text;
// objects, arrays and multi-line values refuse the whole input. A value the
// .env parser would otherwise alter (edge whitespace, surrounding quotes) is
// written quoted so it reads back exactly. A manifest `defaults` entry is
// written when a key of its group is in use and it is absent (zylos-core never
// applies a declared default). No network, no uv: Zylos kills it after 30 s.
// Only key names are printed.

const fs = require('node:fs');
const path = require('node:path');
const h = require('../lib.js');

const KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
const LINE_KEY = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/;
const USAGE = 'pipe one JSON object of "KEY": "value" pairs on stdin';

let values;
try {
  values = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
} catch (e) {
  h.fail(`configure: stdin is not JSON (${e.message})`, USAGE);
}
if (!values || typeof values !== 'object' || Array.isArray(values)) {
  h.fail('configure: stdin JSON is not an object', USAGE);
}

const updates = new Map();
const bad = [];
for (const [k, v] of Object.entries(values)) {
  if (v === null || v === undefined || v === '') continue;
  if (!KEY.test(k)) { bad.push(`${k} (not a KEY name)`); continue; }
  if (typeof v === 'object') { bad.push(`${k} (an object or array, not a string)`); continue; }
  const s = String(v); // numbers/booleans: their JSON text
  if (/[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]/.test(s)) { bad.push(`${k} (multi-line value)`); continue; }
  updates.set(k, s);
}
if (bad.length) h.fail(`configure: nothing written; bad values for ${bad.join(', ')}`, USAGE);

// The parser strips edge whitespace and one pair of matching outer quotes;
// wrapping in "…" makes it return exactly `s`.
function encode(s) {
  const altered = s !== s.trim() || (s.length >= 2 && `'"`.includes(s[0]) && s.at(-1) === s[0]);
  return altered ? `"${s}"` : s;
}

const file = path.join(h.DATA_DIR, '.env');
fs.mkdirSync(h.DATA_DIR, { recursive: true });
fs.chmodSync(h.DATA_DIR, 0o700); // core creates it 0755
for (const f of fs.readdirSync(h.DATA_DIR)) {
  if (f.startsWith('.env.tmp-')) fs.rmSync(path.join(h.DATA_DIR, f), { force: true }); // an interrupted run
}
let lines = [];
try { lines = fs.readFileSync(file, 'utf8').split('\n'); } catch { /* new file */ }
if (lines.length && lines.at(-1) === '') lines.pop();

const present = new Set(lines.map((l) => LINE_KEY.exec(l)?.[1]).filter(Boolean));
const defaults = Object.entries(h.M.defaults || {}).filter(([k]) => k !== '_comment');
const defaulted = [];
for (const [k, d] of defaults) {
  const inUse = d.when.some((w) => updates.has(w) || present.has(w));
  if (inUse && !updates.has(k) && !present.has(k)) { updates.set(k, d.value); defaulted.push(`${k}=${d.value}`); }
}

const replaced = new Set();
lines = lines.map((line) => {
  const m = LINE_KEY.exec(line);
  if (!m || !updates.has(m[1])) return line;
  replaced.add(m[1]);
  return `${m[1]}=${encode(updates.get(m[1]))}`;
});
const added = [...updates.keys()].filter((k) => !replaced.has(k));
for (const k of added) lines.push(`${k}=${encode(updates.get(k))}`);

const tmp = `${file}.tmp-${process.pid}`;
fs.writeFileSync(tmp, lines.length ? `${lines.join('\n')}\n` : '', { mode: 0o600 });
fs.chmodSync(tmp, 0o600);
fs.renameSync(tmp, file);

h.log(`configure: ${file} (0600): updated ${[...replaced].join(', ') || 'none'}; `
      + `added ${added.join(', ') || 'none'}`
      + (defaulted.length ? `; defaulted ${defaulted.join(', ')}` : ''));
