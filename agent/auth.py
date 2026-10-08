"""Auth context and permission checks. Complete; do not weaken.

This implements SPEC.md section 3. The slogan the course repeats:
**authorization is not a prompt.** The server builds the context from the
actors table, the tools call these checks before touching data, and the model
cannot reach outside the caller's row of the matrix however it is asked.

What is different here from Oakline, and the reason this domain is worth
teaching: `sod_conflict` is **stateful**. Whether a caller may act depends on
what that caller already did on the same purchase-order item, so the same
actor, the same action and the same item can be permitted or denied depending
on history. That is an authorization rule which is a *process* property rather
than a record property, and no prompt can implement it.

Tool-result convention used across the repo:
  - success: a dict containing "ok": True plus payload fields.
  - failure: {"ok": False, "error": <code>, "reason": <str>}.
    Codes: "permission_denied", "not_found", "not_eligible",
    "invalid_argument", "duplicate_invoice", "paused", "not_implemented".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

ROLES = ("buyer", "ap_clerk", "controller")

# SPEC AUTH-2: role-level separation of duties.
GOODS_RECEIPT_ROLES = frozenset({"buyer"})
INVOICE_ROLES = frozenset({"ap_clerk"})
CLEARING_ROLES = frozenset({"ap_clerk"})
BLOCK_ROLES = frozenset({"ap_clerk"})
APPROVAL_ROLES = frozenset({"controller"})


@dataclass(frozen=True)
class AuthContext:
    """Who is on the other end of the session.

    Built by the CLI or the server from the actors table and passed to every
    tool. Never taken from the conversation.
    """

    actor_id: str
    role: str
    company_code: str | None = None
    purchasing_group: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role!r}")
        if self.role == "buyer" and not self.purchasing_group:
            raise ValueError("a buyer auth context requires a purchasing_group")
        if self.role in ("ap_clerk", "controller") and not self.company_code:
            raise ValueError(f"a {self.role} auth context requires a company_code")


def permission_denied(reason: str) -> dict[str, Any]:
    """The structured error every denied action returns."""
    return {"ok": False, "error": "permission_denied", "reason": reason}


def not_eligible(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": "not_eligible", "reason": reason}


def not_found(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": "not_found", "reason": reason}


def invalid_argument(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": "invalid_argument", "reason": reason}


def can_view_item(ctx: AuthContext, item: dict[str, Any]) -> bool:
    """SPEC AUTH-1, the view rows.

    A buyer sees their own purchasing group; an AP clerk sees their own company
    code; a controller sees everything. The scopes are different *dimensions*,
    not nested ones, which is why this cannot be collapsed to a single level
    check.
    """
    if ctx.role == "controller":
        return True
    if ctx.role == "buyer":
        return item.get("purchasing_group") == ctx.purchasing_group
    return item.get("company_code") == ctx.company_code


def can_record_goods_receipt(ctx: AuthContext, item: dict[str, Any]) -> bool:
    return ctx.role in GOODS_RECEIPT_ROLES and can_view_item(ctx, item)


def can_record_invoice(ctx: AuthContext, item: dict[str, Any]) -> bool:
    return ctx.role in INVOICE_ROLES and can_view_item(ctx, item)


def can_clear_invoice(ctx: AuthContext, item: dict[str, Any]) -> bool:
    """Scope and role only. The amount limit and the control checks are the
    tool's job, deliberately: mixing them here would hide the ordering that
    SPEC TOOL-6 fixes."""
    return ctx.role in CLEARING_ROLES and can_view_item(ctx, item)


def can_change_payment_block(ctx: AuthContext, item: dict[str, Any]) -> bool:
    return ctx.role in BLOCK_ROLES and can_view_item(ctx, item)


def can_decide_approval(ctx: AuthContext) -> bool:
    return ctx.role in APPROVAL_ROLES


def clearing_limit_cents(ctx: AuthContext, facts: dict[str, Any]) -> int:
    """The caller's own ceiling, above which a clearing queues."""
    if ctx.role == "controller":
        return int(round(facts["approval_limits_eur"]["controller"] * 100))
    return int(round(facts["clearing_auto_approve_limit_eur"] * 100))


def sod_conflict(
    ctx: AuthContext,
    activity: str,
    history: Sequence[tuple[str, str]],
    facts: dict[str, Any],
) -> tuple[str, str] | None:
    """SPEC AUTH-3: the stateful check, delegated to the pure oracle.

    `history` is the case's own business history as (activity, actor) pairs --
    the world's history, never the agent's trace log, which would make
    conformance against a mined model self-referential.
    """
    from seed.controls import sod_conflict as oracle

    return oracle(actor_id=ctx.actor_id, activity=activity, history=history, facts=facts)
