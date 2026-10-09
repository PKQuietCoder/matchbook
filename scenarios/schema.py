"""The scenario record, and what makes one valid.

Every rule here exists because a dataset is only as good as its answer key. A
scenario whose `expected` is wrong does not merely fail to catch a bug; it
*teaches* the wrong thing to every label, judge and report built on it
downstream. design.md records this happening once already, when a taxonomy entry
cited a compliant run as an instance of a failure mode.

So validation is grounded, not structural. An actor must exist in the world with
the role the scenario claims. An item key must exist. A requirement id must
appear in SPEC.md. A policy id must be a document the corpus actually renders.
None of those can be checked by reading the JSON alone, and all of them are
wrong often enough to be worth a check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

GROUPS = ("coverage", "challenge")

INTENTS = (
    "block_diagnosis",      # why is this invoice unpaid
    "match_explanation",    # does the three-way match hold, and by how much
    "policy_question",      # what does policy permit
    "record_receipt",       # record goods receipt evidence
    "clear_invoice",        # clear, or queue for a controller
    "approval_request",     # ask a controller to decide a queued clearing
    "out_of_scope",         # something the agent must refuse
)

ITEM_STATES = (
    "clean",
    "payment_blocked",
    "tolerance_breach",
    "deleted",
    "multi_item",
    "above_limit",
    "no_invoice",
    "two_way",
    "none",                 # the request names no item
)

DIFFICULTY = ("ordinary", "difficult")

OUTCOMES = (
    "answer",               # a question answered from a tool or a policy
    "refuse",               # the control refuses, and the agent explains
    "queue",                # queued for a controller, reported as queued
    "complete",             # the write succeeded and changed the world
    "escalate",             # out of authority or out of scope
    "clarify",              # the agent must ask which item before acting
)

SOURCE_TYPES = (
    "world_query",          # a row in data/matchbook.db
    "policy_document",      # a document in data/policies
    "control_function",     # seed/controls.py
    "data_quality_table",   # a documented damaged record
    "specification",        # a requirement in SPEC.md
)

REQUIRED_TOP = ("id", "scenario_group", "data_quality_case_id", "tuple",
                "opening_message", "followups", "expected")
REQUIRED_TUPLE = ("role", "actor_id", "intent", "item_key", "item_state", "flow",
                  "applicable_policy", "tools_needed", "difficulty", "turn_count")
REQUIRED_EXPECTED = ("evaluation", "outcome", "reason", "requirement", "source",
                     "activities_expected")

# The final dataset's shape. Three data-quality cases rather than six,
# six, so the per-record quota is 15 instead of 5 and the remainder of the
# challenge set comes from the other challenge shapes.
FINAL_TOTAL = 250
FINAL_COVERAGE = 175
FINAL_CHALLENGE = 75
CHALLENGE_PER_DQ_CASE = 15

# SPEC.md declares ids three ways: bold (**AUTH-1.**), in the tool table
# (| TOOL-1 | ...) and in prose (CTRL-GR). One pattern for all three, because a
# whitelist of families would silently miss the next one added.
_REQUIREMENT = re.compile(r"\b((?:[A-Z]{3,}-)(?:[0-9]+|[A-Z]+))\b")


def requirement_ids(spec_path: Path | None = None) -> set[str]:
    """Every requirement id SPEC.md declares, read from SPEC.md.

    Read rather than hard-coded, so a scenario citing a requirement that was
    renamed or removed fails validation instead of citing nothing.
    """
    text = (spec_path or REPO_ROOT / "SPEC.md").read_text()
    return set(_REQUIREMENT.findall(text))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: not valid JSON: {exc}") from exc
    return records


def write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")
