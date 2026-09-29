# Outcome learning: the step that makes the next output better

A harness that produces things (ads, emails, landing pages, reports) is judged by how they perform. The build loop makes them; this step closes the outer loop. It measures the **same factors** on what won and what didn't, and turns the differences into rules the next plan follows.

*Seen once:* a Douyin ad harness. The owner named it as the missing node once the first real sample existed: "extract the winning factors from the winning creatives".

## Parts

| Part | What it is |
| --- | --- |
| Factor catalog (registry) | Every factor with its id, group, how it is measured (`auto:*` by code, `vision`/`script` by the agent) and its allowed values. Start with ~30; each factor names how it's measured, so two cards are comparable. |
| Outcome metrics (registry) | The numbers the client exports per item, named the way their platform names them. Never retyped from screenshots. |
| `dissect` verb | One item in, one card out: the machine-measured factors filled in, the rest listed as `todo`, plus the material the agent needs to fill them (contact sheets, transcript). |
| Fill step | The agent (or a sub-agent per card) fills the `todo` factors and writes down who filled them and how. |
| Label | The owner defines "winner" **before** seeing any factor. The definition is a rule on metrics, stored with the cards. |
| `factors` verb | Winners vs the rest: means for numbers, frequencies for categories. It warns when either side has fewer than 5 items. |
| Rules registry | Candidate rules with their evidence and cards, `pending` until the owner confirms. Pending rules become A/B variants, confirmed ones become defaults. |

## Rules

1. **Always ask for the losers too.** Without the rest, every factor of the winners looks like a cause.
2. **Compare like with like:** same product, same period, same channel. Otherwise the platform's own drift is what you measure.
3. **Correlation first, causation by test.** A rule graduates from `pending` only when a controlled variant (change one factor, keep the rest) wins again.
4. **Rules pass the same lint as everything else.** A winning trick that breaks a regulation is not a rule.
5. **Measure the harness's own outputs with the same catalog** once they run. Then the loop compares your items to the client's past winners on equal terms.
6. **Keep the fill step honest.** Record the method and who filled each card. A later reviewer must be able to redo a card from the sheets.
