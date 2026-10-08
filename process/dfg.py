"""The directly-follows graph: frequencies, timings, and the start/end sets.

The DFG is the workhorse. Nearly every other analysis in the library is a
question asked of it -- discovery cuts the DFG, bottleneck analysis ranks its
edges by waiting time, and the comparison between the human log and the agent's
log is a diff of two DFGs.

Edges are keyed by interned activity ids, so building one over 1.6M events is a
dict of int pairs rather than a graph object per node.

A deliberate choice worth knowing about: durations are recorded per edge as a
*histogram of log-spaced buckets*, not as a list of every observation. Keeping
1.6M durations to compute a median costs more memory than the whole log; 48
buckets per edge give quantiles accurate to within a bucket and cost nothing.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from process.log import EventLog

Edge = tuple[int, int]

# Log-spaced duration buckets: 0s, then ~1s up to ~8 years, 6 buckets per decade.
_BUCKET_COUNT = 48
_BUCKET_BASE = 10 ** (1 / 6)


def _bucket_of(seconds: int) -> int:
    if seconds <= 0:
        return 0
    index = 1 + int(math.log(seconds, _BUCKET_BASE))
    return min(index, _BUCKET_COUNT - 1)


def _bucket_lower_bound(index: int) -> float:
    if index <= 0:
        return 0.0
    return _BUCKET_BASE ** (index - 1)


def _bucket_representative(index: int) -> float:
    """The value a bucket stands for: its geometric midpoint.

    Reporting the lower bound instead would bias every quantile downwards by up
    to the full bucket width. The geometric midpoint of log-spaced buckets is
    within a factor of sqrt(base) = 1.21 of any member, so a reported quantile
    is accurate to about +/-21% in the worst case and considerably better in
    practice. That is the price of not keeping 1.6M durations in memory, and it
    is stated rather than hidden.
    """
    if index <= 0:
        return 0.0
    return _BUCKET_BASE ** (index - 0.5)


@dataclass
class EdgeStats:
    """Frequency and timing for one directly-follows relation."""

    count: int = 0
    case_count: int = 0
    total_seconds: int = 0
    min_seconds: int | None = None
    max_seconds: int | None = None
    buckets: Counter[int] = field(default_factory=Counter)
    tie_broken: int = 0

    def observe(self, seconds: int, *, tie_broken: bool = False) -> None:
        self.count += 1
        self.total_seconds += seconds
        if self.min_seconds is None or seconds < self.min_seconds:
            self.min_seconds = seconds
        if self.max_seconds is None or seconds > self.max_seconds:
            self.max_seconds = seconds
        self.buckets[_bucket_of(seconds)] += 1
        if tie_broken:
            self.tie_broken += 1

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.count if self.count else 0.0

    # Worst-case relative error of a reported quantile, from the bucket width.
    QUANTILE_TOLERANCE = _BUCKET_BASE ** 0.5 - 1  # ~0.21

    def quantile_seconds(self, q: float) -> float:
        """Approximate quantile from the bucket histogram.

        Accurate to within `QUANTILE_TOLERANCE` relative error; exact at the
        extremes, which are tracked precisely. See `_bucket_representative`.
        """
        if not self.count:
            return 0.0
        if q <= 0:
            return float(self.min_seconds or 0)
        target = q * self.count
        seen = 0
        for index in sorted(self.buckets):
            seen += self.buckets[index]
            if seen >= target:
                estimate = _bucket_representative(index)
                # Never report outside the range actually observed.
                low = float(self.min_seconds if self.min_seconds is not None else 0)
                high = float(self.max_seconds if self.max_seconds is not None else estimate)
                return min(max(estimate, low), high)
        return float(self.max_seconds or 0)

    @property
    def median_seconds(self) -> float:
        return self.quantile_seconds(0.5)


@dataclass
class DFG:
    """A directly-follows graph over one log's interned activity alphabet."""

    log: EventLog
    edges: dict[Edge, EdgeStats] = field(default_factory=dict)
    starts: Counter[int] = field(default_factory=Counter)
    ends: Counter[int] = field(default_factory=Counter)
    activity_counts: Counter[int] = field(default_factory=Counter)
    case_count: int = 0

    # -- names ----------------------------------------------------------------

    def name(self, activity_id: int) -> str:
        return self.log.activities.name_of(activity_id)

    def named_edges(self) -> dict[tuple[str, str], EdgeStats]:
        return {(self.name(a), self.name(b)): stats for (a, b), stats in self.edges.items()}

    # -- structure ------------------------------------------------------------

    @property
    def activities(self) -> set[int]:
        return set(self.activity_counts)

    def successors(self, activity_id: int) -> set[int]:
        return {b for (a, b) in self.edges if a == activity_id}

    def predecessors(self, activity_id: int) -> set[int]:
        return {a for (a, b) in self.edges if b == activity_id}

    def self_loops(self) -> dict[int, EdgeStats]:
        return {a: stats for (a, b), stats in self.edges.items() if a == b}

    def total_edge_count(self) -> int:
        return sum(stats.count for stats in self.edges.values())

    def __len__(self) -> int:
        return len(self.edges)

    # -- filtering ------------------------------------------------------------

    def filter_edges(self, *, min_count: int = 0, keep_fraction: float | None = None) -> "DFG":
        """Drop infrequent edges. This is the noise knob discovery depends on.

        `keep_fraction` keeps the heaviest edges accounting for that fraction of
        all observed transitions, which is the more useful control in practice:
        `keep_fraction=0.8` gives the spine of the process rather than a
        threshold whose meaning changes with log size.
        """
        kept = {edge: stats for edge, stats in self.edges.items() if stats.count >= min_count}
        if keep_fraction is not None and kept:
            ordered = sorted(kept.items(), key=lambda item: -item[1].count)
            budget = keep_fraction * sum(stats.count for _edge, stats in ordered)
            running = 0
            limited: dict[Edge, EdgeStats] = {}
            for edge, stats in ordered:
                if running >= budget:
                    break
                limited[edge] = stats
                running += stats.count
            kept = limited
        clone = DFG(
            log=self.log,
            edges=kept,
            starts=self.starts.copy(),
            ends=self.ends.copy(),
            activity_counts=self.activity_counts.copy(),
            case_count=self.case_count,
        )
        return clone

    # -- reporting ------------------------------------------------------------

    def top_edges(self, limit: int = 20) -> list[tuple[str, str, EdgeStats]]:
        ordered = sorted(self.edges.items(), key=lambda item: -item[1].count)[:limit]
        return [(self.name(a), self.name(b), stats) for (a, b), stats in ordered]

    def bottlenecks(self, limit: int = 20, *, min_count: int = 5) -> list[tuple[str, str, EdgeStats]]:
        """Edges ranked by total waiting time -- where the process actually sits.

        Ranked by *total* rather than mean, because a slow edge traversed twice
        is a curiosity and a medium edge traversed 40,000 times is the problem.
        """
        candidates = [
            (edge, stats) for edge, stats in self.edges.items() if stats.count >= min_count
        ]
        ordered = sorted(candidates, key=lambda item: -item[1].total_seconds)[:limit]
        return [(self.name(a), self.name(b), stats) for (a, b), stats in ordered]


def build(log: EventLog, *, activities: Iterable[int] | None = None) -> DFG:
    """Build the DFG for a log, in one pass over the event columns."""
    allowed = set(activities) if activities is not None else None
    graph = DFG(log=log)
    edges: dict[Edge, EdgeStats] = {}
    edge_cases: dict[Edge, int] = defaultdict(int)

    activity_column = log.activity_id
    timestamp_column = log.timestamp
    offsets = log.case_offsets

    for case_number in range(len(log.case_ids)):
        start = offsets[case_number]
        end = offsets[case_number + 1]
        if start == end:
            continue
        graph.case_count += 1

        previous_activity: int | None = None
        previous_timestamp = 0
        seen_in_case: set[Edge] = set()
        first_recorded = False

        for position in range(start, end):
            activity = activity_column[position]
            if allowed is not None and activity not in allowed:
                continue
            graph.activity_counts[activity] += 1
            if not first_recorded:
                graph.starts[activity] += 1
                first_recorded = True
            timestamp = timestamp_column[position]
            if previous_activity is not None:
                edge = (previous_activity, activity)
                stats = edges.get(edge)
                if stats is None:
                    stats = EdgeStats()
                    edges[edge] = stats
                stats.observe(
                    timestamp - previous_timestamp,
                    tie_broken=position in log.tie_broken,
                )
                if edge not in seen_in_case:
                    seen_in_case.add(edge)
                    edge_cases[edge] += 1
            previous_activity = activity
            previous_timestamp = timestamp

        if previous_activity is not None:
            graph.ends[previous_activity] += 1

    for edge, stats in edges.items():
        stats.case_count = edge_cases[edge]
    graph.edges = edges
    return graph


def diff(left: DFG, right: DFG) -> list[dict[str, object]]:
    """Compare two DFGs by activity *name*, not id.

    This is the project's headline comparison -- the human log against the
    agent's log -- and it only works because the bridge lifts the agent's tool
    spans onto the human log's activity alphabet. Edges present on one side
    only are the interesting rows.
    """
    left_edges = left.named_edges()
    right_edges = right.named_edges()
    rows: list[dict[str, object]] = []
    for edge in sorted(set(left_edges) | set(right_edges)):
        left_stats = left_edges.get(edge)
        right_stats = right_edges.get(edge)
        left_share = (
            left_stats.count / left.total_edge_count() if left_stats and left.total_edge_count() else 0.0
        )
        right_share = (
            right_stats.count / right.total_edge_count()
            if right_stats and right.total_edge_count()
            else 0.0
        )
        rows.append(
            {
                "from": edge[0],
                "to": edge[1],
                "left_count": left_stats.count if left_stats else 0,
                "right_count": right_stats.count if right_stats else 0,
                "left_share": left_share,
                "right_share": right_share,
                "share_delta": right_share - left_share,
                "only_in": (
                    "left" if right_stats is None else "right" if left_stats is None else ""
                ),
            }
        )
    return sorted(rows, key=lambda row: -abs(float(row["share_delta"])))
