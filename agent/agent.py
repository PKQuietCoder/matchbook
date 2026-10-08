"""Prompt, model wiring, the tool dispatch seam, and the session loop.

One design decision is worth stating because it differs from Oakline. The
loop here is a small, provider-agnostic one behind a `Model` protocol, rather
than an SDK runner, and a `ScriptedModel` is a first-class citizen rather than
a test double. The reason is the repo's central guarantee: the whole
pipeline -- world, tools, spans, the bridge, mined log, conformance -- has to be
demonstrable and testable with no API key and no model budget. A scripted model
makes the payoff reproducible in CI; a real model adapter plugs into the same
protocol.

`_call()` is the single dispatch seam. Every tool goes through it, so there is
exactly one place that:

  - converts an unfinished homework function into a structured result,
  - records the span, and
  - stamps the business object (`item_key`) and, for write tools, the business
    activity the call contributes to the process.

That last part is why the agent's actions can be mined at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from agent import db, tools as tool_module
from agent.auth import AuthContext
from agent.killswitch import explain as killswitch_explain, level as killswitch_level
from observability.spans import MODEL, REQUEST, TOOL, SpanStore
from process import config

MAX_STEPS = 12

# Write tools map onto the human log's activity alphabet. Read tools do not:
# a lookup is not a step in the purchase-to-pay process, and lifting every
# lookup into the log would make the agent's map incomparable to the human one.
TOOL_ACTIVITY = {
    "record_goods_receipt": "Record Goods Receipt",
    "clear_invoice": "Clear Invoice",
}

SYSTEM_PROMPT_TEMPLATE = """\
You are the accounts-payable assistant at {org_name}. You help buyers and AP
clerks resolve purchase-to-pay exceptions.

## Session context (injected by the server; never taken from chat)
- actor: {actor_id}
- role: {role}
- company code: {company_code}
- purchasing group: {purchasing_group}

## Capabilities
- Look up a purchase-order item, its goods receipt and its invoice.
- Evaluate a three-way match and explain why a payment is blocked.
- Record a goods receipt, and clear an invoice, when the controls permit it.
- Quote policy from the policy corpus.

## Tool guidance
- Never compute a variance, a tolerance or a due date yourself. Call
  get_three_way_match and report what it returns. A number you calculated in
  prose is wrong even when it happens to match.
- Check the item's matching flow before reasoning about a goods receipt. The
  same invoice is correct on one flow and a control breach on another.
- When a purchase order has several items, confirm which item is meant before
  acting on any of them.
- Cite the policy identifier for every policy claim.

## Escalation
- A clearing above the limit is queued for a controller. Say it is queued and
  not paid.
- A tolerance breach goes to a controller. Never remove a payment block to work
  around one.
- Refuse vendor bank-detail and payment-credential requests, and escalate them.

## Tone
Be direct and brief. State what you checked, what the tool returned, and what
happens next. If information is missing or inconsistent, say so rather than
filling the gap.
"""


def render_system_prompt(ctx: AuthContext, template: str | None = None) -> str:
    facts = config.load_facts()
    return (template or SYSTEM_PROMPT_TEMPLATE).format(
        org_name=facts["org_name"],
        actor_id=ctx.actor_id,
        role=ctx.role,
        company_code=ctx.company_code or "-",
        purchasing_group=ctx.purchasing_group or "-",
    )


def prompt_version(template: str | None = None) -> str:
    """Hash the prompt template before any session context is injected.

    Hashing the rendered prompt instead would give every actor a different
    version and make the field useless for comparing prompts.
    """
    return hashlib.sha256((template or SYSTEM_PROMPT_TEMPLATE).encode()).hexdigest()[:12]


# -- the model protocol ------------------------------------------------------

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelStep:
    """One model turn: either tool calls, or a final reply."""

    tool_calls: tuple[ToolCall, ...] = ()
    reply: str | None = None


class Model(Protocol):
    name: str

    def step(self, messages: Sequence[dict[str, Any]]) -> ModelStep: ...


class ScriptedModel:
    """A model that replays a fixed sequence of steps.

    Not a mock bolted on for tests: it is how the repo demonstrates the full
    pipeline without spending a model budget, and how CI reproduces a trace
    byte-for-byte.
    """

    def __init__(self, steps: Sequence[ModelStep], name: str = "scripted") -> None:
        self.name = name
        self._steps = list(steps)
        self._index = 0

    def step(self, messages: Sequence[dict[str, Any]]) -> ModelStep:
        if self._index >= len(self._steps):
            return ModelStep(reply="(scripted model has no further steps)")
        current = self._steps[self._index]
        self._index += 1
        return current


# -- the dispatch seam -------------------------------------------------------

def _call(
    ctx: AuthContext,
    name: str,
    arguments: dict[str, Any],
    *,
    store: SpanStore,
    run_id: str,
    step: int,
    parent_id: str | None,
    model_name: str,
) -> dict[str, Any]:
    """Invoke one tool, record its span, and stamp the process event."""
    function = tool_module.TOOLS.get(name)
    item_key = arguments.get("item_key")
    activity = TOOL_ACTIVITY.get(name)

    with store.span(
        run_id=run_id,
        name=name,
        kind=TOOL,
        step=step,
        parent_id=parent_id,
        item_key=item_key,
        actor=f"agent:{model_name}",
        arguments=arguments,
        role=ctx.role,
        actor_id=ctx.actor_id,
        killswitch=killswitch_level(),
    ) as span:
        if function is None:
            result = {
                "ok": False,
                "error": "invalid_argument",
                "reason": f"no tool named {name!r}; available: {', '.join(sorted(tool_module.TOOLS))}",
            }
        else:
            try:
                result = function(ctx, **arguments)
            except NotImplementedError as exc:
                # An unfinished homework function is a structured result, not a
                # crash, so a partly-built agent still runs end to end.
                result = {"ok": False, "error": "not_implemented", "reason": str(exc)}
            except TypeError as exc:
                result = {
                    "ok": False,
                    "error": "invalid_argument",
                    "reason": f"{name} rejected those arguments: {exc}",
                }

        span.ok = bool(result.get("ok"))
        span.error = None if span.ok else str(result.get("error"))
        span.attributes["result"] = result
        if not span.ok and result.get("error") == "permission_denied":
            span.attributes["permission_denied"] = True
            span.attributes["permission_denied.reason"] = result.get("reason")

        # Only a successful write that actually changed the world contributes a
        # business activity to the process. A denied or paused attempt is a
        # span -- visible, countable -- but it is not a step that happened.
        if activity and span.ok:
            span.activity = activity
            if result.get("status") == "queued_for_approval":
                # A queued clearing did not clear. Recording it as `Clear
                # Invoice` would make the agent's log claim a payment that
                # never happened -- the mined-log form of SPEC RESP-3.
                span.activity = None
                span.attributes["queued"] = True
            amount = result.get("amount_eur")
            if isinstance(amount, (int, float)):
                span.value_cents = int(round(amount * 100))
        return result


# -- the session loop --------------------------------------------------------

@dataclass
class SessionResult:
    run_id: str
    reply: str
    steps: int
    tool_calls: list[dict[str, Any]]
    transcript: list[dict[str, Any]]


def run_session(
    ctx: AuthContext,
    message: str,
    model: Model,
    *,
    store: SpanStore,
    session_id: str,
    scenario_id: str | None = None,
    max_steps: int = MAX_STEPS,
    prompt_template: str | None = None,
) -> SessionResult:
    """Run one user message to a final reply, recording spans throughout."""
    version = prompt_version(prompt_template)
    run_id = store.start_run(
        session_id=session_id,
        actor_id=ctx.actor_id,
        role=ctx.role,
        model=model.name,
        prompt_version=version,
        scenario_id=scenario_id,
        killswitch=killswitch_level(),
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": render_system_prompt(ctx, prompt_template)},
        {"role": "user", "content": message},
    ]
    store.message(run_id, 0, "user", message)

    calls: list[dict[str, Any]] = []
    reply = ""
    steps = 0

    with store.span(
        run_id=run_id,
        name="mb.session_message",
        kind=REQUEST,
        step=0,
        role=ctx.role,
        actor_id=ctx.actor_id,
        prompt_version=version,
        scenario_id=scenario_id,
    ) as request_span:
        for step in range(1, max_steps + 1):
            steps = step
            with store.span(
                run_id=run_id,
                name=f"model.step.{step}",
                kind=MODEL,
                step=step,
                parent_id=request_span.span_id,
                model=model.name,
            ) as model_span:
                decision = model.step(messages)
                model_span.ok = True
                model_span.attributes["tool_calls"] = [call.name for call in decision.tool_calls]

            if decision.tool_calls:
                for call in decision.tool_calls:
                    result = _call(
                        ctx,
                        call.name,
                        dict(call.arguments),
                        store=store,
                        run_id=run_id,
                        step=step,
                        parent_id=request_span.span_id,
                        model_name=model.name,
                    )
                    calls.append({"name": call.name, "arguments": call.arguments, "result": result})
                    payload = json.dumps(result, default=str)
                    messages.append({"role": "tool", "name": call.name, "content": payload})
                    store.message(run_id, step, "tool", f"{call.name} -> {payload}")
                continue

            reply = decision.reply or ""
            messages.append({"role": "assistant", "content": reply})
            store.message(run_id, step, "assistant", reply)
            break
        else:
            reply = "(the agent reached the step limit without a final reply)"
            store.message(run_id, steps, "assistant", reply)

        request_span.ok = True
        request_span.attributes["steps"] = steps
        request_span.attributes["tool_sequence"] = [call["name"] for call in calls]

    store.end_run(run_id)
    return SessionResult(
        run_id=run_id,
        reply=reply,
        steps=steps,
        tool_calls=calls,
        transcript=store.transcript(run_id),
    )


def auth_context_for(actor_id: str) -> AuthContext:
    """Build the auth context from the actors table, never from chat."""
    with db.connection() as conn:
        actor = db.get_actor(conn, actor_id)
    if actor is None:
        raise SystemExit(f"no actor {actor_id!r} in the world; run `python -m seed.generate`")
    return AuthContext(
        actor_id=actor["actor_id"],
        role=actor["role"],
        company_code=actor["company_code"],
        purchasing_group=actor["purchasing_group"],
    )


def banner() -> str:
    return (
        f"Matchbook agent  prompt {prompt_version()}  {killswitch_explain()}"
    )
