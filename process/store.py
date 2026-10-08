"""The SQLite event store: persist and reload a canonical log.

Why a store at all, when `process.csvio` can already read a log? Because the
whole point of the project is comparing logs -- a human recording and an agent
recording of the same process -- and they have to live in one place, under one
interned alphabet, with their provenance attached. `process.store` is that
place.

Loading is chunked and ordered by the log's own `seq`, so a reload reproduces
the exact event order the ingest decided, tie-breaks included. Nothing is
re-sorted on the way back in; re-deriving the order would silently discard the
audit trail.
"""

from __future__ import annotations

import json
import sqlite3
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from process.log import SECOND, EventLog, Interner

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
WRITE_CHUNK = 20_000


def connect(path: str | Path) -> sqlite3.Connection:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_PATH.read_text())
    return connection


def _rows(log: EventLog) -> Iterable[tuple]:
    precision = log.precision
    tie_broken = log.tie_broken
    attributes = log.event_attributes
    for position in range(len(log)):
        extra = attributes.get(position)
        yield (
            log.log_id,
            position,
            log.case_index[position],
            log.activity_id[position],
            log.resource_id[position],
            log.timestamp[position],
            precision.get(position, SECOND),
            1 if position in tie_broken else 0,
            log.value_cents[position],
            json.dumps(extra, sort_keys=True) if extra else None,
        )


def save(
    log: EventLog,
    path: str | Path,
    *,
    doi: str = "",
    sha256: str = "",
    notes: str = "",
    resource_kind: Callable[[str], str] | None = None,
    replace: bool = True,
) -> Path:
    """Write one log into the store, replacing any log with the same id."""
    destination = Path(path)
    connection = connect(destination)
    try:
        with connection:
            if replace:
                for table in (
                    "events",
                    "cases",
                    "activities",
                    "resources",
                    "variants",
                    "objects",
                    "event_objects",
                    "logs",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE log_id = ?", (log.log_id,))

            connection.execute(
                "INSERT INTO logs (log_id, source, doi, license, attribution, sha256,"
                " ingested_at, event_count, case_count, tie_broken, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    log.log_id,
                    log.source,
                    doi,
                    log.license,
                    log.attribution,
                    sha256,
                    datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                    log.event_count,
                    log.case_count,
                    len(log.tie_broken),
                    notes,
                ),
            )

            connection.executemany(
                "INSERT INTO activities (log_id, activity_id, name) VALUES (?,?,?)",
                [(log.log_id, identifier, name) for identifier, name in log.activities],
            )
            classify = resource_kind or (lambda _name: "unknown")
            connection.executemany(
                "INSERT INTO resources (log_id, resource_id, name, kind) VALUES (?,?,?,?)",
                [
                    (log.log_id, identifier, name, classify(name))
                    for identifier, name in log.resources
                ],
            )

            variant_ids: dict[tuple[int, ...], int] = {}
            variant_counts: dict[int, int] = {}
            case_rows = []
            for index, case_id in enumerate(log.case_ids):
                trace = log.trace(index)
                signature = trace.activity_ids
                variant_id = variant_ids.setdefault(signature, len(variant_ids))
                variant_counts[variant_id] = variant_counts.get(variant_id, 0) + 1
                case_rows.append(
                    (
                        log.log_id,
                        case_id,
                        index,
                        variant_id,
                        log.timestamp[trace.start] if len(trace) else 0,
                        log.timestamp[trace.end - 1] if len(trace) else 0,
                        len(trace),
                        json.dumps(trace.attributes, sort_keys=True, default=str),
                    )
                )
            connection.executemany(
                "INSERT INTO cases (log_id, case_id, case_index, variant_id, start_ts,"
                " end_ts, event_count, attrs) VALUES (?,?,?,?,?,?,?,?)",
                case_rows,
            )
            connection.executemany(
                "INSERT INTO variants (log_id, variant_id, activity_sequence, case_count)"
                " VALUES (?,?,?,?)",
                [
                    (
                        log.log_id,
                        variant_id,
                        json.dumps(list(signature)),
                        variant_counts[variant_id],
                    )
                    for signature, variant_id in variant_ids.items()
                ],
            )

            batch: list[tuple] = []
            for row in _rows(log):
                batch.append(row)
                if len(batch) >= WRITE_CHUNK:
                    connection.executemany(
                        "INSERT INTO events (log_id, seq, case_index, activity_id,"
                        " resource_id, ts, ts_precision, tie_broken, value_cents, attrs)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                connection.executemany(
                    "INSERT INTO events (log_id, seq, case_index, activity_id,"
                    " resource_id, ts, ts_precision, tie_broken, value_cents, attrs)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
    finally:
        connection.close()
    return destination


def list_logs(path: str | Path) -> list[dict[str, Any]]:
    connection = connect(path)
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM logs ORDER BY log_id")]
    finally:
        connection.close()


def load(path: str | Path, log_id: str) -> EventLog:
    """Reload a log, preserving the ingested event order exactly."""
    connection = connect(path)
    try:
        header = connection.execute(
            "SELECT * FROM logs WHERE log_id = ?", (log_id,)
        ).fetchone()
        if header is None:
            known = [row["log_id"] for row in connection.execute("SELECT log_id FROM logs")]
            raise KeyError(f"no log {log_id!r} in {path}; store has: {', '.join(known) or 'nothing'}")

        activities = Interner()
        for row in connection.execute(
            "SELECT activity_id, name FROM activities WHERE log_id = ? ORDER BY activity_id",
            (log_id,),
        ):
            activities.intern(row["name"])
        resources = Interner()
        for row in connection.execute(
            "SELECT resource_id, name FROM resources WHERE log_id = ? ORDER BY resource_id",
            (log_id,),
        ):
            resources.intern(row["name"])

        log = EventLog(
            log_id=log_id,
            activities=activities,
            resources=resources,
            source=header["source"],
            license=header["license"],
            attribution=header["attribution"],
        )

        case_ids: list[str] = []
        case_attributes: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT case_id, attrs FROM cases WHERE log_id = ? ORDER BY case_index", (log_id,)
        ):
            case_ids.append(row["case_id"])
            attributes = json.loads(row["attrs"]) if row["attrs"] else {}
            if attributes:
                case_attributes[row["case_id"]] = attributes
        log.case_ids = case_ids
        log.case_attributes = case_attributes

        offsets = array("i", [0])
        previous_case: int | None = None
        position = 0
        for row in connection.execute(
            "SELECT case_index, activity_id, resource_id, ts, ts_precision, tie_broken,"
            " value_cents, attrs FROM events WHERE log_id = ? ORDER BY seq",
            (log_id,),
        ):
            case_index = row["case_index"]
            if previous_case is not None and case_index != previous_case:
                offsets.append(position)
            previous_case = case_index
            log.case_index.append(case_index)
            log.activity_id.append(row["activity_id"])
            log.resource_id.append(row["resource_id"])
            log.timestamp.append(row["ts"])
            log.value_cents.append(row["value_cents"])
            if row["ts_precision"] != SECOND:
                log.precision[position] = row["ts_precision"]
            if row["tie_broken"]:
                log.tie_broken.add(position)
            if row["attrs"]:
                log.event_attributes[position] = json.loads(row["attrs"])
            position += 1
        offsets.append(position)
        log.case_offsets = offsets
        return log
    finally:
        connection.close()
