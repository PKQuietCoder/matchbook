# Matchbook accounts-payable agent: specification

The specification is the source of intended behaviour. **The application does
not read this file at runtime.** Developers translate its requirements into
model instructions, tool code, authorization checks, control rules and tests.
Scenario generation later uses the same requirement IDs to decide which
situations the agent must encounter, and conformance deviations cite them.

## How the specification enters the application

| Specification content | Implementation location | Reason |
| --- | --- | --- |
| Supported and refused requests | `SYSTEM_PROMPT_TEMPLATE` in `agent/agent.py` | The model must decide whether to answer, use a tool, or refuse. |
| Tool-choice guidance and policy citation | `SYSTEM_PROMPT_TEMPLATE` | The model chooses the next tool and writes the reply. |
| Role permissions | `agent/auth.py` and each tool | Authorization must hold even when the model decides badly. |
| Three-way match, tolerance, approval limits | `seed/controls.py`, `facts.yaml`, the tools | Deterministic code enforces the rule exactly. |
| Segregation of duties | `seed/controls.py` + `agent/auth.py` against the case history | The rule depends on the case's own past, so it cannot be a prompt. |
| Control conformance on any log | `process/rules.py` | One definition judges the humans and the agent alike. |
| Expected behaviour in evaluation | `scenarios/*.jsonl`, `eval_cases/` | A case cites the requirement or control it is judged by. |

The system prompt is therefore one implementation of *part* of the
specification. Copying the whole specification into the prompt would be
insufficient, because a prompt cannot enforce access control, compute a
tolerance, or know what the caller already did on this case.

## 1. Purpose

**PURPOSE-1.** The agent is Meridian Industrial Supply's accounts-payable
assistant. It helps buyers and AP clerks resolve purchase-to-pay exceptions:
why an invoice is blocked, whether a three-way match holds, what policy
permits, and what must go to a controller. It acts through tools, cites a
policy document for every policy claim, and escalates anything above its
authority.

## 2. Scope

**SCOPE-1.** The agent supports:

- Purchase-order, goods-receipt and invoice lookups within the caller's scope.
- Three-way-match explanations and blocked-payment diagnosis.
- Recording a goods receipt and clearing an invoice, within the control rules.
- Policy questions answered from the policy corpus.
- Escalation to a controller for anything above its authority.

**SCOPE-2.** The agent refuses:

- Changing vendor bank details or any payment-credential handling.
- Tax, legal or audit opinions.
- Anything outside Meridian's purchase-to-pay process.

## 3. Roles and permissions

**AUTH-1.** The harness enforces this matrix in the tool layer. The model never
sees rows outside the caller's role. **Authorization is not a prompt.**

| Capability | buyer | ap_clerk | controller |
| --- | --- | --- | --- |
| View items in own purchasing group | yes | — | any |
| View items in own company code | — | yes | any |
| Record goods receipt | yes | **no** | no |
| Record invoice receipt | no | yes | no |
| Clear invoice, at or below the limit | no | yes | no |
| Set / remove payment block | no | yes | no |
| Decide a queued clearing | no | no | yes |

**AUTH-2.** An AP clerk may not record a goods receipt, and a buyer may not
clear an invoice. This is segregation of duties at the role level.

**AUTH-3.** Segregation of duties is also enforced **per case**: no single
actor may perform both activities of a conflicting pair in `facts.yaml`
`segregation_of_duties.conflicting_pairs` on the same purchase-order item,
whatever their role. Authorization therefore depends on the case's own history,
which is the one rule in this specification that no prompt can implement.

## 4. Tools

Success returns `ok: true` plus result fields. Expected failures return
`ok: false`, a machine-readable `error`, and a human-readable `reason`.
Unexpected execution failures raise.

| ID | Tool | Inputs | Side effects | Risk |
| --- | --- | --- | --- | --- |
| TOOL-1 | `get_purchase_item` | item key | none | read |
| TOOL-2 | `get_three_way_match` | item key | none | read |
| TOOL-3 | `get_policy` | policy identifier | none | read |
| TOOL-4 | `record_goods_receipt` | item key, quantity, value, reference | creates a goods receipt | write |
| TOOL-5 | `clear_invoice` | item key, note | clears, or queues for approval | write |

### Success and failure contracts

| Tool | On success | On failure |
| --- | --- | --- |
| `get_purchase_item` | the authorized item: flow, values, receipt and invoice state, payment block, deletion state | `not_found`; `permission_denied` outside the caller's scope |
| `get_three_way_match` | `decision`, the three values, the variance, the tolerance it was measured against, `within_tolerance`, and `blocking_reasons` | `not_found`; `permission_denied` |
| `get_policy` | `policy_id`, `title`, `body` | `not_found` |
| `record_goods_receipt` | `gr_id`, `item_key`, `status: recorded` | `paused`; `permission_denied`; `not_found`; `not_eligible` for a deleted item or an unreleased order; `invalid_argument` for a quantity outside tolerance |
| `clear_invoice` | `clearing_id`, `amount_eur`, `status` of `cleared` or `queued_for_approval` | `paused`; `permission_denied`; `not_found`; `not_eligible` for a payment block, a missing goods receipt on a GR-required flow, a tolerance breach, a deleted item, or no invoice |

**TOOL-6.** `clear_invoice` evaluates its checks in a fixed order, and the
order is part of the specification: kill switch → authorization scope → amount
against the caller's limit → item not deleted → an unpaid invoice exists →
no payment block → three-way match within tolerance → per-case segregation of
duties. Nothing clears without passing all of them.

**TOOL-7.** The three-way match is computed by `seed/controls.py` and never by
the model. A numeric claim about a variance that is not preceded by a
`get_three_way_match` call is a failure, not a shortcut.

## 5. Escalation policy

- **ESC-1.** A clearing above `clearing_auto_approve_limit_eur` is queued for a
  controller; the tool queues it and the agent explains that it is queued, not paid.
- **ESC-2.** A tolerance breach goes to a controller. The agent must not remove a
  payment block to work around it.
- **ESC-3.** Vendor bank-detail or payment-credential requests are refused and escalated.
- **ESC-4.** Any case where the agent is unsure whether policy permits an action.

## 6. Other response requirements

- **RESP-1.** Cite the policy identifier for every claim taken from a policy document.
- **RESP-2.** Do not state that an action succeeded before the tool reports success.
- **RESP-3.** A queued clearing is reported as queued, never as paid.
- **RESP-4.** State when required information is missing or inconsistent rather than
  inventing a value.
- **RESP-5.** Explain refusals and escalations without revealing items outside the
  caller's scope.
- **RESP-6.** When several items of one purchase order could match the request, confirm
  which item before acting on any of them.

## 7. Control rules

The controls in `process/rules.py` apply to the agent's own behaviour, judged on
the agent's mined event log exactly as they are judged on the human log:
`CTRL-GR`, `CTRL-TOLERANCE`, `CTRL-SOD`, `CTRL-BLOCK`, `CTRL-DELETED`.
