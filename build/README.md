# build/: meetings to signed rows, through the owner console

Stages 0 to 2 of a new harness, with no hand-written ask and no hand edit of an owner file. Stdlib-only Python 3.11+.

```
intake.json → check_intake → intake_to_asks → ask.py add → owner answers → ask.py answers → apply_answers → ask.py applied
                                                                                                    └→ digest, for the team
```

1. **Organize** each meeting with [`templates/meeting-intake.md`](../templates/meeting-intake.md) into an intake file in the client's data folder, shaped by [`templates/intake.schema.json`](../templates/intake.schema.json): every item has an `iid`, an `audience` and a source.
2. **Check** all of a client's intake files together: `python3 build/check_intake.py FILE...`. No quote, no item; a number is the digits the client said, with its unit; a contradiction is a question. *(tests/test_check_intake.py)*
3. **Pick at most 10** for one person: `python3 build/intake_to_asks.py --intake FILE... --pick n1,w3,s2 --audience client --console-dir DIR > asks.json`, then `python3 console/ask.py add asks.json`. *(tests/test_intake_to_asks.py)*
4. **The owner answers** on the console page. Each person (client owner, builder owner) has a console folder of their own.
5. **Apply**: `python3 console/ask.py answers > answers.json`, then `python3 build/apply_answers.py --answers answers.json --intake FILE... --ssot ssot --data-dir DIR`. A yes to a word or story is an owner row plus a sibling naming its answer; a no is a dropped sibling; a number waits as pending in the client's data folder, never in the repo. Then run the `ask.py applied` commands it prints. It is the only tool that writes an owner's answer. *(tests/test_apply_answers.py)*
6. **Share** the round: `python3 build/digest.py --console-dir DIR --asks asks.json > round-1.md`: every ask, its evidence, the suggestion, the answer, who gave it and where it went. *(tests/test_digest.py)*

Tests: `python3 build/tests/run.py`. A file passes only if it prints `RESULT: N passed`.
