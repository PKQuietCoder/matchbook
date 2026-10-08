# Homework 1, implementing and examining the purchase-to-pay agent

Homework 1 asks you to complete five tools for the Matchbook accounts-payable
agent and examine the resulting behaviour through recorded conversations.

## Expected work

- Estimated time: 4 to 6 hours for a student who is comfortable with Python, or
  6 to 8 hours for a student who is learning the repository.
- Expected Python code: approximately 150 to 220 lines across five required
  functions in `agent/tools.py`, plus your own tools. `clear_invoice` is about
  half of that on its own, because its nine checks are ordered and the order is
  part of the specification.
- Other work: at least 10 JSONL records, an analysis of one possible system
  prompt revision, and a video of no more than 5 minutes.

The estimate includes time to explore the agent beyond the required cases,
because Part C depends on observing how the model interprets the specification.

## Preparation

A guided walkthrough is available in
[hw1-tutorial.md](hw1-tutorial.md) if you would like a coding agent to take you
through the assignment one step at a time. Read it before you begin either way:
it describes the workflow this handout expects.

Run the assignment from the repository root. Install the environment and
generate the local world:

```bash
uv sync --extra agent
uv run python -m seed.generate
uv run python -m seed.validate
```

Copy `.env.example` to `.env` if `.env` does not already exist, then add
`ANTHROPIC_API_KEY`. The tests and the seed commands need no key; the recorded
conversations in Part B do.

`seed.generate` creates `data/matchbook.db` — the actors, vendors, purchase
orders, items, goods receipts, invoices and case history the assignment uses —
and renders the policy corpus into `data/policies/`. Generation is deterministic
from a fixed seed and a fixed `world_asof` of 2026-07-01, so every student gets
the same five demonstration items and the same policy numbers. No external
database is required.

`seed.validate` is the step with no Oakline equivalent, and it is worth
understanding before you write any code. Every number in the policy corpus is
rendered from `facts.yaml`, and the validator fails the seed if any document
disagrees with it. The LLM writes prose; code computes facts. When you implement
a tolerance check in Part A you are implementing the *same* constant that the
policy document quotes and that `process/rules.py` evaluates against the real
human event log.

Confirm the generated files:

```bash
ls data/matchbook.db
ls data/policies
```

Then open the holes:

```bash
git apply homework/module-1/hw1-holes.patch
```

Read [README.md](README.md) in this directory for what that does and how to undo
it. In short: it empties five function bodies, leaves every signature and
docstring, and turns `tests/test_tools.py` red on purpose.

The starter contains the following components:

- `data/matchbook.db` — the world. Regenerated identically by `seed.generate`.
- `agent/cli.py` — runs a session and writes spans locally.
- `agent/auth.py` — the access matrix, including the stateful duty rule.
- `agent/agent.py` — the system prompt, the session loop, and the `_call`
  dispatch seam.
- `agent/tools.py` — the five tools you are about to write.
- `seed/controls.py` — the control oracle: match, tolerance, approval, duties.
- `tests/test_hw_holes.py` — one contract test per unfinished tool.

The agent has access only to the functions registered in `TOOLS` in
`agent/tools.py`. It has no filesystem, shell, web or search tool. A model
instruction is not an access control mechanism, so every tool checks the
authenticated caller itself.

Before editing code, read:

- `SPEC.md`, which defines the agent's scope, permissions, tool contracts,
  check order and escalation rules.
- `agent/auth.py`, which implements authorization by role **and by case
  history**.
- `seed/controls.py`, which computes every control decision.
- The docstring of each function marked for Homework 1.

The supplied data layer in `agent/db.py` provides everything the tools need:

- `get_purchase_item` uses `get_item`, `items_of_order`, `receipt_total_cents`,
  `any_invoice` and `to_public_item`.
- `get_three_way_match` uses the same lookups and then
  `seed.controls.three_way_match`. It must not compute anything itself.
- `get_policy` reads the rendered corpus from `policies_dir()`.
- `record_goods_receipt` uses `case_history` for the duty check and
  `insert_goods_receipt` to write.
- `clear_invoice` uses `open_invoice`, `case_history`, `clear_invoice_row` and
  `queue_for_approval`.

Use `with db.connection() as conn:` for database access; it closes the
connection on every path, including early returns and errors:

```python
with db.connection() as conn:
    item = db.get_item(conn, item_key)
```

The supplied write helpers commit their own changes and emit the matching row
into `case_events`, which is the world's business history. Do not write a second
data layer, and do not emit case events by hand: `case_events` is what the
per-case duty rule reads, and an activity recorded twice would make a caller
look like they had acted when they had not.

`SPEC.md` is a design document; **the running application never loads it.** The
starter translates it into three kinds of implementation, and the distinction is
what Part C turns on:

- `SYSTEM_PROMPT_TEMPLATE` in `agent/agent.py` carries what the *model* must
  decide: scope, refusals, tool choice, citation, escalation.
- `agent/auth.py` and the tool functions enforce permissions, because
  authorization cannot depend on whether the model follows an instruction.
- `facts.yaml`, `seed/controls.py` and the tools enforce every number.

## Part A, implement the five tools

Implement the following functions in `agent/tools.py`:

- `get_purchase_item`
- `get_three_way_match`
- `get_policy`
- `record_goods_receipt`
- `clear_invoice`

Follow the contract in each docstring, including the return schema and the error
behaviour. Expected failures return `{"ok": False, "error": ..., "reason": ...}`
with a machine-readable error and a human-readable reason; unexpected failures
should raise.

Three things in this part are harder than they look.

**The match is computed in code, never by you and never by the model.**
`get_three_way_match` assembles the three values and calls
`seed.controls.three_way_match`. SPEC TOOL-7 makes a prose number a failure even
when it is arithmetically right, which is a rule about *provenance*, not about
accuracy.

**Authorization is stateful.** SPEC AUTH-3 says no single actor may perform both
halves of a conflicting pair from `facts.yaml` on the same purchase-order item.
Two callers with identical roles, company codes and purchasing groups can
therefore get different answers, because the rule reads what each has already
done on that case. `agent/auth.py:sod_conflict` and `db.case_history` are the
pieces; the test that pins it is
`test_hw1_record_goods_receipt_respects_per_case_duties`.

**`clear_invoice`'s nine checks are ordered, and the order is specified.** Read
SPEC TOOL-6 before writing it. Checks 1–6 *refuse*; checks 7–9 *queue for a
controller*. The amount is checked last, after every control — deliberately, and
the specification explains why: checking it earlier would queue an above-limit
invoice that is also payment-blocked, handing a controller a decision nobody can
make. Refusing first means human attention goes only to genuine judgement calls.

Run the focused tests while you work:

```bash
uv run pytest --runxfail tests/test_hw_holes.py -k hw1
```

`--runxfail` makes an unfinished function fail instead of appearing as an
expected failure. Before you implement a function its test reports
`NotImplementedError`. After all five are correct, every selected test passes.

Then run the supplied suites for the tools, the access matrix and the oracle:

```bash
uv run pytest tests/test_tools.py tests/test_auth.py tests/test_controls.py
uv run pytest
```

During Part B you will notice things the agent cannot do because no tool exists.
Add tools of your own to fill those gaps. The repo already anticipates several —
`bridge/activity_map.yaml` names them and `agent/auth.py` has the permission
predicates, with no tool reaching them yet:

- `record_invoice_receipt` (an AP clerk capability, AUTH-1)
- `set_payment_block` / `remove_payment_block`
- `get_case_history`, so the agent can see what has already happened on a case
- `list_open_items` within the caller's scope
- `decide_approval`, the controller's side of the approval gate

To add a tool, write the function in `agent/tools.py` with a clear docstring —
the docstring becomes the tool description the model sees — then register it in
`TOOLS`, and in `READ_TOOLS` or `WRITE_TOOLS`. A write tool must call
`kill_switch()` first; `tests/test_tools.py` has a test that fails if any write
tool is unguarded. If your tool records a business activity, add it to
`bridge/activity_map.yaml` so the mined log can see it.

## Part B, examine the agent through recorded conversations

Run at least 10 conversations with the completed agent. Use all three roles, and
save one record for each.

Include every case in the following list:

- An AP clerk asking why the invoice on `4507001234_00010` is still unpaid.
- A clearing attempt on `4507009999_00010`, which is deleted but still carries
  an invoice.
- A clearing attempt on `4507003300_00010`, whose amount is above the automatic
  approval limit.
- A request from `ap-004` about `4507001234_00010`, which belongs to another
  company code.
- A question whose answer depends on a number in the policy corpus.
- A request outside the agent's stated scope.

The five pinned items are documented in `seed/generate.py:pinned_items`, each
with the decision it was built to exercise. Use `ap-003` (AP clerk, MIS-01) for
the clearing and lookup cases, `ap-004` (AP clerk, MIS-02) for the
out-of-scope case, `buy-001` and `buy-004` (buyers, MIS-01, PG-10) for goods
receipts, and `ctl-001` (controller, MIS-01) when you want the controller role.

Add four more conversations after reading `SPEC.md`. Here is the first to add:
as `ap-003`, ask about *"purchase order 4507001234"* without naming an item.
Determine the expected behaviour from `SPEC.md`, then compare it with what the
agent does. Design the other three yourself, including the actor and request.

Run one request like this:

```bash
uv run python -m agent --actor ap-003 \
  --message "Why is the invoice on 4507001234_00010 still unpaid?" \
  --model claude-sonnet-5 --debug
```

`--debug` prints each tool call's name, arguments and result, and the run also
prints `activities recorded` — the business activities the run contributed to
the event log. You need both to fill in a record. This works without Langfuse or
the Homework 2 tracing setup.

A goods receipt or a clearing changes the world. To test another conversation
against the original state, re-seed first:

```bash
uv run python -m agent --actor ... --message "..." --model claude-sonnet-5 --reset
```

Re-seeding **is** the sandbox reset. Keep the world fixed throughout a
conversation you are recording.

Write each conversation as one line of `hw1-session.jsonl`. Each record must
contain:

- `actor_id`, the authenticated actor.
- `role`, that actor's role as stored in the world.
- `company_code` and `purchasing_group` (`null` for an AP clerk or controller).
- `item_key`, the item the request concerns, or `null`.
- `request`, the user's request.
- `tool_calls`, a list of each tool name, its arguments and its result. Use an
  empty list when the agent called no tools.
- `response`, the agent's final reply.
- `activities_recorded`, the business activities the run contributed to the
  event log. Often `[]`, and that is the interesting part.
- `expected`, the behaviour implied by the specification.
- `requirement`, the identifier of the relevant requirement in `SPEC.md`, or
  `null` when no single requirement applies.
- `met_requirement`, `true` or `false`.
- `problem_source`, one of `prompt`, `tool`, `specification`, or `null`.

Use `prompt` when the tools returned the right information but the model decided
badly. Use `tool` when a function returned the wrong information or changed the
wrong state. Use `specification` when `SPEC.md` does not say what correct
behaviour would be.

For example:

```json
{"actor_id":"ap-004","role":"ap_clerk","company_code":"MIS-02","purchasing_group":null,"item_key":"4507001234_00010","request":"Show me the match for 4507001234_00010.","tool_calls":[{"name":"get_purchase_item","arguments":{"item_key":"4507001234_00010"},"result":{"ok":false,"error":"permission_denied"}}],"response":"That item is not in your company code, so I cannot show it.","activities_recorded":[],"expected":"The agent must refuse and must not reveal the item's state.","requirement":"AUTH-1","met_requirement":true,"problem_source":null}
```

`activities_recorded` is the one field Oakline's version of this assignment
has no equivalent for, and it is the reason this repository exists. A reply is
not evidence. A run that says *"I have cleared the invoice"* while contributing
no `Clear Invoice` activity has told you two different things, and only one of
them is checkable.

## Part C, identify and test a missing model instruction

Compare `SPEC.md` with `SYSTEM_PROMPT_TEMPLATE` in `agent/agent.py`. Work
through the requirement families in order — `SCOPE`, `ESC`, `RESP` — and ask of
each whether the prompt carries it, carries it vaguely, or omits it. Ignore
`AUTH` and the control rules: those belong in code, and a prompt that repeated
them would not enforce them.

Identify one requirement that is absent or expressed too vaguely, then design a
conversation that tests whether the omission changes the agent's behaviour. A
Part B conversation may serve as the test if it examines that requirement.

If the agent fails the test while the tools behave correctly, add the smallest
instruction that addresses the observed failure. Run the same conversation again
and save the response before and after.

If the agent already satisfies the requirement, test another candidate. **Do not
revise the prompt unless a recorded conversation provides evidence for the
revision.** A model often satisfies a requirement that the prompt never states,
and "the prompt does not mention it" is not a finding — a failed conversation
is. If none of your conversations reveals a prompt-caused failure, say which
omissions you tested and why no edit was justified. That is a complete answer.

Note that editing the prompt changes `prompt_version()`, a hash of the template.
Homework 2 uses that hash to group traces by the prompt that produced them.

## Files to commit

- `agent/tools.py`
- `agent/agent.py`, if you revised the system prompt
- `hw1-session.jsonl`, containing at least 10 conversations

## Video

Record one continuous screen video of no more than 5 minutes. In the recording:

- Show one authorized request that succeeds.
- Show one permission denial.
- Show how the agent handles the clearing on `4507003300_00010`, including
  whether it reports the result as queued or as paid.
- Explain the requirement you examined in Part C and how you tested it. If you
  revised the prompt, show the failure and the exact edit. If you did not, explain
  why the recorded evidence did not justify one.
- Run at least one test.
- Regenerate the number of records in `hw1-session.jsonl`.

The purpose of the recording is to connect your explanation to the committed
artifacts. It is not a polished presentation.
