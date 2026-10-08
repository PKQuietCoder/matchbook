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

def no_clearing_before_receipt(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-GR: on a GR-required flow, nothing clears before receipt evidence.

    The control that actually stops money leaving for goods that never arrived.
    Only flows listed in facts.yaml `gr_required_flows` are in scope -- a
    2-way-match or consignment item legitimately has no goods receipt, and
    flagging those would make the rule noise.
    """
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


def three_way_match_tolerance(facts: CaseFacts, policy: dict[str, Any]) -> Iterator[Violation]:
    """CTRL-TOLERANCE: the cleared value must match the receipt value.

    BPI 2019 records a running `Cumulative net worth`, so the comparison is
    between the value at receipt and the value at clearing. The allowance is
    the looser of the absolute and percentage limits in facts.yaml -- the same
    arithmetic the agent's `get_three_way_match` tool must use.

    Note the honest limit: these values are anonymized by a linear translation,
    so the *scale* is not real currency. The rule is still meaningful because
    it compares two values from the same translated scale.
    """
    if facts.flow not in policy["gr_required_flows"]:
        return
    clearing = facts.first_index(CLEAR_INVOICE)
    receipt = facts.first_index(*RECEIPT_ACTIVITIES)
    if clearing is None or receipt is None:
        return
    receipt_value = facts.values[receipt]
    cleared_value = facts.values[clearing]
    if receipt_value <= 0:
        return
    variance = cleared_value - receipt_value
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
            at_seq=clearing,
            order_assumed=facts.tie_broken,
            detail={
                "receipt_value_eur": receipt_value / 100,
                "cleared_value_eur": cleared_value / 100,
                "variance_eur": variance / 100,
                "allowance_eur": allowance / 100,
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
    "CTRL-TOLERANCE": three_way_match_tolerance,
    "CTRL-SOD": segregation_of_duties,
    "CTRL-BLOCK": payment_block_discipline,
    "CTRL-DELETED": no_activity_after_deletion,
}

RULE_TITLES = {
    "CTRL-GR": "No clearing before goods receipt on a GR-required flow",
    "CTRL-TOLERANCE": "Cleared value within the three-way-match tolerance",
    "CTRL-SOD": "Segregation of duties across conflicting activity pairs",
    "CTRL-BLOCK": "A removed payment block was set first",
    "CTRL-DELETED": "No financial activity after item deletion",
}


def evaluate(
    log: EventLog,
    policy: dict[str, Any],
    *,
    rule_ids: Iterable[str] | None = None,
) -> list[Violation]:
    """Run the selected rules over every case."""
    selected = list(rule_ids) if rule_ids else list(RULES)
    unknown = [rule_id for rule_id in selected if rule_id not in RULES]
    if unknown:
        raise KeyError(f"unknown rule(s): {', '.join(unknown)}; known: {', '.join(RULES)}")
    found: list[Violation] = []
    for trace in log.traces():
        facts = case_facts(log, trace)
        for rule_id in selected:
            found.extend(RULES[rule_id](facts, policy))
    return found


def report(log: EventLog, policy: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Violation counts per rule, with the in-scope denominator for each.

    A bare count is not interpretable: CTRL-GR only applies to GR-required
    flows, so "412 violations" means nothing without "out of 1,877 in-scope
    cases". The scope is reported with the rate.
    """
    violations = evaluate(log, policy, **kwargs)
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
            "Monetary values in BPI 2019 are anonymized by a linear translation, so the "
            "scale is not real currency. The comparison is still valid because both "
            "values come from the same translated scale."
        ),
    }
    per_rule: dict[str, Any] = {}
    for rule_id in RULES:
        matching = [violation for violation in violations if violation.rule_id == rule_id]
        cases = {violation.case_id for violation in matching}
        assumed = {violation.case_id for violation in matching if violation.order_assumed}
        denominator = scope.get(rule_id, log.case_count) or 1
        per_rule[rule_id] = {
            "title": RULE_TITLES[rule_id],
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
    }
