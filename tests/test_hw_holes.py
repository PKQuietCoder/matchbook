"""One contract test per homework hole.

Every test here is marked `xfail`, which means three different things in three
situations, and the handouts depend on all three:

  * On a finished repo the function works, so the test **xpasses**. That is
    reported, not celebrated -- `xfail_strict = false` in pyproject.toml keeps
    an xpass from becoming a failure.
  * After `git apply homework/module-N/hwN-holes.patch` the function raises
    `NotImplementedError` and the test **xfails** quietly. Running the suite
    will not tell you the homework is unfinished, which is exactly why the
    handout does not ask you to.
  * Under `--runxfail` the mark is ignored and the test **fails loudly**, with
    the NotImplementedError in the traceback. This is the command every handout
    gives you:

        uv run pytest --runxfail tests/test_hw_holes.py -k hw1

AGENTS.md puts it in one line: "Expected failures are unfinished work, not
proof of completion."

These tests are deliberately thin. They pin the *contract* a handout states --
the return shape, the refusal, the ordering -- and nothing more. The thorough
assertions live in tests/test_tools.py and tests/test_auth.py, which a student
is told to run once the holes are filled. Keeping them separate means a hole
test never becomes the specification by accident.
"""

from __future__ import annotations

import pytest

from agent import tools
from agent.auth import AuthContext

# The same identities and fixtures the handouts name, so a student reading a
# failure here can find the item in `seed/generate.py:pinned_items`.
CLERK = AuthContext(actor_id="ap-003", role="ap_clerk", company_code="MIS-01")
CLERK_OTHER_COMPANY = AuthContext(actor_id="ap-004", role="ap_clerk", company_code="MIS-02")
BUYER_RELEASER = AuthContext(
    actor_id="buy-001", role="buyer", company_code="MIS-01", purchasing_group="PG-10"
)
BUYER_OTHER = AuthContext(
    actor_id="buy-004", role="buyer", company_code="MIS-01", purchasing_group="PG-10"
)

DEMO = "4507001234_00010"      # price variance outside tolerance, payment blocked
TRAP = "4507001234_00020"      # same PO, no receipt and no invoice
CLEAN = "4507002001_00010"     # two-way match, clean
DELETED = "4507009999_00010"   # deleted, but still carries an invoice
LARGE = "4507003300_00010"     # clean, above the clearing limit

hole = pytest.mark.xfail(reason="homework hole", raises=NotImplementedError)


# -- Homework 1: the five tools ----------------------------------------------

@hole
def test_hw1_get_purchase_item_returns_the_authorized_item(world):
    """TOOL-1: flow, values, receipt and invoice state, payment block."""
    result = tools.get_purchase_item(CLERK, DEMO)
    assert result["ok"]
    item = result["item"]
    assert item["item_key"] == DEMO
    assert item["payment_blocked"] is True
    assert item["deleted"] is False
    # RESP-6 needs the ambiguity to be visible, so siblings come back too.
    assert TRAP in item["sibling_items"]


@hole
def test_hw1_get_purchase_item_denies_out_of_scope(world):
    """AUTH-1 and RESP-5: refuse, and reveal nothing while refusing."""
    result = tools.get_purchase_item(CLERK_OTHER_COMPANY, DEMO)
    assert result["ok"] is False
    assert result["error"] == "permission_denied"
    assert "item" not in result


@hole
def test_hw1_get_three_way_match_computes_the_variance(world):
    """TOOL-2 and TOOL-7: the numbers come from code, never from the model."""
    result = tools.get_three_way_match(CLERK, DEMO)
    assert result["ok"]
    assert result["variance_eur"] == 120.0
    # The looser-of rule from facts.yaml: max(50, 2% of 4,000) = 80.
    assert result["tolerance_eur"] == 80.0
    assert result["within_tolerance"] is False


@hole
def test_hw1_get_policy_returns_id_title_and_body(world):
    """TOOL-3: RESP-1 requires a citable identifier, so it must come back."""
    result = tools.get_policy(CLERK, "mb-three-way-match")
    assert result["ok"]
    assert result["policy_id"] == "mb-three-way-match"
    assert result["title"]
    assert result["body"]


@hole
def test_hw1_get_policy_not_found(world):
    assert tools.get_policy(CLERK, "no-such-policy")["error"] == "not_found"


@hole
def test_hw1_record_goods_receipt_requires_a_buyer(world_copy):
    """AUTH-2: an AP clerk may not record a goods receipt."""
    result = tools.record_goods_receipt(
        CLERK, TRAP, quantity=10.0, value_eur=1500.0, reference="DN-1"
    )
    assert result["ok"] is False
    assert result["error"] == "permission_denied"


@hole
def test_hw1_record_goods_receipt_respects_per_case_duties(world_copy):
    """AUTH-3, the rule no prompt can implement.

    Both callers are buyers in PG-10 with identical roles and scope. The only
    difference is what each has already done on this case: buy-001 released the
    purchase order, so receipting it would put both halves of a conflicting
    pair in one pair of hands.
    """
    denied = tools.record_goods_receipt(
        BUYER_RELEASER, TRAP, quantity=10.0, value_eur=1500.0, reference="DN-2"
    )
    assert denied["ok"] is False

    allowed = tools.record_goods_receipt(
        BUYER_OTHER, TRAP, quantity=10.0, value_eur=1500.0, reference="DN-3"
    )
    assert allowed["ok"] is True
    assert allowed["status"] == "recorded"


@hole
def test_hw1_clear_invoice_refuses_a_payment_block(world_copy):
    """TOOL-6 step 5, and ESC-2: never work around a block by removing it."""
    result = tools.clear_invoice(CLERK, DEMO)
    assert result["ok"] is False
    assert result["error"] == "not_eligible"
    assert "block" in result["reason"].lower()


@hole
def test_hw1_clear_invoice_refuses_a_deleted_item(world_copy):
    """TOOL-6 step 3: deletion is checked before the invoice is even looked at."""
    result = tools.clear_invoice(CLERK, DELETED)
    assert result["ok"] is False
    assert "delet" in result["reason"].lower()


@hole
def test_hw1_clear_invoice_queues_above_the_limit(world_copy):
    """ESC-1 and RESP-3: queued for a controller, and not paid.

    The distinction is the whole point. `ok` is true because the tool did what
    it was asked; `status` is the only thing that says whether money moved.
    """
    result = tools.clear_invoice(CLERK, LARGE)
    assert result["ok"] is True
    assert result["status"] == "queued_for_approval"
    assert result["status"] != "cleared"


@hole
def test_hw1_clear_invoice_clears_a_clean_invoice_once(world_copy):
    """The clean path, and only once: the second attempt finds no open invoice."""
    first = tools.clear_invoice(CLERK, CLEAN)
    assert first["ok"] is True
    assert first["status"] == "cleared"
    assert tools.clear_invoice(CLERK, CLEAN)["ok"] is False


@hole
def test_hw1_clear_invoice_check_order_matches_the_spec(world_copy):
    """TOOL-6: the order of the nine checks is part of the specification.

    DELETED is deleted *and* carries an invoice. If the invoice check ran
    first the refusal would mention the invoice; the spec says deletion is
    checked earlier, so the reason must name the deletion.
    """
    reason = tools.clear_invoice(CLERK, DELETED)["reason"].lower()
    assert "delet" in reason
    assert "invoice" not in reason


@hole
def test_hw1_kill_switch_is_checked_first(world_copy, monkeypatch):
    """TOOL-6 step 1. The switch outranks authorization, so a caller who is
    not allowed to clear anyway must still be told the system is paused --
    otherwise the switch's own status is unobservable from outside."""
    monkeypatch.setenv("MB_KILL_SWITCH", "clearing")
    result = tools.clear_invoice(BUYER_OTHER, CLEAN)
    assert result["ok"] is False
    assert result["error"] == "paused"
