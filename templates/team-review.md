# Team review

A client meeting has several people in it; each kind of ask has one person who decides. Team review lets everyone else see each ask and each answer, and disagree with it, while exactly one person decides. Fill the names in [decision-rights.md](decision-rights.md), "Deciders and advisers".

## Who does what

| Role | Does | Never |
| --- | --- | --- |
| **Decider** (one per kind of ask) | Answers, reopens and writes notes in the console | Answers for another kind of ask |
| **Adviser** | Sees every page; says Agree or Disagree about an ask or its answer, with a reason to disagree | Answers, reopens, or writes a note. Advice is evidence, not an answer |
| **Agent** | Posts asks, applies answers, turns each disagreement into a new ask, forwards the digest | Applies advice, or anything that is not in the decider's `ask.py answers` |

The console enforces the split. Started with `serve.py --deciders NAME`, only the named decider answers; anyone else logged in (behind a login proxy that names each person, `--user-header`) is an adviser whose one write is a view. Advice never counts toward the 10 open asks. *(console/tests/test_serve.py)*

A console with no login (one person on their own machine, `--user`) has no advisers in it. Then the team reads the forwarded digest and says its views in chat; the agent quotes a disagreement, with who said it and when, as evidence in the new ask.

## One round

1. The agent posts at most 10 asks to the decider's console (`console/ask.py add`), each with evidence, a recommendation and what "no" means.
2. The decider answers. Advisers agree or disagree, before or after the answer.
3. The agent applies each answer through the harness's own path, records `ask.py applied ID@SEQ --where "…"`, and writes `console:ID@SEQ` in the `decided` column of the `.agent.tsv` row.
4. The agent reads the views: `ask.py answers --since SEQ` lists every view in `advice`, never cut short.
5. Each disagreement becomes one new ask to the decider (below). The first answer stands until then.
6. The agent forwards the round's digest where the team reads it. Where that is, is asked once, of the builder owner.

## The digest: the shared record

`ask.py digest` renders it from the log: every ask, what the decider was shown, the answer, who gave it and when, whether it took the suggestion, the team's views, and where it was applied or why it was withdrawn. The agent never types it. For a console without that verb, `python3 build/digest.py --console-dir DIR` makes the same record through `ask.py`.

- Keep each round's digest in the client's data folder, `$DATA_DIR/build/digest/<date>-round<N>.md`. It may hold client numbers, so never in a code repository.
- A teammate who missed the meeting reads the digest, not the chat.
- A published digest is never edited. The next round's digest shows what changed.

## A disagreement, as a new ask

A new id (an answered id is never reused), both views side by side, and "no" keeps the first answer:

```json
{
  "id": "intake-w1-d1",
  "step": "confirm",
  "kind": "word",
  "group": "A teammate disagrees",
  "title": "Change \"product family\" to \"product line\", as an adviser suggests?",
  "why": "You chose \"product family\" last round. An adviser disagrees: the sales team says \"product line\". Your first answer stays until you change it.",
  "evidence": [
    {"label": "Your answer", "value": "product family", "source": "console intake-w1@9"},
    {"quote": "Our sales team and our price list both say product line.", "source": "adviser's view on intake-w1@9, 2026-09-29"}
  ],
  "recommend": {"value": "no", "because": "\"Product family\" is the word you used in the meeting, and the reports already show it."},
  "if_no": "\"Product family\" stays, and the digest records the adviser's view."
}
```
