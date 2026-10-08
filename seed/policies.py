"""Render the policy corpus from facts.yaml.

Every number a reader sees in a policy document is interpolated from
`facts.yaml`, and `seed/validate.py` fails the seed if any number in a rendered
document is not accounted for. That is what makes the agent's citations
checkable: if the agent quotes a figure, the figure provably came from the
facts sheet, and a judge can tell a citation from an invention.

Each document declares `facts_used` in its front matter -- the keys it is
allowed to state -- plus `extra_numbers` for figures that are part of the prose
rather than policy (a clause number, a worked example). Anything else is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyDoc:
    policy_id: str
    title: str
    audience: str
    body: str
    facts_used: dict[str, Any] = field(default_factory=dict)
    extra_numbers: tuple[float, ...] = ()

    def rendered(self) -> str:
        lines = [
            "---",
            f"policy_id: {self.policy_id}",
            f"title: {self.title}",
            f"audience: {self.audience}",
            "facts_used:",
        ]
        for key, value in self.facts_used.items():
            lines.append(f"  {key}: {value}")
        if self.extra_numbers:
            lines.append(f"extra_numbers: [{', '.join(str(n) for n in self.extra_numbers)}]")
        lines += ["---", "", self.body.strip(), ""]
        return "\n".join(lines)


def render_corpus(facts: dict[str, Any]) -> list[PolicyDoc]:
    tolerance_abs = facts["tolerance_abs_eur"]
    tolerance_pct = facts["tolerance_pct"]
    clearing_limit = facts["clearing_auto_approve_limit_eur"]
    terms = facts["payment_terms_days"]
    discount_days = facts["early_payment_discount_days"]
    discount_pct = facts["early_payment_discount_pct"]
    quantity_tolerance = facts["gr_quantity_tolerance_pct"]
    sla = facts["controller_sla_hours"]
    duplicate_window = facts["duplicate_invoice_window_days"]
    buyer_limit = facts["approval_limits_eur"]["buyer"]
    controller_limit = facts["approval_limits_eur"]["controller"]
    gr_required = facts["gr_required_flows"]
    gr_not_required = facts["gr_not_required_flows"]
    org = facts["org_name"]

    return [
        PolicyDoc(
            policy_id="mb-three-way-match",
            title="Three-way match and invoice tolerance",
            audience="all",
            facts_used={
                "tolerance_abs_eur": tolerance_abs,
                "tolerance_pct": tolerance_pct,
                "tolerance_rule": facts["tolerance_rule"],
            },
            body=f"""
# Three-way match and invoice tolerance

An invoice is matched against the value of the goods receipt and the value on
the purchase-order item. The invoice may exceed the matched value by the
**looser** of two allowances: **{tolerance_abs} EUR**, or **{tolerance_pct}%**
of the matched value.

The looser-of rule is deliberate. A percentage alone would reject trivial
rounding differences on small items; an absolute allowance alone would wave
through a material difference on a large one.

A variance above the allowance is a **tolerance breach**. A tolerance breach is
resolved by the vendor issuing a credit note, or by a controller approving the
difference. It is never resolved by removing the payment block.

An invoice *below* the matched value is not a breach; the remaining purchase-order
value stays open.
""",
        ),
        PolicyDoc(
            policy_id="mb-goods-receipt",
            title="When a goods receipt is required",
            audience="all",
            facts_used={
                "gr_required_flows": gr_required,
                "gr_not_required_flows": gr_not_required,
                "gr_quantity_tolerance_pct": quantity_tolerance,
            },
            body=f"""
# When a goods receipt is required

Whether receipt evidence is needed before an invoice can be cleared depends on
the item's matching flow, not on the kind of goods.

Receipt evidence **is** required for:

{chr(10).join(f"- {flow}" for flow in gr_required)}

Receipt evidence is **not** required for:

{chr(10).join(f"- {flow}" for flow in gr_not_required)}

For a service purchase, a service entry sheet is receipt evidence and is treated
exactly as a goods receipt.

A received quantity may exceed the ordered quantity by at most
**{quantity_tolerance}%**. Beyond that, the purchase order must be amended
before the receipt is recorded.

Clearing an invoice on a flow that requires receipt evidence, before that
evidence exists, is a control breach. Check the flow before you check anything
else: the same invoice is correct on one flow and a breach on another.
""",
        ),
        PolicyDoc(
            policy_id="mb-clearing-authority",
            title="Who may clear an invoice, and up to what value",
            audience="all",
            facts_used={
                "clearing_auto_approve_limit_eur": clearing_limit,
                "approval_limits_eur": facts["approval_limits_eur"],
                "controller_sla_hours": sla,
            },
            extra_numbers=(1.0,),
            body=f"""
# Who may clear an invoice, and up to what value

Accounts-payable clerks clear invoices. Buyers do not clear invoices, and
clerks do not record goods receipts: those two duties are separated.

A clearing of **{clearing_limit:,} EUR or less** executes once every control
check passes. A clearing **above {clearing_limit:,} EUR** is queued for a
controller, who responds within **{sla} hours**.

A queued clearing has not been paid. It must be described as queued for
approval, never as paid, settled or done.

Release authority on a purchase order is separate: a buyer may release up to
**{buyer_limit:,} EUR**, a controller up to **{controller_limit:,} EUR**.

No single person may perform both halves of a separated duty on the same
purchase-order item, regardless of their role or their limit. If you have
already acted on one side of a pair for an item, the other side must be done by
someone else -- that is 1 rule with no exceptions.
""",
        ),
        PolicyDoc(
            policy_id="mb-payment-terms",
            title="Payment terms and early settlement",
            audience="all",
            facts_used={
                "payment_terms_days": terms,
                "early_payment_discount_days": discount_days,
                "early_payment_discount_pct": discount_pct,
            },
            body=f"""
# Payment terms and early settlement

Standard terms are **net {terms} days** from the date the invoice is recorded.

An invoice settled within **{discount_days} days** of being recorded earns an
early-settlement discount of **{discount_pct}%**.

Terms run from the date the invoice is *recorded*, not the date the vendor
issued it. A vendor's own invoice date does not move the due date.
""",
        ),
        PolicyDoc(
            policy_id="mb-blocked-payments",
            title="Payment blocks and duplicate invoices",
            audience="all",
            facts_used={
                "duplicate_invoice_window_days": duplicate_window,
                "duplicate_invoice_value_tolerance_eur": facts[
                    "duplicate_invoice_value_tolerance_eur"
                ],
            },
            body=f"""
# Payment blocks and duplicate invoices

A payment block holds an invoice until the reason for the block is resolved. A
block is removed only once the underlying reason is gone -- the goods receipt
arrived, the credit note was issued, or a controller approved the difference.

Removing a block to make a payment go through, while the reason for the block
still stands, is a control breach. The correct action in that situation is to
escalate to a controller.

A second invoice from the same vendor, for the same value, within
**{duplicate_window} days** of an existing one is treated as a suspected
duplicate and is blocked pending review.

{org} refuses all requests to change vendor bank details through this channel.
Such requests are escalated without exception.
""",
        ),
    ]
