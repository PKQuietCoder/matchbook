"""Typed access to the agent's world. Complete.

Reads return plain dicts limited to the fields a tool may show the model --
`to_public_item` is the boundary. Writes go through named functions so every
state change also writes the matching business event into `case_events`, in the
event log's activity alphabet. That coupling is deliberate: a write that
changed state without emitting its event would be invisible to process mining,
which is the one thing this repo cannot afford.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    return Path(os.environ.get("MB_DB", REPO_ROOT / "data" / "matchbook.db"))


def policies_dir() -> Path:
    return Path(os.environ.get("MB_POLICIES_DIR", REPO_ROOT / "data" / "policies"))


@contextmanager
def connection(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def world_asof(conn: sqlite3.Connection) -> date:
    """The world's fixed 'today'. All date arithmetic uses this, never now()."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'world_asof'").fetchone()
    return date.fromisoformat(row["value"])


def _stamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# -- reads -------------------------------------------------------------------

ITEM_QUERY = """
SELECT i.*, p.vendor_id, p.company_code, p.purchasing_group, p.released_at, p.document_type,
       v.name AS vendor_name
  FROM po_items i
  JOIN purchase_orders p ON p.po_number = i.po_number
  LEFT JOIN vendors v ON v.vendor_id = p.vendor_id
 WHERE i.item_key = ?
"""


def get_item(conn: sqlite3.Connection, item_key: str) -> dict[str, Any] | None:
    row = conn.execute(ITEM_QUERY, (item_key,)).fetchone()
    return dict(row) if row else None


def items_of_order(conn: sqlite3.Connection, po_number: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT item_key, item_no, description, flow, po_value_cents, payment_blocked,"
            " deleted_at FROM po_items WHERE po_number = ? ORDER BY item_no",
            (po_number,),
        )
    ]


def receipt_total_cents(conn: sqlite3.Connection, item_key: str) -> int | None:
    row = conn.execute(
        "SELECT SUM(value_cents) AS total, COUNT(*) AS n FROM goods_receipts"
        " WHERE item_key = ? AND cancelled_at IS NULL",
        (item_key,),
    ).fetchone()
    return int(row["total"]) if row and row["n"] else None


def open_invoice(conn: sqlite3.Connection, item_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM invoices WHERE item_key = ? AND status IN ('open','queued_for_approval')"
        " ORDER BY recorded_at LIMIT 1",
        (item_key,),
    ).fetchone()
    return dict(row) if row else None


def any_invoice(conn: sqlite3.Connection, item_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM invoices WHERE item_key = ? AND status != 'cancelled'"
        " ORDER BY recorded_at LIMIT 1",
        (item_key,),
    ).fetchone()
    return dict(row) if row else None


def case_history(conn: sqlite3.Connection, item_key: str) -> list[tuple[str, str]]:
    """The case's own business history as (activity, actor) pairs.

    This is the world's history, not the agent's trace log -- see agent/auth.py
    on why that distinction matters.
    """
    return [
        (row["activity"], row["actor_id"])
        for row in conn.execute(
            "SELECT activity, actor_id FROM case_events WHERE item_key = ?"
            " ORDER BY occurred_at, event_id",
            (item_key,),
        )
    ]


def case_events(conn: sqlite3.Connection, item_key: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT activity, actor_id, actor_kind, value_cents, occurred_at FROM case_events"
            " WHERE item_key = ? ORDER BY occurred_at, event_id",
            (item_key,),
        )
    ]


def get_actor(conn: sqlite3.Connection, actor_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
    return dict(row) if row else None


def to_public_item(item: dict[str, Any], *, receipt_cents: int | None, invoice: dict[str, Any] | None) -> dict[str, Any]:
    """The fields a tool may return to the model. The boundary is explicit."""
    return {
        "item_key": item["item_key"],
        "po_number": item["po_number"],
        "item_no": item["item_no"],
        "description": item["description"],
        "flow": item["flow"],
        "item_type": item["item_type"],
        "spend_area": item["spend_area"],
        "vendor_id": item["vendor_id"],
        "vendor_name": item["vendor_name"],
        "company_code": item["company_code"],
        "purchasing_group": item["purchasing_group"],
        "ordered_quantity": item["ordered_quantity"],
        "po_value_eur": item["po_value_cents"] / 100,
        "goods_receipt_value_eur": None if receipt_cents is None else receipt_cents / 100,
        "invoice_value_eur": None if invoice is None else invoice["value_cents"] / 100,
        "invoice_status": None if invoice is None else invoice["status"],
        "payment_blocked": bool(item["payment_blocked"]),
        "block_reason": item["block_reason"],
        "released": item["released_at"] is not None,
        "deleted": item["deleted_at"] is not None,
    }


# -- writes ------------------------------------------------------------------

def _emit(
    conn: sqlite3.Connection,
    item_key: str,
    activity: str,
    actor_id: str,
    value_cents: int,
    *,
    actor_kind: str = "agent",
) -> None:
    conn.execute(
        "INSERT INTO case_events (item_key, activity, actor_id, actor_kind, value_cents,"
        " occurred_at) VALUES (?,?,?,?,?,?)",
        (item_key, activity, actor_id, actor_kind, value_cents, _stamp()),
    )


def insert_goods_receipt(
    conn: sqlite3.Connection,
    *,
    item_key: str,
    quantity: float,
    value_cents: int,
    reference: str,
    actor_id: str,
    service: bool = False,
) -> str:
    gr_id = f"GR-{item_key}-{int(datetime.now(tz=timezone.utc).timestamp())}"
    with conn:
        conn.execute(
            "INSERT INTO goods_receipts (gr_id, item_key, quantity, value_cents, reference,"
            " recorded_at, recorded_by, cancelled_at) VALUES (?,?,?,?,?,?,?,NULL)",
            (gr_id, item_key, quantity, value_cents, reference, _stamp(), actor_id),
        )
        _emit(
            conn,
            item_key,
            "Record Service Entry Sheet" if service else "Record Goods Receipt",
            actor_id,
            value_cents,
        )
    return gr_id


def clear_invoice_row(
    conn: sqlite3.Connection,
    *,
    item_key: str,
    invoice_id: str,
    amount_cents: int,
    actor_id: str,
) -> None:
    with conn:
        conn.execute(
            "UPDATE invoices SET status = 'cleared', cleared_at = ? WHERE invoice_id = ?",
            (_stamp(), invoice_id),
        )
        _emit(conn, item_key, "Clear Invoice", actor_id, amount_cents)


def queue_for_approval(
    conn: sqlite3.Connection,
    *,
    item_key: str,
    invoice_id: str | None,
    actor_id: str,
    activity: str,
    amount_cents: int,
    trigger: str,
    evidence: str = "{}",
) -> int:
    """Queue a decision for a controller.

    The invoice goes to `queued_for_approval`, NOT to cleared. SPEC RESP-3: a
    queued clearing has not been paid, and the agent must not say it has.
    """
    with conn:
        cursor = conn.execute(
            "INSERT INTO approval_queue (item_key, invoice_id, requested_by,"
            " requested_activity, amount_cents, trigger, evidence, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,'pending',?)",
            (item_key, invoice_id, actor_id, activity, amount_cents, trigger, evidence, _stamp()),
        )
        if invoice_id:
            conn.execute(
                "UPDATE invoices SET status = 'queued_for_approval' WHERE invoice_id = ?",
                (invoice_id,),
            )
    return int(cursor.lastrowid)


def set_payment_block(
    conn: sqlite3.Connection, *, item_key: str, blocked: bool, reason: str, actor_id: str
) -> None:
    with conn:
        conn.execute(
            "UPDATE po_items SET payment_blocked = ?, block_reason = ? WHERE item_key = ?",
            (1 if blocked else 0, reason if blocked else None, item_key),
        )
        _emit(
            conn,
            item_key,
            "Set Payment Block" if blocked else "Remove Payment Block",
            actor_id,
            0,
        )


def pending_approvals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM approval_queue WHERE status = 'pending' ORDER BY created_at"
        )
    ]
