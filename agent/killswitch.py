"""The kill switch: one environment variable, three rungs.

Checked as the FIRST statement of every write tool's logic, before any work and
before any authorization check, so a paused system cannot be talked into acting
by any argument.

The level is recorded on the span, so a launch drill is provable from traces
rather than from memory. And because a paused call is still a span, running the
same scenario set with the switch off and on produces two process maps whose
diff *is* the drill's effect.
"""

from __future__ import annotations

import os
from typing import Any

ENV_VAR = "MB_KILL_SWITCH"

OFF = "off"
CLEARING = "clearing"
PAYMENTS = "payments"
READONLY = "readonly"

LEVELS = (OFF, CLEARING, PAYMENTS, READONLY)

# The write tools that EXIST today. Listing tools that are merely planned
# makes the ladder look broader than it is: `MB_KILL_SWITCH=payments` appeared
# to pause remove_payment_block, a tool with no implementation, so the rung
# read as a stronger guarantee than it gave. A test asserts this set matches
# the tool registry.
WRITE_TOOLS = frozenset({"record_goods_receipt", "clear_invoice"})

# Tools these rungs will also pause once they exist (SPEC lists five tools for
# this milestone; invoice recording and payment-block changes come later).
# Kept so the ladder's intent is documented, and deliberately NOT folded into
# WRITE_TOOLS, where it would overstate what is enforced.
PLANNED_WRITE_TOOLS = frozenset(
    {"record_invoice_receipt", "set_payment_block", "remove_payment_block"}
)

PAUSED_BY_LEVEL: dict[str, frozenset[str]] = {
    OFF: frozenset(),
    CLEARING: frozenset({"clear_invoice"}),
    # remove_payment_block belongs on this rung and will be added with the tool.
    PAYMENTS: frozenset({"clear_invoice"}),
    READONLY: WRITE_TOOLS,
}


def level() -> str:
    """The current level. An unset, blank or unrecognised value means `off`.

    Failing closed would be worse than it sounds: a typo in the variable name
    would silently freeze every write and look like a bug in the agent. An
    unknown value is reported by `explain()` instead.
    """
    raw = (os.environ.get(ENV_VAR) or "").strip().lower()
    return raw if raw in LEVELS else OFF


def kill_switch(tool_name: str) -> dict[str, Any] | None:
    """Return a structured `paused` result when `tool_name` is paused, else None."""
    current = level()
    if tool_name in PAUSED_BY_LEVEL[current]:
        return {
            "ok": False,
            "error": "paused",
            "reason": (
                f"{tool_name} is paused: the kill switch is at '{current}'. "
                "Explain the pause and escalate; do not attempt another write to "
                "achieve the same effect."
            ),
        }
    return None


def explain() -> str:
    raw = os.environ.get(ENV_VAR)
    current = level()
    if raw and raw.strip().lower() not in LEVELS:
        return (
            f"{ENV_VAR}={raw!r} is not a recognised level, so the kill switch is "
            f"OFF. Valid levels: {', '.join(LEVELS)}."
        )
    paused = sorted(PAUSED_BY_LEVEL[current])
    return f"kill switch '{current}'" + (f", pausing: {', '.join(paused)}" if paused else "")
