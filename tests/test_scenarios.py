"""The scenario schema and its validator.

The validator's job is to refuse a dataset whose answer key is wrong, so these
tests are mostly about what it *rejects*. A validator that only checks shape
would pass a scenario naming an actor who does not exist, claiming a role the
world disagrees with, or citing a requirement that was renamed -- and each of
those produces a confident label pointing the wrong way.
"""

from __future__ import annotations

import json

import pytest

from scenarios import schema
from scenarios.validate import check


def _scenario(**overrides):
    """A valid scenario, with overrides applied by dotted path."""
    record = {
        "id": "mb-0001",
        "scenario_group": "coverage",
        "data_quality_case_id": None,
        "tuple": {
            "role": "ap_clerk",
            "actor_id": "ap-003",
            "intent": "block_diagnosis",
            "item_key": "4507001234_00010",
            "item_state": "payment_blocked",
            "flow": "3-way match, invoice after GR",
            "applicable_policy": "mb-three-way-match",
            "tools_needed": 2,
            "difficulty": "ordinary",
            "turn_count": 1,
        },
        "opening_message": "Why is the invoice on 4507001234_00010 still unpaid?",
        "followups": [],
        "expected": {
            "evaluation": "Diagnose the block and the tolerance breach, citing the policy.",
            "outcome": "answer",
            "reason": "120.00 variance against an 80.00 allowance.",
            "requirement": "RESP-1",
            "source": {
                "type": "control_function",
                "reference": "seed/controls.py:three_way_match",
            },
            "activities_expected": [],
        },
    }
    for path, value in overrides.items():
        target = record
        *parents, leaf = path.split(".")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return record


def test_a_grounded_scenario_passes(world):
    assert check([_scenario()]) == []


def test_requirement_ids_are_read_from_the_spec(world):
    """Not hard-coded, so a renamed requirement fails rather than citing nothing."""
    ids = schema.requirement_ids()
    # All three declaration styles SPEC.md uses: bold, table, prose.
    assert {"AUTH-3", "TOOL-1", "CTRL-SOD", "RESP-6"} <= ids
    assert "RESP-99" not in ids


@pytest.mark.parametrize(
    "overrides, expected_fragment",
    [
        ({"tuple.actor_id": "nobody"}, "is not in the world"),
        ({"tuple.role": "controller"}, "not 'controller'"),
        ({"tuple.item_key": "4507001234_99999"}, "does not exist"),
        ({"tuple.flow": "4-way match"}, "not an Item Category"),
        ({"tuple.applicable_policy": "mb-invented"}, "not a rendered document"),
        ({"expected.requirement": "RESP-99"}, "not declared in SPEC.md"),
        ({"expected.source": {"type": "vibes", "reference": "x"}}, "never from a model"),
        ({"expected.outcome": "telepathy"}, "must be one of"),
        ({"tuple.intent": "telepathy"}, "is not known"),
        ({"tuple.difficulty": "easy"}, "must be one of"),
        ({"tuple.tools_needed": -1}, "non-negative integer"),
        ({"tuple.turn_count": 3}, "turn_count is 1 + followups"),
        ({"expected.activities_expected": "none"}, "must be a list"),
        ({"data_quality_case_id": "dq-invented"}, "not in the data_quality_cases table"),
    ],
)
def test_the_validator_rejects_ungrounded_scenarios(world, overrides, expected_fragment):
    errors = check([_scenario(**overrides)])
    assert any(expected_fragment in error for error in errors), (overrides, errors)


def test_a_damaged_record_scenario_belongs_in_the_challenge_set(world):
    errors = check([_scenario(data_quality_case_id="dq-deleted-item-with-invoice")])
    assert any("belongs in the challenge set" in error for error in errors)
    assert check(
        [
            _scenario(
                data_quality_case_id="dq-deleted-item-with-invoice",
                scenario_group="challenge",
            )
        ]
    ) == []


def test_duplicate_ids_and_duplicate_messages_are_both_caught(world):
    errors = check([_scenario(), _scenario()])
    assert any("ids must be unique" in error for error in errors)
    assert any("duplicate opening message" in error for error in errors)


def test_an_itemless_scenario_must_say_so(world):
    """A request that names no item is legitimate; silently claiming an item
    state for one is not."""
    errors = check([_scenario(**{"tuple.item_key": None})])
    assert any("item_state must be 'none'" in error for error in errors)
    assert check(
        [_scenario(**{"tuple.item_key": None, "tuple.item_state": "none"})]
    ) == []


def test_final_checks_enforce_the_datasets_shape(world):
    """The quotas exist so a dataset cannot be 250 copies of the easy case."""
    errors = check([_scenario()], final=True)
    joined = " ".join(errors)
    assert f"expected {schema.FINAL_TOTAL}" in joined
    assert "coverage set has 1" in joined
    assert "challenge set has 0" in joined
    # Three damaged records, each needing its quota.
    assert joined.count("expected at least 15") == 3
    assert "no scenario uses role 'buyer'" in joined
    assert "no scenario uses intent" in joined


def test_round_trip_through_jsonl(world, tmp_path):
    path = tmp_path / "scenarios.jsonl"
    schema.write_jsonl([_scenario(), _scenario(id="mb-0002")], path)
    read_back = schema.read_jsonl(path)
    assert [record["id"] for record in read_back] == ["mb-0001", "mb-0002"]


def test_a_malformed_line_names_its_line_number(world, tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(_scenario()) + "\n{not json}\n")
    with pytest.raises(ValueError, match=r"broken\.jsonl:2"):
        schema.read_jsonl(path)


def test_an_unseeded_world_says_so_instead_of_passing(world, monkeypatch, tmp_path):
    """An empty world would make every grounded check vacuous, so it is an error
    rather than a pass."""
    empty = tmp_path / "empty.db"
    import sqlite3

    connection = sqlite3.connect(empty)
    connection.executescript(
        "CREATE TABLE actors (actor_id TEXT, role TEXT);"
        "CREATE TABLE po_items (item_key TEXT);"
        "CREATE TABLE data_quality_cases (case_id TEXT);"
    )
    connection.close()
    monkeypatch.setenv("MB_DB", str(empty))
    assert check([_scenario()]) == ["the world is empty; run `python -m seed.generate` first"]
