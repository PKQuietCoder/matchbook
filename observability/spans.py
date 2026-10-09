"""Span capture into local SQLite.

The mining half has to run on a laptop with no account, no collector and no key,
so spans land in a local SQLite file by default; the OTel exporter in
`observability/instrument.py` is opt-in and additive. The attribute namespace is `mb.*`
and the shape is OTel-compatible on purpose -- an OTel exporter can be added
later without changing the bridge.

The attribute that matters most is `item_key`: the business object a span acted
on. It is what lets the bridge choose the *business object* as the case
identifier rather than the trace id, which is the single decision that makes
the agent's log comparable to the human log.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

REQUEST = "request"
MODEL = "model"
TOOL = "tool"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="microseconds")


# Columns added to `spans` after the first release. `CREATE TABLE IF NOT EXISTS`
# is a no-op on a table that already exists, so a span store written before
# these columns existed would silently keep its old shape and every token query
# would fail on it. Adding them here makes opening an old store the migration.
ADDED_SPAN_COLUMNS = (
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("cache_read_tokens", "INTEGER"),
    ("cache_write_tokens", "INTEGER"),
)


def _migrate(connection: sqlite3.Connection) -> None:
    """Add any missing late columns. Idempotent, and safe on a fresh store."""
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(spans)")}
    with connection:
        for column, declaration in ADDED_SPAN_COLUMNS:
            if column not in existing:
                connection.execute(f"ALTER TABLE spans ADD COLUMN {column} {declaration}")


def connect(path: str | Path) -> sqlite3.Connection:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_PATH.read_text())
    _migrate(connection)
    return connection


@dataclass
class Span:
    span_id: str
    run_id: str
    name: str
    kind: str
    step: int
    parent_id: str | None = None
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None
    ok: bool | None = None
    error: str | None = None
    item_key: str | None = None
    activity: str | None = None
    actor: str | None = None
    value_cents: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class SpanStore:
    """Writes runs, spans and messages. One instance per process is plenty."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection = connect(self.path)

    def close(self) -> None:
        self._connection.close()

    def start_run(
        self,
        *,
        session_id: str,
        actor_id: str,
        role: str,
        model: str,
        prompt_version: str,
        scenario_id: str | None = None,
        killswitch: str = "off",
        run_id: str | None = None,
    ) -> str:
        identifier = run_id or uuid.uuid4().hex
        with self._connection:
            self._connection.execute(
                "INSERT INTO runs (run_id, session_id, actor_id, role, model, prompt_version,"
                " scenario_id, started_at, killswitch) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    session_id,
                    actor_id,
                    role,
                    model,
                    prompt_version,
                    scenario_id,
                    _now(),
                    killswitch,
                ),
            )
        return identifier

    def end_run(self, run_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE runs SET ended_at = ? WHERE run_id = ?", (_now(), run_id)
            )

    def message(self, run_id: str, step: int, role: str, content: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO messages (run_id, step, role, content, created_at) VALUES (?,?,?,?,?)",
                (run_id, step, role, content, _now()),
            )

    def _write(self, span: Span) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO spans (span_id, run_id, parent_id, name, kind, step,"
                " started_at, ended_at, ok, error, item_key, activity, actor, value_cents,"
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
                " attributes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    span.span_id,
                    span.run_id,
                    span.parent_id,
                    span.name,
                    span.kind,
                    span.step,
                    span.started_at,
                    span.ended_at,
                    None if span.ok is None else int(span.ok),
                    span.error,
                    span.item_key,
                    span.activity,
                    span.actor,
                    span.value_cents,
                    span.input_tokens,
                    span.output_tokens,
                    span.cache_read_tokens,
                    span.cache_write_tokens,
                    json.dumps(span.attributes, sort_keys=True, default=str),
                ),
            )

    # Promoted to their own columns because the bridge queries on them. Anything
    # else a caller passes lands in the JSON attributes blob. The token fields
    # are columns for the same reason: the cost report aggregates over them, and
    # a JSON extract in every cost query would be miserable to read.
    PROMOTED = (
        "item_key",
        "activity",
        "actor",
        "value_cents",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )

    @contextmanager
    def span(
        self,
        *,
        run_id: str,
        name: str,
        kind: str,
        step: int,
        parent_id: str | None = None,
        **attributes: Any,
    ) -> Iterator[Span]:
        """Open a span; it is written on exit whether or not the body raised.

        Keys in `PROMOTED` become real columns rather than JSON, so the bridge
        can select on them. Letting `item_key` fall into the attributes blob
        instead would leave the case identifier unqueryable, which silently
        breaks the whole spans-to-log conversion.
        """
        promoted = {key: attributes.pop(key, None) for key in self.PROMOTED}
        record = Span(
            span_id=uuid.uuid4().hex,
            run_id=run_id,
            name=name,
            kind=kind,
            step=step,
            parent_id=parent_id,
            attributes=dict(attributes),
            **promoted,
        )
        try:
            yield record
        except Exception as exc:  # an unexpected failure is still a span
            record.ok = False
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.ended_at = _now()
            self._write(record)

    # -- reading, for the bridge and for tests --------------------------------

    def runs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute("SELECT * FROM runs ORDER BY started_at")]

    def all_spans(self, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM spans"
        params: list[Any] = []
        if run_id:
            sql += " WHERE run_id = ?"
            params.append(run_id)
        sql += " ORDER BY started_at, step"
        rows = []
        for row in self._connection.execute(sql, params):
            record = dict(row)
            record["attributes"] = json.loads(record["attributes"])
            rows.append(record)
        return rows

    def transcript(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                "SELECT step, role, content, created_at FROM messages WHERE run_id = ?"
                " ORDER BY message_id",
                (run_id,),
            )
        ]
