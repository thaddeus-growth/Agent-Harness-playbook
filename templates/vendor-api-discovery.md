# Vendor API discovery: find the real endpoint before writing the client

The prior-art stage names a vendor; this step pins down how its API really behaves, spending as little as possible. Run it for every paid generator, gateway or data source.

*Seen once, in a Douyin ad harness.*
- **The token was for a reseller.** The client's "H3 API key" belonged to a third-party gateway, not MiniMax.
- **The gateway's docs index didn't list the model.** The real H3 route was a set of ComfyUI workflows, found only on its product pages.
- **The parameter schemas weren't in the docs.** They came from a public JSON endpoint the product page itself calls.
- **The token belonged to the wrong group.** The first call was refused for ComfyUI access, at no cost.

## Steps, cheapest first

1. **Read the official docs**, and prefer `llms.txt`, `.md` or OpenAPI sources. Record the endpoint, auth header format, async/poll flow, how inputs are passed (URL, upload, base64/data URI), how long output URLs live, and the price unit.
2. **Read the product pages as well.** Marketplace, workflow and model pages often hold what the docs index omits. If a page renders client-side, open it in a browser and read its **network requests**: the page's own JSON endpoints (e.g. `GET /workflows/{id}` returning `input_rules` and `billing_config`) are the schema. Turn that into a harness verb that refreshes a catalog file.
3. **Make free calls before paid ones.** Use list-models and account endpoints: they prove the token works, show which *group* or scope it has, and cost nothing.
4. **The first paid call is the smallest one that answers the most questions:** lowest resolution and shortest duration. Also test whether inline `data:` URIs are accepted, because a yes removes a whole hosting layer.
5. **Record what you learned as facts with a date.** Prices go into the price registry as `pending` rows with their source. Schema quirks (mutually exclusive roles, integer durations, fps) go into the provider's docstring.
6. **Write the client against what you observed,** then test it against a fake server that returns the recorded shapes. That test costs nothing and survives vendor outages.

## Rules

- **Never read or print a credential,** to compare, debug or "check". Report key **names** and set/unset only. If you need to know which of two keys works, ask the owner to put the working one under the agreed name.
- **A refused call is information:** "no permission for this API" means the token has the wrong group, not that the approach is wrong.
- **Another session may be spending on the same account.** Read the ledger before and after, and report entries you didn't make instead of deleting them.
- **Long jobs run in the background** (H3 takes: ~8–10 min each). Submit in parallel when the vendor allows it, and never sleep-poll in the foreground.
