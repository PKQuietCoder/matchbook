"""The five tools, against the seeded world and its pinned fixtures."""

from __future__ import annotations

import pytest

from agent import tools
from agent.auth import AuthContext

CLERK = AuthContext(actor_id="ap-003", role="ap_clerk", company_code="MIS-01")
BUYER_RELEASER = AuthContext(
    actor_id="buy-001", role="buyer", company_code="MIS-01", purchasing_group="PG-10"
)
BUYER_OTHER = AuthContext(
    actor_id="buy-004", role="buyer", company_code="MIS-01", purchasing_group="PG-10"
)

DEMO = "4507001234_00010"
TRAP = "4507001234_00020"
CLEAN = "4507002001_00010"
DELETED = "4507009999_00010"
LARGE = "4507003300_00010"


def test_match_is_computed_in_code_with_its_tolerance(world):
    result = tools.get_three_way_match(CLERK, DEMO)
    assert result["ok"]
    assert result["decision"] == "price_variance"
    assert result["variance_eur"] == 120.0
    # The looser-of rule: max(50, 2% of 4,000) = 80.
    assert result["tolerance_eur"] == 80.0
    assert result["within_tolerance"] is False
    assert result["policy_id"] == "mb-three-way-match"


def test_two_way_match_needs_no_goods_receipt(world):
    result = tools.get_three_way_match(CLERK, CLEAN)
    assert result["decision"] == "match"
    assert result["goods_receipt_required"] is False


def test_multi_item_order_exposes_its_siblings(world):
    """SPEC RESP-6 needs the agent to know the ambiguity exists."""
    result = tools.get_purchase_item(CLERK, DEMO)
    assert result["item"]["sibling_items"] == [TRAP]


def test_unknown_item_and_unknown_policy(world):
    assert tools.get_purchase_item(CLERK, "nope_00010")["error"] == "not_found"
    assert tools.get_policy(CLERK, "nope")["error"] == "not_found"
    assert tools.get_policy(CLERK, "")["error"] == "invalid_argument"


def test_out_of_scope_item_reveals_nothing(world):
    out_of_scope = AuthContext(
        actor_id="buy-003", role="buyer", company_code="MIS-02", purchasing_group="PG-30"
    )
    result = tools.get_purchase_item(out_of_scope, DEMO)
    assert result["error"] == "permission_denied"
    # SPEC RESP-5: the denial must not leak the item's state.
    assert "4,000" not in result["reason"] and "variance" not in result["reason"]


def test_blocked_item_is_not_cleared_and_says_why(world_copy):
    result = tools.clear_invoice(CLERK, DEMO)
    assert result["error"] == "not_eligible"
    assert "payment block" in result["reason"]
    assert "mb-blocked-payments" in result["reason"]


def test_deleted_item_is_never_cleared(world_copy):
    result = tools.clear_invoice(CLERK, DELETED)
    assert result["error"] == "not_eligible"
    assert "deleted" in result["reason"]


def test_clean_clearing_succeeds_once(world_copy):
    first = tools.clear_invoice(CLERK, CLEAN)
    assert first["ok"] and first["status"] == "cleared"
    # And the state change is real: a second attempt finds nothing open.
    second = tools.clear_invoice(CLERK, CLEAN)
    assert second["error"] == "not_eligible"


def test_above_limit_queues_and_does_not_pay(world_copy):
    result = tools.clear_invoice(CLERK, LARGE)
    assert result["ok"]
    assert result["status"] == "queued_for_approval"
    assert result["trigger"] == "above_limit"
    # SPEC RESP-3: the distinction the agent must not blur.
    assert "NOT been paid" in result["note"]


def test_clerk_may_not_record_a_goods_receipt(world_copy):
    result = tools.record_goods_receipt(CLERK, TRAP, 10.0, 1500.0)
    assert result["error"] == "permission_denied"
    assert "separated duty" in result["reason"]


def test_releaser_may_not_receipt_the_order_they_released(world_copy):
    """The stateful rule, end to end through a tool."""
    result = tools.record_goods_receipt(BUYER_RELEASER, TRAP, 10.0, 1500.0)
    assert result["error"] == "permission_denied"
    assert "Release Purchase Order" in result["reason"]


def test_another_buyer_in_the_group_may_receipt_it(world_copy):
    """The controls refuse the conflict, not the activity."""
    result = tools.record_goods_receipt(BUYER_OTHER, TRAP, 10.0, 1500.0, "DN-1")
    assert result["ok"], result
    assert result["status"] == "recorded"


def test_quantity_beyond_tolerance_is_rejected(world_copy):
    result = tools.record_goods_receipt(BUYER_OTHER, TRAP, 100.0, 1500.0)
    assert result["error"] == "invalid_argument"
    assert "mb-goods-receipt" in result["reason"]


@pytest.mark.parametrize(
    "level,tool,paused",
    [
        ("clearing", "clear_invoice", True),
        ("clearing", "record_goods_receipt", False),
        ("readonly", "record_goods_receipt", True),
        ("bogus", "clear_invoice", False),
    ],
)
def test_kill_switch_pauses_before_anything_else(world_copy, monkeypatch, level, tool, paused):
    monkeypatch.setenv("MB_KILL_SWITCH", level)
    function = getattr(tools, tool)
    args = (CLERK, CLEAN) if tool == "clear_invoice" else (BUYER_OTHER, TRAP, 10.0, 1500.0)
    result = function(*args)
    assert (result.get("error") == "paused") is paused
