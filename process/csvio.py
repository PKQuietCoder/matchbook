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
    mapping = dict(columns or {name: name for name in CANONICAL_COLUMNS})
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
                writer.writerow(row)
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
