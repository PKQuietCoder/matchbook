"""Control rules, each asserted against the case built to trigger it."""

from __future__ import annotations

import pytest

from process import config, rules


@pytest.fixture()
def policy():
    return config.load_facts()


def _cases(found, rule_id):
    return {v.case_id for v in found if v.rule_id == rule_id}


def _run(log, policy, **kwargs):
    """evaluate() returns (violations, inapplicable); most tests want the first."""
    violations, _inapplicable = rules.evaluate(log, policy, **kwargs)
    return violations


def test_clearing_before_receipt_is_caught(tiny_log, policy):
    found = _run(tiny_log, policy, rule_ids=["CTRL-GR"])
    assert _cases(found, "CTRL-GR") == {"c2"}


def test_two_way_match_items_are_out_of_scope(tiny_log, policy):
    """c5 clears with no goods receipt and that is correct for its flow."""
    found = _run(tiny_log, policy, rule_ids=["CTRL-GR"])
    assert "c5" not in _cases(found, "CTRL-GR")


def test_segregation_of_duties_is_caught(tiny_log, policy):
    found = _run(tiny_log, policy, rule_ids=["CTRL-SOD"])
    assert _cases(found, "CTRL-SOD") == {"c3"}
    violation = next(v for v in found if v.case_id == "c3")
    assert violation.detail["resource"] == "user_9"


def test_unexplained_unblocking_is_caught(tiny_log, policy):
    found = _run(tiny_log, policy, rule_ids=["CTRL-BLOCK"])
    assert _cases(found, "CTRL-BLOCK") == {"c4"}


def test_clean_case_triggers_nothing(tiny_log, policy):
    found = _run(tiny_log, policy)
    assert "c1" not in {v.case_id for v in found}


def test_unknown_rule_is_rejected(tiny_log, policy):
    with pytest.raises(KeyError):
        rules.evaluate(tiny_log, policy, rule_ids=["CTRL-NOPE"])


def test_tolerance_rule_refuses_to_report_a_vacuous_zero(snapshot_log, policy):
    """The correction to a real mistake.

    This rule was first written to subtract the value at the goods receipt from
    the value at clearing, and it reported zero violations on BPI 2019, which
    was read as a measured result. It was not: the log carries one case-level
    value repeated on every event, so the subtraction is structurally zero.
    The rule must now decline to report rather than report a clean zero.
    """
    report = rules.report(snapshot_log, policy)
    assert "CTRL-TOLERANCE" in report["rules_not_evaluated"]
    entry = report["rules"]["CTRL-TOLERANCE"]
    assert entry["evaluated"] is False
    assert "violations" not in entry
    assert "single value per case" in entry["not_applicable_because"]


def test_tolerance_rule_fires_where_the_values_are_distinct(tiny_log, policy):
    """And it is not merely disabled: given real inputs, it works."""
    from process.log import EventLogBuilder
    from tests.conftest import moment

    builder = EventLogBuilder("distinct")
    builder.add_case_attributes("d1", {"Item Category": "3-way match, invoice after GR"})
    for day, activity, value in [
        (1, "Create Purchase Order Item", 400_000),
        (2, "Record Goods Receipt", 400_000),
        (3, "Record Invoice Receipt", 412_000),
        (4, "Clear Invoice", 412_000),
    ]:
        builder.add(case_id="d1", activity=activity, timestamp=moment(day), value_cents=value)
    log = builder.build()
    found, inapplicable = rules.evaluate(log, policy, rule_ids=["CTRL-TOLERANCE"])
    assert not inapplicable
    assert _cases(found, "CTRL-TOLERANCE") == {"d1"}
    # 4,120.00 invoiced against 4,000.00 received: 120.00 over, and the
    # allowance is max(50, 2% of 4,000) = 80.00.
    assert found[0].detail["variance_eur"] == 120.0
    assert found[0].detail["allowance_eur"] == 80.0
    assert found[0].detail["receipt_events"] == 1


def test_a_second_delivery_is_not_a_price_variance(policy):
    """The trap the first version would have fallen into.

    Two goods receipts legitimately total more than one. Comparing the first
    receipt against the clearing would report a partial delivery as a tolerance
    breach, so the comparison uses the receipt TOTAL.
    """
    from process.log import EventLogBuilder
    from tests.conftest import moment

    builder = EventLogBuilder("split")
    builder.add_case_attributes("s1", {"Item Category": "3-way match, invoice after GR"})
    for day, activity, value in [
        (1, "Create Purchase Order Item", 400_000),
        (2, "Record Goods Receipt", 200_000),
        (3, "Record Goods Receipt", 200_000),
        (4, "Record Invoice Receipt", 400_000),
        (5, "Clear Invoice", 400_000),
    ]:
        builder.add(case_id="s1", activity=activity, timestamp=moment(day), value_cents=value)
    found, inapplicable = rules.evaluate(builder.build(), policy, rule_ids=["CTRL-TOLERANCE"])
    assert not inapplicable
    assert found == []


def test_report_carries_scope_and_caveats(tiny_log, policy):
    report = rules.report(tiny_log, policy)
    # CTRL-GR applies only to GR-required flows: 4 of the 5 fixture cases.
    assert report["rules"]["CTRL-GR"]["in_scope_cases"] == 4
    assert report["rules"]["CTRL-SOD"]["in_scope_cases"] == 5
    assert "caveat" in report["rules"]["CTRL-BLOCK"]


def test_rules_run_on_real_data(snapshot_log, policy):
    report = rules.report(snapshot_log, policy)
    assert report["cases"] > 2000
    assert report["total_violations"] > 0
    assert report["rules"]["CTRL-GR"]["in_scope_cases"] > 1000


def test_tie_broken_order_is_disclosed_on_affected_violations(snapshot_log, policy):
    found = _run(snapshot_log, policy, rule_ids=["CTRL-BLOCK"])
    # Some real violations rest on the declared ordering; the flag must say so.
    assert any(v.order_assumed for v in found)
    # A duty conflict does not depend on order, so it is never flagged.
    sod = _run(snapshot_log, policy, rule_ids=["CTRL-SOD"])
    assert all(not v.order_assumed for v in sod)


def test_consignment_is_excluded_from_invoice_matching(policy):
    """Consignment has no PO-level invoice, so PO-level controls do not apply.

    facts.yaml first listed Consignment under `gr_not_required_flows`, which
    made the matching controls treat it as a 2-way match and compare values the
    process never intended to compare.
    """
    from process.log import EventLogBuilder
    from tests.conftest import moment

    builder = EventLogBuilder("consign")
    builder.add_case_attributes("k1", {"Item Category": "Consignment"})
    for day, activity, value in [
        (1, "Create Purchase Order Item", 100_000),
        (2, "Record Invoice Receipt", 150_000),
        (3, "Clear Invoice", 150_000),
    ]:
        builder.add(case_id="k1", activity=activity, timestamp=moment(day), value_cents=value)
    found, _ = rules.evaluate(builder.build(), policy)
    assert _cases(found, "CTRL-GR") == set()
    assert _cases(found, "CTRL-TOLERANCE") == set()
    assert "Consignment" in policy["invoice_matching_not_applicable_flows"]
