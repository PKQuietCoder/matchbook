"""The control oracle, including the parts no tool calls yet.

These functions encode SPEC rules that the milestone-1 tool set does not reach
(payment terms, early-settlement discounts, duplicate detection, the role
matrix for invoice recording and payment blocks). Untested, they are dead code
a reader cannot distinguish from load-bearing code. Tested, they are a verified
contract the next milestone can build on -- and the arithmetic is pinned now,
while the intent is fresh, rather than reconstructed later.
"""

from __future__ import annotations

from datetime import date

import pytest

from agent import auth
from process import config
from seed import controls


@pytest.fixture()
def facts():
    return config.load_facts()


def test_payment_terms_run_from_the_recording_date(facts):
    """Policy mb-payment-terms: the vendor's own invoice date does not count."""
    recorded = date(2026, 6, 1)
    assert controls.payment_due_date(recorded, facts) == date(2026, 7, 1)  # net 30
    assert controls.discount_deadline(recorded, facts) == date(2026, 6, 11)  # 10 days


def test_duplicate_invoice_window_and_value_tolerance(facts):
    existing = [("vendor_0001", 120_000, date(2026, 5, 1))]
    # Same vendor, same value, inside the 90-day window.
    assert controls.is_duplicate_invoice(
        vendor_id="vendor_0001", value_cents=120_000,
        invoice_date=date(2026, 6, 1), existing=existing, facts=facts,
    )
    # A different vendor is never a duplicate, however similar the invoice.
    assert not controls.is_duplicate_invoice(
        vendor_id="vendor_0002", value_cents=120_000,
        invoice_date=date(2026, 6, 1), existing=existing, facts=facts,
    )
    # Outside the window.
    assert not controls.is_duplicate_invoice(
        vendor_id="vendor_0001", value_cents=120_000,
        invoice_date=date(2026, 11, 1), existing=existing, facts=facts,
    )
    # Beyond the 1 EUR value tolerance.
    assert not controls.is_duplicate_invoice(
        vendor_id="vendor_0001", value_cents=120_500,
        invoice_date=date(2026, 6, 1), existing=existing, facts=facts,
    )


def test_quantity_tolerance_is_one_sided(facts):
    """Over-delivery is bounded; under-delivery is not an error."""
    assert controls.quantity_within_tolerance(100, 105, facts)       # exactly 5%
    assert not controls.quantity_within_tolerance(100, 106, facts)   # over
    assert controls.quantity_within_tolerance(100, 50, facts)        # partial delivery
    assert not controls.quantity_within_tolerance(0, 1, facts)       # nothing ordered


def test_tolerance_is_the_looser_of_the_two_allowances(facts):
    """The rule that disciplined the demo fixtures.

    A percentage alone rejects rounding on small items; an absolute allowance
    alone waves through a material difference on large ones.
    """
    small = controls.tolerance_cents(
        100_00, tolerance_abs_eur=facts["tolerance_abs_eur"], tolerance_pct=facts["tolerance_pct"]
    )
    large = controls.tolerance_cents(
        10_000_00, tolerance_abs_eur=facts["tolerance_abs_eur"], tolerance_pct=facts["tolerance_pct"]
    )
    assert small == 50_00     # the absolute allowance wins
    assert large == 200_00    # the percentage wins


def test_clearing_limit_boundary_is_inclusive(facts):
    """Exactly the limit executes; a cent more queues. An off-by-one here is
    a money bug, so the boundary is pinned rather than inferred."""
    limit = int(facts["clearing_auto_approve_limit_eur"] * 100)
    assert not controls.clearing_needs_approval(limit, facts)
    assert controls.clearing_needs_approval(limit + 1, facts)


def test_role_matrix_for_tools_that_do_not_exist_yet():
    """SPEC AUTH-1 rows for invoice recording, payment blocks and approvals.

    No milestone-1 tool calls these, so pinning them now is what keeps the
    access matrix a specification rather than a comment.
    """
    item = {"purchasing_group": "PG-10", "company_code": "MIS-01"}
    buyer = auth.AuthContext("buy-001", "buyer", "MIS-01", "PG-10")
    clerk = auth.AuthContext("ap-003", "ap_clerk", "MIS-01")
    controller = auth.AuthContext("ctl-001", "controller", "MIS-01")

    # Recording an invoice is the clerk's duty, not the buyer's.
    assert auth.can_record_invoice(clerk, item)
    assert not auth.can_record_invoice(buyer, item)

    # Payment blocks are the clerk's, and notably NOT the controller's: a
    # controller decides queued items, it does not reach in and unblock.
    assert auth.can_change_payment_block(clerk, item)
    assert not auth.can_change_payment_block(buyer, item)
    assert not auth.can_change_payment_block(controller, item)

    # Only a controller decides an approval.
    assert auth.can_decide_approval(controller)
    assert not auth.can_decide_approval(clerk)
    assert not auth.can_decide_approval(buyer)
