"""The live model adapter: Claude Sonnet behind Matchbook's `Model` protocol.

This is the first real model in the repo. It implements exactly one method,
`step()`, so nothing about the session loop, the dispatch seam, the spans or
the bridge changes when it is used instead of `ScriptedModel`.

Two things in here are not obvious and are worth reading before editing.

**The adapter keeps its own history.** `run_session` appends one
`{"role": "tool", ...}` message per call, and never appends the assistant turn
that *requested* those calls -- it has no need to, because `ScriptedModel`
ignores the history entirely. The Messages API does need it: a `tool_result`
must be answered for by a preceding assistant turn carrying the matching
`tool_use` block. So this adapter folds the loop's message list into its own
provider-shaped history, and keeps the assistant turns verbatim as the API
returned them. Keeping them verbatim also preserves thinking blocks, which must
be echoed back unchanged while continuing on the same model.

**Tool results are grouped.** Every `tool_result` answering one assistant turn
goes into a single user message. Splitting them across several messages is
accepted by the API but teaches the model to stop requesting tools in parallel,
which would quietly change the process the agent executes -- and this repo
exists to measure that process.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from agent.agent import ModelStep, ToolCall, Usage

REPO_ROOT = Path(__file__).resolve().parent.parent

# Claude Sonnet 5: 1M context, $2/MTok input, $10/MTok output.
DEFAULT_MODEL = "claude-sonnet-5"

# Adaptive thinking is on by default on this model and `budget_tokens` is
# rejected, so depth is controlled by effort instead. `medium` is the starting
# point for accounts-payable exception handling: the reasoning is short and the
# controls are enforced in code, not by the model. Measure before raising it.
DEFAULT_EFFORT = "medium"

# Thinking tokens count against max_tokens, so leave real headroom. Replies in
# this loop are a few sentences; the budget is for deliberation.
DEFAULT_MAX_TOKENS = 16_000


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into the environment; existing vars win.

    A few lines instead of a dependency, and it keeps the key in a gitignored
    file rather than in a shell history. Values are never logged.
    """
    source = path or REPO_ROOT / ".env"
    if not source.exists():
        return
    for line in source.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            os.environ.setdefault(key, value)


# -- tool schemas ------------------------------------------------------------
#
# Hand-written rather than derived from the signatures, because the description
# text is what the model actually steers on and a generated schema has none.
# Every tool is `strict`, so arguments validate exactly and `_call()`'s
# TypeError branch stays a genuine edge case instead of a routine path.
#
# Optional parameters are listed in `required` with "" documented as "omitted":
# strict mode wants a closed schema, and an empty string is what the underlying
# functions already default to.

_ITEM_KEY = {
    "type": "string",
    "description": (
        "The purchase-order item key, formatted <po_number>_<5-digit item>, "
        "for example 4507001234_00010."
    ),
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_purchase_item",
        "description": (
            "Return one purchase-order item with its goods-receipt and invoice "
            "state. Also returns sibling_items when the purchase order has more "
            "than one line; confirm which item is meant before acting."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"item_key": _ITEM_KEY},
            "required": ["item_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_three_way_match",
        "description": (
            "Evaluate the three-way match for an item and return the variance, "
            "the tolerance decision and whether payment is blocked. Call this "
            "before making any numeric claim about a variance; a figure stated "
            "in prose without this call is wrong even when it happens to match."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"item_key": _ITEM_KEY},
            "required": ["item_key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_policy",
        "description": (
            "Return a policy document in full so a claim can cite it. Known "
            "ids: mb-three-way-match, mb-clearing-authority, mb-goods-receipt, "
            "mb-blocked-payments, mb-payment-terms."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "policy_id": {
                    "type": "string",
                    "description": "The policy identifier, e.g. mb-goods-receipt.",
                }
            },
            "required": ["policy_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_goods_receipt",
        "description": (
            "Record goods-receipt evidence against an item. Buyers only: an AP "
            "clerk recording a receipt and then clearing the invoice is the "
            "duty conflict the controls exist to prevent."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "item_key": _ITEM_KEY,
                "quantity": {
                    "type": "number",
                    "description": "Quantity received. Must be positive.",
                },
                "value_eur": {
                    "type": "number",
                    "description": "Value received in EUR. Must be positive.",
                },
                "reference": {
                    "type": "string",
                    "description": "Delivery-note reference, or \"\" if none.",
                },
            },
            "required": ["item_key", "quantity", "value_eur", "reference"],
            "additionalProperties": False,
        },
    },
    {
        "name": "clear_invoice",
        "description": (
            "Clear an item's open invoice, or queue it for a controller when it "
            "is above the caller's limit. A queued clearing has not been paid; "
            "say so rather than reporting a payment."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "item_key": _ITEM_KEY,
                "note": {
                    "type": "string",
                    "description": "Short justification, or \"\" if none.",
                },
            },
            "required": ["item_key", "note"],
            "additionalProperties": False,
        },
    },
]


def build_client() -> Any:
    """Construct the SDK client, or exit with the one thing the user must do.

    The import is here rather than at module scope so `import agent.agent`
    keeps working with the package absent -- which is what the scripted path,
    and therefore CI, relies on.
    """
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Put it in .env (gitignored) or export it, "
            "or run with --model scripted, which needs no key."
        )
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "the anthropic package is not installed; install the agent extra: "
            "uv sync --extra agent"
        ) from exc
    # The SDK resolves ANTHROPIC_API_KEY itself; passing it explicitly would
    # put the value in a traceback frame for no benefit.
    return anthropic.Anthropic()


# -- the adapter -------------------------------------------------------------

class AnthropicModel:
    """Claude Sonnet as a Matchbook `Model`. One instance per session.

    Instance state is deliberate: the adapter has to remember the `tool_use`
    ids it emitted on the previous step in order to pair them with the tool
    results the loop appends next. Reusing one instance across sessions would
    mix their histories.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_system: bool = True,
        client: Any | None = None,
    ) -> None:
        self.name = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.cache_system = cache_system
        # Built eagerly when the caller did not inject one, so a missing key
        # fails before `run_session` has written a run row. A lazy client would
        # leave a run in the span store with no spans under it, and the cost
        # report would have to reason about a session that never happened.
        self._client = client if client is not None else build_client()
        self._system: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self._consumed = 0
        self._pending_tool_use_ids: list[str] = []

    @property
    def client(self) -> Any:
        return self._client

    # -- history ------------------------------------------------------------

    def _fold(self, messages: Sequence[dict[str, Any]]) -> None:
        """Fold messages the loop has appended since the last step into history."""
        pending_results: list[dict[str, Any]] = []
        for entry in list(messages)[self._consumed:]:
            role = entry.get("role")
            content = entry.get("content") or ""
            if role == "system":
                block: dict[str, Any] = {"type": "text", "text": content}
                if self.cache_system:
                    # The prefix renders tools, then system, then messages, so a
                    # breakpoint on the last system block caches the tool
                    # definitions too. Note there is a model-dependent minimum
                    # cacheable prefix: below it nothing is cached and no error
                    # is raised, so verify with cache_read_tokens rather than
                    # assuming a saving.
                    block["cache_control"] = {"type": "ephemeral"}
                self._system = [block]
            elif role == "user":
                self._history.append({"role": "user", "content": content})
            elif role == "tool":
                pending_results.append(entry)
            # An assistant entry is appended by the loop only just before it
            # breaks, so step() is never called again after one. Our own
            # verbatim assistant turns are already in _history.
            self._consumed += 1

        if not pending_results:
            return

        # One user message carrying every result for the previous assistant
        # turn. The ids were recorded in the order the loop dispatches them.
        blocks: list[dict[str, Any]] = []
        for index, entry in enumerate(pending_results):
            if index < len(self._pending_tool_use_ids):
                tool_use_id = self._pending_tool_use_ids[index]
            else:  # pragma: no cover - a result with no request is a loop bug
                continue
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": str(entry.get("content") or ""),
                }
            )
        if blocks:
            self._history.append({"role": "user", "content": blocks})
        self._pending_tool_use_ids = []

    # -- the protocol -------------------------------------------------------

    def step(self, messages: Sequence[dict[str, Any]]) -> ModelStep:
        self._fold(messages)

        response = self.client.messages.create(
            model=self.name,
            max_tokens=self.max_tokens,
            system=self._system,
            # A snapshot: the adapter keeps appending to _history after this
            # call, and handing out the live list makes the request mutate
            # under anything that inspects or retries it.
            messages=list(self._history),
            tools=TOOL_SCHEMAS,
            output_config={"effort": self.effort},
        )

        usage = _usage_of(response)

        # Guard the stop reason before reading content: a declined request
        # returns 200 with no usable content.
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            return ModelStep(
                reply=(
                    "I can't act on that request. It was declined by a safety "
                    f"classifier (category: {category}). Escalate it to a controller."
                ),
                usage=usage,
            )

        content = list(getattr(response, "content", None) or [])
        # Verbatim, so thinking blocks survive to the next turn on this model.
        self._history.append({"role": "assistant", "content": content})

        calls: list[ToolCall] = []
        texts: list[str] = []
        tool_use_ids: list[str] = []
        for block in content:
            kind = getattr(block, "type", None)
            if kind == "tool_use":
                tool_use_ids.append(getattr(block, "id", ""))
                arguments = getattr(block, "input", None) or {}
                calls.append(
                    ToolCall(
                        name=str(getattr(block, "name", "")),
                        arguments=dict(arguments) if isinstance(arguments, dict) else {},
                    )
                )
            elif kind == "text":
                text = str(getattr(block, "text", "") or "")
                if text:
                    texts.append(text)

        if calls:
            self._pending_tool_use_ids = tool_use_ids
            return ModelStep(tool_calls=tuple(calls), usage=usage)

        reply = "\n\n".join(texts)
        if getattr(response, "stop_reason", None) == "max_tokens" and not reply:
            reply = "(the model hit its output limit before producing a reply)"
        return ModelStep(reply=reply, usage=usage)


def _usage_of(response: Any) -> Usage:
    """Read token counts defensively; a missing field is 0, never a crash.

    Instrumentation must not be able to fail a run that already succeeded.
    """
    raw = getattr(response, "usage", None)

    def count(name: str) -> int:
        value = getattr(raw, name, 0)
        return int(value) if isinstance(value, (int, float)) else 0

    return Usage(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_read_tokens=count("cache_read_input_tokens"),
        cache_write_tokens=count("cache_creation_input_tokens"),
    )
