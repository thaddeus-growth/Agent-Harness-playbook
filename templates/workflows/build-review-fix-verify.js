export const meta = {
  name: 'build-review-fix-verify',
  description: 'Build each item on its own branch and worktree, review it with two read-only lenses, fix it in the same worktree, and pass a read-only verify gate; nothing is pushed',
  whenToUse: 'Decided work, one branch each: verified fixes, features, or the merge requests sweep-skeptic-plan planned',
  phases: [
    { title: 'Build', detail: 'one builder per item in its own worktree, from the pinned base' },
    { title: 'Review', detail: 'two read-only reviewers per branch: correctness; invariants and scope' },
    { title: 'Fix', detail: 'in the existing worktree; a finding is declined only with evidence' },
    { title: 'Verify', detail: 'read-only gate: refs, clean tree, full suite, golden diff; one repair round at most' },
  ],
}

// Build -> review -> fix -> verify, one branch per item. Nothing here pushes.
//
// Fill CFG and ITEMS, or pass them: Workflow({scriptPath, args: {config: {...}, items: [...]}}).
// sweep-skeptic-plan.js returns its planned merge requests in the ITEMS shape, each with a brief.
//
// Per item: a builder branches from the pinned base in its own worktree and returns the merge
// request text, or stops for the owner and commits nothing. Two read-only reviewers run in
// parallel. A fixer works on the same branch (EnterWorktree, or a detached worktree plus a
// compare-and-swap ref move) and declines a finding only with evidence. A read-only gate checks
// that the branch ref is the head the last writer reported, that the tree is clean, the full suite
// and the golden diff; one repair round, then a second gate. An item with `stack_on` starts from
// its predecessor's verified head; a stack stops at its first step that is not ready.
//
// Returns {base, repo, items}; each item's status is ready | not_ready | stopped_for_owner |
// no_result | no_commit | skipped | not_run. Save it as JSON and run refcheck.py on it: it checks
// every ready item against the real refs and prints the push commands.

const A = args || {}
const CFG = {
  repo: '/abs/path/to/checkout',              // every worktree shares this checkout's refs
  scratch: '/abs/path/to/session/scratch',    // TMPDIR, reviewer worktrees, briefs; never the repo
  base: '0000000',                            // pinned base SHA, never a moving branch name
  test: 'python3 tests/run.py',               // the full suite; every file prints its RESULT line
  golden: 'uv run tests/golden/engine.py compare {base} HEAD',   // --strict is added for a refactor; '' if the repo has no golden tool yet
  commit: 'git -c user.name="Build Agent" -c user.email=agent@example.com commit',
  trailer: '',                                // a line every commit message ends with, if any
  in_flight: 'none',                          // other branches and sessions, and the files they own
  schema_owner: '',                           // key of the one item that may bump a schema version
  risky: 'the human gate, the write path, contract removals or renames, owner files, threshold values, human-data tables',
  port_base: 8800,                            // each agent gets its own block of 10 ports
  ...A.config,
}

const ITEMS = A.items || [
  { key: 'total-unknown', branch: 'fix/total-unknown', story: 'S03', kind: 'fix', owner_merge: false,
    spec: 'A report total over a window with a missing day shows a number. Make it null with a coded reason whenever a day in the window is missing. Tests: one missing day gives null and the reason; a full window still gives the sum.' },
  { key: 'report-helpers', branch: 'refactor/report-helpers', story: 'S09', kind: 'refactor', owner_merge: false,
    spec: 'Move the report formatting helpers into their own module. No behaviour change.' },
  { key: 'report-helpers-dedupe', branch: 'refactor/report-helpers-dedupe', story: 'S09', kind: 'refactor', owner_merge: false,
    stack_on: 'report-helpers', spec: 'Replace the two copies of the date-window helper with the one in the new module. No behaviour change.' },
]
// Item fields: key, branch, story (S.. or P..), kind (fix | feature | refactor), owner_merge,
// spec; optional: named_change (the one behaviour change allowed), stack_on (key of the item it
// stacks on), brief (a design brief to read first), resume (the existing worktree of a builder
// that was cut off: it is finished there, not rebuilt), needs_owner and owner_question (set by a
// brief that needs the owner: the item is not built until the answer is in its spec).

const RULES = {
  R1: c => `Pinned base: branch from, diff against and compare with ${c.base} exactly, never a moving branch such as main. In flight elsewhere, do not touch (if your task needs one of these files, stop and say so): ${CFG.in_flight}. Only item "${CFG.schema_owner || '(none)'}" may bump a schema or data-format version in this run.`,
  R2: () => 'Red, then green: run every new or changed test on the base (or with your change reverted) and on your branch, and report both results. A test that passes on the base guards nothing; rewrite it until it fails there.',
  R3: () => 'Both ways: write every safety check as a pair, the bad case refused AND the good case accepted. A check that only asserts a refusal also passes on code that refuses everything.',
  R4: c => `Temp files: run every command with TMPDIR=${c.tmp} (create it first). Nothing goes to the system temp dir, the repo or a real data folder.`,
  R5: c => `Ports and stores: a server you start listens only on ports ${c.ports} and keeps its store under your TMPDIR; stop it before you finish. Never use a port or store another agent or a running service uses.`,
  R6: () => 'Golden diff: run its cases one at a time, never while the suite runs in your session, and only on a copy of real data, never the original. Name every difference and why it is intended.',
  R7: () => `Stop for the owner: when a choice is not mechanical (what a word means, a number, money), or the change reaches the risky list (${CFG.risky}) beyond what your task authorises, commit nothing, set stopped_for_owner and return one question with your recommendation and what "no" would mean.`,
  R8: c => `Write where you may: you did not create ${c.worktree}, and an edit hook may refuse writes there. Either call EnterWorktree with that path first and work there, or fix in your own detached worktree (git -C ${CFG.repo} worktree add --detach <dir under TMPDIR> <head>) and move the branch with a compare-and-swap: git -C ${CFG.repo} update-ref refs/heads/<branch> <new> <old>. If ${c.worktree} was clean before the move, run git -C ${c.worktree} checkout -f after it. Add commits; never amend or rebase commits the reviewers saw.`,
  R9: () => 'Heads, not pushes: after your last commit, git rev-parse refs/heads/<your branch> must print your commit; return it as head_sha. Never push, open a merge request or move another branch; the orchestrator pushes after checking every ref.',
  R10: () => `Reproduce or drop, read-only: a finding you did not reproduce (the command you ran, what you saw) is not a finding; list what you probed and found fine under checked_and_fine. Edit nothing in any worktree: run things in your own detached worktree (git -C ${CFG.repo} worktree add --detach <dir under TMPDIR> <sha>) and remove it when done.`,
}
const rules = (c, ...ids) => 'Rules:\n' + ids.map(id => `${id}. ${RULES[id](c)}`).join('\n')

const LENSES = [
  { key: 'correctness', text: 'CORRECTNESS: is the behaviour right? Hunt the inputs the tests lack: empty, missing, zero against null, the boundary day, another scope, a second run. Check joins, double counting and error paths. Rerun the new tests yourself, then revert one line of the change and rerun that one test file: it must fail (R2).' },
  { key: 'invariants', text: 'INVARIANTS AND SCOPE: check every invariant in the agent instructions the diff touches (the contract only adds keys, coded messages, one place per value, owner files untouched, layering, boundary). Is it the smallest change on existing paths, one reason, no drive-by edits or dead code? Do the safety checks go both ways (R3)? Is owner_merge right for the risky list? Commit subjects name the story; no stray files.' },
]

const str = description => ({ type: 'string', description })
const list = (description, items = { type: 'string' }) => ({ type: 'array', items, description })
const schema = (properties, required) => ({ type: 'object', properties, required: required || Object.keys(properties) })
const BUILD = schema({
  branch: str('the branch you created'), worktree: str('absolute path of your worktree'),
  head_sha: str('git rev-parse refs/heads/<branch> after your last commit; empty if you committed nothing'),
  stopped_for_owner: { type: 'boolean' }, owner_question: str('the one question, your recommendation, what no means; empty unless stopped'),
  summary: str('what changed and why, plain words'),
  tests_red_green: list('each new or changed test: its result on the base, then on the branch'),
  suite: str('the RESULT lines of the full suite'), golden: str('which cases differ and why each is intended'),
  mr_title: str('names the story or policy'), mr_description: str('what and why, story, how verified, contract impact'),
  owner_merge: { type: 'boolean' }, owner_merge_why: str('which risky path, or empty'),
  schema_change: str('none, or old -> new and what'), host_steps: str('commands a host needs after release, or none'),
  queue_questions: list('decisions only the owner can make, each with a recommendation'),
  deviations: str('what you did differently from the task and why, or empty'),
})
const FINDING = schema({
  severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] }, file: str('path:line'),
  problem: str('what is wrong'), repro: str('the command you ran and what you saw'), fix: str('the smallest fix'),
})
const REVIEW = schema({
  verdict: { type: 'string', enum: ['ship', 'fix'] }, findings: list('reproduced problems only', FINDING),
  checked_and_fine: list('what you probed that held up'), owner_merge_correct: { type: 'boolean' },
})
const FIX = schema({
  head_sha: str('git rev-parse refs/heads/<branch> after your last commit'),
  fixed: list('each finding fixed, one line'),
  declined: list('each finding declined, with the evidence', schema({ finding: str(''), evidence: str('what you ran and what it showed') })),
  suite: str('the RESULT lines of the full suite'), golden: str('what differs and why'),
  stopped_for_owner: { type: 'boolean' }, owner_question: str('empty unless stopped'),
})
const VERIFY = schema({
  ok: { type: 'boolean', description: 'true only if every check passed' },
  branch_head: str('git rev-parse refs/heads/<branch>, as it is now'),
  suite: str('the totals and any file without its RESULT line'), golden: str('what differs, and whether each difference was expected'),
  problems: list('every failed check, one line each'),
})

if (!/^[0-9a-f]{7,64}$/.test(CFG.base)) throw new Error(`config.base must be a commit SHA, not ${CFG.base}: a moving branch changes under the builders`)
const byKey = {}
for (const it of ITEMS) {
  if (!it.key || !it.branch || !it.story || !it.spec) throw new Error(`every item needs key, branch, story and spec: ${JSON.stringify(it)}`)
  if (!['fix', 'feature', 'refactor'].includes(it.kind || 'fix')) throw new Error(`item ${it.key}: kind is fix, feature or refactor, not ${it.kind}`)
  if (byKey[it.key]) throw new Error(`two items share the key ${it.key}`)
  if (ITEMS.filter(x => x.branch === it.branch).length > 1) throw new Error(`two items share the branch ${it.branch}`)
  byKey[it.key] = it
}
for (const it of ITEMS) if (it.stack_on && !byKey[it.stack_on]) throw new Error(`item ${it.key} stacks on ${it.stack_on}, which is not an item`)

const sameSha = (a, b) => !!a && !!b && a.length >= 7 && b.length >= 7 && (a.startsWith(b) || b.startsWith(a))
const ports = slot => { const p = CFG.port_base + 10 * slot; return `${p}-${p + 9}` }
const ctx = (it, slot, base, extra) => ({ base, ports: ports(ITEMS.indexOf(it) * 8 + slot), tmp: `${CFG.scratch}/tmp/${it.key}-${slot}`, ...extra })
const golden = (it, base) => !CFG.golden ? 'there is no golden tool yet; say so under golden'
  : `\`${CFG.golden.replace('{base}', base)}${it.kind === 'refactor' ? ' --strict' : ''}\`, expecting ${it.kind === 'refactor' ? 'no difference at all' : it.named_change ? `only the named change (${it.named_change})` : 'only the additions this task makes'}`
const task = it => `TASK ${it.key} (serves ${it.story}; kind ${it.kind || 'fix'}${it.named_change ? `; the one behaviour change allowed: ${it.named_change}` : ''}):\n${it.spec}`

const buildBody = (it, base, c) => `${it.brief ? `Read the design brief ${it.brief} fully before designing; the task overrides it where they differ.\n` : ''}${task(it)}

Proof before you finish: the new tests red on the base, then green (R2); the full suite, TMPDIR=${c.tmp} ${CFG.test}, with its RESULT lines; after committing, the golden diff: ${golden(it, base)}.
Commit with ${CFG.commit}; the subject names ${it.story}${CFG.trailer ? `; end each message with: ${CFG.trailer}` : ''}.
Merge: planned ${it.owner_merge ? 'owner merge' : 'auto-merge on green CI'}. Set owner_merge if the diff touches ${CFG.risky}; never lower a planned owner merge.
${rules(c, 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R9')}`

const buildPrompt = (it, base, c) => `You build one item of a multi-agent run, in the git worktree you were started in (report its absolute path). First: git checkout -b ${it.branch} ${base}.\n${buildBody(it, base, c)}`

const resumePrompt = (it, base, c) => `RESUMING a builder that was cut off. Do NOT start over, create a worktree or re-create the branch. Work in the EXISTING worktree ${it.resume}, where ${it.branch} is checked out (call EnterWorktree with that path first). Its commits since ${base} and any uncommitted changes are your own earlier work: read git status and git diff ${base}, keep what is correct, finish what is not, commit, then produce every proof below.\n${buildBody(it, base, c)}`

const reviewPrompt = (it, b, base, lens, c) => `INDEPENDENT reviewer, read-only. Branch ${b.branch}, head ${b.head_sha}, base ${base}; the builder's worktree ${b.worktree} is not yours. Review git -C ${CFG.repo} diff ${base}...${b.head_sha}.
${task(it)}
The builder's summary (verify it, do not trust it): ${b.summary}
Deviations it reports: ${b.deviations || 'none'}. Planned merge: ${it.owner_merge || b.owner_merge ? 'owner' : 'auto'}.
Lens: ${lens.text}
Severity: blocker = wrong result, broken contract or invariant; major = a missing acceptance item or an untested branch that matters; minor = fix before merge; nit = optional.
${rules(c, 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R10')}`

const fixPrompt = (it, b, base, findings, c) => `Fix pass for branch ${b.branch} (head ${b.head_sha}, base ${base}). Do not create another branch.
Fix every real blocker, major and minor finding; nits by judgement. Decline a finding only with evidence (what you ran and what it showed). Commit on ${b.branch}, then rerun the changed tests, the full suite (TMPDIR=${c.tmp} ${CFG.test}) and the golden diff: ${golden(it, base)}.
${task(it)}
FINDINGS:\n${JSON.stringify(findings, null, 1)}
${rules(c, 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9')}`

const verifyPrompt = (it, b, base, head, round, c) => `Verify gate for ${b.branch}, round ${round}. Read-only: edit nothing, commit nothing.
(1) git -C ${CFG.repo} rev-parse refs/heads/${b.branch} must print ${head}, the head the last writer reported; return what it prints as branch_head.
(2) git -C ${b.worktree} status --porcelain is empty and that worktree is on ${b.branch}; git -C ${CFG.repo} log --oneline ${base}..${b.branch} lists only this item's commits.
(3) In your own detached worktree at the branch head: TMPDIR=${c.tmp} ${CFG.test}. Every file passes and prints its RESULT line; quote the totals.
(4) Then, not at the same time: ${golden(it, base)}.
ok is true only if all four hold; list every failed check under problems.
${rules(c, 'R4', 'R5', 'R6', 'R10')}`

const repairPrompt = (it, b, base, head, v, c) => `Repair branch ${b.branch}: the verify gate failed. The last reported head was ${head}; the branch ref is at ${v.branch_head}. If a reviewed fix sits on a commit the branch does not point at, move the branch to it with the compare-and-swap (R8) rather than redoing it.
PROBLEMS:\n${v.problems.map(p => '- ' + p).join('\n')}
Suite: ${v.suite}\nGolden: ${v.golden}
Fix the root causes (not a test's expectation, unless it was wrong), commit on ${b.branch}, rerun the suite and the golden diff: ${golden(it, base)}.
${task(it)}
${rules(c, 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9')}`

const passed = (v, head) => !!v && v.ok && sameSha(v.branch_head, head)
const gateProblems = (v, head) => !v ? ['the verify gate returned nothing']
  : [...v.problems, ...(sameSha(v.branch_head, head) ? [] : [`ref_mismatch: refs/heads/<branch> is at ${v.branch_head}, the last writer reported ${head}`])]

async function runItem(it, base) {
  const out = { key: it.key, branch: it.branch, base, stack_on: it.stack_on || '', story: it.story, kind: it.kind || 'fix', status: 'no_result',
    head_sha: '', owner_merge: !!it.owner_merge, problems: [], notes: [] }
  if (it.needs_owner) return { ...out, status: 'stopped_for_owner', owner_question: it.owner_question || 'its brief needs the owner' }
  const b = await agent((it.resume ? resumePrompt : buildPrompt)(it, base, ctx(it, 0, base)),
    { label: `build:${it.key}`, phase: 'Build', schema: BUILD, ...(it.resume ? {} : { isolation: 'worktree' }) })
  if (!b) return out
  Object.assign(out, { worktree: b.worktree, owner_merge: out.owner_merge || b.owner_merge, owner_merge_why: b.owner_merge_why,
    mr_title: b.mr_title, mr_description: b.mr_description, schema_change: b.schema_change, host_steps: b.host_steps,
    queue_questions: b.queue_questions, owner_question: b.owner_question })
  if (b.stopped_for_owner) return { ...out, status: 'stopped_for_owner' }
  if (!b.head_sha || sameSha(b.head_sha, base)) return { ...out, status: 'no_commit' }

  const reviews = await parallel(LENSES.map((l, j) => () => agent(reviewPrompt(it, b, base, l, ctx(it, 1 + j, base)),
    { label: `review:${it.key}:${l.key}`, phase: 'Review', schema: REVIEW })))
  reviews.forEach((r, j) => { if (!r) out.notes.push(`the ${LENSES[j].key} reviewer returned nothing`) })
  if (reviews.some(r => r && !r.owner_merge_correct) && !out.owner_merge) {
    out.owner_merge = true
    out.owner_merge_why = 'a reviewer found it touches the risky list'
  }
  const findings = reviews.flatMap((r, j) => (r ? r.findings.map(f => ({ lens: LENSES[j].key, ...f })) : []))

  let head = b.head_sha
  if (findings.length) {
    const fix = await agent(fixPrompt(it, b, base, findings, ctx(it, 3, base, { worktree: b.worktree })),
      { label: `fix:${it.key}`, phase: 'Fix', schema: FIX })
    if (!fix) return { ...out, status: 'not_ready', problems: ['the fix pass returned nothing'] }
    Object.assign(out, { fixed: fix.fixed, declined: fix.declined })
    if (fix.stopped_for_owner) return { ...out, status: 'stopped_for_owner', owner_question: fix.owner_question }
    head = fix.head_sha
  }

  let v = await agent(verifyPrompt(it, b, base, head, 1, ctx(it, 4, base)), { label: `verify:${it.key}:1`, phase: 'Verify', schema: VERIFY })
  if (v && !passed(v, head)) {
    const rep = await agent(repairPrompt(it, b, base, head, { ...v, problems: gateProblems(v, head) }, ctx(it, 5, base, { worktree: b.worktree })),
      { label: `repair:${it.key}`, phase: 'Fix', schema: FIX })
    if (!rep) return { ...out, status: 'not_ready', problems: ['the repair pass returned nothing'] }
    if (rep.stopped_for_owner) return { ...out, status: 'stopped_for_owner', owner_question: rep.owner_question }
    head = rep.head_sha
    v = await agent(verifyPrompt(it, b, base, head, 2, ctx(it, 6, base)), { label: `verify:${it.key}:2`, phase: 'Verify', schema: VERIFY })
  }
  if (!passed(v, head)) return { ...out, status: 'not_ready', head_sha: v ? v.branch_head : '', problems: gateProblems(v, head) }
  return { ...out, status: 'ready', head_sha: v.branch_head }
}

const children = key => ITEMS.filter(x => x.stack_on === key)
const skip = (it, why) => [{ key: it.key, branch: it.branch, status: 'skipped', problems: [why] },
  ...children(it.key).flatMap(k => skip(k, `stacked on ${it.key}, which was skipped`))]

async function runStack(it, base) {
  const r = await runItem(it, base)
  const kids = children(it.key)
  if (r.status !== 'ready') return [r, ...kids.flatMap(k => skip(k, `stacked on ${it.key}, which is ${r.status}`))]
  const rest = await parallel(kids.map(k => () => runStack(k, r.head_sha)))
  return [r, ...rest.flatMap(x => x || [])]
}

const done = (await parallel(ITEMS.filter(it => !it.stack_on).map(it => () => runStack(it, CFG.base)))).flatMap(x => x || [])
const seen = new Set(done.map(r => r.key))
const items = [...done, ...ITEMS.filter(it => !seen.has(it.key)).map(it => ({ key: it.key, branch: it.branch, status: 'not_run',
  problems: ['never reached: its stack_on chain loops, or a step before it threw'] }))]
  .sort((x, y) => ITEMS.indexOf(byKey[x.key]) - ITEMS.indexOf(byKey[y.key]))
const count = s => items.filter(r => r.status === s).length
log(`${count('ready')} ready, ${count('not_ready')} not ready, ${count('stopped_for_owner')} stopped for the owner, ${items.length - count('ready') - count('not_ready') - count('stopped_for_owner')} other`)
return { base: CFG.base, repo: CFG.repo, items }
