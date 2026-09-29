# Prior-art research: the stage before the first line of code

Run it after the first client meeting (stage 0) and before the words and stories (stage 1). A harness is mostly glue between things other people already built. This stage finds those things, proves what they really do, and writes down what the harness will adopt, keep as a fallback, and avoid.

A research agent can run it alone. The owner reads only the **Decisions** table.

## Inputs

- The goals and draft stories from the meeting intake.
- The client's sample of the output they want: a report, a video, a filing. Reverse-engineer it first. Its parts are the capability list.
- Any vendor, key or account the client already has. *Paid for:* a "MiniMax H3 API" key turned out to be a token for a third-party gateway that runs H3's open weights as ComfyUI workflows. The gateway's own docs index did not list H3 at all; only its model and workflow pages did.

## Steps

1. **Decompose.** Split the work into 4–8 capability categories: inputs, generation, assembly or render, hand-off or export, QA, compliance. Write one sentence per category saying what we would reuse (a schema, a renderer, a writer, only an idea).
2. **Seed candidates**, for each category:
   - `gh search repos "<capability> <domain>" --sort stars --limit 20`
   - the same in the client's language, e.g. `gh search repos 剪映 草稿`
   - web searches: `"<capability>" API docs <year>`, `awesome-<topic>`, `"<known tool>" alternative`
   - READMEs' "similar projects" and sponsor lists, which is where commercial APIs and resellers show up
   - **the vendor's product pages, not only its docs index.** Workflow, model and marketplace pages often hold the endpoint list, the parameters and the price.
3. **Verify mechanically.** Check what a tool can tell you before trusting a README:
   - `gh api repos/O/R --jq '[.full_name,.stargazers_count,.pushed_at,.license.spdx_id,.archived]'`. This catches renames, redirects and archives.
   - Read LICENSE itself. Custom terms hide in LICENSE.md ("non-commercial", "company licence above 3 staff").
   - For an API, take the official page or its `llms.txt`/OpenAPI source. Record the exact endpoint paths, model or workflow ids, enums, limits, the async or polling flow, how inputs are passed (URL, upload, base64), how long output URLs live, and the price unit.
   - Mark anything not confirmed **UNVERIFIED**. Never fill a gap from memory.
4. **Score** each candidate on one row:

   | Name | URL | Stars | Last push | License (SPDX, commercial OK?) | Maintainers / bus factor | Reuse type | Platform limits | Cost / lock-in | Risk | Verified? |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

5. **Mine the schemas.** For the top 2–3 per category, write down the data model (scene, timeline, job, record) even when you won't use the code. Commercial APIs' schemas are usually the cleanest to copy.
6. **Find the closest analogue**, a project that already combines the pieces. Read its README FAQ and issue tracker for the failures it hit. They become your first bug-class tests.
7. **Decide.** Each category gets one primary, one fallback, and an avoid list with the reason (licence, dormancy, closed core, price).
8. **Stop.** Stop a category when two consecutive new searches surface nothing that beats the current top three, or when one verified permissive primary and one fallback exist. Time-box to about 20–30 tool calls per category.

## Output: `docs/prior-art.md` in the harness repo

1. **Decisions** table (the owner reads this): need | adopt | fallback | avoid (why).
2. The scored tables, one per category.
3. The borrowed schema patterns, one line each, naming the source.
4. The vendor facts the code relies on: endpoints, parameters, limits, prices, each with its source URL and the date it was read.
5. Pitfalls others hit, each mapped to a test or a rule in this harness.

## Rules

- **Prices and limits enter as pending facts** in the harness's registry (`provider_prices.tsv`, `fact_keys.tsv`), with their source and date. The owner confirms them; code never hard-codes a number it read on a web page.
- **A decision names what would change it.** Example: "if the gateway accepts `data:` URIs, drop the hosting layer."
- **Re-run the stage when a vendor changes.** New model, new price or a deprecated endpoint each become a pending change, never a silent edit.
- **The research agent writes no product code.** It is read-only apart from `docs/prior-art.md`.
