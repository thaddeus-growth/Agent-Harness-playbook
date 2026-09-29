// Runs a workflow script under node with stub hooks, for the tests. No agent is started.
//
//   node sim.js SCRIPT < {"args": ..., "replies": {"<label>": reply, ...}}
//
// agent(prompt, opts) records the call and returns replies[opts.label], or null when the label
// has none (as the runtime does for an agent that died). A reply missing a key its schema
// requires is recorded under `problems`, so tests and schemas cannot drift apart. parallel() and
// pipeline() follow the runtime's contract: a thunk or stage that throws becomes null.
// Date.now, Math.random and an argless new Date() throw, as in the runtime (they break resume).
// Prints one JSON document: {result, error, calls, logs, problems}.
'use strict'
const fs = require('fs')

const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const src = fs.readFileSync(process.argv[2], 'utf8').replace(/^export const meta\b/m, 'const meta')
const calls = [], logs = [], problems = []

const RealDate = Date
Date.now = () => { throw new Error('Date.now() breaks resume') }
Math.random = () => { throw new Error('Math.random() breaks resume') }
globalThis.Date = new Proxy(RealDate, {
  construct(target, a) { if (!a.length) throw new Error('new Date() breaks resume'); return new target(...a) },
})

const tick = () => new Promise(resolve => setImmediate(resolve))

async function agent(prompt, opts = {}) {
  calls.push({ label: opts.label || null, phase: opts.phase || null, isolation: opts.isolation || null, prompt })
  await tick()
  const has = Object.prototype.hasOwnProperty.call(input.replies, opts.label)
  const reply = has ? input.replies[opts.label] : null
  if (reply && opts.schema) {
    for (const k of opts.schema.required || []) if (!(k in reply)) problems.push(`reply for ${opts.label} lacks ${k}`)
  }
  return reply == null ? null : JSON.parse(JSON.stringify(reply))
}

const parallel = thunks => Promise.all(thunks.map(t => Promise.resolve().then(t).catch(() => null)))

const pipeline = (items, ...stages) => Promise.all(items.map(async (item, i) => {
  let r = item
  for (const stage of stages) {
    try { r = await stage(r, item, i) } catch (e) { return null }
  }
  return r
}))

const phase = title => { calls.push({ phase_call: title }) }
const log = message => { logs.push(String(message)) }
const budget = { total: null, spent: () => 0, remaining: () => Infinity }
const workflow = () => { throw new Error('workflow() is not simulated') }

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow', src)
const done = doc => process.stdout.write(JSON.stringify({ calls, logs, problems, ...doc }))
run(agent, parallel, pipeline, phase, log, input.args, budget, workflow)
  .then(result => done({ result, error: null }), e => done({ result: null, error: String(e && e.message || e) }))
