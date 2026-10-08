"""The world: determinism, the pinned fixtures, and the policy guard."""

from __future__ import annotations

import sqlite3

import pytest

from process import config
from seed import controls, policies, validate
from seed.generate import generate_world, pinned_items


def test_two_seeds_are_identical(tmp_path):
    first = tmp_path / "a.db"
    second = tmp_path / "b.db"
    generate_world(db_path=first, policies_dir=tmp_path / "pa", quiet=True)
    generate_world(db_path=second, policies_dir=tmp_path / "pb", quiet=True)
    assert first.read_bytes() == second.read_bytes()


def test_pinned_items_have_the_decisions_they_were_built_for(world):
    facts = config.load_facts()
    expected = {
        "4507001234_00010": "price_variance",
        "4507001234_00020": "no_invoice",
        "4507002001_00010": "match",
        "4507009999_00010": "item_deleted",
        "4507003300_00010": "match",
    }
    conn = sqlite3.connect(world["db"])
    conn.row_factory = sqlite3.Row
    try:
        for item_key, decision in expected.items():
            item = conn.execute("SELECT * FROM po_items WHERE item_key = ?", (item_key,)).fetchone()
            row = conn.execute(
                "SELECT SUM(value_cents) AS v, COUNT(*) AS n FROM goods_receipts"
                " WHERE item_key = ? AND cancelled_at IS NULL",
                (item_key,),
            ).fetchone()
            invoice = conn.execute(
                "SELECT value_cents FROM invoices WHERE item_key = ? AND status != 'cancelled'",
                (item_key,),
            ).fetchone()
            result = controls.three_way_match(
                flow=item["flow"],
                po_value_cents=item["po_value_cents"],
                receipt_value_cents=int(row["v"]) if row["n"] else None,
                invoice_value_cents=invoice["value_cents"] if invoice else None,
                item_deleted=item["deleted_at"] is not None,
                payment_blocked=bool(item["payment_blocked"]),
                facts=facts,
            )
            assert result.decision == decision, item_key
    finally:
        conn.close()


def test_the_large_item_queues_and_the_small_one_does_not(world):
    facts = config.load_facts()
    assert controls.clearing_needs_approval(1_840_000, facts)
    assert not controls.clearing_needs_approval(60_000, facts)


def test_flow_strings_partition_the_real_logs_item_categories(snapshot_log):
    """Every flow the log contains must be declared in exactly one bucket.

    The three buckets have to PARTITION the observed flows: a flow in none of
    them is silently exempt from every control, and a flow in two is ambiguous.
    This test caught Consignment being moved between buckets, which is exactly
    the drift it exists for.
    """
    facts = config.load_facts()
    buckets = {
        "gr_required_flows": set(facts["gr_required_flows"]),
        "gr_not_required_flows": set(facts["gr_not_required_flows"]),
        "invoice_matching_not_applicable_flows": set(
            facts["invoice_matching_not_applicable_flows"]
        ),
    }
    observed = {
        snapshot_log.case_attributes[c]["Item Category"]
        for c in snapshot_log.case_ids
        if "Item Category" in snapshot_log.case_attributes.get(c, {})
    }

    declared = set().union(*buckets.values())
    assert declared == observed, (
        f"facts.yaml flows drifted from the log; symmetric difference: {declared ^ observed}"
    )

    for name, first in buckets.items():
        for other_name, second in buckets.items():
            if name < other_name:
                assert not (first & second), (
                    f"{name} and {other_name} both claim {first & second}"
                )


def test_policy_corpus_agrees_with_the_facts_sheet():
    assert validate.validate_corpus() == []


def test_the_policy_guard_is_not_vacuous(monkeypatch):
    """Plant a number no fact accounts for; the validator must catch it."""
    original = policies.render_corpus

    def drifted(facts):
        docs = original(facts)
        object.__setattr__(docs[0], "body", docs[0].body + "\n\nPaid within 45 days.")
        return docs

    monkeypatch.setattr(policies, "render_corpus", drifted)
    problems = validate.validate_corpus()
    assert problems and "45" in problems[0]


def test_seed_refuses_to_write_a_corpus_that_disagrees(monkeypatch, tmp_path):
    original = policies.render_corpus

    def drifted(facts):
        docs = original(facts)
        object.__setattr__(docs[0], "body", docs[0].body + "\n\nTolerance is 999 EUR.")
        return docs

    monkeypatch.setattr(policies, "render_corpus", drifted)
    with pytest.raises(validate.PolicyValidationError):
        validate.write_corpus(tmp_path / "p")


def test_documented_data_quality_cases_exist(world):
    conn = sqlite3.connect(world["db"])
    try:
        rows = conn.execute("SELECT case_id, expected_handling FROM data_quality_cases").fetchall()
    finally:
        conn.close()
    assert len(rows) >= 3
    assert all(handling for _case_id, handling in rows)
