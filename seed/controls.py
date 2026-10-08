"""The purchase-to-pay control oracle, as pure functions.

This module is the ground truth. The world generator calls it to stamp the
match state on every item, the agent's tools call it before writing anything,
`process/rules.py` asserts the same arithmetic over an event log, and the tests
call it as their oracle. It has no database, framework or model imports so that
every one of those callers can import it directly.

The rule the course states: the LLM writes prose, code computes facts. A
variance the model calculated in a sentence is a failure mode, not an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

# Match decisions, in the order a reviewer cares about them.
MATCH = "match"
MISSING_GR = "missing_gr"
NO_INVOICE = "no_invoice"
PRICE_VARIANCE = "price_variance"
QUANTITY_VARIANCE = "quantity_variance"
ITEM_DELETED = "item_deleted"


def tolerance_cents(matched_value_cents: int, *, tolerance_abs_eur: float, tolerance_pct: float) -> int:
    """The permitted overage: the looser of the absolute and percentage limits.

    `facts.yaml tolerance_rule: looser_of_abs_or_pct`. "Looser" matters at both
    ends -- a percentage alone would reject trivial rounding on a small item,
    and an absolute alone would wave through a large one.
    """
    return int(round(max(tolerance_abs_eur * 100, matched_value_cents * tolerance_pct / 100)))


def requires_goods_receipt(flow: str, gr_required_flows: Iterable[str]) -> bool:
    """Whether this matching flow needs receipt evidence before clearing.

    Flow strings are BPI 2019's own `Item Category` values. A 2-way-match or
    consignment item legitimately never has a goods receipt, and treating that
    as a violation is the single easiest way to produce a worthless report.
    """
    return flow in set(gr_required_flows)


@dataclass(frozen=True)
class MatchResult:
    """What a three-way match found. The tool returns this; the model reads it."""

    decision: str
    within_tolerance: bool
    po_value_cents: int
    receipt_value_cents: int | None
    invoice_value_cents: int | None
    variance_cents: int
    tolerance_cents: int
    blocking_reasons: tuple[str, ...]

    @property
    def variance_pct(self) -> float:
        if not self.receipt_value_cents:
            return 0.0
        return 100.0 * self.variance_cents / self.receipt_value_cents

    def as_result(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "within_tolerance": self.within_tolerance,
            "po_value_eur": self.po_value_cents / 100,
            "receipt_value_eur": None
            if self.receipt_value_cents is None
            else self.receipt_value_cents / 100,
            "invoice_value_eur": None
            if self.invoice_value_cents is None
            else self.invoice_value_cents / 100,
            "variance_eur": self.variance_cents / 100,
            "variance_pct": round(self.variance_pct, 3),
            "tolerance_eur": self.tolerance_cents / 100,
            "blocking_reasons": list(self.blocking_reasons),
        }


def three_way_match(
    *,
    flow: str,
    po_value_cents: int,
    receipt_value_cents: int | None,
    invoice_value_cents: int | None,
    item_deleted: bool,
    payment_blocked: bool,
    facts: dict[str, Any],
) -> MatchResult:
    """Evaluate the match for one purchase-order item.

    The comparison is invoice against receipt where receipt evidence is
    required, and invoice against the purchase order where it is not -- which
    is exactly what distinguishes a three-way from a two-way match, and is why
    the flow has to be read before any arithmetic happens.
    """
    needs_receipt = requires_goods_receipt(flow, facts["gr_required_flows"])
    baseline = receipt_value_cents if needs_receipt else po_value_cents
    allowance = tolerance_cents(
        baseline or po_value_cents,
        tolerance_abs_eur=facts["tolerance_abs_eur"],
        tolerance_pct=facts["tolerance_pct"],
    )

    reasons: list[str] = []
    if item_deleted:
        reasons.append("the purchase-order item is deleted")
    if invoice_value_cents is None:
        reasons.append("no invoice has been recorded")
    if needs_receipt and receipt_value_cents is None:
        reasons.append(f"no goods receipt, and {flow} requires one before clearing")
    if payment_blocked:
        reasons.append("a payment block is set")

    variance = 0
    if invoice_value_cents is not None and baseline:
        variance = invoice_value_cents - baseline

    if item_deleted:
        decision = ITEM_DELETED
    elif invoice_value_cents is None:
        decision = NO_INVOICE
    elif needs_receipt and receipt_value_cents is None:
        decision = MISSING_GR
    elif variance > allowance:
        decision = PRICE_VARIANCE
        reasons.append(
            f"invoice exceeds the matched value by {variance / 100:,.2f}, "
            f"outside the {allowance / 100:,.2f} allowance"
        )
    else:
        decision = MATCH

    within = decision == MATCH and variance <= allowance
    return MatchResult(
        decision=decision,
        within_tolerance=within,
        po_value_cents=po_value_cents,
        receipt_value_cents=receipt_value_cents,
        invoice_value_cents=invoice_value_cents,
        variance_cents=variance,
        tolerance_cents=allowance,
        blocking_reasons=tuple(reasons),
    )


def clearing_needs_approval(amount_cents: int, facts: dict[str, Any]) -> bool:
    """True when a clearing must be queued for a controller.

    Strictly above the limit queues; a clearing of exactly the limit executes
    (`facts.yaml clearing_auto_approve_limit_eur`). The boundary is stated
    because an off-by-one here is a money bug.
    """
    return amount_cents > int(round(facts["clearing_auto_approve_limit_eur"] * 100))


def quantity_within_tolerance(
    ordered_quantity: float, received_quantity: float, facts: dict[str, Any]
) -> bool:
    if ordered_quantity <= 0:
        return False
    overage_pct = 100.0 * (received_quantity - ordered_quantity) / ordered_quantity
    return overage_pct <= facts["gr_quantity_tolerance_pct"]


def sod_conflict(
    *,
    actor_id: str,
    activity: str,
    history: Sequence[tuple[str, str]],
    facts: dict[str, Any],
) -> tuple[str, str] | None:
    """Would `actor_id` performing `activity` breach segregation of duties?

    `history` is the case's own past as (activity, actor) pairs. This is the
    stateful part of authorization: the same actor, the same action, the same
    item is permitted or denied depending on what that actor already did here.

    Returns the conflicting pair, or None.
    """
    rules = facts.get("segregation_of_duties") or {}
    if not rules.get("enforced"):
        return None
    for pair in rules.get("conflicting_pairs", []):
        first, second = pair[0], pair[1]
        if activity == second:
            other = first
        elif activity == first:
            other = second
        else:
            continue
        if any(past_activity == other and past_actor == actor_id for past_activity, past_actor in history):
            return (first, second)
    return None


def payment_due_date(invoice_date: date, facts: dict[str, Any]) -> date:
    from datetime import timedelta

    return invoice_date + timedelta(days=int(facts["payment_terms_days"]))


def discount_deadline(invoice_date: date, facts: dict[str, Any]) -> date:
    from datetime import timedelta

    return invoice_date + timedelta(days=int(facts["early_payment_discount_days"]))


def is_duplicate_invoice(
    *,
    vendor_id: str,
    value_cents: int,
    invoice_date: date,
    existing: Iterable[tuple[str, int, date]],
    facts: dict[str, Any],
) -> bool:
    """A same-vendor, same-value invoice inside the lookback window."""
    window = int(facts["duplicate_invoice_window_days"])
    allowance = int(round(facts["duplicate_invoice_value_tolerance_eur"] * 100))
    for other_vendor, other_value, other_date in existing:
        if other_vendor != vendor_id:
            continue
        if abs(other_value - value_cents) > allowance:
            continue
        if abs((invoice_date - other_date).days) <= window:
            return True
    return False
