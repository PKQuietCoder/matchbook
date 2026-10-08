"""Authorization, including the stateful rule that a prompt cannot implement."""

from __future__ import annotations

import pytest

from agent import auth
from process import config


def ctx(role: str, **kwargs) -> auth.AuthContext:
    defaults = {
        "buyer": {"actor_id": "buy-001", "company_code": "MIS-01", "purchasing_group": "PG-10"},
        "ap_clerk": {"actor_id": "ap-003", "company_code": "MIS-01"},
        "controller": {"actor_id": "ctl-001", "company_code": "MIS-01"},
    }[role]
    return auth.AuthContext(role=role, **{**defaults, **kwargs})


ITEM = {"purchasing_group": "PG-10", "company_code": "MIS-01"}
OTHER_GROUP = {"purchasing_group": "PG-99", "company_code": "MIS-01"}
OTHER_COMPANY = {"purchasing_group": "PG-10", "company_code": "MIS-09"}


def test_context_requires_the_fields_its_role_is_scoped_by():
    with pytest.raises(ValueError):
        auth.AuthContext(actor_id="x", role="buyer", company_code="MIS-01")
    with pytest.raises(ValueError):
        auth.AuthContext(actor_id="x", role="ap_clerk")
    with pytest.raises(ValueError):
        auth.AuthContext(actor_id="x", role="auditor", company_code="MIS-01")


def test_scopes_are_different_dimensions_not_nested_levels():
    """A buyer is scoped by purchasing group, a clerk by company code."""
    assert auth.can_view_item(ctx("buyer"), ITEM)
    assert not auth.can_view_item(ctx("buyer"), OTHER_GROUP)
    # The clerk ignores purchasing group entirely...
    assert auth.can_view_item(ctx("ap_clerk"), OTHER_GROUP)
    # ...and is bounded by company code instead.
    assert not auth.can_view_item(ctx("ap_clerk"), OTHER_COMPANY)
    # The controller sees everything.
    assert auth.can_view_item(ctx("controller"), OTHER_COMPANY)


def test_role_level_separation_of_duties():
    """SPEC AUTH-2: receipting and clearing are different roles."""
    assert auth.can_record_goods_receipt(ctx("buyer"), ITEM)
    assert not auth.can_record_goods_receipt(ctx("ap_clerk"), ITEM)
    assert auth.can_clear_invoice(ctx("ap_clerk"), ITEM)
    assert not auth.can_clear_invoice(ctx("buyer"), ITEM)
    assert not auth.can_clear_invoice(ctx("controller"), ITEM)


def test_per_case_separation_of_duties_is_stateful():
    """SPEC AUTH-3: the same actor and action, permitted or denied by history."""
    facts = config.load_facts()
    clerk = ctx("ap_clerk")
    clean_history = [("Record Goods Receipt", "buy-002")]
    own_history = [("Record Goods Receipt", "ap-003")]
    assert auth.sod_conflict(clerk, "Clear Invoice", clean_history, facts) is None
    assert auth.sod_conflict(clerk, "Clear Invoice", own_history, facts) == (
        "Record Goods Receipt",
        "Clear Invoice",
    )


def test_sod_is_symmetric_across_the_pair():
    facts = config.load_facts()
    buyer = ctx("buyer")
    # Having released the order blocks receipting it, in either order of asking.
    assert auth.sod_conflict(buyer, "Record Goods Receipt", [("Release Purchase Order", "buy-001")], facts)
    assert auth.sod_conflict(buyer, "Release Purchase Order", [("Record Goods Receipt", "buy-001")], facts)


def test_clearing_limit_comes_from_facts():
    facts = config.load_facts()
    assert auth.clearing_limit_cents(ctx("ap_clerk"), facts) == int(
        facts["clearing_auto_approve_limit_eur"] * 100
    )
