export const meta = {
  name: 'sweep-skeptic-plan',
  description: 'Read-only sweep for improvement candidates, one finder per dimension; one skeptic per candidate reproduces it and defaults to refuted; a planner turns the survivors into small ordered merge requests, each with a design brief',
  whenToUse: 'Before a refactor or clean-up round, or whenever a backlog should be verified before anyone builds it',
  phases: [
    { title: 'Sweep', detail: 'one read-only finder per dimension; evidence and a proof method per candidate' },
    { title: 'Skeptic', detail: 'one per candidate, as soon as its finder is done: reproduce it, default to refuted' },
    { title: 'Plan', detail: 'the survivors grouped into small, ordered, one-reason merge requests' },
    { title: 'Brief', detail: 'one design brief per planned merge request, for its builder to read first' },
  ],
}

// Sweep -> skeptic -> plan -> brief. Read-only: nothing in the repo is edited; the only files
// written are the briefs, under <scratch>/briefs/.
//
// Fill CFG and DIMENSIONS, or pass them: Workflow({scriptPath, args: {config: {...}, dimensions: [...]}}).
// Each finder reads a detached worktree at the pinned base and reports at most max_candidates
// candidates, each with evidence and the way a builder would prove it safe. A skeptic per
// candidate starts as soon as its finder is done, reproduces it on its own and returns
// CONFIRMED, REFUTED or DOCUMENTED_LIMIT; a skeptic that returns nothing counts as unverified.
// Only CONFIRMED candidates reach the planner. Each planned merge request gets a design brief.
//
// Returns {verdict, items, not_now, confirmed, refuted, limits, unverified, overviews}. `items`
// has the ITEMS shape of build-review-fix-verify.js, each with its brief: review it, then pass
// it on as args.items.

const A = args || {}
const CFG = {
  repo: '/abs/path/to/checkout',
  scratch: '/abs/path/to/session/scratch',
  base: '0000000',                             // pinned base SHA: every finder reads the same code
  goal: 'why this sweep, in the owner\'s words',
  help: 'python3 tool.py --help',              // the command whose output wins over the docs
  in_flight: 'none',                           // other branches and sessions, and the files they own
  known: 'none',                               // known and accepted limits: never reported
  risky: 'the human gate, the write path, contract removals or renames, owner files, threshold values, human-data tables',
  guarantee_story: 'S09',                      // the story a behaviour-preserving refactor names
  max_candidates: 12,
  port_base: 9800,
  ...A.config,
}

const DIMENSIONS = A.dimensions || [
  { key: 'dead', prompt: 'DEAD CODE: functions, constants, flags, modules, registry rows (message codes, thresholds) and test helpers nothing reads. Check lookups by name, keys built from strings, registry readers and other consumers (pages, adapters, docs) before calling anything dead.' },
  { key: 'duplication', prompt: 'DUPLICATION: the same logic written more than once (date windows, scope resolution, threshold loading, money math, JSON output, queries, argument parsing). Name every copy, the exact differences between them (empty and missing values, rounding, scope) and the one existing module that should own it.' },
  { key: 'size', prompt: 'SIZE AND COHESION: rank the largest modules; in the biggest few, find sections with their own reason to change that could move without changing what other modules import. A split that only moves lines is not worth it: say so.' },
  { key: 'coupling', prompt: 'COUPLING AND LAYERING: build the import graph. Find imports that break the layering rules in the agent instructions, cycles, and consumers (a console, an adapter) that reach into internals instead of the contract. Give the smallest decoupling for each.' },
  { key: 'tests', prompt: 'TESTS: duplicated fixture helpers, the slowest files (time them), dead tests, tests that cannot fail (revert the line one guards and run it), and safety checks that test only one way.' },
  { key: 'docs', prompt: `DOCS DRIFT: compare the output of \`${CFG.help}\` with the agent instructions, the READMEs and the module docstrings, and find registry rows that name paths or commands that no longer exist. Concrete mismatches only; drift in an owner file is a proposal for the owner, not a fix.` },
]

const RULES = {
  R1: c => `Pinned base: read and compare against ${c.base} exactly, never a moving branch such as main. In flight elsewhere: ${CFG.in_flight}. A candidate touching those files waits for them (waits_for).`,
  R4: c => `Temp files: run every command with TMPDIR=${c.tmp} (create it first). Nothing goes to the system temp dir, the repo or a real data folder.`,
  R5: c => `Ports and stores: a server you start listens only on ports ${c.ports} and keeps its store under your TMPDIR; stop it before you finish. Never use a port or store another agent or a running service uses.`,
  R10: () => `Reproduce or drop, read-only: a finding you did not reproduce (the command you ran, what you saw) is not a finding; list what you probed and found fine under checked_and_fine. Edit nothing in any worktree: run things in your own detached worktree (git -C ${CFG.repo} worktree add --detach <dir under TMPDIR> <sha>) and remove it when done.`,
  R11: () => `Known and accepted, do not report: ${CFG.known}.`,
  R12: () => 'Skeptic: reproduce the claim yourself from scratch; do not trust the finder\'s quotes. Answer REFUTED when you cannot reproduce it or it is not worth doing, DOCUMENTED_LIMIT when it is real but already accepted or documented as a limit, CONFIRMED only when you reproduced it and the change is safe. List every consumer you opened (other modules, pages, adapters, docs, tests) in consumers_checked.',
  R13: () => 'Evidence and proof: every candidate carries hard evidence (grep counts, call sites, line ranges, sizes) and the way a builder would prove the change safe (tests red on the base first, a golden diff with no difference, a grep). A change to the contract, a flag, a schema or the human gate is not a clean-up: report it as out_of_scope.',
}
const rules = (c, ...ids) => 'Rules:\n' + ids.map(id => `${id}. ${RULES[id](c)}`).join('\n')

const str = description => ({ type: 'string', description })
const list = (description, items = { type: 'string' }) => ({ type: 'array', items, description })
const schema = (properties, required) => ({ type: 'object', properties, required: required || Object.keys(properties) })
const CANDIDATE = schema({
  id: str('short kebab-case id'), kind: { type: 'string', enum: ['dead', 'duplication', 'split', 'coupling', 'tests', 'docs', 'out_of_scope'] },
  title: str(''), files: list(''), evidence: str('grep counts, call sites, line ranges, sizes'),
  change: str('the exact change'), risk: { type: 'string', enum: ['none', 'low', 'medium', 'high'] },
  proof: str('how a builder proves it safe'), waits_for: list('in-flight branches it collides with'),
})
const CANDIDATES = schema({
  candidates: list('strongest first', CANDIDATE), overview: str('the state of this dimension in a few lines'),
  checked_and_fine: list('what you probed that held up'),
})
const VERDICT = schema({
  verdict: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'DOCUMENTED_LIMIT'] },
  evidence: str('what you ran and saw; for DOCUMENTED_LIMIT, where the limit is written down'),
  consumers_checked: list('every consumer of the code you opened'),
  corrected_change: str('the narrower change if only that survives, else empty'),
  risk: { type: 'string', enum: ['none', 'low', 'medium', 'high'] },
})
const PLAN_ITEM = schema({
  key: str('short id'), branch: str(''), story: str('S.. or P..'), kind: { type: 'string', enum: ['fix', 'feature', 'refactor'] },
  owner_merge: { type: 'boolean' }, owner_merge_why: str('which risky path, or empty'),
  stack_on: str('key of the planned item it builds on, or empty'), waits_for: list('in-flight branches to wait for'),
  candidate_ids: list(''), spec: str('precise enough to build without this run: files, the exact change, what must not change, tests'),
  proof: str('the commands that prove it'),
})
const PLAN = schema({
  verdict: str('is now the time, what it buys, what it risks: a few lines'),
  items: list('in build order', PLAN_ITEM), not_now: list('ideas left out, each with why'),
})
const BRIEF = schema({
  key: str(''), path: str('absolute path of the brief you wrote'), summary: str('three lines at most'),
  needs_owner: { type: 'boolean' }, owner_question: str('empty unless needs_owner'),
})

if (!/^[0-9a-f]{7,64}$/.test(CFG.base)) throw new Error(`config.base must be a commit SHA, not ${CFG.base}`)
const slot = (i, j) => i * (CFG.max_candidates + 1) + j
const ctx = (label, s) => { const p = CFG.port_base + 10 * s; return { base: CFG.base, ports: `${p}-${p + 9}`, tmp: `${CFG.scratch}/tmp/${label}` } }

const finderPrompt = (d, c) => `READ-ONLY sweep of ${CFG.repo} at ${CFG.base}. Goal: ${CFG.goal}.
Dimension ${d.key}. ${d.prompt}
Report at most ${CFG.max_candidates} candidates, strongest first; a few strong ones beat many cosmetic ones.
${rules(c, 'R1', 'R4', 'R5', 'R10', 'R11', 'R13')}`

const skepticPrompt = (cand, d, c) => `You are a skeptic. A finder (dimension ${d.key}) reported the candidate below against ${CFG.repo} at ${CFG.base}. Try to REFUTE it: show it is not dead (a reader by name, a consumer, a documented contract), not a true duplicate (edge cases, empty or missing values, rounding, scope), not behaviour-preserving (any output byte, error text, exit code or import side effect), or not worth it (it only moves lines).
CANDIDATE:\n${JSON.stringify(cand, null, 1)}
${rules(c, 'R1', 'R4', 'R5', 'R10', 'R11', 'R12')}`

const planPrompt = (overviews, confirmed, dropped) => `You are the planner. Group the confirmed candidates below into a short ordered list of SMALL merge requests: one reason each, reviewable in minutes, never a refactor mixed with a behaviour change. Order by value and risk. A request that touches an in-flight branch's files waits for it (waits_for); two requests that touch one file are stacked (stack_on) or ordered. owner_merge when it touches ${CFG.risky}. A behaviour-preserving refactor names the story ${CFG.guarantee_story}. Each spec must be buildable without this run: files, the exact change, what must not change, the tests (red on the base first); each proof names the full suite and a golden diff with no difference for a refactor. Weak ideas go to not_now with why. Give a frank verdict on whether now is the time.
Overviews: ${JSON.stringify(overviews)}
Confirmed: ${JSON.stringify(confirmed)}
Refuted or documented limits (do not plan these): ${JSON.stringify(dropped)}
${rules({ base: CFG.base }, 'R1')}`

const briefPrompt = (item, c) => `Write the design brief a builder reads before building this merge request. Read-only on the repo; write exactly one file, ${CFG.scratch}/briefs/${item.key}.md, and nothing else.
The brief holds: every site the change touches, with path:line and the current code; the exact change, step by step; what must not change (outputs, flags, errors, what other modules import) and how to prove it; the tests to add, each failing on the base first; the risks and the consumers you checked. If a choice in it needs the owner, say so at the top and set needs_owner.
MERGE REQUEST:\n${JSON.stringify(item, null, 1)}
${rules(c, 'R1', 'R4', 'R10')}`

const swept = await pipeline(
  DIMENSIONS,
  (d, _, i) => agent(finderPrompt(d, ctx(`sweep-${d.key}`, slot(i, 0))), { label: `sweep:${d.key}`, phase: 'Sweep', schema: CANDIDATES }),
  async (res, d, i) => {
    if (!res) { log(`${d.key}: the finder returned nothing`); return null }
    const inScope = res.candidates.filter(c => c.kind !== 'out_of_scope')
    if (inScope.length > CFG.max_candidates) log(`${d.key}: ${inScope.length - CFG.max_candidates} candidates past max_candidates were not checked`)
    const checked = await parallel(inScope.slice(0, CFG.max_candidates).map((cand, j) => () =>
      agent(skepticPrompt(cand, d, ctx(`skeptic-${d.key}-${j}`, slot(i, j + 1))), { label: `skeptic:${d.key}:${cand.id}`, phase: 'Skeptic', schema: VERDICT })
        .then(v => ({ ...cand, dimension: d.key, verdict: v }))))
    return { dimension: d.key, overview: res.overview, out_of_scope: res.candidates.filter(c => c.kind === 'out_of_scope').map(c => c.title), checked: checked.filter(Boolean) }
  },
)

const dims = swept.filter(Boolean)
const all = dims.flatMap(d => d.checked)
const pick = v => all.filter(c => (c.verdict ? c.verdict.verdict : null) === v)
const confirmed = pick('CONFIRMED'), refuted = pick('REFUTED'), limits = pick('DOCUMENTED_LIMIT'), unverified = pick(null)
const brief = c => ({ id: c.id, title: c.title, why: c.verdict ? c.verdict.evidence : 'no verdict' })
const overviews = dims.map(d => ({ dimension: d.dimension, overview: d.overview, out_of_scope: d.out_of_scope }))
log(`${confirmed.length} confirmed, ${refuted.length} refuted, ${limits.length} documented limits, ${unverified.length} unverified`)
const report = { confirmed, refuted: refuted.map(brief), limits: limits.map(brief), unverified: unverified.map(brief), overviews }
if (!confirmed.length) return { verdict: 'nothing survived the skeptics', items: [], not_now: [], ...report }

const plan = await agent(planPrompt(overviews, confirmed, [...refuted, ...limits].map(brief)), { label: 'plan', phase: 'Plan', schema: PLAN })
if (!plan) return { verdict: 'the planner returned nothing', items: [], not_now: [], ...report }
const briefs = await parallel(plan.items.map((item, k) => () =>
  agent(briefPrompt(item, ctx(`brief-${item.key}`, slot(DIMENSIONS.length, k))), { label: `brief:${item.key}`, phase: 'Brief', schema: BRIEF })))
const items = plan.items.map((item, k) => ({ ...item, brief: briefs[k] ? briefs[k].path : '',
  ...(briefs[k] && briefs[k].needs_owner ? { needs_owner: true, owner_question: briefs[k].owner_question } : {}) }))
return { verdict: plan.verdict, items, not_now: plan.not_now, ...report }
