"""Streaming IEEE-XES reader and writer.

The flagship log is a single 695 MB line of XML, so reading it is a streaming
problem: `iterparse`, and clear each `<trace>` the moment it is consumed so peak
memory stays flat instead of tracking file size. Nothing else in the library
depends on XES -- this module's job is to turn XES into the canonical
`EventLog` and back out again, so Matchbook logs round-trip into other tools.

BPI 2019's actual schema, read off the file rather than from a paper
---------------------------------------------------------------------
Trace attributes: ``concept:name`` (already "<document>_<item>"),
``Purchasing Document``, ``Item``, ``Item Type``, ``Item Category``,
``GR-Based Inv. Verif.`` (boolean), ``Goods Receipt`` (boolean), ``Source``,
``Purch. Doc. Category name``, ``Company``, ``Spend classification text``,
``Spend area text``, ``Sub spend area text``, ``Vendor``, ``Name``,
``Document Type``.

Event attributes: ``concept:name``, ``time:timestamp``, ``org:resource``,
``User``, ``Cumulative net worth (EUR)`` (float).

Two things that matter downstream. Timestamps are recorded only to the minute,
and several events in a case routinely share one -- the first trace in the file
has three events at 13:53:00 -- so the declared tie-break in
`EventLogBuilder` is load-bearing, not a detail. And resources are named by
convention (``batch_00`` ... for the 20 batch users, ``user_NNN`` for the 607
humans), which is where the human/automation split comes from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from xml.etree.ElementTree import iterparse

from process.log import BATCH, DAY, HUMAN, SECOND, UNKNOWN, VENDOR, EventLog, EventLogBuilder

# XES value-carrying element tags we understand.
_SCALAR_TAGS = {"string", "date", "int", "float", "boolean", "id"}

CASE_ATTRIBUTE = "concept:name"
ACTIVITY_ATTRIBUTE = "concept:name"
TIMESTAMP_ATTRIBUTE = "time:timestamp"
RESOURCE_ATTRIBUTE = "org:resource"
BPIC19_VALUE_ATTRIBUTE = "Cumulative net worth (EUR)"


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_timestamp(text: str) -> tuple[int, str]:
    """Parse an XES date into (epoch seconds, recorded precision).

    XES dates are ISO 8601, usually with milliseconds and a trailing Z. We
    report DAY precision when the time-of-day is exactly midnight, because in
    this log that is what a date-only record looks like once a tool has
    serialized it -- and a conformance claim built on a guessed intra-day order
    should know it is guessing.
    """
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    precision = DAY if (moment.hour, moment.minute, moment.second) == (0, 0, 0) else SECOND
    return int(moment.timestamp()), precision


def _coerce(tag: str, value: str) -> Any:
    if tag == "boolean":
        return value.strip().lower() == "true"
    if tag == "int":
        try:
            return int(value)
        except ValueError:
            return value
    if tag == "float":
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _attributes_of(element) -> dict[str, Any]:
    """Flat attribute dict for one <trace> or <event>, ignoring nested lists."""
    collected: dict[str, Any] = {}
    for child in element:
        tag = _strip_namespace(child.tag)
        if tag not in _SCALAR_TAGS:
            continue
        key = child.get("key")
        if key is None:
            continue
        collected[key] = _coerce(tag, child.get("value", ""))
    return collected


def iter_traces(path: str | Path) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Yield (trace attributes, event attribute dicts) one trace at a time.

    Memory stays flat: each <trace> is cleared as soon as it is yielded, and
    the root's children are dropped with it.
    """
    source = iter(iterparse(str(path), events=("start", "end")))
    _, root = next(source)
    for event_kind, element in source:
        if event_kind != "end":
            continue
        tag = _strip_namespace(element.tag)
        if tag != "trace":
            continue
        trace_attributes: dict[str, Any] = {}
        events: list[dict[str, Any]] = []
        for child in element:
            child_tag = _strip_namespace(child.tag)
            if child_tag == "event":
                events.append(_attributes_of(child))
            elif child_tag in _SCALAR_TAGS:
                key = child.get("key")
                if key is not None:
                    trace_attributes[key] = _coerce(child_tag, child.get("value", ""))
        yield trace_attributes, events
        element.clear()
        root.clear()


def classify_resource(name: str) -> str:
    """Resource kind from BPI 2019's naming convention.

    The log names its 20 automated users `batch_NN` and its 607 people
    `user_NNN`; vendor-recorded events carry a vendor-shaped resource. Getting
    this split right is the point of several later analyses, so it is a named
    function rather than an inline prefix test.
    """
    lowered = name.lower()
    if not lowered:
        return UNKNOWN
    if lowered.startswith("batch"):
        return BATCH
    if lowered.startswith("user"):
        return HUMAN
    if lowered.startswith("vendor"):
        return VENDOR
    return UNKNOWN


def read_xes(
    path: str | Path,
    *,
    log_id: str,
    activity_rank: dict[str, int] | None = None,
    case_attribute: str = CASE_ATTRIBUTE,
    value_attribute: str | None = BPIC19_VALUE_ATTRIBUTE,
    license: str = "",
    attribution: str = "",
    keep_case: Callable[[dict[str, Any]], bool] | None = None,
    max_cases: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> EventLog:
    """Read XES into the canonical log.

    `keep_case` and `max_cases` are the sampling hooks: the committed snapshot
    is produced by reading the full file with a deterministic `keep_case`, so
    the sample is reproducible from the manifest rather than being a mystery
    file (see logs/snapshot/README.md).
    """
    builder = EventLogBuilder(
        log_id,
        activity_rank=activity_rank,
        source=str(path),
        license=license,
        attribution=attribution,
    )
    kept = 0
    seen = 0
    for trace_attributes, events in iter_traces(path):
        seen += 1
        if keep_case is not None and not keep_case(trace_attributes):
            continue
        case_id = str(trace_attributes.get(case_attribute) or f"case-{seen}")
        builder.add_case_attributes(case_id, trace_attributes)
        for record in events:
            activity = str(record.get(ACTIVITY_ATTRIBUTE, "")) or "UNKNOWN"
            raw_timestamp = record.get(TIMESTAMP_ATTRIBUTE)
            if raw_timestamp is None:
                continue
            timestamp, precision = parse_timestamp(str(raw_timestamp))
            resource = str(record.get(RESOURCE_ATTRIBUTE, "") or "")
            value_cents = 0
            if value_attribute is not None:
                raw_value = record.get(value_attribute)
                if isinstance(raw_value, (int, float)):
                    value_cents = int(round(float(raw_value) * 100))
            extra = {
                key: value
                for key, value in record.items()
                if key
                not in (ACTIVITY_ATTRIBUTE, TIMESTAMP_ATTRIBUTE, RESOURCE_ATTRIBUTE, value_attribute)
            }
            builder.add(
                case_id=case_id,
                activity=activity,
                timestamp=timestamp,
                resource=resource,
                value_cents=value_cents,
                precision=precision,
                attributes=extra or None,
            )
        kept += 1
        if on_progress is not None and seen % 10000 == 0:
            on_progress(seen, kept)
        if max_cases is not None and kept >= max_cases:
            break
    return builder.build()


_XML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


def _escape(value: Any) -> str:
    text = str(value)
    for raw, encoded in _XML_ESCAPES:
        text = text.replace(raw, encoded)
    return text


def _scalar_tag(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def write_xes(log: EventLog, path: str | Path, *, value_attribute: str | None = None) -> Path:
    """Write the canonical log back out as XES, so it round-trips elsewhere."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<log xes.version="1849.2016" xes.features="">\n')
        for prefix, name, uri in (
            ("concept", "Concept", "http://www.xes-standard.org/concept.xesext"),
            ("time", "Time", "http://www.xes-standard.org/time.xesext"),
            ("org", "Organizational", "http://www.xes-standard.org/org.xesext"),
        ):
            out.write(f'  <extension name="{name}" prefix="{prefix}" uri="{uri}"/>\n')
        out.write('  <classifier name="Event Name" scope="event" keys="concept:name"/>\n')
        out.write(f'  <string key="concept:name" value="{_escape(log.log_id)}"/>\n')
        if log.attribution:
            out.write(f'  <string key="matchbook:attribution" value="{_escape(log.attribution)}"/>\n')
        if log.license:
            out.write(f'  <string key="matchbook:license" value="{_escape(log.license)}"/>\n')
        for trace in log.traces():
            out.write("  <trace>\n")
            out.write(f'    <string key="concept:name" value="{_escape(trace.case_id)}"/>\n')
            for key, value in sorted(trace.attributes.items()):
                if key == CASE_ATTRIBUTE:
                    continue
                tag = _scalar_tag(value)
                rendered = str(value).lower() if isinstance(value, bool) else value
                out.write(f'    <{tag} key="{_escape(key)}" value="{_escape(rendered)}"/>\n')
            for position in trace.indices:
                record = log.event(position)
                stamp = record["timestamp"].strftime("%Y-%m-%dT%H:%M:%S.000%z")
                stamp = stamp[:-2] + ":" + stamp[-2:]
                out.write("    <event>\n")
                out.write(f'      <string key="concept:name" value="{_escape(record["activity"])}"/>\n')
                out.write(f'      <date key="time:timestamp" value="{stamp}"/>\n')
                if record["resource"]:
                    out.write(
                        f'      <string key="org:resource" value="{_escape(record["resource"])}"/>\n'
                    )
                if value_attribute and record["value_cents"]:
                    amount = record["value_cents"] / 100
                    out.write(f'      <float key="{_escape(value_attribute)}" value="{amount}"/>\n')
                out.write("    </event>\n")
            out.write("  </trace>\n")
        out.write("</log>\n")
    return destination
