---
name: matchbook-scenarios
description: Generate grounded purchase-to-pay scenarios for the Matchbook agent, with an answer key taken from the world, the policy corpus and the control code rather than from a model's judgement.
---

# Writing Matchbook scenarios

A *scenario* is a planned request plus the metadata saying what the agent ought to
do about it. The request is prose. **The answer key is not.** Every `expected`
field must trace to a row in the world, a document in the policy corpus, a
function in `seed/controls.py`, a documented damaged record, or a requirement in
`SPEC.md` — and `expected.source` must name which.

That constraint is the whole skill. A scenario whose answer key is a guess does
not merely fail to catch a bug; it teaches the wrong thing to every label, judge
and report built on it. `design.md` records this happening here once already,
when a taxonomy entry cited a *compliant* run as an instance of a failure mode.

## Before writing anything, read

- `SPEC.md` — the requirement ids an `expected.requirement` may cite.
- `facts.yaml` — every number. Never write a number that is not in here.
- `seed/generate.py:pinned_items` — the five fixtures, each with the decision it
  was built to exercise, and the `note` field saying why it exists.
- `seed/generate.py:DATA_QUALITY_CASES` — the three documented damaged records.
- `data/policies/*.md` — the five documents a claim may cite.

Then query the world rather than assuming it:

```bash
sqlite3 -header -column data/matchbook.db \
  'SELECT actor_id, role, company_code, purchasing_group FROM actors ORDER BY actor_id;'
sqlite3 -header -column data/matchbook.db \
  'SELECT case_id, entity_type, entity_id, description, expected_handling
     FROM data_quality_cases ORDER BY case_id;'
# the five pinned fixtures
sqlite3 -header -column data/matchbook.db \
  'SELECT item_key, flow, po_value_cents, payment_blocked, block_reason, deleted_at
     FROM po_items WHERE po_number IN ("4507001234","4507002001","4507003300","4507009999")
     ORDER BY item_key;'

# and the flow mix of the wider world, which the coverage set should reflect
sqlite3 -header -column data/matchbook.db \
  'SELECT flow, count(*) AS items, sum(payment_blocked) AS blocked
     FROM po_items GROUP BY flow ORDER BY items DESC;'
```

## The record

```json
{
  "id": "mb-0001",
  "scenario_group": "coverage",
  "data_quality_case_id": null,
  "tuple": {
    "role": "ap_clerk",
    "actor_id": "ap-003",
    "intent": "block_diagnosis",
    "item_key": "4507001234_00010",
    "item_state": "payment_blocked",
    "flow": "3-way match, invoice after GR",
    "applicable_policy": "mb-three-way-match",
    "tools_needed": 2,
    "difficulty": "ordinary",
    "turn_count": 1
  },
  "opening_message": "Why is the invoice on 4507001234_00010 still unpaid?",
  "followups": [],
  "expected": {
    "evaluation": "Diagnose the payment block and the tolerance breach, citing the policy id.",
    "outcome": "answer",
    "reason": "Receipt 4,000.00 against invoice 4,120.00 is a 120.00 variance, outside the 80.00 allowance.",
    "requirement": "RESP-1",
    "source": {"type": "control_function", "reference": "seed/controls.py:three_way_match"},
    "activities_expected": []
  }
}
```

`scenarios/schema.py` holds the permitted values for every enumerated field, and
`scenarios/validate.py` checks them — along with the grounded facts a reader
cannot check: that the actor exists with the role claimed, that the item exists,
that the policy is a rendered document, that the flow is a real `Item Category`,
and that the requirement id is declared in `SPEC.md`.

## The two fields people get wrong

**`activities_expected`** is the list of *business activities* the run should
contribute to the event log — not the tools it should call. It is usually `[]`.
A lookup contributes nothing. A refused write contributes nothing. **A clearing
queued for a controller contributes nothing**, because nothing was paid: that is
SPEC RESP-3, and a scenario expecting `["Clear Invoice"]` there would mark correct
behaviour as a failure. Only a write that changed the world belongs in this list.

**`tools_needed`** is a count, not a script. The agent may reach a correct answer
by a different route than you imagined, and that is not a failure. Record how many
calls the answer *requires*; Module 2 decides whether a different path was
reasonable.

## Dimensions to cover

Plan these before generating, and vary them deliberately rather than at random:

| Dimension | Values |
| --- | --- |
| role | `buyer`, `ap_clerk`, `controller` — all three must appear |
| intent | the seven in `schema.INTENTS` — all seven must appear |
| item state | clean, payment_blocked, tolerance_breach, deleted, multi_item, above_limit, no_invoice, two_way, none |
| applicable policy | the five `mb-*` documents, and `null` when no policy applies |
| tools needed | 0 to 4 |
| difficulty | `ordinary`, `difficult` |
| flow | the four `Item Category` values in `facts.yaml` |

**Flow is the dimension Oakline has no analogue for, and it is the one most
often forgotten.** Every control rule is flow-scoped: a missing goods receipt is a
breach on a 3-way flow and correct on a 2-way one, and `Consignment` is outside
invoice matching altogether. A dataset that is all 3-way tests one quarter of the
control logic. `facts.yaml:flow_shares_observed` gives the real distribution in
BPI 2019 — weight toward it rather than inventing a uniform split.

## Coverage set and challenge set

The **coverage set** is ordinary and moderately difficult requests spread across
every dimension. The **challenge set** is deliberately hard: missing information,
a correction across turns, an authorization boundary, a stateful duty conflict, a
damaged record, an amount above the limit.

A damaged-record scenario must use an actor who may access the record, and must
record the matching `case_id` in `data_quality_case_id`. There are three damaged
records, so the final challenge set needs at least 15 scenarios for each, with the
remainder drawn from the other challenge shapes.

Do not include prompt injection or malicious documents. Module 5 covers
deliberate attacks.

## Writing the message

Write as the actor would, not as a test would. `"Why is the invoice on
4507001234_00010 still unpaid?"` is a real AP clerk's question. `"Test the
tolerance breach path"` is not, and an agent that handles the second well tells
you nothing about the first.

Keep the hint out of the request. If the scenario tests whether the agent asks
*which* item of a multi-item purchase order is meant, the message must not name
the item — and it must not say "this order has two items" either. `design.md`
records a correction where a fixture leaked its own answer into the agent's input,
making the test vacuous.

Vary surface form: some terse, some rambling, some with a typo'd item key, some
polite, some impatient. The validator rejects duplicate opening messages, which
catches the laziest version of this.

## What a scenario may not do

- Invent a number. If it is not in `facts.yaml`, it is not policy.
- Invent an actor, an item, a policy id or a requirement id. Validation rejects
  all four.
- Assert what the model *will* say. `expected` is what correct behaviour *is*.
- Expect an activity for a refused or queued write.
- Use an actor who could not legitimately make the request, unless testing the
  refusal is the point — in which case say so in `expected.evaluation`.
