"""CSV ingest and export for the canonical log.

CSV is the fast path and the format the committed snapshot ships in: gzipped
CSV is a tenth the size of the equivalent XES, every tool on earth reads it, and
a reviewer can open it. XES remains the interchange format (`process.xes`).

The reader is column-mapping driven rather than convention driven, because real
exports never agree on names -- the committed Helpdesk fixture calls them
`CaseID,ActivityID,CompleteTimestamp`, and the BPI 2019 derivative uses the
canonical names below.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from process.log import DAY, SECOND, EventLog, EventLogBuilder

CANONICAL_COLUMNS = (
    "case_id",
    "activity",
    "timestamp",
    "resource",
    "value_cents",
    "precision",
)

# The canonical columns plus the JSON attribute column that `write_csv` appends.
DEFAULT_COLUMNS = {name: name for name in CANONICAL_COLUMNS} | {"attributes": "attributes"}

HELPDESK_COLUMNS = {
    "case_id": "CaseID",
    "activity": "ActivityID",
    "timestamp": "CompleteTimestamp",
}

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def _open(path: Path, mode: str):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8", newline="")
    return open(path, mode, encoding="utf-8", newline="")


def parse_timestamp(text: str) -> tuple[int, str]:
    """Parse a CSV timestamp into (epoch seconds, precision).

    Day precision is reported for a date-only value or an exact midnight, so
    the tie-break audit in `EventLogBuilder` knows which orderings it invented.
    """
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
    moment: datetime | None = None
    try:
        moment = datetime.fromisoformat(cleaned)
    except ValueError:
        for fmt in _TIMESTAMP_FORMATS:
            try:
                moment = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                continue
    if moment is None:
        raise ValueError(f"unparseable timestamp: {text!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    date_only = len(cleaned) <= 10
    precision = DAY if date_only or (moment.hour, moment.minute, moment.second) == (0, 0, 0) else SECOND
    return int(moment.timestamp()), precision


def read_csv(
    path: str | Path,
    *,
    log_id: str,
    columns: Mapping[str, str] | None = None,
    activity_rank: dict[str, int] | None = None,
    license: str = "",
    attribution: str = "",
    case_attribute_columns: Iterable[str] = (),
    keep_case: Callable[[dict[str, Any]], bool] | None = None,
) -> EventLog:
    """Read a CSV (optionally gzipped) into the canonical log."""
    source = Path(path)
    # `attributes` is in the default mapping but NOT in CANONICAL_COLUMNS, because
    # that tuple is also the write header and `write_csv` appends the column
    # separately -- putting it in the tuple would emit it twice. Leaving it out of
    # the mapping was a silent round-trip hole: `write_csv` wrote the column
    # faithfully and `read_csv` ignored it, so every event attribute (BPI 2019's
    # `User`, on all 1.6M events) was dropped on read.
    mapping = dict(columns or DEFAULT_COLUMNS)
    case_attribute_columns = tuple(case_attribute_columns)

    builder = EventLogBuilder(
        log_id,
        activity_rank=activity_rank,
        source=str(source),
        license=license,
        attribution=attribution,
    )
    with _open(source, "r") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            case_id = str(row[mapping["case_id"]]).strip()
            if keep_case is not None and not keep_case(row):
                continue
            timestamp, precision = parse_timestamp(str(row[mapping["timestamp"]]))
            precision_column = mapping.get("precision")
            if precision_column and row.get(precision_column):
                precision = str(row[precision_column]).strip() or precision
            value_column = mapping.get("value_cents")
            raw_value = row.get(value_column) if value_column else None
            try:
                value_cents = int(float(raw_value)) if raw_value not in (None, "") else 0
            except (TypeError, ValueError):
                value_cents = 0
            resource_column = mapping.get("resource")
            resource = str(row.get(resource_column) or "") if resource_column else ""

            attributes_column = mapping.get("attributes")
            extra: dict[str, Any] | None = None
            if attributes_column and row.get(attributes_column):
                try:
                    extra = json.loads(row[attributes_column])
                except json.JSONDecodeError:
                    extra = {"raw": row[attributes_column]}

            builder.add(
                case_id=case_id,
                activity=str(row[mapping["activity"]]).strip(),
                timestamp=timestamp,
                resource=resource,
                value_cents=value_cents,
                precision=precision,
                attributes=extra,
            )
            if case_attribute_columns:
                builder.add_case_attributes(
                    case_id,
                    {name: row[name] for name in case_attribute_columns if name in row},
                )
    return builder.build()


def _event_row(log: EventLog, position: int, include_attributes: bool) -> list[Any]:
    """One CSV row for one event.

    Shared by `write_csv` and `write_csv_tables` so the two writers cannot drift
    into producing different files from the same log.
    """
    record = log.event(position)
    row = [
        record["case_id"],
        record["activity"],
        record["timestamp"].strftime("%Y-%m-%dT%H:%M:%S"),
        record["resource"],
        record["value_cents"],
        record["precision"],
    ]
    if include_attributes:
        row.append(json.dumps(record.get("attributes") or {}, sort_keys=True))
    return row


def write_csv(log: EventLog, path: str | Path, *, include_attributes: bool = True) -> Path:
    """Write the canonical log as CSV. Gzipped when the path ends in .gz."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = list(CANONICAL_COLUMNS) + (["attributes"] if include_attributes else [])
    with _open(destination, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for trace in log.traces():
            for position in trace.indices:
                writer.writerow(_event_row(log, position, include_attributes))
    return destination


def write_case_attributes(log: EventLog, path: str | Path) -> Path:
    """Write per-case attributes alongside the event CSV.

    Kept in a separate file rather than repeated on every event row: BPI 2019
    has 15 case attributes and repeating them across 1.6M events would multiply
    the snapshot's size for no gain.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    seen: set[str] = set()
    for attributes in log.case_attributes.values():
        for key in attributes:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with _open(destination, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id"] + keys)
        for case_id in log.case_ids:
            attributes = log.case_attributes.get(case_id, {})
            writer.writerow([case_id] + [attributes.get(key, "") for key in keys])
    return destination


def read_case_attributes(log: EventLog, path: str | Path) -> EventLog:
    """Attach a case-attribute CSV to an already-loaded log."""
    with _open(Path(path), "r") as handle:
        for row in csv.DictReader(handle):
            case_id = row.pop("case_id")
            log.case_attributes[case_id] = {
                key: value for key, value in row.items() if value != ""
            }
    return log


# -- streaming both tables in one pass ------------------------------------------

_DTYPES = {bool: "bool", int: "int64", float: "float64", str: "str"}

EVENT_DTYPES = {
    "case_id": "str",
    "activity": "str",
    "resource": "str",
    "value_cents": "int64",
    "precision": "str",
    "attributes": "str",
}


def write_csv_tables(
    traces: Iterable[Any],
    events_path: str | Path,
    cases_path: str | Path,
    *,
    include_attributes: bool = True,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Write the event table and the case table from ONE pass over the traces.

    `write_csv` needs a materialised `EventLog`, which costs ~1.4 GB on BPI 2019.
    This consumes an iterator of `Trace` -- `process.otlp.traces_of_xes` yields
    one case at a time off the raw XES.

    Memory is therefore flat in the number of EVENTS but linear in the number of
    CASES, and the distinction is the honest one: event rows are written as they
    arrive, while case rows are *buffered*, because a CSV header has to name every
    column before the first data row and a later case may carry a key the first
    one did not. String values are interned here so the heavily repeated ones
    (`companyID_0000` occurs on 250,686 cases) are stored once. Measured on the
    full log: **469 MB peak** for 251,734 cases, against ~1.4 GB for the
    materialised-log route.

    Returns the counts and the observed per-column dtypes. The dtypes are the
    point: a CSV has no schema, so without them `GR-Based Inv. Verif.` reads back
    as the string 'False' and `Item` '00001' reads back as the integer 1.
    """
    events_destination = Path(events_path)
    cases_destination = Path(cases_path)
    for destination in (events_destination, cases_destination):
        destination.parent.mkdir(parents=True, exist_ok=True)

    header = list(CANONICAL_COLUMNS) + (["attributes"] if include_attributes else [])
    case_keys: list[str] = []
    seen_keys: set[str] = set()
    case_rows: list[tuple[str, dict[str, Any]]] = []
    pool: dict[Any, Any] = {}
    observed: dict[str, set[type]] = {}

    stats: dict[str, Any] = {
        "cases": 0,
        "events": 0,
        "tie_broken_events": 0,
        "non_second_precision_events": 0,
        "value_cents_total": 0,
        "activities": set(),
        "resources": set(),
        "first_timestamp": None,
        "last_timestamp": None,
    }

    with _open(events_destination, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for trace in traces:
            log = trace.log
            stats["cases"] += 1
            for position in trace.indices:
                writer.writerow(_event_row(log, position, include_attributes))
                stats["events"] += 1
                stats["activities"].add(log.activities.name_of(log.activity_id[position]))
                stats["resources"].add(log.resources.name_of(log.resource_id[position]))
                stats["value_cents_total"] += log.value_cents[position]
                if position in log.tie_broken:
                    stats["tie_broken_events"] += 1
                if log.precision.get(position, SECOND) != SECOND:
                    stats["non_second_precision_events"] += 1
                moment = log.timestamp[position]
                if stats["first_timestamp"] is None or moment < stats["first_timestamp"]:
                    stats["first_timestamp"] = moment
                if stats["last_timestamp"] is None or moment > stats["last_timestamp"]:
                    stats["last_timestamp"] = moment

            attributes = trace.attributes or {}
            kept: dict[str, Any] = {}
            for key, value in attributes.items():
                if key not in seen_keys:
                    seen_keys.add(key)
                    case_keys.append(key)
                observed.setdefault(key, set()).add(type(value))
                kept[key] = pool.setdefault(value, value) if isinstance(value, str) else value
            case_rows.append((pool.setdefault(trace.case_id, trace.case_id), kept))

            if on_progress is not None and stats["cases"] % 20000 == 0:
                on_progress(stats)

    with _open(cases_destination, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id"] + case_keys)
        for case_id, attributes in case_rows:
            writer.writerow([case_id] + [attributes.get(key, "") for key in case_keys])

    case_dtypes: dict[str, str] = {"case_id": "str"}
    mixed: list[str] = []
    for key in case_keys:
        types = observed.get(key, {str})
        if len(types) == 1:
            case_dtypes[key] = _DTYPES.get(next(iter(types)), "str")
        else:
            # Two types in one column and a CSV column has one dtype. Fall back to
            # str and SAY SO, rather than pick a winner and lose the other silently.
            case_dtypes[key] = "str"
            mixed.append(key)

    stats["activities"] = sorted(stats["activities"])
    stats["resources_count"] = len(stats["resources"])
    del stats["resources"]
    stats["case_columns"] = case_keys
    stats["dtypes"] = {
        "events": {k: v for k, v in EVENT_DTYPES.items() if k in header},
        "cases": case_dtypes,
    }
    stats["mixed_type_columns"] = mixed
    stats["events_bytes"] = events_destination.stat().st_size
    stats["cases_bytes"] = cases_destination.stat().st_size
    return stats
