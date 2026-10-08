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


def test_clear_invoice_check_order_matches_the_spec(world_copy):
    """SPEC TOOL-6 pins the order, and the order is observable.

    The spec and the implementation disagreed about this: the spec said the
    amount was checked third, the code checked it last, and the docstring
    argued for the spec while the code did the opposite. The implemented order
    is the right one, so the spec was corrected -- and pinned here, because a
    prose ordering with no test is a comment.
    """
    from agent import db

    # Above the clearing limit AND payment-blocked. If the amount were checked
    # before the controls, this would queue for a controller who cannot act,
    # because the block's reason is unresolved. It must refuse instead.
    with db.connection() as conn:
        db.set_payment_block(
            conn, item_key=LARGE, blocked=True, reason="price query", actor_id="ap-001"
        )
    blocked = tools.clear_invoice(CLERK, LARGE)
    assert blocked["error"] == "not_eligible"
    assert "payment block" in blocked["reason"]

    # With the block lifted, the same invoice queues on the amount.
    with db.connection() as conn:
        db.set_payment_block(
            conn, item_key=LARGE, blocked=False, reason="resolved", actor_id="ap-001"
        )
    queued = tools.clear_invoice(CLERK, LARGE)
    assert queued["ok"] and queued["trigger"] == "above_limit"


def test_deletion_is_checked_before_the_invoice_exists(world_copy):
    """A deleted item refuses on the deletion, not on anything downstream."""
    result = tools.clear_invoice(CLERK, DELETED)
    assert result["error"] == "not_eligible"
    assert "deleted" in result["reason"]


def test_kill_switch_precedes_authorization(world_copy, monkeypatch):
    """Rung 1: a paused system cannot be argued into acting by any caller.

    A buyer has no authority to clear at all, so if authorization ran first
    this would say permission_denied. It must say paused: the switch is the
    outermost gate.
    """
    monkeypatch.setenv("MB_KILL_SWITCH", "readonly")
    result = tools.clear_invoice(BUYER_RELEASER, CLEAN)
    assert result["error"] == "paused"


def test_the_kill_switch_only_names_tools_that_exist():
    """A ladder that lists planned tools overstates what it enforces.

    `MB_KILL_SWITCH=payments` once appeared to pause `remove_payment_block`,
    which has no implementation, so the rung read as a stronger guarantee than
    it gave.
    """
    from agent import killswitch

    assert killswitch.WRITE_TOOLS <= set(tools.TOOLS)
    for level, paused in killswitch.PAUSED_BY_LEVEL.items():
        assert paused <= set(tools.TOOLS), f"level {level!r} names a tool that does not exist"
    # The planned set is documented but must stay out of what is enforced.
    assert not (killswitch.PLANNED_WRITE_TOOLS & set(tools.TOOLS))


def test_every_write_tool_is_actually_guarded(world_copy, monkeypatch):
    """readonly must pause all of them, not just the ones someone remembered."""
    from agent import killswitch

    monkeypatch.setenv("MB_KILL_SWITCH", "readonly")
    for name in killswitch.WRITE_TOOLS:
        args = (CLERK, CLEAN) if name == "clear_invoice" else (BUYER_OTHER, TRAP, 1.0, 10.0)
        assert tools.TOOLS[name](*args)["error"] == "paused", name
