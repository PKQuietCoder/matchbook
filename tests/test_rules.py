"""Control rules, each asserted against the case built to trigger it."""

from __future__ import annotations

import pytest

from process import config, rules


@pytest.fixture()
def policy():
    return config.load_facts()


def _cases(found, rule_id):
    return {v.case_id for v in found if v.rule_id == rule_id}


def test_clearing_before_receipt_is_caught(tiny_log, policy):
    found = rules.evaluate(tiny_log, policy, rule_ids=["CTRL-GR"])
    assert _cases(found, "CTRL-GR") == {"c2"}


def test_two_way_match_items_are_out_of_scope(tiny_log, policy):
    """c5 clears with no goods receipt and that is correct for its flow."""
    found = rules.evaluate(tiny_log, policy, rule_ids=["CTRL-GR"])
    assert "c5" not in _cases(found, "CTRL-GR")


def test_segregation_of_duties_is_caught(tiny_log, policy):
    found = rules.evaluate(tiny_log, policy, rule_ids=["CTRL-SOD"])
    assert _cases(found, "CTRL-SOD") == {"c3"}
    violation = next(v for v in found if v.case_id == "c3")
    assert violation.detail["resource"] == "user_9"


def test_unexplained_unblocking_is_caught(tiny_log, policy):
    found = rules.evaluate(tiny_log, policy, rule_ids=["CTRL-BLOCK"])
    assert _cases(found, "CTRL-BLOCK") == {"c4"}


def test_clean_case_triggers_nothing(tiny_log, policy):
    found = rules.evaluate(tiny_log, policy)
    assert "c1" not in {v.case_id for v in found}


def test_unknown_rule_is_rejected(tiny_log, policy):
    with pytest.raises(KeyError):
        rules.evaluate(tiny_log, policy, rule_ids=["CTRL-NOPE"])


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
    found = rules.evaluate(snapshot_log, policy, rule_ids=["CTRL-BLOCK"])
    # Some real violations rest on the declared ordering; the flag must say so.
    assert any(v.order_assumed for v in found)
    # A duty conflict does not depend on order, so it is never flagged.
    sod = rules.evaluate(snapshot_log, policy, rule_ids=["CTRL-SOD"])
    assert all(not v.order_assumed for v in sod)
