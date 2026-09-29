# Prior-art scan

Before building, look at what already exists: tools the client could buy, open-source code, the platform's own features, and how practitioners measure the same thing. A scan is a few hours at fixed points, not a background job.

## When to scan

| When | Question it answers | Skip if |
| --- | --- | --- |
| After the first meeting's intake, before stories are accepted ([BUILD.md](../BUILD.md) B0.5) | Is this already solved? What does the client compare us to? | Never skip the first scan |
| Before the data and rules stages (3–6; BUILD.md B3 and B4) | Which API fields, metrics and thresholds do others use? What guardrails do their write paths have? | The last scan is under 30 days old and covered these |
| Before a release that adds a write path or a paid call (BUILD.md B7 and B9) | What went wrong for others doing the same writes? | No new write or paid call |
| When the client names a tool or a competitor | What does that tool do that we do not? | — |

Each scan is dated. A scan older than 90 days is stale: re-scan before quoting it to the owner.

## How to scan

Split the scan into lenses and give each one to its own agent, so no lens is starved:

1. **Products the client could buy instead**: what they do, how they price (per seat, % of spend, per outcome).
2. **The platform's own features and API**: what it already automates, what the API can read and write, rate limits, permissions.
3. **Method**: how practitioners measure success and set thresholds (minimum data before a call, leading signals).
4. **Agent patterns**: open-source tools and servers doing the same job for agents, their write actions and guardrails.

Rules:

- Every claim carries its URL and the date it was read. A claim whose page could not be opened is dropped, not guessed.
- Never copy code or text from a source into the harness; write down the idea and where it came from.
- Record what to **avoid** as carefully as what to borrow.
- A borrowed idea names the module it changes (a report, a rule, the gate, a registry). An idea with no module is a note, not a change.
- **Verify mechanically before trusting a README.** `gh api repos/O/R --jq '[.full_name,.stargazers_count,.pushed_at,.license.spdx_id,.archived]'` catches renames and archives. Then read LICENSE itself: custom terms hide there ("non-commercial", "company licence above 3 staff"). Mark anything not confirmed UNVERIFIED; never fill a gap from memory.
- **Read the vendor's product pages, not only its docs index.** Workflow, model and marketplace pages often hold the endpoint list, the parameters and the price; pin the API down with [vendor-api-discovery.md](vendor-api-discovery.md).
- **Stop a lens** when two new searches in a row surface nothing better than its top three, or at about 20–30 tool calls.
- **Prices and limits enter as pending facts** with their source and date; code never hard-codes a number read on a web page. A decision names what would change it ("if the gateway accepts `data:` URIs, drop the hosting layer").

*Seen once, in a video-ad harness:* the scan replaced a planned renderer whose licence needed a company licence, found that the target editor's drafts are encrypted on read, and found that the client's "model API" key belonged to a gateway serving the model's open weights as ComfyUI workflows, with a lip-sync workflow the vendor's own API lacked. None of this was in the brief.

## Output

A folder in the harness repository, `docs/prior-art/`:

- `README.md`: the dated front page. A one-paragraph verdict (where the harness sits and why), **Borrow** (at most 12 ideas, each with its module and source), **Avoid**, open questions for the owner (at most 5) and the next re-scan date.
- One file per lens, `<date>-<lens>.md`: a table of name, what it does, what to borrow, what to avoid, source.

## After the scan

1. Borrowed ideas that change meaning or a number become owner asks in the console (at most 10 open, each with the source as evidence).
2. The rest become `proposed` rows in `ssot/user-stories.agent.tsv` or issues, each naming its source.
3. The owner questions go to the next meeting's agenda.
