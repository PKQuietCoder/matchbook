"""OpenTelemetry spans for the agent, exported to Langfuse.

Two recorders, one decision. `observability/spans.py` writes spans to local
SQLite and is **authoritative**: `bridge/spans_to_log.py` mines the event log
from it and `bridge/cost.py` prices it. This module additionally emits OTel
spans so the same run is readable in a trace UI. Everything that both of them
record goes through one function here, so the two cannot disagree about what
happened -- see `record_tool_result` and `record_activity`.

Nothing in this module may be imported by `process/`. The mining half must run
with no model, agent, HTTP or OTel dependency, and
`tests/test_offline_mining.py` blocks `opentelemetry` by name to prove it.

The module is import-safe and call-safe **without** the SDK installed. With a
plain `uv sync` (no `--extra agent`), or with the `LANGFUSE_*` variables unset,
every function here still runs and records nothing. That is what keeps the
scripted, no-API-key pipeline working: tracing is an addition to Matchbook, not
a precondition for it.

There is no auto-instrumentation to lean on. Matchbook has no agent framework,
so every span below is opened by hand -- which is more work than importing an
instrumentor, and is also the only way the span tree can be shaped around
*purchase-order items* rather than around a framework's idea of a run.
"""

from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent.auth import AuthContext
    from observability.spans import Span

# -- attribute vocabulary ----------------------------------------------------
#
# The `mb.` prefix is not new here: process/otlp.py already exports the human
# event log with mb.case_id / mb.activity / mb.tie_broken. Reusing it means the
# agent's spans and the real company's spans speak one vocabulary, which is the
# entire point of the comparison this repo exists to make.

ACTOR_ID = "mb.actor_id"
ROLE = "mb.role"
COMPANY_CODE = "mb.company_code"
PURCHASING_GROUP = "mb.purchasing_group"
ITEM_KEY = "mb.item_key"
ACTIVITY = "mb.activity"
QUEUED = "mb.queued"
PERMISSION_DENIED = "mb.permission_denied"
PERMISSION_DENIED_REASON = "mb.permission_denied.reason"
PROMPT_VERSION = "mb.prompt_version"
SCENARIO_ID = "mb.scenario_id"
SESSION_ID = "mb.session_id"
RUN_ID = "mb.run_id"
KILLSWITCH = "mb.killswitch"
STEP = "mb.step"
VALUE_CENTS = "mb.value_cents"

# Anthropic reports four token counts that are DISJOINT (see agent/agent.py's
# Usage docstring); OTel GenAI's gen_ai.usage.input_tokens means total prompt
# tokens. Summing them to fit the standard field would publish a number no
# provider reported, so the cache counts travel beside it under mb.* and the
# handout says why. Same discipline as EventLog.tie_broken.
CACHE_READ_TOKENS = "mb.cache_read_tokens"
CACHE_WRITE_TOKENS = "mb.cache_write_tokens"

OPERATION = "gen_ai.operation.name"
TOOL_NAME = "gen_ai.tool.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"

ROOT_SPAN_NAME = "mb.session_message"
DEFAULT_HOST = "http://localhost:3000"
OTLP_PATH = "/api/public/otel/v1/traces"


# -- the no-op fallback ------------------------------------------------------

class _NoopSpan:
    """Stands in for a span when there is nothing to export to.

    `is_recording()` is False, so a caller that checks it skips its work, and
    `set_attribute` is a sink for callers that do not.
    """

    def is_recording(self) -> bool:
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        return None

    def record_exception(self, exception: BaseException) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


_NOOP_SPAN = _NoopSpan()
_NOOP_TRACER = _NoopTracer()
_tracer: Any = None
_configured = False


# -- configuration -----------------------------------------------------------

def trace_content() -> bool:
    """Whether to attach message text to spans.

    Off by default. With it off the span tree, tool order, roles and token
    counts are all still recorded -- enough for Module 1's inspection and for
    the smoke report -- but the message text Module 2's review needs is not.
    Turn it off when message content must not leave the machine, and redact
    before exporting traces.
    """
    return os.environ.get("MB_TRACE_CONTENT", "false").strip().lower() == "true"


def langfuse_headers() -> dict[str, str] | None:
    """Basic-auth headers for Langfuse's OTLP endpoint, or None if unconfigured."""
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not public or not secret:
        return None
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def langfuse_host() -> str:
    return os.environ.get("LANGFUSE_HOST", DEFAULT_HOST).rstrip("/")


def configure(service_name: str = "matchbook-agent", *, force: bool = False) -> bool:
    """Install a tracer that exports to Langfuse. Return whether it worked.

    Returns False -- having installed nothing and raised nothing -- when the
    SDK is absent or the keys are unset. A missing trace backend must never be
    the reason an agent run fails.
    """
    global _tracer, _configured
    if _configured and not force:
        return _tracer is not None
    _configured = True

    headers = langfuse_headers()
    if headers is None:
        _tracer = None
        return False
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _tracer = None
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{langfuse_host()}{OTLP_PATH}", headers=headers)
        )
    )
    otel_trace.set_tracer_provider(provider)
    _tracer = otel_trace.get_tracer("matchbook.agent")
    return True


def use_tracer(tracer: Any) -> None:
    """Install a tracer directly. For tests, which export in memory."""
    global _tracer, _configured
    _tracer = tracer
    _configured = True


def tracer() -> Any:
    if not _configured:
        configure()
    return _tracer or _NOOP_TRACER


def current_span() -> Any:
    """The active OTel span, or a no-op when there is no SDK."""
    if tracer() is _NOOP_TRACER:
        return _NOOP_SPAN
    from opentelemetry import trace as otel_trace

    return otel_trace.get_current_span()


def shutdown() -> None:
    """Flush pending spans. The batch processor exports on a timer, so a short
    CLI run can exit before its spans leave the process."""
    if _tracer is None:
        return
    try:
        from opentelemetry import trace as otel_trace

        provider = otel_trace.get_tracer_provider()
    except ImportError:  # pragma: no cover - SDK absent
        return
    for method in ("force_flush", "shutdown"):
        hook = getattr(provider, method, None)
        if callable(hook):
            hook()


# -- span shapes -------------------------------------------------------------

def _actor_attributes(ctx: "AuthContext") -> dict[str, Any]:
    attributes = {ACTOR_ID: ctx.actor_id, ROLE: ctx.role, COMPANY_CODE: ctx.company_code}
    if ctx.purchasing_group:
        attributes[PURCHASING_GROUP] = ctx.purchasing_group
    return attributes


def _messages(role: str, text: str) -> str:
    """One message in the OTel GenAI shape."""
    return json.dumps([{"role": role, "parts": [{"type": "text", "content": text}]}])


@contextmanager
def request_span(
    ctx: "AuthContext",
    message: str,
    *,
    run_id: str,
    session_id: str,
    prompt_version: str,
    scenario_id: str | None = None,
    killswitch: str = "off",
) -> Iterator[Any]:
    """The root span for one request, mirroring Matchbook's own `mb.session_message`."""
    with tracer().start_as_current_span(ROOT_SPAN_NAME) as span:
        if span.is_recording():
            attributes = _actor_attributes(ctx)
            attributes[RUN_ID] = run_id
            attributes[SESSION_ID] = session_id
            attributes[PROMPT_VERSION] = prompt_version
            attributes[KILLSWITCH] = killswitch
            if scenario_id:
                attributes[SCENARIO_ID] = scenario_id
            for key, value in attributes.items():
                span.set_attribute(key, value)
            if trace_content():
                span.set_attribute(INPUT_MESSAGES, _messages("user", message))
        yield span


def record_reply(span: Any, reply: str) -> None:
    """Attach the final assistant reply to the root span."""
    if span.is_recording() and trace_content():
        span.set_attribute(OUTPUT_MESSAGES, _messages("assistant", reply))


@contextmanager
def model_span(step: int, model_name: str) -> Iterator[Any]:
    with tracer().start_as_current_span(f"model.step.{step}") as span:
        if span.is_recording():
            span.set_attribute(OPERATION, "chat")
            span.set_attribute(REQUEST_MODEL, model_name)
            span.set_attribute(STEP, step)
        yield span


def record_usage(span: Any, model_name: str, usage: Any) -> None:
    """Record token counts, keeping Anthropic's disjoint fields distinguishable.

    `gen_ai.usage.input_tokens` carries the uncached input only. The cache
    counts are real tokens that were really billed, but they are *not* part of
    that field's definition, so they travel under mb.* instead of being summed
    into a total no provider reported.
    """
    if usage is None or not span.is_recording():
        return
    span.set_attribute(RESPONSE_MODEL, model_name)
    span.set_attribute(INPUT_TOKENS, usage.input_tokens)
    span.set_attribute(OUTPUT_TOKENS, usage.output_tokens)
    if usage.cache_read_tokens:
        span.set_attribute(CACHE_READ_TOKENS, usage.cache_read_tokens)
    if usage.cache_write_tokens:
        span.set_attribute(CACHE_WRITE_TOKENS, usage.cache_write_tokens)


@contextmanager
def tool_span(name: str, arguments: dict[str, Any]) -> Iterator[Any]:
    with tracer().start_as_current_span(f"tool.{name}") as span:
        if span.is_recording():
            span.set_attribute(OPERATION, "execute_tool")
            span.set_attribute(TOOL_NAME, name)
            item_key = arguments.get("item_key")
            if item_key:
                span.set_attribute(ITEM_KEY, str(item_key))
            if trace_content():
                span.set_attribute("mb.tool.arguments", json.dumps(arguments, default=str))
        yield span


# -- the two recorders both pipelines go through -----------------------------

def record_tool_result(span: "Span", ctx: "AuthContext", result: dict[str, Any]) -> None:
    """Record the authenticated caller and the permission decision on a tool call.

    Called once per tool call, from `_call` in agent/agent.py, with the SQLite
    span. The OTel span is taken from the active context, so one call keeps
    both recorders in step.

    The caller attributes answer a question a reply cannot: *which identity
    reached this tool*. Authorization is enforced in `agent/auth.py` and in the
    tools, never by a prompt (SPEC AUTH-1) -- these attributes are how that
    enforcement becomes auditable after the fact. The smoke report counts
    permission denials from them, and Module 3 asserts on them.
    """
    otel = current_span()
    for key, value in _actor_attributes(ctx).items():
        span.attributes.setdefault(key.removeprefix("mb."), value)
        if otel.is_recording():
            otel.set_attribute(key, value)
    _set_permission_denied_attributes(span, otel, result)


def _set_permission_denied_attributes(
    span: "Span", otel: Any, result: dict[str, Any]
) -> None:
    """Set the permission-denied attributes on both recorders.

    The flag is set on *every* tool call, not only the denied ones. An absent
    attribute and a false one are different claims: the first says nobody
    looked, the second says the call was allowed. A denial rate computed over
    spans that only carry the attribute when it is true is not a rate.
    """
    denied = isinstance(result, dict) and result.get("error") == "permission_denied"
    span.attributes[PERMISSION_DENIED.removeprefix("mb.")] = bool(denied)
    if otel.is_recording():
        otel.set_attribute(PERMISSION_DENIED, bool(denied))
    if denied:
        reason = str(result.get("reason") or "")
        span.attributes[PERMISSION_DENIED_REASON.removeprefix("mb.")] = reason
        if otel.is_recording():
            otel.set_attribute(PERMISSION_DENIED_REASON, reason)


def record_activity(span: "Span", activity: str | None, result: dict[str, Any]) -> None:
    """Decide whether this tool call contributed a business activity, and record it.

    This is the one function in the repo where the agent's event log is
    written, so it is the one that can falsify it.

    Only a successful write that actually changed the world is an activity. A
    refused or paused attempt is a span -- visible, countable, minable in the
    `attempts` layer -- but it is not a step that happened.

    A clearing that was *queued for a controller* is the sharp case. The tool
    returned ok, so a naive reading records `Clear Invoice`; but nothing was
    paid, and a mined log saying otherwise would claim a payment that never
    happened. That is SPEC RESP-3 in mined-log form: a queued clearing is
    reported as queued, never as paid. So the activity is dropped and
    `mb.queued` is set instead.
    """
    otel = current_span()
    if not (activity and span.ok):
        return
    if result.get("status") == "queued_for_approval":
        span.activity = None
        span.attributes[QUEUED.removeprefix("mb.")] = True
        if otel.is_recording():
            otel.set_attribute(QUEUED, True)
        return
    span.activity = activity
    if otel.is_recording():
        otel.set_attribute(ACTIVITY, activity)
    amount = result.get("amount_eur")
    if isinstance(amount, (int, float)):
        span.value_cents = int(round(amount * 100))
        if otel.is_recording():
            otel.set_attribute(VALUE_CENTS, span.value_cents)
