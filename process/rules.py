"""Declarative control rules: conformance that a controller can read.

This is the cheap half of conformance and the half that earns its keep. A rule
is a pure function over one case's events and attributes, returning violations.
No model, no alignment, no noise threshold -- just "did this case break a
control we can name?"

The same functions do three jobs, which is the point of putting them here:

  1. they measure control violations in the real human log,
  2. they are the oracle the agent's write tools enforce, and
  3. they are the machine checks in evaluation cases.

Every threshold comes from facts.yaml, so a rule and the policy document a
reader is shown cannot drift apart.

A caution the reports must carry: a case whose relevant events were ordered by
the declared tie-break (facts.yaml `activity_rank`) rather than by recorded
time has a violation verdict that depends on that assumption. Each violation
reports `order_assumed`, and `CaseFacts.tie_broken` says whether the case was
affected at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from process.log import EventLog, Trace

# Activity names this module reasons about. They are BPI 2019's own spellings,
# which is what lets the agent's log be judged by the same rules -- the bridge
# lifts tool calls onto this alphabet.
CREATE_ITEM = "Create Purchase Order Item"
RELEASE_PO = "Release Purchase Order"
GOODS_RECEIPT = "Record Goods Receipt"
SERVICE_ENTRY = "Record Service Entry Sheet"
CANCEL_GR = "Cancel Goods Receipt"
INVOICE_RECEIPT = "Record Invoice Receipt"
SUBSEQUENT_INVOICE = "Record Subsequent Invoice"
CANCEL_INVOICE = "Cancel Invoice Receipt"
CLEAR_INVOICE = "Clear Invoice"
SET_BLOCK = "Set Payment Block"
REMOVE_BLOCK = "Remove Payment Block"
DELETE_ITEM = "Delete Purchase Order Item"
VENDOR_INVOICE = "Vendor creates invoice"
CHANGE_PRICE = "Change Price"
CHANGE_QUANTITY = "Change Quantity"

# A goods receipt may be evidenced by either a physical receipt or, for
# services, a service entry sheet. Treating only the former as receipt evidence
# would report a violation on every service purchase in the log.
RECEIPT_ACTIVITIES = (GOODS_RECEIPT, SERVICE_ENTRY)

FLOW_ATTRIBUTE = "Item Category"


class NotApplicable(Exception):
    """Raised by a rule that cannot be evaluated on the log it was given.

    This exists because of a mistake worth keeping a guard against. The
    value-match rule below was first written to compare the value recorded at
    the goods receipt against the value recorded at clearing, and it reported
    zero violations on BPI 2019 -- which was read as a finding. It was not. The
    log carries ONE case-level value replicated onto every event, so the
    subtraction is structurally zero and the rule could never fire.

    A rule that cannot fire must say so. Reporting "0 violations" for a rule
    whose inputs are absent is worse than reporting nothing, because a zero
    reads as evidence of compliance.
    """

    def __init__(self, rule_id: str, reason: str) -> None:
        super().__init__(f"{rule_id} is not applicable: {reason}")
        self.rule_id = rule_id
        self.reason = reason


@dataclass(frozen=True)
class Violation:
    rule_id: str
    case_id: str
    summary: str
    at_seq: int | None = None
    order_assumed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "case_id": self.case_id,
            "summary": self.summary,
            "at_seq": self.at_seq,
            "order_assumed": self.order_assumed,
            **{f"detail_{key}": value for key, value in self.detail.items()},
        }


@dataclass
class CaseFacts:
    """One case, pre-digested into what the rules need.

    Built once per case and shared across rules: every rule otherwise re-walks
    the same events, which at 1.6M events is the difference between seconds and
    minutes.
    """

    trace: Trace
    activities: tuple[str, ...]
    timestamps: tuple[int, ...]
    resources: tuple[str, ...]
    values: tuple[int, ...]
    attributes: dict[str, Any]
    tie_broken: bool

    @property
    def case_id(self) -> str:
        return self.trace.case_id

    @property
    def flow(self) -> str:
        return str(self.attributes.get(FLOW_ATTRIBUTE) or "")

    def first_index(self, *names: str) -> int | None:
        for index, activity in enumerate(self.activities):
            if activity in names:
                return index
        return None

    def indices(self, *names: str) -> list[int]:
        return [index for index, activity in enumerate(self.activities) if activity in names]

    def has(self, *names: str) -> bool:
        return self.first_index(*names) is not None

    def resource_at(self, index: int) -> str:
        return self.resources[index]

    @property
    def has_distinct_values(self) -> bool:
        """Does this case record more than one distinct value across its events?

        The probe that tells a value-comparing rule whether its inputs exist.
        A log with one value per case cannot support an invoice-to-receipt
        comparison, however the arithmetic is arranged.
        """
        return len({value for value in self.values if value}) > 1

    def receipt_total_cents(self) -> int | None:
        """Total value across receipt events, or None if there are none.

        A total rather than the first receipt, so a legitimate second delivery
        is not reported as a price variance.
        """
        indices = self.indices(*RECEIPT_ACTIVITIES)
        if not indices:
            return None
        return sum(self.values[index] for index in indices)

    def invoice_value_cents(self) -> int | None:
        indices = self.indices(INVOICE_RECEIPT)
        if not indices:
            return None
        return sum(self.values[index] for index in indices)


def case_facts(log: EventLog, trace: Trace) -> CaseFacts:
    return CaseFacts(
        trace=trace,
        activities=trace.activities,
        timestamps=trace.timestamps,
        resources=trace.resources,
        values=tuple(log.value_cents[trace.start : trace.end]),
        attributes=trace.attributes,
        tie_broken=any(position in log.tie_broken for position in trace.indices),
    )


# ---------------------------------------------------------------------------
# The rules.

def _invoice_matching_applies(facts: CaseFacts, policy: dict[str, Any]) -> bool:
    """Is this item's flow subject to PO-level invoice matching at all?

    Consignment is not: settlement happens outside the purchase order, so there
    is no PO-level invoice to match. Treating it as a flow with the goods
    receipt merely optional -- the first version of facts.yaml did -- makes
    every consignment item a candidate for controls that do not apply to it.
    """
    excluded = set(policy.get("invoice_matching_not_applicable_flows") or ())
    return facts.flow not in excluded


def no_clearing_before_receipt(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-GR: on a GR-required flow, nothing clears before receipt evidence.

    The control that actually stops money leaving for goods that never arrived.
    Only flows listed in facts.yaml `gr_required_flows` are in scope -- a
    2-way-match or consignment item legitimately has no goods receipt, and
    flagging those would make the rule noise.
    """
    if not _invoice_matching_applies(facts, policy):
        return
    if facts.flow not in policy["gr_required_flows"]:
        return
    clearing = facts.first_index(CLEAR_INVOICE)
    if clearing is None:
        return
    receipt = facts.first_index(*RECEIPT_ACTIVITIES)
    if receipt is None:
        yield Violation(
            rule_id="CTRL-GR",
            case_id=facts.case_id,
            summary=f"cleared an invoice on a {facts.flow} item with no goods receipt at all",
            at_seq=clearing,
            order_assumed=facts.tie_broken,
            detail={"flow": facts.flow},
        )
    elif receipt > clearing:
        yield Violation(
            rule_id="CTRL-GR",
            case_id=facts.case_id,
            summary="cleared an invoice before the goods receipt was recorded",
            at_seq=clearing,
            order_assumed=facts.tie_broken,
            detail={
                "flow": facts.flow,
                "clearing_position": clearing,
                "receipt_position": receipt,
                "same_minute": facts.timestamps[receipt] == facts.timestamps[clearing],
            },
        )


def value_match_tolerance(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-TOLERANCE: the cleared value must match the received value.

    Requires the log to record the receipt value and the invoice value as
    DISTINCT quantities. That is true of the synthetic and agent logs, where we
    control the instrumentation, and false of BPI 2019, which carries a single
    case-level value replicated onto every event. On such a log this raises
    :class:`NotApplicable` rather than returning nothing, because a silent zero
    would read as a clean bill of health.

    Where the values are distinct, the allowance is the looser of the absolute
    and percentage limits in facts.yaml -- the same arithmetic the agent's
    `get_three_way_match` tool uses, asserted identical by a test.

    A second trap this rule has to avoid: a case with more than one goods
    receipt legitimately carries a larger total at clearing than at the first
    receipt. Comparing the first receipt against the clearing would report a
    partial delivery as a price variance. The comparison therefore uses the
    receipt total across all uncancelled receipts.
    """
    if not _invoice_matching_applies(facts, policy):
        return
    if facts.flow not in policy["gr_required_flows"]:
        return
    receipt_value = facts.receipt_total_cents()
    invoice_value = facts.invoice_value_cents()
    if receipt_value is None or invoice_value is None:
        return
    if not facts.has_distinct_values:
        raise NotApplicable(
            "CTRL-TOLERANCE",
            "this log records a single value per case rather than separate goods-receipt "
            "and invoice values, so an invoice-to-receipt variance cannot be computed from "
            "it. BPI 2019 is such a log: its 'Cumulative net worth (EUR)' is a case-level "
            "figure repeated on every event. Evaluate this control on a log whose "
            "instrumentation records both values (the synthetic and agent logs do), or use "
            "the agent's get_three_way_match tool, which reads them from the world.",
        )

    variance = invoice_value - receipt_value
    if variance <= 0:
        return
    allowance = max(
        policy["tolerance_abs_eur"] * 100,
        receipt_value * policy["tolerance_pct"] / 100,
    )
    if variance > allowance:
        yield Violation(
            rule_id="CTRL-TOLERANCE",
            case_id=facts.case_id,
            summary=(
                f"cleared {variance / 100:,.2f} above the received value, outside the "
                f"{policy['tolerance_pct']}% / {policy['tolerance_abs_eur']} allowance"
            ),
            at_seq=facts.first_index(CLEAR_INVOICE),
            order_assumed=facts.tie_broken,
            detail={
                "receipt_value_eur": receipt_value / 100,
                "invoice_value_eur": invoice_value / 100,
                "variance_eur": variance / 100,
                "allowance_eur": allowance / 100,
                "receipt_events": len(facts.indices(*RECEIPT_ACTIVITIES)),
            },
        )


def segregation_of_duties(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-SOD: no resource performs both halves of a conflicting pair.

    The rule that makes authorization a process property. It is also the rule
    the agent is most likely to break, because within one conversation it is
    the single actor doing every step.
    """
    rules = policy.get("segregation_of_duties") or {}
    if not rules.get("enforced"):
        return
    for first, second in rules.get("conflicting_pairs", []):
        actors_first = {facts.resource_at(i) for i in facts.indices(first)}
        actors_second = {facts.resource_at(i) for i in facts.indices(second)}
        overlap = {actor for actor in actors_first & actors_second if actor}
        for actor in sorted(overlap):
            yield Violation(
                rule_id="CTRL-SOD",
                case_id=facts.case_id,
                summary=f"{actor} performed both '{first}' and '{second}' on this item",
                at_seq=facts.first_index(second),
                order_assumed=False,  # a duty conflict does not depend on order
                detail={"resource": actor, "pair": [first, second]},
            )


def payment_block_discipline(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-BLOCK: a removed payment block must have been set first.

    A block removed with nothing to remove is the signature of a workaround,
    and in the agent's log it is the mechanical form of escalation avoidance.

    Read the result carefully on the HUMAN log, though -- this rule is the
    repo's standing example of a control test that is really a data-quality
    test. BPI 2019 contains 57,137 `Remove Payment Block` events and only 124
    `Set Payment Block` events, so blocks are evidently set by an automatic
    mechanism that is never written to the log. On the real log this rule
    therefore measures **log completeness**, not misconduct: roughly a fifth of
    cases remove a block whose setting was not recorded.

    That makes it the best teaching rule in the catalogue -- the finding is
    real, the naive interpretation is wrong, and the correction is only
    available by looking at the activity census. On the agent's log, where we
    control the instrumentation and every block is written, the rule means what
    it says.
    """
    for index in facts.indices(REMOVE_BLOCK):
        set_before = [i for i in facts.indices(SET_BLOCK) if i < index]
        if not set_before:
            yield Violation(
                rule_id="CTRL-BLOCK",
                case_id=facts.case_id,
                summary="removed a payment block that was never recorded as set",
                at_seq=index,
                order_assumed=facts.tie_broken,
                detail={},
            )


def no_activity_after_deletion(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-DELETED: a deleted item must not keep transacting."""
    deletion = facts.first_index(DELETE_ITEM)
    if deletion is None:
        return
    financial = [
        index
        for index in facts.indices(CLEAR_INVOICE, INVOICE_RECEIPT, *RECEIPT_ACTIVITIES)
        if index > deletion
    ]
    if financial:
        yield Violation(
            rule_id="CTRL-DELETED",
            case_id=facts.case_id,
            summary=f"{len(financial)} financial event(s) recorded after the item was deleted",
            at_seq=financial[0],
            order_assumed=facts.tie_broken,
            detail={"after_deletion": [facts.activities[i] for i in financial[:5]]},
        )


RULES: dict[str, Callable[[CaseFacts, dict[str, Any]], Iterator[Violation]]] = {
    "CTRL-GR": no_clearing_before_receipt,
    "CTRL-TOLERANCE": value_match_tolerance,
    "CTRL-SOD": segregation_of_duties,
    "CTRL-BLOCK": payment_block_discipline,
    "CTRL-DELETED": no_activity_after_deletion,
}

RULE_TITLES = {
    "CTRL-GR": "No clearing before goods receipt on a GR-required flow",
    "CTRL-TOLERANCE": "Invoice value within the three-way-match tolerance of the receipt value",
    "CTRL-SOD": "Segregation of duties across conflicting activity pairs",
    "CTRL-BLOCK": "A removed payment block was set first",
    "CTRL-DELETED": "No financial activity after item deletion",
}


def evaluate(
    log: EventLog,
    policy: dict[str, Any],
    *,
    rule_ids: Iterable[str] | None = None,
) -> tuple[list[Violation], dict[str, str]]:
    """Run the selected rules over every case.

    Returns the violations found, and a map of rule id -> why that rule could
    not be evaluated on this log. A rule in the second map contributed no
    violations because it could not run, which is a different statement from
    "it ran and found none".
    """
    selected = list(rule_ids) if rule_ids else list(RULES)
    unknown = [rule_id for rule_id in selected if rule_id not in RULES]
    if unknown:
        raise KeyError(f"unknown rule(s): {', '.join(unknown)}; known: {', '.join(RULES)}")
    found: list[Violation] = []
    inapplicable: dict[str, str] = {}
    for trace in log.traces():
        facts = case_facts(log, trace)
        for rule_id in selected:
            if rule_id in inapplicable:
                continue
            try:
                found.extend(RULES[rule_id](facts, policy))
            except NotApplicable as exc:
                inapplicable[exc.rule_id] = exc.reason
    return found, inapplicable


def report(log: EventLog, policy: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Violation counts per rule, with the in-scope denominator for each.

    A bare count is not interpretable: CTRL-GR only applies to GR-required
    flows, so "412 violations" means nothing without "out of 1,877 in-scope
    cases". The scope is reported with the rate.
    """
    violations, inapplicable = evaluate(log, policy, **kwargs)
    gr_required = set(policy["gr_required_flows"])
    in_scope_gr = sum(
        1
        for case_id in log.case_ids
        if str(log.case_attributes.get(case_id, {}).get(FLOW_ATTRIBUTE) or "") in gr_required
    )
    scope = {
        "CTRL-GR": in_scope_gr,
        "CTRL-TOLERANCE": in_scope_gr,
        "CTRL-SOD": log.case_count,
        "CTRL-BLOCK": log.case_count,
        "CTRL-DELETED": log.case_count,
    }
    # Where a rule's result needs a caveat to be read correctly, the caveat
    # travels with the number instead of living in a paper somewhere.
    caveats = {
        "CTRL-BLOCK": (
            "On BPI 2019 this measures log completeness, not misconduct: the log holds "
            "57,137 'Remove Payment Block' events against 124 'Set Payment Block', so "
            "block-setting is not recorded. Interpret as 'unexplained unblocking' on the "
            "human log, and as a genuine control breach only where instrumentation is "
            "known to be complete (the synthetic and agent logs)."
        ),
        "CTRL-TOLERANCE": (
            "Where this rule does run, remember that monetary values in BPI 2019 are "
            "anonymised by a linear translation, so the scale is not real currency; a "
            "variance is meaningful relative to the same translated scale, an absolute "
            "EUR threshold is not."
        ),
    }
    per_rule: dict[str, Any] = {}
    for rule_id in RULES:
        if rule_id in inapplicable:
            # Never report a count for a rule that could not run. A zero here
            # would be read as compliance.
            per_rule[rule_id] = {
                "title": RULE_TITLES[rule_id],
                "evaluated": False,
                "not_applicable_because": inapplicable[rule_id],
            }
            continue
        matching = [violation for violation in violations if violation.rule_id == rule_id]
        cases = {violation.case_id for violation in matching}
        assumed = {violation.case_id for violation in matching if violation.order_assumed}
        denominator = scope.get(rule_id, log.case_count) or 1
        per_rule[rule_id] = {
            "title": RULE_TITLES[rule_id],
            "evaluated": True,
            "violations": len(matching),
            "cases": len(cases),
            "in_scope_cases": scope.get(rule_id, log.case_count),
            "case_rate": len(cases) / denominator,
            "cases_with_assumed_order": len(assumed),
            "examples": sorted(cases)[:3],
        }
        if rule_id in caveats:
            per_rule[rule_id]["caveat"] = caveats[rule_id]
    return {
        "log_id": log.log_id,
        "cases": log.case_count,
        "events": log.event_count,
        "rules": per_rule,
        "total_violations": len(violations),
        "rules_not_evaluated": sorted(inapplicable),
    }
