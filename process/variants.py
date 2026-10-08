"""Variants, rework, and the coverage curve.

A variant is a trace's activity sequence. Counting them is how you find out
whether a process has a shape at all: BPI 2019 has 11,973 distinct variants over
251,734 cases, so "the process" is really a handful of common paths plus a very
long tail, and any model fitted to all of it will be a flower.

The coverage curve is therefore the most useful function here -- it tells you
what fraction of cases the top n variants explain, which is how you pick the
`--coverage` figure discovery is run at and how you justify it.

The rework metrics matter for a second reason specific to this project: an
agent that re-reads the same policy three times, or re-queries the same item,
is exhibiting *rework*, and rework is a process measurement rather than
something a transcript reviewer reliably notices.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterator

from process.log import EventLog, Trace


@dataclass(frozen=True)
class VariantRow:
    variant_id: int
    activities: tuple[str, ...]
    case_count: int
    case_share: float
    cumulative_share: float
    example_case_ids: tuple[str, ...]
    median_duration_seconds: float


def variant_table(log: EventLog, *, examples: int = 3) -> list[VariantRow]:
    """Variants, most frequent first, with cumulative coverage."""
    counts: Counter[tuple[int, ...]] = Counter()
    members: dict[tuple[int, ...], list[str]] = {}
    durations: dict[tuple[int, ...], list[int]] = {}
    for trace in log.traces():
        signature = trace.activity_ids
        counts[signature] += 1
        members.setdefault(signature, [])
        if len(members[signature]) < examples:
            members[signature].append(trace.case_id)
        durations.setdefault(signature, []).append(trace.duration_seconds)

    total = sum(counts.values()) or 1
    rows: list[VariantRow] = []
    cumulative = 0
    for variant_id, (signature, count) in enumerate(counts.most_common()):
        cumulative += count
        observed = sorted(durations[signature])
        middle = observed[len(observed) // 2] if observed else 0
        rows.append(
            VariantRow(
                variant_id=variant_id,
                activities=log.named_variant(signature),
                case_count=count,
                case_share=count / total,
                cumulative_share=cumulative / total,
                example_case_ids=tuple(members[signature]),
                median_duration_seconds=float(middle),
            )
        )
    return rows


def coverage_curve(log: EventLog, points: tuple[float, ...] = (0.5, 0.8, 0.9, 0.95, 0.99)) -> dict[float, int]:
    """How many variants are needed to cover each fraction of cases."""
    counts = sorted(log.variants().values(), reverse=True)
    total = sum(counts) or 1
    answer: dict[float, int] = {}
    running = 0
    index = 0
    for target in points:
        while index < len(counts) and running / total < target:
            running += counts[index]
            index += 1
        answer[target] = index
    return answer


def cases_covering(log: EventLog, coverage: float) -> list[str]:
    """Case ids belonging to the most frequent variants up to `coverage`.

    This is how a sublog is chosen for discovery: keep the spine of the process
    and leave the tail out, explicitly and reproducibly, rather than letting a
    noise threshold decide invisibly.
    """
    counts = log.variants()
    total = sum(counts.values()) or 1
    budget = coverage * total
    running = 0
    kept: set[tuple[int, ...]] = set()
    for signature, count in counts.most_common():
        if running >= budget:
            break
        kept.add(signature)
        running += count
    return [trace.case_id for trace in log.traces() if trace.activity_ids in kept]


def collapse_repeats(signature: tuple[int, ...]) -> tuple[int, ...]:
    """Collapse immediate repetitions, so A A A B becomes A B."""
    collapsed: list[int] = []
    for activity in signature:
        if not collapsed or collapsed[-1] != activity:
            collapsed.append(activity)
    return tuple(collapsed)


def collapsed_variant_table(log: EventLog) -> Counter[tuple[str, ...]]:
    """Variants after collapsing self-loops -- the shape without the stutter."""
    counts: Counter[tuple[str, ...]] = Counter()
    for trace in log.traces():
        counts[log.named_variant(collapse_repeats(trace.activity_ids))] += 1
    return counts


def rework(trace: Trace) -> dict[str, int]:
    """Repeat counts per activity within one case (occurrences beyond the first)."""
    counts = Counter(trace.activities)
    return {activity: count - 1 for activity, count in counts.items() if count > 1}


def rework_ratio(log: EventLog) -> float:
    """Share of events that are a repeat of an activity already seen in the case."""
    repeats = 0
    for trace in log.traces():
        seen: set[int] = set()
        for activity in trace.activity_ids:
            if activity in seen:
                repeats += 1
            else:
                seen.add(activity)
    return repeats / len(log) if len(log) else 0.0


def self_loop_ratio(log: EventLog) -> float:
    """Share of events that immediately repeat the previous activity."""
    loops = 0
    for trace in log.traces():
        signature = trace.activity_ids
        loops += sum(1 for i in range(1, len(signature)) if signature[i] == signature[i - 1])
    return loops / len(log) if len(log) else 0.0


def rework_by_activity(log: EventLog) -> Counter[str]:
    """Which activities get redone. The bottleneck's usual explanation."""
    totals: Counter[str] = Counter()
    for trace in log.traces():
        for activity, extra in rework(trace).items():
            totals[activity] += extra
    return totals


def cases_with_rework(log: EventLog) -> Iterator[Trace]:
    for trace in log.traces():
        if rework(trace):
            yield trace


def summary(log: EventLog) -> dict[str, object]:
    rows = variant_table(log, examples=1)
    return {
        "cases": log.case_count,
        "variants": len(rows),
        "most_common_share": rows[0].case_share if rows else 0.0,
        "coverage": coverage_curve(log),
        "collapsed_variants": len(collapsed_variant_table(log)),
        "rework_ratio": rework_ratio(log),
        "self_loop_ratio": self_loop_ratio(log),
        "top_rework_activities": rework_by_activity(log).most_common(5),
    }
