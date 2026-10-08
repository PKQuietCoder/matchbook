"""Filters. Each returns a new log over the same alphabet, never a mutation.

Filtering is not a convenience here, it is a precondition. BPI 2019 has 11,973
variants over 251,734 cases, so discovery run on everything returns a flower
model. The honest workflow is to pick an explicit sublog -- one flow, or the
variants covering 80% of cases -- and say which, so the model's scope is part
of the result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from process import variants as variant_tools
from process.log import EventLog

FLOW_ATTRIBUTE = "Item Category"


def by_case_attribute(log: EventLog, key: str, values: Iterable[Any], *, log_id: str | None = None) -> EventLog:
    wanted = {str(value) for value in values}
    keep = [
        case_id
        for case_id in log.case_ids
        if str(log.case_attributes.get(case_id, {}).get(key, "")) in wanted
    ]
    return log.select(keep, log_id=log_id or f"{log.log_id}+{key}")


def by_flow(log: EventLog, *flows: str, log_id: str | None = None) -> EventLog:
    """One matching flow. The most useful single filter on this dataset.

    Comparing across flows is usually a mistake -- a consignment item has no
    invoice at PO level by design, so mixing it with 3-way-match items makes
    every aggregate meaningless.
    """
    return by_case_attribute(log, FLOW_ATTRIBUTE, flows, log_id=log_id or f"{log.log_id}+flow")


def by_variant_coverage(log: EventLog, coverage: float, *, log_id: str | None = None) -> EventLog:
    """The most frequent variants covering `coverage` of cases."""
    keep = variant_tools.cases_covering(log, coverage)
    return log.select(keep, log_id=log_id or f"{log.log_id}+cov{coverage:g}")


def containing_activity(log: EventLog, *activities: str, log_id: str | None = None) -> EventLog:
    wanted = {log.activities.id_of(name) for name in activities}
    wanted.discard(None)
    keep = [trace.case_id for trace in log.traces() if wanted & set(trace.activity_ids)]
    return log.select(keep, log_id=log_id or f"{log.log_id}+has")


def excluding_activity(log: EventLog, *activities: str, log_id: str | None = None) -> EventLog:
    wanted = {log.activities.id_of(name) for name in activities}
    wanted.discard(None)
    keep = [trace.case_id for trace in log.traces() if not (wanted & set(trace.activity_ids))]
    return log.select(keep, log_id=log_id or f"{log.log_id}+without")


def by_timeframe(
    log: EventLog,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    mode: str = "contained",
    log_id: str | None = None,
) -> EventLog:
    """Cases within a window.

    `mode` matters more than it looks, and silently choosing one is how people
    get wrong throughput numbers:

      - "contained": the whole case falls inside the window. Correct for
        duration statistics, but drops long cases, which biases the result
        toward fast ones.
      - "intersecting": any part of the case falls inside. Correct for "what
        was in flight", wrong for durations.
      - "started": the case started inside the window. Correct for arrival
        rates and cohort comparisons.
    """
    if mode not in ("contained", "intersecting", "started"):
        raise ValueError("mode must be 'contained', 'intersecting' or 'started'")
    low = int(start.replace(tzinfo=start.tzinfo or timezone.utc).timestamp()) if start else None
    high = int(end.replace(tzinfo=end.tzinfo or timezone.utc).timestamp()) if end else None

    keep: list[str] = []
    for trace in log.traces():
        if not len(trace):
            continue
        first = log.timestamp[trace.start]
        last = log.timestamp[trace.end - 1]
        if mode == "contained":
            if (low is None or first >= low) and (high is None or last <= high):
                keep.append(trace.case_id)
        elif mode == "intersecting":
            if (low is None or last >= low) and (high is None or first <= high):
                keep.append(trace.case_id)
        else:
            if (low is None or first >= low) and (high is None or first <= high):
                keep.append(trace.case_id)
    return log.select(keep, log_id=log_id or f"{log.log_id}+{mode}")


def by_case_length(
    log: EventLog, minimum: int = 0, maximum: int | None = None, *, log_id: str | None = None
) -> EventLog:
    keep = [
        trace.case_id
        for trace in log.traces()
        if len(trace) >= minimum and (maximum is None or len(trace) <= maximum)
    ]
    return log.select(keep, log_id=log_id or f"{log.log_id}+len")


def where(log: EventLog, predicate: Callable[[Any], bool], *, log_id: str | None = None) -> EventLog:
    """Escape hatch: keep cases whose Trace satisfies an arbitrary predicate."""
    keep = [trace.case_id for trace in log.traces() if predicate(trace)]
    return log.select(keep, log_id=log_id or f"{log.log_id}+where")
