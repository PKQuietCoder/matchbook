"""The agent's tools: pure logic functions, no SDK and no model.

Each tool is a plain typed function `fn(ctx, ...) -> dict`. The SDK wrappers
live in `agent/agent.py` behind a single `_call()` seam, so there is exactly one
place that converts an unfinished function into a structured result and emits
the process event. That split is what makes every tool testable with no model,
no key and no network.

The three-way match is computed by `seed/controls.py`, never here and never by
the model (SPEC TOOL-7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent import db
from agent.auth import (
    AuthContext,
    can_clear_invoice,
    can_record_goods_receipt,
    can_view_item,
    clearing_limit_cents,
    invalid_argument,
    not_eligible,
    not_found,
    permission_denied,
    sod_conflict,
)
from agent.killswitch import kill_switch
from process import config
from seed import controls

SERVICE_ITEM_TYPE = "Service"


def _article(role: str) -> str:
    """"an ap_clerk", "a buyer" -- the reason strings are read by people."""
    return f"{'an' if role[0] in 'aeiou' else 'a'} {role}"


def _facts() -> dict[str, Any]:
    return config.load_facts()


def _load_item(conn, ctx: AuthContext, item_key: str):
    """Fetch an item and apply the view scope. Returns (item, error)."""
    item = db.get_item(conn, item_key)
    if item is None:
        return None, not_found(f"no purchase-order item {item_key}")
    if not can_view_item(ctx, item):
        # SPEC RESP-5: say no without disclosing anything about the item.
        return None, permission_denied(
            f"{item_key} is outside your scope as {ctx.role}"
        )
    return item, None


# -- TOOL-1 ------------------------------------------------------------------

def get_purchase_item(ctx: AuthContext, item_key: str) -> dict[str, Any]:
    """Return one purchase-order item, with its receipt and invoice state.

    Also returns `sibling_items` when the purchase order has more than one
    line, because acting on the wrong item of a multi-item order is a real
    failure mode and SPEC RESP-6 requires confirming which item is meant.
    """
    with db.connection() as conn:
        item, error = _load_item(conn, ctx, item_key)
        if error:
            return error
        receipt = db.receipt_total_cents(conn, item_key)
        invoice = db.any_invoice(conn, item_key)
        public = db.to_public_item(item, receipt_cents=receipt, invoice=invoice)
        siblings = [
            row["item_key"]
            for row in db.items_of_order(conn, item["po_number"])
            if row["item_key"] != item_key
        ]
        if siblings:
            public["sibling_items"] = siblings
        return {"ok": True, "item": public}


# -- TOOL-2 ------------------------------------------------------------------

def get_three_way_match(ctx: AuthContext, item_key: str) -> dict[str, Any]:
    """Evaluate the three-way match for an item, in code.

    The model must call this before making any numeric claim about a variance.
    A variance stated in prose without this call is failure mode
    `tolerance_math_in_prose`, not a shortcut.
    """
    facts = _facts()
    with db.connection() as conn:
        item, error = _load_item(conn, ctx, item_key)
        if error:
            return error
        invoice = db.any_invoice(conn, item_key)
        result = controls.three_way_match(
            flow=item["flow"],
            po_value_cents=item["po_value_cents"],
            receipt_value_cents=db.receipt_total_cents(conn, item_key),
            invoice_value_cents=invoice["value_cents"] if invoice else None,
            item_deleted=item["deleted_at"] is not None,
            payment_blocked=bool(item["payment_blocked"]),
            facts=facts,
        )
        payload = {"ok": True, "item_key": item_key, "flow": item["flow"]}
        payload.update(result.as_result())
        payload["goods_receipt_required"] = controls.requires_goods_receipt(
            item["flow"], facts["gr_required_flows"]
        )
        payload["policy_id"] = "mb-three-way-match"
        return payload


# -- TOOL-3 ------------------------------------------------------------------

def get_policy(ctx: AuthContext, policy_id: str) -> dict[str, Any]:
    """Return a policy document in full, so a claim can cite it."""
    if not policy_id or not policy_id.strip():
        return invalid_argument("policy_id must not be empty")
    path = db.policies_dir() / f"{policy_id.strip()}.md"
    if not path.exists():
        available = sorted(p.stem for p in db.policies_dir().glob("*.md"))
        return not_found(
            f"no policy {policy_id!r}; available: {', '.join(available) or 'none'}"
        )
    body = path.read_text()
    title = policy_id
    for line in body.splitlines():
        if line.startswith("title:"):
            title = line.split(":", 1)[1].strip()
            break
    return {"ok": True, "policy_id": policy_id, "title": title, "body": body}


# -- TOOL-4 ------------------------------------------------------------------

def record_goods_receipt(
    ctx: AuthContext,
    item_key: str,
    quantity: float,
    value_eur: float,
    reference: str = "",
) -> dict[str, Any]:
    """Record receipt evidence against an item.

    Buyers only (SPEC AUTH-2): an AP clerk recording their own goods receipt
    and then clearing the invoice is exactly the duty conflict the controls
    exist to prevent.
    """
    paused = kill_switch("record_goods_receipt")
    if paused:
        return paused
    facts = _facts()
    if quantity <= 0:
        return invalid_argument("quantity must be positive")
    if value_eur <= 0:
        return invalid_argument("value_eur must be positive")

    with db.connection() as conn:
        item, error = _load_item(conn, ctx, item_key)
        if error:
            return error
        if not can_record_goods_receipt(ctx, item):
            return permission_denied(
                f"{_article(ctx.role)} may not record a goods receipt; this is a separated duty "
                "(policy mb-clearing-authority)"
            )
        if item["deleted_at"] is not None:
            return not_eligible(f"{item_key} is deleted; nothing may be recorded against it")
        if item["released_at"] is None:
            return not_eligible(f"{item['po_number']} is not released yet")
        if not controls.quantity_within_tolerance(item["ordered_quantity"], quantity, facts):
            return invalid_argument(
                f"received quantity {quantity} exceeds the ordered {item['ordered_quantity']} by "
                f"more than {facts['gr_quantity_tolerance_pct']}%; amend the purchase order first "
                "(policy mb-goods-receipt)"
            )
        conflict = sod_conflict(ctx, "Record Goods Receipt", db.case_history(conn, item_key), facts)
        if conflict:
            return permission_denied(
                f"you already performed '{conflict[1] if conflict[0] == 'Record Goods Receipt' else conflict[0]}'"
                f" on {item_key}; '{conflict[0]}' and '{conflict[1]}' are separated duties"
            )
        gr_id = db.insert_goods_receipt(
            conn,
            item_key=item_key,
            quantity=quantity,
            value_cents=int(round(value_eur * 100)),
            reference=reference,
            actor_id=ctx.actor_id,
            service=item["item_type"] == SERVICE_ITEM_TYPE,
        )
        return {"ok": True, "gr_id": gr_id, "item_key": item_key, "status": "recorded"}


# -- TOOL-5 ------------------------------------------------------------------

def clear_invoice(ctx: AuthContext, item_key: str, note: str = "") -> dict[str, Any]:
    """Clear an item's open invoice, or queue it for a controller.

    The check order is fixed by SPEC TOOL-6 and must not be reordered: kill
    switch, authorization scope, amount against the caller's limit, item not
    deleted, an unpaid invoice exists, no payment block, the three-way match
    within tolerance, and finally per-case segregation of duties.

    The order is not arbitrary. Checking the amount before the controls means a
    large but *correct* clearing queues for a human rather than being refused,
    and a small but *incorrect* one is refused rather than queued -- a human's
    time goes to decisions, not to rubber-stamping.
    """
    paused = kill_switch("clear_invoice")
    if paused:
        return paused
    facts = _facts()

    with db.connection() as conn:
        item, error = _load_item(conn, ctx, item_key)
        if error:
            return error
        if not can_clear_invoice(ctx, item):
            return permission_denied(
                f"{_article(ctx.role)} may not clear invoices (policy mb-clearing-authority)"
            )
        if item["deleted_at"] is not None:
            return not_eligible(
                f"{item_key} is deleted; a deleted item is never cleared. Escalate instead."
            )
        invoice = db.open_invoice(conn, item_key)
        if invoice is None:
            return not_eligible(f"{item_key} has no open invoice to clear")
        if invoice["status"] == "queued_for_approval":
            return not_eligible(
                f"invoice {invoice['invoice_id']} is already queued for a controller; "
                "it has not been paid"
            )
        amount_cents = int(invoice["value_cents"])

        match = controls.three_way_match(
            flow=item["flow"],
            po_value_cents=item["po_value_cents"],
            receipt_value_cents=db.receipt_total_cents(conn, item_key),
            invoice_value_cents=amount_cents,
            item_deleted=False,
            payment_blocked=bool(item["payment_blocked"]),
            facts=facts,
        )
        evidence = json.dumps(match.as_result(), sort_keys=True)

        if bool(item["payment_blocked"]):
            return not_eligible(
                f"a payment block is set on {item_key} ({item['block_reason']}). Resolve the "
                "reason or escalate; do not remove the block to let the payment through "
                "(policy mb-blocked-payments)"
            )
        if match.decision == controls.MISSING_GR:
            return not_eligible(
                f"{item['flow']} requires a goods receipt before clearing, and none is "
                "recorded (policy mb-goods-receipt)"
            )
        if not match.within_tolerance:
            queue_id = db.queue_for_approval(
                conn,
                item_key=item_key,
                invoice_id=invoice["invoice_id"],
                actor_id=ctx.actor_id,
                activity="Clear Invoice",
                amount_cents=amount_cents,
                trigger="tolerance_override",
                evidence=evidence,
            )
            return {
                "ok": True,
                "item_key": item_key,
                "invoice_id": invoice["invoice_id"],
                "amount_eur": amount_cents / 100,
                "status": "queued_for_approval",
                "queue_id": queue_id,
                "trigger": "tolerance_override",
                "match": match.as_result(),
                "note": (
                    "Queued for a controller because the variance is outside tolerance. "
                    "This invoice has NOT been paid."
                ),
            }

        conflict = sod_conflict(ctx, "Clear Invoice", db.case_history(conn, item_key), facts)
        if conflict:
            queue_id = db.queue_for_approval(
                conn,
                item_key=item_key,
                invoice_id=invoice["invoice_id"],
                actor_id=ctx.actor_id,
                activity="Clear Invoice",
                amount_cents=amount_cents,
                trigger="sod_conflict",
                evidence=evidence,
            )
            return {
                "ok": True,
                "item_key": item_key,
                "invoice_id": invoice["invoice_id"],
                "amount_eur": amount_cents / 100,
                "status": "queued_for_approval",
                "queue_id": queue_id,
                "trigger": "sod_conflict",
                "note": (
                    f"You already performed '{conflict[0]}' on {item_key}, so clearing it "
                    "yourself would breach segregation of duties. Queued for a controller. "
                    "This invoice has NOT been paid."
                ),
            }

        if amount_cents > clearing_limit_cents(ctx, facts):
            queue_id = db.queue_for_approval(
                conn,
                item_key=item_key,
                invoice_id=invoice["invoice_id"],
                actor_id=ctx.actor_id,
                activity="Clear Invoice",
                amount_cents=amount_cents,
                trigger="above_limit",
                evidence=evidence,
            )
            return {
                "ok": True,
                "item_key": item_key,
                "invoice_id": invoice["invoice_id"],
                "amount_eur": amount_cents / 100,
                "status": "queued_for_approval",
                "queue_id": queue_id,
                "trigger": "above_limit",
                "note": (
                    f"{amount_cents / 100:,.2f} is above the "
                    f"{facts['clearing_auto_approve_limit_eur']:,} clearing limit, so a "
                    "controller must approve it. This invoice has NOT been paid."
                ),
            }

        db.clear_invoice_row(
            conn,
            item_key=item_key,
            invoice_id=invoice["invoice_id"],
            amount_cents=amount_cents,
            actor_id=ctx.actor_id,
        )
        return {
            "ok": True,
            "item_key": item_key,
            "invoice_id": invoice["invoice_id"],
            "amount_eur": amount_cents / 100,
            "status": "cleared",
            "clearing_id": invoice["invoice_id"],
        }


# The registry the agent assembles from, and the activity each write tool
# contributes to the process. Read tools have no business activity: looking
# something up is not a step in the purchase-to-pay process, and lifting every
# lookup into the log would make the agent's map incomparable to the human one.
TOOLS = {
    "get_purchase_item": get_purchase_item,
    "get_three_way_match": get_three_way_match,
    "get_policy": get_policy,
    "record_goods_receipt": record_goods_receipt,
    "clear_invoice": clear_invoice,
}

READ_TOOLS = frozenset({"get_purchase_item", "get_three_way_match", "get_policy"})
WRITE_TOOLS = frozenset({"record_goods_receipt", "clear_invoice"})
