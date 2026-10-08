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


def order_sensitivity(
    events, *, log_id: str = "sensitivity", activity_rank: dict[str, int] | None = None
) -> dict[str, object]:
    """How much does the declared tie-break change the discovered process?

    99.99% of BPI 2019's timestamps are minute-precision and same-minute ties
    affect ~17% of events, so something must order them, and `facts.yaml
    activity_rank` does. This measures what that choice costs.

    `events` must be an iterable of `(case_id, activity, timestamp)` **in
    source order**, because that is the only honest baseline. Measuring this
    from a log already loaded out of the store cannot work: its stored order IS
    the ranked order, so the comparison would trivially report no difference
    and read as "the ranking is free". It is not.
    """
    from process.log import EventLogBuilder

    rows = list(events)

    def rebuild(rank):
        builder = EventLogBuilder(log_id, activity_rank=rank)
        for case_id, activity, timestamp in rows:
            builder.add(case_id=case_id, activity=activity, timestamp=timestamp)
        return builder.build()

    plain = rebuild(None)
    ranked = rebuild(activity_rank or {})

    # Not every tie matters. Two events of the SAME activity at the same
    # instant cannot be meaningfully ordered and reordering them changes
    # nothing -- a self-repeat is a self-repeat either way. Only a tie between
    # *different* activities can change the sequence, so that is the figure to
    # quote. On the helpdesk log the distinction is the whole story: 91 tied
    # events, and a declared ranking changes not one case.
    consequential = 0
    for trace in plain.traces():
        signature = trace.activity_ids
        stamps = trace.timestamps
        for index in range(1, len(signature)):
            if stamps[index] == stamps[index - 1] and signature[index] != signature[index - 1]:
                consequential += 1
    plain_variants = plain.variants()
    ranked_variants = ranked.variants()
    changed = sum(
        1
        for case_id in plain.case_ids
        if plain.trace(case_id).activity_ids != ranked.trace(case_id).activity_ids
    )
    return {
        "events": plain.event_count,
        "cases": plain.case_count,
        "tie_broken_events": len(plain.tie_broken),
        "tie_broken_share": len(plain.tie_broken) / plain.event_count if plain.event_count else 0.0,
        "cases_affected": len(plain.tie_broken_cases()),
        "consequential_ties": consequential,
        "consequential_tie_share": consequential / plain.event_count if plain.event_count else 0.0,
        "variants_arrival_order": len(plain_variants),
        "variants_declared_order": len(ranked_variants),
        "variants_collapsed": len(plain_variants) - len(ranked_variants),
        "cases_whose_sequence_changed": changed,
    }


def events_of(log) -> list[tuple[str, str, int]]:
    """(case_id, activity, timestamp) triples in the log's stored order."""
    rows: list[tuple[str, str, int]] = []
    for trace in log.traces():
        for position in trace.indices:
            rows.append(
                (
                    trace.case_id,
                    log.activities.name_of(log.activity_id[position]),
                    log.timestamp[position],
                )
            )
    return rows
