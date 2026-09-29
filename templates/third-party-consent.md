# Third-party consent: a real person in a marketing output

Marketing harnesses use real people: an AI twin of a founder or staff member, a cloned voice, a customer testimonial, a creator's clip reused as an ad, a quoted review. Each needs that person's permission for exactly this use. That permission is data the harness checks before it spends or publishes. It is not something the agent remembers from chat.

*Seen once:* a video-ad harness added an "authorised real presenter" (the person's footage became the still, their speech became a cloned voice). The first version let a JSON field say `status: confirmed`. On the owner's word in chat, the agent set that field itself, and the harness accepted it. Consent now counts only as a row the human gate wrote, bound to the record's content. Code: [`kit/consent.py`](../kit/consent.py).

## The record

One file per person per purpose (for example `cast/<id>/consent.json`):

| Field | What it holds |
| --- | --- |
| `person`, `relation` | Who, and what they are to the advertiser (a closed set the harness declares: staff, owner, creator, customer, external) |
| `uses` | What of them may be used (a closed set: face, voice, name, words, footage) |
| `scope` | Lists per key, for example brands, channels, purposes; `*` means any |
| `valid_from`, `valid_until` | ISO dates; outside them nothing is made |
| `document`, `document_sha256` | The signed letter or contract on disk, and its hash. A new scan is a new record |
| `revoked` | Anyone may set it; it never needs the gate |
| `status`, `confirmed_by`, `confirmed_at`, `confirm_reason` | Written by the confirm verb, never typed; they are not part of the content hash |

## Rules

1. **A person confirms, through the gate.** The confirm verb shows who, which uses, the scope, the dates and the document hash; a person retypes the record id at a terminal, or relays a one-time code. The gate's subject is the record's content hash, so the code confirms exactly what was shown.
2. **Any edit after confirmation needs a new one:** longer dates, another brand, another use or a new scan. A hand-set `status` never counts.
3. **Lowering trust is free.** Revoke needs a reason, not the gate. Revocation stops new outputs. Taking down published ones follows the letter's term.
4. **Check at the money path and at the build.** A paid call that uses the person (their face, their voice) is refused without valid consent; a build that includes an already-made take is refused too, so a revocation also stops re-use. Free previews that don't use the person (a placeholder, a stock voice) need none.
5. **The agent drafts, the person signs, a person confirms.** The agent may fill the record from the signed letter. It never confirms, and a runtime that refuses to upload someone's biometrics is not to be worked around (see [vendor-api-discovery.md](vendor-api-discovery.md)).
6. **AI content stays labelled.** Consent to a likeness is not consent to hide that it is synthetic. The AI label stays on every frame where the platform or law asks for one.
7. **Category rules still apply.** Some categories forbid endorsers or testimonials outright (drug ads in several jurisdictions). A consented person is then limited to what the category allows: for example, staff speak as the manufacturer, never as a patient or doctor. Put this in the harness's category rules with a lint test.

## The letter (skeleton; the client's lawyer adapts it)

Who authorises whom; what (face, voice, name, words, footage, and whether AI may generate new speech or motion); for which brands, channels and purposes; from when to when; the AI label; what the likeness may never be used for (false claims, a role the person doesn't hold, defamation); revocation and take-down time; how material and models are stored and deleted; payment; signatures and dates.

**Legal bases to check with the client's lawyer** (examples, not advice): portrait and voice rights; biometric data as sensitive personal data needing separate consent (China PIPL art. 28–29, EU GDPR art. 9); deep-synthesis rules requiring the subject's consent to edit a face or voice (China, 2023); rules on labelling AI-generated content; endorsement and testimonial rules (for example the US FTC Endorsement Guides, China Advertising Law art. 16 for drugs).

## Checks

- A hand-set `status: confirmed` with no gate row is refused.
- Confirm is refused without a person (no terminal, no relay secret), and with a wrong retype.
- An edit after confirmation invalidates it; revoke blocks at once.
- Plan lists the consent gaps; generate refuses before anything is sent.
