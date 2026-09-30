# Claim scope: copy may say only what the approved document allows

Regulated products carry an approved document that fixes what may be claimed: a drug's indication on its package insert, a health food's approved function on its registration, a cosmetic's efficacy class, a fund's prospectus. An ad that names anything outside it is refused by the platform's review, however ordinary the words. A banned-word list can't catch this, because the words are not bad in themselves. What makes them wrong is the product. So the check runs the other way round: every claim the copy makes must be one the approved document names.

*Seen once:* a short-video ad harness made creatives for an OTC throat product whose indication reads, in full, "dry throat and sore throat caused by acute and chronic pharyngitis". In one afternoon the platform refused 20 of its creatives, all for the same reason: they named an itchy throat, phlegm, a foreign-body feeling, hoarseness, a cough. None of those words was on any banned list. The harness then held every line to the indication. Of the 10 phrases the reviewer had quoted, it caught all 10, and the approved wording passed. A second batch of 26 rejected creatives turned out to be 5 scripts rearranged, so one bad sentence was refused again and again. That is why the harness also keeps a phrase library. Code: [`kit/claimscope.py`](../kit/claimscope.py), [`kit/phrasebook.py`](../kit/phrasebook.py).

## The claims record, from the approved document

One JSON file per product (for example `products/<id>/claims.json`). The agent fills it from a photo or scan of the approved document. A person confirms it.

| Field | What it holds |
| --- | --- |
| `subject` | The product, as the person retypes it at the gate |
| `category` | The harness's category word (the harness decides which categories need a record) |
| `scope_text` | The approved document's wording, verbatim, in its own language. Never a summary |
| `allowed` | The claims that wording allows, as the lexicon's canonical names |
| `source` | Where the wording was read: the insert photo, the registration page, the filing |
| `status`, `confirmed_by`, `confirmed_at`, `confirm_reason`, `content_sha` | Written by `claimscope.confirm()` only. They are outside the content hash, so a hand-set status never counts and any edit needs a new confirmation |

Map `allowed` narrowly. "Dry throat, sore throat caused by pharyngitis" allows dry throat, sore throat and pharyngitis. It doesn't allow laryngitis, an itchy throat or a cough, even though they sound close. When in doubt, leave a claim out and let the owner add it at the gate.

A category that needs a record and has none is itself an error (`claimscope.required()`): declare it, never guess.

## The lexicon: what copy can claim

A harness TSV (for example `ssot/claim_terms.tsv`), an owner file with a pending-until-confirmed status per row:

| Column | What it holds |
| --- | --- |
| `id` | Unique; findings name it |
| `kind` | `claim`: must be inside the scope (a symptom, a benefit, a return). `always`: a finding whatever the scope (a scope-widener such as "every kind of discomfort" or "specially targets", an audience or scene phrase such as "for teachers and streamers", "after a late night") |
| `canonical` | The claim's name; a phrase that says two things names both (`dry+itchy`) |
| `pattern` | A regex covering how people actually say it, including the spoken forms |
| `severity`, `note`, `law_ref`, `status` | `error` or `warn`; why; the clause it rests on; `pending`, `confirmed` or `retired` |

A harness that already has its own kind words (symptom, condition, widener, scene) maps them once: `kinds={"symptom": "claim", "condition": "claim", "widener": "always", "scene": "always"}`. The longest match wins, so "dry and itchy throat" names both dry (allowed) and itchy (not), and is not read as just "dry". Two hits of one row side by side are one finding. A broken row (bad regex, unknown kind) is refused, never skipped. A finding from a row or record nobody has confirmed says "draft, not advice".

## The gates: script, asset, render

Run the same check wherever words enter the ad:

1. **Script**, before any paid generation: every spoken line, subtitle, callout, card and call to action. An open error blocks the spend.
2. **Assets**, before they are placed: text on packaging shots, stock clips and the client's own footage. A pack front can carry a claim the ad must not repeat.
3. **Render**, before anything leaves: read the finished video the way the reviewer does, speech by recognition and screen text by OCR, and check that. Recognition makes homophone errors, so the harness keeps a small fix table and applies it before checking and storing.

A finding with its time (`(where, text, t)`) lets a person jump to the frame.

## Waivers

Sometimes a finding is accepted on purpose (the regulator's own wording trips a widener, say). A person waives it at the gate by retyping the finding's words. The waiver is stored under a hash of rule, place and words, so if the line changes, the waiver no longer fits. `open_errors(findings, waivers_path)` gives what still blocks. Agents never waive.

## The phrase library

One TSV per product (`phrases.tsv`): every phrase its copy has said or may say, each `approved`, `pending` or `rejected`.

- **Harvest** the spoken lines of every creative, clause by clause, checked on the way in. A phrase with an error is stored rejected; a clean one is only pending.
- **Reject** what the platform refused: when a creative is refused and the lexicon doesn't see why, a person marks the phrase rejected (a reason, no gate). From then on any line that contains it is an error, whatever the punctuation.
- **Approve** at the gate: a person retypes the phrase's id. It is refused while the check still finds an error, and an edited text is no longer approved.
- **Assemble** new variants from approved phrases. A clause that isn't an approved phrase is a warning, so the owner sees what is new.

## The learning loop: every rejection is a test case

1. Keep every platform verdict as a row: creative id, time, product, category, the reviewer's reason verbatim.
2. `quoted(reason, markers=…)` takes the phrases the reviewer quoted inside “…”. The markers skip the reviewer's own paraphrase (a quote that holds "beyond the insert" is their summary, not the copy).
3. `recall(quotes, check)` must be 100%. Each miss becomes a pending lexicon row (or a rejected phrase), with the rejection id in its note, and the quote joins the fixture.
4. `false_alarms(approved_lines, check)` must be empty: run the same check over creatives the platform approved. An alarm is a row that is too wide. Narrow it rather than waive it.
5. A reason that quotes nothing tells you to audit that video (speech and screen text) to find what was said.

Both numbers go in the harness's report. Recall without the false-alarm side just teaches the lexicon to refuse everything.

## Checks

- The approved wording passes. Every quoted phrase of the rejection fixture is caught.
- A hand-set `status: confirmed` is a draft. Confirm is refused without a person, with a wrong retype, and for an incomplete record. An edit after confirmation invalidates it.
- A waiver needs a person and the exact words. A changed line is no longer waived.
- A rejected phrase is refused inside a longer line with other punctuation. A phrase with an error can't be approved.
- A spend verb with an open error refuses before anything is sent.

This is a pre-check a person confirms, not legal advice. The owner (and their regulatory adviser) decides what the approved document allows.
