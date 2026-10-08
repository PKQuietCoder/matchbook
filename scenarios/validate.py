"""`python -m scenarios.validate <file> [--final]` -- check a scenario dataset.

Structure first, then grounding. The structural checks catch typos; the grounded
checks catch the expensive mistake, which is a scenario whose answer key is
wrong. Running 250 scenarios costs money and an afternoon, and a dataset with a
wrong answer key is worse than no dataset -- it will label, judge and report
confidently in the wrong direction.

Every error names the scenario and says what to do about it.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db  # noqa: E402
from process import config  # noqa: E402
from scenarios import schema  # noqa: E402


def _world_facts() -> dict[str, Any]:
    """Actors, items and policy ids, read once from the generated world."""
    with db.connection() as connection:
        actors = {
            row["actor_id"]: row["role"]
            for row in connection.execute("SELECT actor_id, role FROM actors")
        }
        items = {
            row["item_key"]
            for row in connection.execute("SELECT item_key FROM po_items")
        }
        dq_cases = {
            row["case_id"]
            for row in connection.execute("SELECT case_id FROM data_quality_cases")
        }
    policies = {path.stem for path in db.policies_dir().glob("*.md")}
    flows = set(config.load_facts()["flow_shares_observed"])
    return {
        "actors": actors,
        "items": items,
        "dq_cases": dq_cases,
        "policies": policies,
        "flows": flows,
    }


def check(records: list[dict[str, Any]], *, final: bool = False) -> list[str]:
    errors: list[str] = []
    world = _world_facts()
    requirements = schema.requirement_ids()
    seen_ids: Counter[str] = Counter()
    seen_messages: Counter[str] = Counter()

    if not world["actors"]:
        return ["the world is empty; run `python -m seed.generate` first"]

    for record in records:
        rid = record.get("id", "<no id>")

        missing = [key for key in schema.REQUIRED_TOP if key not in record]
        if missing:
            errors.append(f"{rid}: missing top-level {', '.join(missing)}")
            continue
        seen_ids[record["id"]] += 1
        seen_messages[record["opening_message"].strip().lower()] += 1

        if record["scenario_group"] not in schema.GROUPS:
            errors.append(
                f"{rid}: scenario_group {record['scenario_group']!r} "
                f"must be one of {schema.GROUPS}"
            )

        tuple_ = record["tuple"]
        missing = [key for key in schema.REQUIRED_TUPLE if key not in tuple_]
        if missing:
            errors.append(f"{rid}: tuple missing {', '.join(missing)}")
            continue

        # -- the grounded checks ------------------------------------------
        actor_role = world["actors"].get(tuple_["actor_id"])
        if actor_role is None:
            errors.append(
                f"{rid}: actor {tuple_['actor_id']!r} is not in the world. "
                "Scenarios must name a seeded actor."
            )
        elif actor_role != tuple_["role"]:
            errors.append(
                f"{rid}: actor {tuple_['actor_id']} has role {actor_role!r}, "
                f"not {tuple_['role']!r}. The world decides the role, not the scenario."
            )

        if tuple_["item_key"] is not None and tuple_["item_key"] not in world["items"]:
            errors.append(
                f"{rid}: item {tuple_['item_key']!r} does not exist. "
                "Use item_state 'none' for a request that names no item."
            )
        if tuple_["item_key"] is None and tuple_["item_state"] != "none":
            errors.append(f"{rid}: no item_key, so item_state must be 'none'")

        if tuple_["item_state"] not in schema.ITEM_STATES:
            errors.append(f"{rid}: item_state {tuple_['item_state']!r} is not known")
        if tuple_["intent"] not in schema.INTENTS:
            errors.append(f"{rid}: intent {tuple_['intent']!r} is not known")
        if tuple_["difficulty"] not in schema.DIFFICULTY:
            errors.append(f"{rid}: difficulty must be one of {schema.DIFFICULTY}")
        if tuple_["flow"] is not None and tuple_["flow"] not in world["flows"]:
            errors.append(
                f"{rid}: flow {tuple_['flow']!r} is not an Item Category in facts.yaml"
            )
        policy = tuple_["applicable_policy"]
        if policy is not None and policy not in world["policies"]:
            errors.append(
                f"{rid}: applicable_policy {policy!r} is not a rendered document; "
                f"have {sorted(world['policies'])}"
            )
        if not isinstance(tuple_["tools_needed"], int) or tuple_["tools_needed"] < 0:
            errors.append(f"{rid}: tools_needed must be a non-negative integer")

        followups = record["followups"]
        if not isinstance(followups, list):
            errors.append(f"{rid}: followups must be a list")
        elif tuple_["turn_count"] != 1 + len(followups):
            errors.append(
                f"{rid}: turn_count {tuple_['turn_count']} but "
                f"{len(followups)} followups; turn_count is 1 + followups"
            )

        expected = record["expected"]
        missing = [key for key in schema.REQUIRED_EXPECTED if key not in expected]
        if missing:
            errors.append(f"{rid}: expected missing {', '.join(missing)}")
            continue
        if expected["outcome"] not in schema.OUTCOMES:
            errors.append(
                f"{rid}: outcome {expected['outcome']!r} must be one of {schema.OUTCOMES}"
            )
        requirement = expected["requirement"]
        if requirement is not None and requirement not in requirements:
            errors.append(
                f"{rid}: requirement {requirement!r} is not declared in SPEC.md"
            )
        source = expected["source"]
        if not isinstance(source, dict) or "type" not in source or "reference" not in source:
            errors.append(f"{rid}: expected.source needs a type and a reference")
        elif source["type"] not in schema.SOURCE_TYPES:
            errors.append(
                f"{rid}: source type {source['type']!r} must be one of "
                f"{schema.SOURCE_TYPES}. The answer comes from the world, a "
                "document, the control code or the specification -- never from a model."
            )
        if not isinstance(expected["activities_expected"], list):
            errors.append(f"{rid}: activities_expected must be a list (often empty)")

        dq = record["data_quality_case_id"]
        if dq is not None and dq not in world["dq_cases"]:
            errors.append(
                f"{rid}: data_quality_case_id {dq!r} is not in the "
                f"data_quality_cases table; have {sorted(world['dq_cases'])}"
            )
        if dq is not None and record["scenario_group"] != "challenge":
            errors.append(f"{rid}: a damaged-record scenario belongs in the challenge set")

    for identifier, count in seen_ids.items():
        if count > 1:
            errors.append(f"{identifier}: appears {count} times; ids must be unique")
    for message, count in seen_messages.items():
        if count > 1:
            errors.append(
                f"duplicate opening message across {count} scenarios: {message[:60]!r}"
            )

    if final:
        errors.extend(_final_checks(records, world))
    return errors


def _final_checks(records: list[dict[str, Any]], world: dict[str, Any]) -> list[str]:
    errors = []
    groups = Counter(record.get("scenario_group") for record in records)
    if len(records) != schema.FINAL_TOTAL:
        errors.append(f"final dataset has {len(records)} scenarios, expected {schema.FINAL_TOTAL}")
    if groups["coverage"] != schema.FINAL_COVERAGE:
        errors.append(
            f"coverage set has {groups['coverage']}, expected {schema.FINAL_COVERAGE}"
        )
    if groups["challenge"] != schema.FINAL_CHALLENGE:
        errors.append(
            f"challenge set has {groups['challenge']}, expected {schema.FINAL_CHALLENGE}"
        )

    per_case = Counter(
        record.get("data_quality_case_id")
        for record in records
        if record.get("data_quality_case_id")
    )
    for case_id in sorted(world["dq_cases"]):
        if per_case[case_id] < schema.CHALLENGE_PER_DQ_CASE:
            errors.append(
                f"damaged record {case_id} has {per_case[case_id]} scenarios, "
                f"expected at least {schema.CHALLENGE_PER_DQ_CASE}"
            )

    roles = Counter(record["tuple"]["role"] for record in records if "tuple" in record)
    for role in ("buyer", "ap_clerk", "controller"):
        if roles[role] == 0:
            errors.append(f"no scenario uses role {role!r}; all three must appear")
    intents = {record["tuple"]["intent"] for record in records if "tuple" in record}
    unused = set(schema.INTENTS) - intents
    if unused:
        errors.append(f"no scenario uses intent(s) {sorted(unused)}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="a scenario JSONL file")
    parser.add_argument(
        "--final",
        action="store_true",
        help="also check the final dataset's group counts, damaged-record quota "
        "and dimension coverage",
    )
    args = parser.parse_args(argv)

    records = schema.read_jsonl(Path(args.path))
    errors = check(records, final=args.final)
    if errors:
        print(f"{len(errors)} problem(s) in {args.path}:\n")
        for error in errors:
            print(f"  {error}")
        print("\nRepair every one before running the dataset: a run costs money,")
        print("and a scenario with a wrong answer key poisons everything downstream.")
        return 1
    scope = "final" if args.final else "draft"
    print(f"{len(records)} scenarios in {args.path}: {scope} checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
