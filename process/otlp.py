"""OTLP/JSON span export and re-import for the canonical log.

Matchbook's human baseline is IEEE XES; agent telemetry everywhere else is
OpenTelemetry. This module is the bridge between those two worlds, so the human
log and the agent's own log (via `bridge.spans_to_log`) can be opened by the
same trace tooling. `observability/spans.py` already declared the intent -- its
span shape is "OTel-compatible on purpose" -- and this is the exporter it
promised.

It is hand-written, standard library only. There is no `opentelemetry`
dependency and deliberately so: `pyproject.toml` records that an unused
`opentelemetry-sdk` extra was deleted because nothing imported it, and
`tests/test_offline_mining.py` blocks the package outright. An exporter that
needed an SDK to emit JSON would put the mining half on the wrong side of that
line.

What the mapping asserts, and what it refuses to assert
-------------------------------------------------------
One trace per case, one root span per case, one child span per event.

* **Event spans have zero duration.** BPI 2019 carries no `lifecycle:transition`
  attribute on any of its 1,595,923 events, so an event is an instant and there
  is no second timestamp to pair with it. `startTimeUnixNano` equals
  `endTimeUnixNano`. Nothing is interpolated to give a UI a bar to draw.
* **The root's interval is measured**, first to last recorded timestamp. It is
  calendar time -- it includes net-30 payment waits -- not work time.
* **Spans are flat.** The log records no nesting, so every event span is a
  sibling under the case root. A call tree would be invention.
* **No span carries a `status`.** The log records no per-event outcome, so an
  error rate computed from this file is undefined, not zero.
* **Order lives in `mb.seq`.** Timestamps are minute-precision and same-minute
  ties affect ~17% of events; within a minute the order comes from the declared
  `activity_rank` in `facts.yaml`, and those events carry `mb.tie_broken`. A
  consumer sorting by `startTimeUnixNano` will reorder them arbitrarily.

Why child spans rather than span events
---------------------------------------
OTel's exact model for a timestamped instant is `Span.events[]`, not a child
span, and it would be cheaper. It was rejected: span-event rendering is poor to
absent in most UIs, a 990-event root span (this log's largest case) is unusable,
and a process-mining consumer expecting one span per activity would find one
span per case instead. Child spans are a choice, not an oversight.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from process.log import SECOND, EventLog, EventLogBuilder, Trace
from process.xes import (
    BPIC19_VALUE_ATTRIBUTE,
    CASE_ATTRIBUTE,
    add_trace_events,
    classify_resource,
    iter_traces,
)

SCOPE_NAME = "matchbook.process.otlp"

# The exporter's own attribute schema, not the repo version. It changes when the
# attribute registry below changes shape, so a reader can refuse a file it does
# not understand instead of silently mis-reading it.
SCHEMA_VERSION = "1"

# OTLP/JSON encodes enum fields as INTEGERS. This is a deliberate deviation from
# the standard protobuf JSON mapping, which would emit the name
# ("SPAN_KIND_INTERNAL"): the OTLP spec says "only integer enum values are
# allowed in OTLP JSON Protobuf Encoding; the enum name strings MUST NOT be
# used." Most collectors accept either on read, which is exactly why this is
# easy to get wrong in both directions. Do not "fix" it to the name.
SPAN_KIND_INTERNAL = 1

# -- the attribute registry -----------------------------------------------------
# Writer and reader share one definition of every key. A typo is then caught by
# `test_attribute_names_are_declared` rather than by eye, and a key that stops
# being emitted shows up as a dead constant.

ATTR_CASE_ID = "mb.case_id"
ATTR_SEQ = "mb.seq"
ATTR_ACTIVITY = "mb.activity"
ATTR_RESOURCE = "mb.resource"
ATTR_RESOURCE_KIND = "mb.resource.kind"
ATTR_VALUE_CENTS = "mb.value_cents"
ATTR_PRECISION = "mb.timestamp.precision"
ATTR_TIE_BROKEN = "mb.tie_broken"
ATTR_EVENT_COUNT = "mb.event_count"
ATTR_TIE_BROKEN_COUNT = "mb.tie_broken_events"

CASE_ATTRIBUTE_PREFIX = "mb.case."
EVENT_ATTRIBUTE_PREFIX = "mb.event."

ROOT_ATTRIBUTES = (ATTR_CASE_ID, ATTR_EVENT_COUNT, ATTR_TIE_BROKEN_COUNT)
EVENT_ATTRIBUTES = (
    ATTR_CASE_ID,
    ATTR_SEQ,
    ATTR_ACTIVITY,
    ATTR_RESOURCE,
    ATTR_RESOURCE_KIND,
    ATTR_VALUE_CENTS,
    ATTR_PRECISION,
    ATTR_TIE_BROKEN,
)
RESOURCE_ATTRIBUTES = (
    "service.name",
    "mb.schema_version",
    "mb.log.source",
    "mb.log.license",
    "mb.log.attribution",
    "mb.log.doi",
    "mb.log.case_notion",
    "mb.log.timestamp_precision",
    "mb.log.order_assumption",
)
ATTRIBUTE_PREFIXES = (CASE_ATTRIBUTE_PREFIX, EVENT_ATTRIBUTE_PREFIX)

# A span name should be low-cardinality, so the root takes the case's flow
# rather than its id -- BPI 2019 has four Item Category values across 251,734
# cases. The id is on `mb.case_id`, where identity belongs.
ROOT_NAME_ATTRIBUTE = "Item Category"

_NANOS = 1_000_000_000


# -- provenance -----------------------------------------------------------------


@dataclass(frozen=True)
class ExportHeader:
    """Provenance that travels inside the artifact rather than beside it.

    An `EventLog` carries source/license/attribution but not the DOI, which
    lives in the store's `logs` row -- so the header is explicit instead of
    being read off a Trace's parent log. Being explicit is also what lets the
    writer consume a bare iterator of traces: the streaming path builds one
    header and 251,734 single-case logs.

    `timestamp_precision` is the MEASURED log-level fact ("minute" for BPI
    2019). It is deliberately not the same as the per-event `mb.timestamp.
    precision`, which carries the canonical model's own value so the export
    round-trips. See the module docstring and `logs/README.md`.
    """

    log_id: str
    source: str = ""
    license: str = ""
    attribution: str = ""
    doi: str = ""
    case_notion: str = "purchase-order item"
    timestamp_precision: str = "minute"
    order_assumption: str = "facts.yaml activity_rank"
    filters: dict[str, Any] = field(default_factory=dict)

    def resource(self) -> dict[str, Any]:
        """The OTLP Resource for this export."""
        values = {
            "service.name": self.service_name(),
            "mb.schema_version": SCHEMA_VERSION,
            "mb.log.source": self.source,
            "mb.log.license": self.license,
            "mb.log.attribution": self.attribution,
            "mb.log.doi": self.doi,
            "mb.log.case_notion": self.case_notion,
            "mb.log.timestamp_precision": self.timestamp_precision,
            "mb.log.order_assumption": self.order_assumption,
        }
        return {"attributes": _attributes({k: v for k, v in values.items() if v != ""})}

    def service_name(self) -> str:
        """The log id, plus any filter, so a filtered export self-identifies.

        A `--flow Consignment` export is a valid OTLP file carrying the full
        log's license and DOI. Without this it would look like the whole log.
        """
        parts = [self.log_id]
        for key in sorted(self.filters):
            value = self.filters[key]
            if value not in (None, "", False):
                parts.append(f"{key}={value}")
        return "+".join(parts)


def header_of(log: EventLog, *, doi: str = "", **extra: Any) -> ExportHeader:
    """Build an export header from a log's own provenance.

    `extra` overrides rather than collides: a caller that knows the stable
    relative source path, or an attribution the log was loaded without, can say
    so without having to rebuild the whole header by hand.
    """
    fields: dict[str, Any] = {
        "log_id": log.log_id,
        "source": log.source,
        "license": log.license,
        "attribution": log.attribution,
        "doi": doi,
    }
    fields.update(extra)
    return ExportHeader(**fields)


@dataclass
class ExportStats:
    """Counts with denominators, gathered while writing.

    Every number the disclosure quotes is counted here as the spans are emitted,
    never recomputed afterwards by a second pass that could disagree with the
    file it is describing.
    """

    cases: int = 0
    events: int = 0
    spans: int = 0
    lines: int = 0
    bytes_written: int = 0
    largest_line_bytes: int = 0
    largest_case_events: int = 0
    tie_broken_events: int = 0
    non_second_precision_events: int = 0
    events_without_resource: int = 0
    whole_minute_events: int = 0
    value_cents_total: int = 0
    activities: Counter = field(default_factory=Counter)

    def as_record(self) -> dict[str, Any]:
        record = {
            "cases": self.cases,
            "events": self.events,
            "spans": self.spans,
            "lines": self.lines,
            "bytes_written": self.bytes_written,
            "largest_line_bytes": self.largest_line_bytes,
            "largest_case_events": self.largest_case_events,
            "distinct_activities": len(self.activities),
            "tie_broken_events": self.tie_broken_events,
            "non_second_precision_events": self.non_second_precision_events,
            "events_without_resource": self.events_without_resource,
            "whole_minute_events": self.whole_minute_events,
            "value_cents_total": self.value_cents_total,
        }
        # A bare count is not interpretable. Rates ship with their denominator.
        if self.events:
            record["tie_broken_share"] = round(self.tie_broken_events / self.events, 4)
            record["whole_minute_share"] = round(self.whole_minute_events / self.events, 4)
        return record


# -- OTLP/JSON encoding ---------------------------------------------------------


def _any_value(value: Any) -> dict[str, Any]:
    """Wrap a Python value as an OTLP `AnyValue`.

    The trap: proto3 JSON maps every 64-bit integer to a STRING, so an int must
    be emitted as `{"intValue": "42"}`, not `{"intValue": 42}`. This is the most
    common bug in hand-written OTLP and collectors differ in how loudly they
    complain.

    `bool` is checked before `int` because in Python `True` is an `int`, and
    emitting `{"intValue": "1"}` for a boolean would lose the type on the way
    back -- the committed CSV path already loses it, and this module exists
    partly to stop doing that.
    """
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_any_value(item) for item in value]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(k), "value": _any_value(v)} for k, v in sorted(value.items())
                ]
            }
        }
    return {"stringValue": str(value)}


def _plain_value(any_value: dict[str, Any]) -> Any:
    """Unwrap an OTLP `AnyValue`. The inverse of `_any_value`."""
    if "stringValue" in any_value:
        return any_value["stringValue"]
    if "boolValue" in any_value:
        return bool(any_value["boolValue"])
    if "intValue" in any_value:
        return int(any_value["intValue"])
    if "doubleValue" in any_value:
        return float(any_value["doubleValue"])
    if "arrayValue" in any_value:
        return [_plain_value(item) for item in any_value["arrayValue"].get("values", [])]
    if "kvlistValue" in any_value:
        return {
            entry["key"]: _plain_value(entry["value"])
            for entry in any_value["kvlistValue"].get("values", [])
        }
    return None


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    """An OTLP KeyValue list, key-sorted so the bytes are reproducible."""
    return [{"key": key, "value": _any_value(values[key])} for key in sorted(values)]


def _read_attributes(span: dict[str, Any]) -> dict[str, Any]:
    return {entry["key"]: _plain_value(entry["value"]) for entry in span.get("attributes", [])}


def trace_id(log_id: str, case_id: str) -> str:
    """A stable 16-byte trace id for one case, as 32 lowercase hex characters.

    Derived rather than random so that two exports of the same log are
    byte-identical and the committed fixture can be diffed. `\\x00` separates
    the parts so no two different (log, case) pairs can concatenate alike.
    """
    digest = hashlib.sha256(f"{log_id}\x00{case_id}".encode()).hexdigest()[:32]
    if digest == "0" * 32:
        raise ValueError(f"all-zero trace id for case {case_id!r}; invalid per the OTLP spec")
    return digest


def span_id(log_id: str, case_id: str, seq: int, activity: str) -> str:
    """A stable 8-byte span id, as 16 lowercase hex characters."""
    raw = f"{log_id}\x00{case_id}\x00{seq}\x00{activity}".encode()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    if digest == "0" * 16:
        raise ValueError(f"all-zero span id for {case_id!r} seq {seq}; invalid per the OTLP spec")
    return digest


def root_span_id(log_id: str, case_id: str) -> str:
    """The case root's span id. `seq = -1` keeps it out of the event id space."""
    return span_id(log_id, case_id, -1, "")


def spans_for_trace(trace: Trace, header: ExportHeader) -> list[dict[str, Any]]:
    """One case as a root span plus one zero-duration child span per event.

    The children have `startTimeUnixNano == endTimeUnixNano` because the source
    records instants; see the module docstring. The root's interval is the only
    measured duration here, and it is calendar time, not work time.
    """
    positions = list(trace.indices)
    if not positions:
        return []

    log = trace.log
    case_id = trace.case_id
    log_id = header.log_id
    attributes = trace.attributes

    timestamps = [log.timestamp[p] for p in positions]
    tie_broken = sum(1 for p in positions if p in log.tie_broken)

    root_id = root_span_id(log_id, case_id)
    root_values: dict[str, Any] = {
        ATTR_CASE_ID: case_id,
        ATTR_EVENT_COUNT: len(positions),
        ATTR_TIE_BROKEN_COUNT: tie_broken,
    }
    for key, value in attributes.items():
        root_values[f"{CASE_ATTRIBUTE_PREFIX}{key}"] = value

    trace_hex = trace_id(log_id, case_id)
    spans: list[dict[str, Any]] = [
        {
            "traceId": trace_hex,
            "spanId": root_id,
            "name": str(attributes.get(ROOT_NAME_ATTRIBUTE) or header.case_notion),
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": str(min(timestamps) * _NANOS),
            "endTimeUnixNano": str(max(timestamps) * _NANOS),
            "attributes": _attributes(root_values),
        }
    ]

    seen_ids = {root_id}
    for seq, position in enumerate(positions):
        activity = log.activities.name_of(log.activity_id[position])
        resource = log.resources.name_of(log.resource_id[position])
        precision = log.precision.get(position, SECOND)
        nanos = str(log.timestamp[position] * _NANOS)

        values: dict[str, Any] = {
            ATTR_CASE_ID: case_id,
            ATTR_SEQ: seq,
            ATTR_ACTIVITY: activity,
            ATTR_RESOURCE: resource,
            ATTR_RESOURCE_KIND: classify_resource(resource),
            ATTR_VALUE_CENTS: log.value_cents[position],
        }
        # Sparse, mirroring the canonical model where `precision` is a dict and
        # `tie_broken` a set: absence means the default, and on the full log
        # this is tens of megabytes.
        if precision != SECOND:
            values[ATTR_PRECISION] = precision
        if position in log.tie_broken:
            values[ATTR_TIE_BROKEN] = True
        for key, value in (log.event_attributes.get(position) or {}).items():
            values[f"{EVENT_ATTRIBUTE_PREFIX}{key}"] = value

        event_id = span_id(log_id, case_id, seq, activity)
        if event_id in seen_ids:
            raise ValueError(f"span id collision in case {case_id!r} at seq {seq}")
        seen_ids.add(event_id)

        spans.append(
            {
                "traceId": trace_hex,
                "spanId": event_id,
                "parentSpanId": root_id,
                # `name` duplicates mb.activity on purpose: the file stays
                # idiomatic for a span UI, and the reader never has to trust a
                # display name. A test asserts the two agree.
                "name": activity,
                "kind": SPAN_KIND_INTERNAL,
                "startTimeUnixNano": nanos,
                "endTimeUnixNano": nanos,
                "attributes": _attributes(values),
            }
        )
    return spans


# -- trace sources --------------------------------------------------------------


def traces_of_log(log: EventLog) -> Iterator[Trace]:
    """Traces of a log already in memory. Cases come out in case-id order."""
    return log.traces()


def traces_of_xes(
    path: str | Path,
    *,
    log_id: str,
    activity_rank: dict[str, int] | None = None,
    case_attribute: str = CASE_ATTRIBUTE,
    value_attribute: str | None = BPIC19_VALUE_ATTRIBUTE,
    max_cases: int | None = None,
    keep_case: Callable[[dict[str, Any]], bool] | None = None,
) -> Iterator[Trace]:
    """Stream a raw XES one case at a time, never holding the whole log.

    Each `<trace>` becomes a one-case `EventLog` through the normal
    `EventLogBuilder`, so the declared tie-break and the `tie_broken` audit are
    applied exactly as a whole-log ingest applies them. That is sound because
    the builder's sort key and its tie marking are both case-LOCAL -- the sort
    keys on case id first, and the tie comparison resets at every case boundary
    -- and because no case id repeats across traces in this file (measured:
    251,734 distinct ids in 251,734 traces).

    This matters for more than memory. Going around the builder to save a
    kilobyte is how tie-break marking gets silently lost, and a lost audit
    trail is indistinguishable from an exact result.

    Measured on the full 728 MB log: 81 MB peak resident and 59.6s for
    1,847,657 spans, where reading the same log into one `EventLog` first costs
    ~1.4 GB. Memory is flat in the number of cases, not merely smaller.

    Cases come out in SOURCE order, which in this file is not case-id order.
    Identity lives in `mb.case_id`, never in file position.
    """
    kept = 0
    seen = 0
    for trace_attributes, events in iter_traces(path):
        seen += 1
        if keep_case is not None and not keep_case(trace_attributes):
            continue
        case_id = str(trace_attributes.get(case_attribute) or f"case-{seen}")
        builder = EventLogBuilder(log_id, activity_rank=activity_rank, source=str(path))
        builder.add_case_attributes(case_id, trace_attributes)
        add_trace_events(builder, case_id, events, value_attribute=value_attribute)
        if not len(builder):
            continue
        one_case = builder.build()
        yield one_case.trace(0)
        kept += 1
        if max_cases is not None and kept >= max_cases:
            break


# -- writing --------------------------------------------------------------------


def _open_write(path: Path):
    """Text stream for writing, gzipped when the path ends in .gz.

    Unlike `process.csvio._open` this uses `GzipFile(..., mtime=0)` rather than
    `gzip.open`, which stamps the current time into the header and makes the
    bytes differ between two identical exports. The committed fixture is
    diff-tested, so byte stability is a requirement here. Do not simplify this
    back to `gzip.open`.
    """
    if str(path).endswith(".gz"):
        raw = open(path, "wb")
        # `filename=""` as well as `mtime=0`: given only a fileobj, GzipFile
        # copies `fileobj.name` into the gzip header, so two exports to
        # different paths would differ in bytes that are not content.
        return io.TextIOWrapper(
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0),
            encoding="utf-8",
            newline="\n",
        )
    return open(path, "w", encoding="utf-8", newline="\n")


def _open_read(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _request_line(spans: list[dict[str, Any]], header: ExportHeader) -> str:
    """One `ExportTraceServiceRequest` as a single compact JSON line.

    Resource and scope repeat on every line because each line must stand alone:
    a line is POST-able to an OTLP/HTTP collector unchanged.
    """
    payload = {
        "resourceSpans": [
            {
                "resource": header.resource(),
                "scopeSpans": [
                    {"scope": {"name": SCOPE_NAME, "version": SCHEMA_VERSION}, "spans": spans}
                ],
            }
        ]
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def write_otlp(
    traces: Iterable[Trace],
    path: str | Path,
    header: ExportHeader,
    *,
    batch_spans: int = 2000,
    on_progress: Callable[[ExportStats], None] | None = None,
) -> ExportStats:
    """Write traces as OTLP/JSON spans, NDJSON, one request per line.

    Gzipped when the path ends in .gz. A case is never split across lines, so a
    reader can attach case attributes without buffering and a consumer can take
    any single line as a complete request. `batch_spans` is therefore a floor,
    not a cap: a batch closes once it is reached, after the current case is
    complete.

    Consumes the traces lazily, so the streaming XES source never materialises
    the whole log.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stats = ExportStats()
    pending: list[dict[str, Any]] = []

    def flush(handle) -> None:
        if not pending:
            return
        line = _request_line(pending, header)
        handle.write(line)
        handle.write("\n")
        stats.lines += 1
        size = len(line.encode("utf-8"))
        stats.bytes_written += size + 1
        stats.largest_line_bytes = max(stats.largest_line_bytes, size)
        pending.clear()

    with _open_write(destination) as handle:
        for trace in traces:
            spans = spans_for_trace(trace, header)
            if not spans:
                continue
            log = trace.log
            stats.cases += 1
            events = len(spans) - 1
            stats.events += events
            stats.spans += len(spans)
            stats.largest_case_events = max(stats.largest_case_events, events)
            for position in trace.indices:
                activity = log.activities.name_of(log.activity_id[position])
                stats.activities[activity] += 1
                stats.value_cents_total += log.value_cents[position]
                if position in log.tie_broken:
                    stats.tie_broken_events += 1
                if log.precision.get(position, SECOND) != SECOND:
                    stats.non_second_precision_events += 1
                if not log.resources.name_of(log.resource_id[position]):
                    stats.events_without_resource += 1
                # Measured, not inherited: the dataset is described as
                # day-granularity and is in fact minute-precision.
                if log.timestamp[position] % 60 == 0:
                    stats.whole_minute_events += 1
            pending.extend(spans)
            if len(pending) >= batch_spans:
                flush(handle)
                if on_progress is not None:
                    on_progress(stats)
        flush(handle)

    if on_progress is not None:
        on_progress(stats)
    return stats


# -- reading back ---------------------------------------------------------------


def iter_spans(path: str | Path) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Yield (resource, scope, span) per span, in file order."""
    with _open_read(Path(path)) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            for resource_spans in payload.get("resourceSpans", []):
                resource = resource_spans.get("resource", {})
                for scope_spans in resource_spans.get("scopeSpans", []):
                    scope = scope_spans.get("scope", {})
                    for span in scope_spans.get("spans", []):
                        yield resource, scope, span


def read_otlp(path: str | Path, *, log_id: str | None = None) -> EventLog:
    """Read OTLP/JSON spans back into the canonical log.

    Deliberately takes no `activity_rank`. The order is RESTORED from the
    record, not re-derived: `mb.seq` is passed as `sort_hint` -- which orders
    before `activity_rank` and does not itself mark an event as assumed -- and
    `mb.tie_broken` is passed straight through, exactly as `EventLog.select`
    does. So the reconstruction reproduces the emitted order and keeps the
    original audit trail instead of computing a fresh one on top of a file that
    has already been ranked once.

    Offering a rank here would invite re-ranking an already-ranked export,
    which is the trap `ingest --sensitivity` exists to warn about.
    """
    source = ""
    license_text = ""
    attribution = ""
    resolved = log_id
    case_attributes: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    # Read once, on the first span, rather than "until every field is non-empty".
    # The latter looks equivalent and is not: a log with no recorded license
    # never fills that field, so the guard never goes false and the resource
    # attributes get re-parsed 1.6M times.
    header_read = False

    for resource, _scope, span in iter_spans(path):
        if not header_read:
            header_read = True
            values = {
                entry["key"]: _plain_value(entry["value"])
                for entry in resource.get("attributes", [])
            }
            resolved = resolved or str(values.get("service.name") or "otlp")
            source = str(values.get("mb.log.source") or "")
            license_text = str(values.get("mb.log.license") or "")
            attribution = str(values.get("mb.log.attribution") or "")

        attributes = _read_attributes(span)
        case_id = str(attributes.get(ATTR_CASE_ID) or "")
        if not case_id:
            continue
        if not span.get("parentSpanId"):
            case_attributes[case_id] = {
                key[len(CASE_ATTRIBUTE_PREFIX) :]: value
                for key, value in attributes.items()
                if key.startswith(CASE_ATTRIBUTE_PREFIX)
            }
            continue
        events.append(
            {
                "case_id": case_id,
                "seq": int(attributes.get(ATTR_SEQ, 0)),
                "activity": str(attributes.get(ATTR_ACTIVITY) or span.get("name") or "UNKNOWN"),
                "timestamp": int(span["startTimeUnixNano"]) // _NANOS,
                "resource": str(attributes.get(ATTR_RESOURCE) or ""),
                "value_cents": int(attributes.get(ATTR_VALUE_CENTS, 0)),
                "precision": str(attributes.get(ATTR_PRECISION) or SECOND),
                "tie_broken": bool(attributes.get(ATTR_TIE_BROKEN, False)),
                "attributes": {
                    key[len(EVENT_ATTRIBUTE_PREFIX) :]: value
                    for key, value in attributes.items()
                    if key.startswith(EVENT_ATTRIBUTE_PREFIX)
                },
            }
        )

    builder = EventLogBuilder(
        resolved or "otlp", source=source, license=license_text, attribution=attribution
    )
    for case_id, attributes in case_attributes.items():
        builder.add_case_attributes(case_id, attributes)
    for record in events:
        builder.add(
            case_id=record["case_id"],
            activity=record["activity"],
            timestamp=record["timestamp"],
            resource=record["resource"],
            value_cents=record["value_cents"],
            precision=record["precision"],
            tie_broken=record["tie_broken"],
            attributes=record["attributes"] or None,
            sort_hint=record["seq"],
        )
    return builder.build()


# -- verification ---------------------------------------------------------------


def verify(path: str | Path, log: EventLog | None = None) -> dict[str, Any]:
    """Re-read an export and report every discrepancy, with denominators.

    Returns a report rather than a bool: a conservation failure should name
    what failed to conserve. Without `log`, only the self-consistency and
    OTLP-conformance checks run, so the command is still useful on a file whose
    source is no longer at hand.
    """
    problems: list[str] = []
    spans = 0
    roots = 0
    service_name = ""
    trace_ids: dict[str, str] = {}
    roots_by_trace: dict[str, str] = {}
    parents: list[tuple[str, str]] = []

    for resource, _scope, span in iter_spans(path):
        if not spans:
            service_name = str(
                next(
                    (
                        _plain_value(entry["value"])
                        for entry in resource.get("attributes", [])
                        if entry["key"] == "service.name"
                    ),
                    "",
                )
            )
        spans += 1
        hex_trace = span.get("traceId", "")
        hex_span = span.get("spanId", "")
        if len(hex_trace) != 32 or hex_trace.strip("0123456789abcdef"):
            problems.append(f"traceId is not 32 lowercase hex characters: {hex_trace!r}")
        if len(hex_span) != 16 or hex_span.strip("0123456789abcdef"):
            problems.append(f"spanId is not 16 lowercase hex characters: {hex_span!r}")
        if set(hex_trace) == {"0"} or set(hex_span) == {"0"}:
            problems.append(f"all-zero id in trace {hex_trace!r}")
        if span.get("kind") != SPAN_KIND_INTERNAL:
            problems.append(f"kind is not the integer {SPAN_KIND_INTERNAL}: {span.get('kind')!r}")
        if "status" in span:
            problems.append("a span carries a status; this log records no per-event outcome")
        for key in ("startTimeUnixNano", "endTimeUnixNano"):
            value = span.get(key)
            if not isinstance(value, str):
                problems.append(f"{key} must be a decimal string, got {type(value).__name__}")
            elif int(value) % _NANOS:
                problems.append(f"{key} is not a whole second: {value}")
        for entry in span.get("attributes", []):
            value = entry.get("value", {})
            if len(value) != 1:
                problems.append(f"AnyValue for {entry.get('key')!r} has {len(value)} fields")
            if "intValue" in value and not isinstance(value["intValue"], str):
                problems.append(f"intValue for {entry.get('key')!r} is not a JSON string")

        attributes = _read_attributes(span)
        case_id = str(attributes.get(ATTR_CASE_ID) or "")
        if not span.get("parentSpanId"):
            roots += 1
            roots_by_trace[hex_trace] = hex_span
            if case_id:
                trace_ids[case_id] = hex_trace
        else:
            parents.append((hex_trace, span["parentSpanId"]))
            if attributes.get(ATTR_ACTIVITY) != span.get("name"):
                problems.append(f"name and {ATTR_ACTIVITY} disagree in case {case_id!r}")
            if span["startTimeUnixNano"] != span["endTimeUnixNano"]:
                problems.append(f"event span in case {case_id!r} is not zero-duration")

    for hex_trace, parent in parents:
        if roots_by_trace.get(hex_trace) != parent:
            problems.append(f"parentSpanId {parent!r} is not the root of its own trace")

    report: dict[str, Any] = {
        "path": str(path),
        "spans": spans,
        "traces": roots,
        "events": spans - roots,
    }

    report["service_name"] = service_name

    if log is not None and service_name and service_name != log.log_id:
        # The export is a filtered slice -- `service_name` carries the filter,
        # e.g. "bpic19-sample+max_cases=50". Comparing a slice against the whole
        # log would emit six or seven "did not conserve" failures whose real
        # cause is the filter, which is worse than useless: it reads as a broken
        # exporter. A check that cannot run says so, the way a rule that cannot
        # be evaluated reports not-applicable rather than a count (see
        # process/rules.py).
        report["compared"] = {
            "evaluated": False,
            "not_applicable_because": (
                f"the export is a filtered slice ({service_name}) and {log.log_id!r} is the "
                "whole log, so conservation is not defined between them. Export without "
                "filters to compare, or drop --against to check conformance only."
            ),
        }
    elif log is not None:
        back = read_otlp(path)
        report["compared"] = {"evaluated": True}
        report["compared_against"] = log.log_id
        checks = {
            "cases": (back.case_count, log.case_count),
            "events": (back.event_count, log.event_count),
            "tie_broken_events": (len(back.tie_broken), len(log.tie_broken)),
            "value_cents_total": (sum(back.value_cents), sum(log.value_cents)),
            "activities": (back.activity_count, log.activity_count),
        }
        for name, (got, want) in checks.items():
            if got != want:
                problems.append(f"{name} did not conserve: export has {got}, log has {want}")
        if back.case_ids != log.case_ids:
            problems.append("case ids did not conserve")
        else:
            for case_id in log.case_ids:
                left, right = back.trace(case_id), log.trace(case_id)
                if left.activities != right.activities:
                    problems.append(f"activity sequence differs in case {case_id!r}")
                elif left.timestamps != right.timestamps:
                    problems.append(f"timestamps differ in case {case_id!r}")
                elif left.resources != right.resources:
                    problems.append(f"resources differ in case {case_id!r}")
        if back.tie_broken != log.tie_broken:
            problems.append("the tie-break audit did not conserve")

    report["problems"] = problems
    report["ok"] = not problems
    return report


# -- disclosure -----------------------------------------------------------------


def manifest(stats: ExportStats, header: ExportHeader) -> dict[str, Any]:
    """The machine-readable disclosure. No wall-clock field, so it is stable."""
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": SCOPE_NAME,
        "service_name": header.service_name(),
        "log_id": header.log_id,
        "source": header.source,
        "license": header.license,
        "attribution": header.attribution,
        "doi": header.doi,
        "case_notion": header.case_notion,
        "timestamp_precision_measured": header.timestamp_precision,
        "order_assumption": header.order_assumption,
        "filters": header.filters,
        "counts": stats.as_record(),
        "assertions_withheld": [
            "event duration (the source records instants)",
            "span nesting (the source records none)",
            "per-event outcome (no status is emitted)",
            "case-level actor (96.3% of BPI 2019 cases involve more than one resource)",
        ],
        "top_activities": dict(stats.activities.most_common(10)),
    }


def write_manifest(stats: ExportStats, header: ExportHeader, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest(stats, header), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def write_disclosure(stats: ExportStats, header: ExportHeader, path: str | Path) -> Path:
    """Write the human-readable disclosure beside the export.

    Modelled on `logs/snapshot/SAMPLE.md`: provenance, then what was measured
    against what was derived, then what was deliberately left unsaid. Every
    number carries its denominator, because a bare count is not interpretable.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    events = stats.events or 1
    ties = stats.tie_broken_events
    minutes = stats.whole_minute_events

    lines = [
        f"# OTLP export of `{header.log_id}`",
        "",
        "## Provenance",
        "",
        f"- Source: `{header.source or 'unrecorded'}`",
        f"- License: {header.license or 'unrecorded'}",
        f"- Attribution: {header.attribution or 'unrecorded'}",
        f"- DOI: {header.doi or 'unrecorded'}",
        f"- Case notion: {header.case_notion}",
        f"- Exporter: `{SCOPE_NAME}`, attribute schema {SCHEMA_VERSION}",
        "",
        "## What is in the file",
        "",
        f"- {stats.cases:,} traces, one per case",
        f"- {stats.spans:,} spans: {stats.events:,} event spans plus {stats.cases:,} case roots",
        f"- {len(stats.activities):,} distinct activities; largest case {stats.largest_case_events:,} events",
        f"- {stats.lines:,} NDJSON line{'' if stats.lines == 1 else 's'}, each a complete "
        "ExportTraceServiceRequest",
        f"- {stats.bytes_written:,} bytes written; largest line {stats.largest_line_bytes:,} bytes",
    ]
    if header.filters:
        shown = ", ".join(f"{k}={v}" for k, v in sorted(header.filters.items()) if v not in (None, "", False))
        if shown:
            lines += [
                "",
                f"**This is a filtered export** ({shown}). The {stats.cases:,} cases here are a",
                "subset; the license and DOI above describe the whole source dataset, not this slice.",
            ]

    lines += [
        "",
        "## Measured",
        "",
        f"- Order assumed for {ties:,} of {stats.events:,} events ({ties / events:.1%}): "
        "they share an instant with the preceding event in their case and were ordered by "
        f"the declared {header.order_assumption}. Those spans carry `{ATTR_TIE_BROKEN}`.",
        f"- {minutes:,} of {stats.events:,} events ({minutes / events:.1%}) fall on a whole "
        "minute, which is what the source's precision actually is.",
        f"- {stats.events_without_resource:,} events record no resource.",
        f"- {stats.non_second_precision_events:,} events carry a non-default precision.",
        "",
        "## Derived, and how to read it",
        "",
        "- **Event spans have zero duration.** The source records instants -- there is no",
        "  `lifecycle:transition` attribute anywhere in it -- so no event duration exists to",
        "  export and none was interpolated.",
        "- **A root span's interval is calendar time**, first to last recorded event, including",
        "  payment waits. It is not work time.",
        "- **Spans are flat.** The source records no nesting; every event span is a sibling.",
        f"- **Sort by `{ATTR_SEQ}`, not by time.** Tied events share `startTimeUnixNano`, so a",
        "  consumer ordering by timestamp will reorder them arbitrarily. No timestamp was",
        "  altered to make a UI render the declared order.",
        "",
        "## Withheld on purpose",
        "",
        "- **No `status` on any span.** The source records no per-event outcome. An error rate",
        "  computed from this file is undefined, not zero.",
        "- **No case-level actor.** Resources are per event. In BPI 2019, 242,457 of 251,734",
        "  cases (96.3%) involve more than one resource, so no single actor describes a case.",
        f"- **`{ATTR_VALUE_CENTS}` is a case-level figure** repeated on every event of a case,",
        "  not a per-event amount. A three-way value match is not computable from this log.",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
