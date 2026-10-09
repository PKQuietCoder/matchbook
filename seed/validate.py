"""Fail the seed if a policy document disagrees with facts.yaml.

Two checks, both of which have caught real drift:

  1. every value a document declares in `facts_used` equals the value in
     `facts.yaml`; and
  2. every number appearing in a document's body is accounted for -- by a
     declared fact, by `extra_numbers`, or by being part of a declared list.

The second is the one that matters. Without it a document can quote "45 days"
while the facts sheet says 30, both files look fine in isolation, and the agent
cites a number that is not policy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from process import config  # noqa: E402
from seed import policies  # noqa: E402

NUMBER = re.compile(r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?)(?![\w.])")


class PolicyValidationError(AssertionError):
    pass


def _numbers_in(text: str) -> set[float]:
    found: set[float] = set()
    for match in NUMBER.finditer(text):
        found.add(float(match.group(1).replace(",", "")))
    return found


def _declared_numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        out: list[float] = []
        for item in value.values():
            out.extend(_declared_numbers(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_declared_numbers(item))
        return out
    if isinstance(value, str):
        return list(_numbers_in(value))
    return []


def validate_corpus(facts: dict[str, Any] | None = None) -> list[str]:
    """Return a list of problems; empty means the corpus agrees with the facts."""
    facts = facts or config.load_facts()
    problems: list[str] = []
    for doc in policies.render_corpus(facts):
        for key, claimed in doc.facts_used.items():
            actual = facts.get(key)
            if actual != claimed:
                problems.append(
                    f"{doc.policy_id}: declares {key}={claimed!r} but facts.yaml says {actual!r}"
                )

        allowed: set[float] = set(doc.extra_numbers)
        for value in doc.facts_used.values():
            allowed.update(_declared_numbers(value))
        # Markdown heading levels and list markers are not claims.
        unaccounted = sorted(_numbers_in(doc.body) - allowed)
        if unaccounted:
            problems.append(
                f"{doc.policy_id}: body states number(s) {unaccounted} that no declared "
                "fact accounts for; add the fact to facts_used or list it in extra_numbers"
            )
    return problems


def write_corpus(directory: Path, facts: dict[str, Any] | None = None) -> list[Path]:
    facts = facts or config.load_facts()
    problems = validate_corpus(facts)
    if problems:
        raise PolicyValidationError(
            "the policy corpus disagrees with facts.yaml:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for doc in policies.render_corpus(facts):
        path = directory / f"{doc.policy_id}.md"
        path.write_text(doc.rendered())
        written.append(path)
    return written


def main() -> int:
    problems = validate_corpus()
    if problems:
        print("policy corpus does NOT agree with facts.yaml:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"policy corpus agrees with facts.yaml ({len(policies.render_corpus(config.load_facts()))} documents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
