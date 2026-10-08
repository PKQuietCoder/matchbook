"""The canonical event log: one in-memory representation for every log.

The same object serves the real human log (BPI 2019), the fitted synthetic log,
and the log mined back out of the agent's own OTel spans. That is the whole
point of the project -- a human recording and an agent recording of the *same*
business process, comparable because they share one representation and one
activity alphabet.

Representation, and why it is not a list of dicts
-------------------------------------------------
BPI 2019 is 1,595,923 events. A list of per-event dicts costs hundreds of bytes
per event and makes every scan a dictionary lookup. So the log is *columnar*:

  - activities and resources are **interned** to small ints, so the hot loops
    compare ints and the DFG is an int-keyed dict;
  - timestamps are epoch seconds in an `array("q")`;
  - events are stored sorted by (case, position), and a case is a *slice*
    `events[start:end]` found through `case_offsets` -- never a copy.

A trace is then a tuple of activity ids, and a variant is just that tuple,
which makes variant counting a `Counter` over tuples rather than a join.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

# Resource kinds. The human/batch split is real data in BPI 2019 (607 human
# users, 20 batch users) and it is the axis the agent's own log extends.
HUMAN = "human"
BATCH = "batch"
VENDOR = "vendor"
AGENT = "agent"
TOOL = "tool"
UNKNOWN = "unknown"

# Timestamp precision. BPI 2019 records several activities at day granularity,
# so intra-day order is genuinely unknown. We never pretend otherwise: the
# precision travels with the event and tie-broken cases are reported.
SECOND = "second"
DAY = "day"


class Interner:
    """Maps strings to dense ints and back. Stable insertion order."""

    __slots__ = ("_ids", "_names")

    def __init__(self, names: Iterable[str] = ()) -> None:
        self._ids: dict[str, int] = {}
        self._names: list[str] = []
        for name in names:
            self.intern(name)

    def intern(self, name: str) -> int:
        existing = self._ids.get(name)
        if existing is not None:
            return existing
        new_id = len(self._names)
        self._ids[name] = new_id
        self._names.append(name)
        return new_id

    def id_of(self, name: str) -> int | None:
        return self._ids.get(name)

    def name_of(self, identifier: int) -> str:
        return self._names[identifier]

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def __contains__(self, name: object) -> bool:
        return name in self._ids

    def __iter__(self) -> Iterator[tuple[int, str]]:
        return iter(enumerate(self._names))


@dataclass(frozen=True)
class Trace:
    """One case, as a view over the parent log. Cheap to construct."""

    log: "EventLog"
    case_id: str
    start: int
    end: int

    def __len__(self) -> int:
        return self.end - self.start

    @property
    def indices(self) -> range:
        return range(self.start, self.end)

    @property
    def activity_ids(self) -> tuple[int, ...]:
        return tuple(self.log.activity_id[self.start : self.end])

    @property
    def activities(self) -> tuple[str, ...]:
        name_of = self.log.activities.name_of
        return tuple(name_of(a) for a in self.log.activity_id[self.start : self.end])

    @property
    def timestamps(self) -> tuple[int, ...]:
        return tuple(self.log.timestamp[self.start : self.end])

    @property
    def resources(self) -> tuple[str, ...]:
        name_of = self.log.resources.name_of
        return tuple(name_of(r) for r in self.log.resource_id[self.start : self.end])

    @property
    def attributes(self) -> dict[str, Any]:
        return self.log.case_attributes.get(self.case_id, {})

    @property
    def duration_seconds(self) -> int:
        if not len(self):
            return 0
        return self.log.timestamp[self.end - 1] - self.log.timestamp[self.start]

    def event(self, index: int) -> dict[str, Any]:
        """One event as a dict. For reporting, never for a hot loop."""
        return self.log.event(self.start + index)

    def events(self) -> Iterator[dict[str, Any]]:
        for position in self.indices:
            yield self.log.event(position)

    def __repr__(self) -> str:
        return f"<Trace {self.case_id} n={len(self)}>"


@dataclass
class EventLog:
    """A columnar event log, sorted by (case_id, position-in-case).

    Build one with :class:`EventLogBuilder`, or load one from
    `process.csvio` / `process.xes` / `process.store`.
    """

    log_id: str
    activities: Interner = field(default_factory=Interner)
    resources: Interner = field(default_factory=Interner)

    # Parallel event columns. All the same length.
    case_index: array = field(default_factory=lambda: array("i"))
    activity_id: array = field(default_factory=lambda: array("i"))
    resource_id: array = field(default_factory=lambda: array("i"))
    timestamp: array = field(default_factory=lambda: array("q"))
    value_cents: array = field(default_factory=lambda: array("q"))

    # Per-event, only where it differs from the default.
    precision: dict[int, str] = field(default_factory=dict)
    tie_broken: set[int] = field(default_factory=set)
    event_attributes: dict[int, dict[str, Any]] = field(default_factory=dict)

    # Per-case.
    case_ids: list[str] = field(default_factory=list)
    case_offsets: array = field(default_factory=lambda: array("i"))  # len(cases)+1
    case_attributes: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Provenance travels with the log, so a derived artifact can always cite it.
    source: str = ""
    license: str = ""
    attribution: str = ""

    # -- sizes ----------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.activity_id)

    @property
    def event_count(self) -> int:
        return len(self.activity_id)

    @property
    def case_count(self) -> int:
        return len(self.case_ids)

    @property
    def activity_count(self) -> int:
        return len(self.activities)

    # -- access ---------------------------------------------------------------

    def trace(self, case: int | str) -> Trace:
        index = self.case_position(case) if isinstance(case, str) else case
        return Trace(self, self.case_ids[index], self.case_offsets[index], self.case_offsets[index + 1])

    def case_position(self, case_id: str) -> int:
        position = bisect_left(self.case_ids, case_id)
        if position >= len(self.case_ids) or self.case_ids[position] != case_id:
            raise KeyError(f"no case {case_id!r} in log {self.log_id!r}")
        return position

    def traces(self) -> Iterator[Trace]:
        offsets = self.case_offsets
        for index, case_id in enumerate(self.case_ids):
            yield Trace(self, case_id, offsets[index], offsets[index + 1])

    def __iter__(self) -> Iterator[Trace]:
        return self.traces()

    def event(self, position: int) -> dict[str, Any]:
        record = {
            "case_id": self.case_ids[self.case_index[position]],
            "activity": self.activities.name_of(self.activity_id[position]),
            "timestamp": datetime.fromtimestamp(self.timestamp[position], tz=timezone.utc),
            "resource": self.resources.name_of(self.resource_id[position]),
            "value_cents": self.value_cents[position],
            "precision": self.precision.get(position, SECOND),
            "tie_broken": position in self.tie_broken,
        }
        extra = self.event_attributes.get(position)
        if extra:
            record["attributes"] = extra
        return record

    # -- derived views --------------------------------------------------------

    def activity_frequency(self) -> Counter[str]:
        counts: Counter[int] = Counter(self.activity_id)
        name_of = self.activities.name_of
        return Counter({name_of(a): n for a, n in counts.items()})

    def resource_frequency(self) -> Counter[str]:
        counts: Counter[int] = Counter(self.resource_id)
        name_of = self.resources.name_of
        return Counter({name_of(r): n for r, n in counts.items()})

    def variant_of(self, trace: Trace) -> tuple[int, ...]:
        return trace.activity_ids

    def variants(self) -> Counter[tuple[int, ...]]:
        return Counter(trace.activity_ids for trace in self.traces())

    def named_variant(self, variant: Sequence[int]) -> tuple[str, ...]:
        name_of = self.activities.name_of
        return tuple(name_of(a) for a in variant)

    def tie_broken_cases(self) -> list[str]:
        """Case ids whose event order was decided by the tie-break, not by data."""
        if not self.tie_broken:
            return []
        affected = {self.case_index[position] for position in self.tie_broken}
        return [self.case_ids[index] for index in sorted(affected)]

    def time_span(self) -> tuple[datetime, datetime] | None:
        if not len(self):
            return None
        low = min(self.timestamp)
        high = max(self.timestamp)
        return (
            datetime.fromtimestamp(low, tz=timezone.utc),
            datetime.fromtimestamp(high, tz=timezone.utc),
        )

    def summary(self) -> dict[str, Any]:
        span = self.time_span()
        return {
            "log_id": self.log_id,
            "events": self.event_count,
            "cases": self.case_count,
            "activities": self.activity_count,
            "resources": len(self.resources),
            "variants": len(self.variants()),
            "first_event": span[0].isoformat() if span else None,
            "last_event": span[1].isoformat() if span else None,
            "tie_broken_events": len(self.tie_broken),
            "license": self.license,
        }

    def select(self, case_ids: Iterable[str], *, log_id: str | None = None) -> "EventLog":
        """A new log over the named cases. Used by every filter."""
        wanted = list(dict.fromkeys(case_ids))
        builder = EventLogBuilder(
            log_id or f"{self.log_id}+selection",
            source=self.source,
            license=self.license,
            attribution=self.attribution,
        )
        for case_id in wanted:
            trace = self.trace(case_id)
            builder.add_case_attributes(case_id, trace.attributes)
            for position in trace.indices:
                builder.add(
                    case_id=case_id,
                    activity=self.activities.name_of(self.activity_id[position]),
                    timestamp=self.timestamp[position],
                    resource=self.resources.name_of(self.resource_id[position]),
                    value_cents=self.value_cents[position],
                    precision=self.precision.get(position, SECOND),
                    tie_broken=position in self.tie_broken,
                    attributes=self.event_attributes.get(position),
                    sort_hint=position,
                )
        return builder.build()


class EventLogBuilder:
    """Accumulates events in any order, then sorts them into an EventLog.

    Ingestion never assumes the source is ordered -- XES usually is, CSV
    exports often are not, and the agent's spans arrive interleaved across
    cases. `build()` does one stable sort and then slices cases out of it.

    Tie-breaking, stated explicitly because it changes results
    ----------------------------------------------------------
    Events are ordered by ``(timestamp, activity_rank, arrival_order)``. When
    two events in one case share a timestamp, `activity_rank` decides -- a
    declared, auditable ordering supplied by the caller (Matchbook reads it
    from facts.yaml) -- and `arrival_order` keeps the sort stable after that.
    Every event whose position was decided this way is recorded in
    ``EventLog.tie_broken`` so a conformance result computed over tied events
    can say so instead of quietly looking exact.
    """

    def __init__(
        self,
        log_id: str,
        *,
        activity_rank: dict[str, int] | None = None,
        source: str = "",
        license: str = "",
        attribution: str = "",
    ) -> None:
        self.log_id = log_id
        self.activity_rank = activity_rank or {}
        self.source = source
        self.license = license
        self.attribution = attribution

        self.activities = Interner()
        self.resources = Interner([""])  # id 0 is "no resource recorded"

        self._case_of: list[str] = []
        self._activity: list[int] = []
        self._resource: list[int] = []
        self._timestamp: list[int] = []
        self._value: list[int] = []
        self._precision: list[str] = []
        self._tie_broken_in: list[bool] = []
        self._attributes: list[dict[str, Any] | None] = []
        self._sort_hint: list[int] = []
        self._case_attributes: dict[str, dict[str, Any]] = {}

    def add(
        self,
        *,
        case_id: str,
        activity: str,
        timestamp: int | datetime,
        resource: str = "",
        value_cents: int = 0,
        precision: str = SECOND,
        tie_broken: bool = False,
        attributes: dict[str, Any] | None = None,
        sort_hint: int = 0,
    ) -> None:
        """Append one event.

        `sort_hint` resolves order *within* a timestamp when the true order is
        known -- the sub-second component of an agent span, for instance. It
        orders before `activity_rank` and, unlike the rank, does NOT mark the
        event as tie-broken, because nothing was assumed.
        """
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp = int(timestamp.timestamp())
        self._case_of.append(case_id)
        self._activity.append(self.activities.intern(activity))
        self._resource.append(self.resources.intern(resource or ""))
        self._timestamp.append(int(timestamp))
        self._value.append(int(value_cents))
        self._precision.append(precision)
        self._tie_broken_in.append(tie_broken)
        self._attributes.append(attributes or None)
        self._sort_hint.append(int(sort_hint))

    def add_case_attributes(self, case_id: str, attributes: dict[str, Any] | None) -> None:
        if not attributes:
            return
        self._case_attributes.setdefault(case_id, {}).update(attributes)

    def __len__(self) -> int:
        return len(self._activity)

    def build(self) -> EventLog:
        rank = self.activity_rank
        name_of = self.activities.name_of
        order = sorted(
            range(len(self._activity)),
            key=lambda i: (
                self._case_of[i],
                self._timestamp[i],
                self._sort_hint[i],
                rank.get(name_of(self._activity[i]), 0),
                i,
            ),
        )

        log = EventLog(
            log_id=self.log_id,
            activities=self.activities,
            resources=self.resources,
            source=self.source,
            license=self.license,
            attribution=self.attribution,
        )

        case_ids: list[str] = []
        case_offsets = array("i", [0])
        previous_case: str | None = None
        previous_timestamp: int | None = None
        previous_hint: int | None = None

        for new_position, old_position in enumerate(order):
            case_id = self._case_of[old_position]
            if case_id != previous_case:
                if previous_case is not None:
                    case_offsets.append(new_position)
                case_ids.append(case_id)
                previous_case = case_id
                previous_timestamp = None
                previous_hint = None
            case_number = len(case_ids) - 1

            timestamp = self._timestamp[old_position]
            log.case_index.append(case_number)
            log.activity_id.append(self._activity[old_position])
            log.resource_id.append(self._resource[old_position])
            log.timestamp.append(timestamp)
            log.value_cents.append(self._value[old_position])

            precision = self._precision[old_position]
            if precision != SECOND:
                log.precision[new_position] = precision
            # A tie the sort had to break: same case, same instant, no
            # sub-second evidence to separate them, and not the case's first
            # event. Where a sort_hint distinguishes them the order is known,
            # so it is not recorded as an assumption.
            hint = self._sort_hint[old_position]
            if self._tie_broken_in[old_position] or (
                previous_timestamp is not None
                and timestamp == previous_timestamp
                and hint == previous_hint
            ):
                log.tie_broken.add(new_position)
            previous_timestamp = timestamp
            previous_hint = hint

            extra = self._attributes[old_position]
            if extra:
                log.event_attributes[new_position] = extra

        # The invariant is len(case_offsets) == len(case_ids) + 1, so an empty
        # log must carry [0] and not [0, 0]. Appending unconditionally gave the
        # latter, which is harmless today only because nothing reads the last
        # offset of an empty log.
        if case_ids:
            case_offsets.append(len(order))
        log.case_ids = case_ids
        log.case_offsets = case_offsets
        # The membership set is built ONCE. Written inline in the comprehension
        # it is rebuilt per candidate, which is quadratic: harmless at 2,000
        # cases (0.04s) and ~23 minutes at the full log's 251,734. That is the
        # shape of bug a small fixture hides and a real dataset exposes.
        kept_cases = set(case_ids)
        log.case_attributes = {
            case_id: attributes
            for case_id, attributes in self._case_attributes.items()
            if case_id in kept_cases
        }
        return log
