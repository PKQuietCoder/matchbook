"""The tracing layer and the authenticated endpoint.

No Langfuse, no Docker, no model key. Spans are exported in memory and the
endpoint is driven through FastAPI's TestClient, because a test that needs a
container is a test that stops being run.

The last test here is the one that earns its place. Matchbook records every run
twice -- to SQLite, which the bridge mines, and to OTel, which the trace UI
reads -- and two recorders that disagree are worse than one, because the
disagreement is invisible until someone reconciles a report by hand. So it
asserts they reach the same answer about the question that matters: did this
tool call put a business activity into the event log?
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry", reason="tracing needs `uv sync --extra agent`")
pytest.importorskip("fastapi", reason="the endpoint needs `uv sync --extra agent`")

from fastapi.testclient import TestClient  # noqa: E402
from agent.agent import _call, run_session  # noqa: E402
from agent.auth import AuthContext  # noqa: E402
from observability import instrument  # noqa: E402
from observability.spans import SpanStore  # noqa: E402
from server import app as server_app  # noqa: E402

CLERK = AuthContext(actor_id="ap-003", role="ap_clerk", company_code="MIS-01")
DEMO = "4507001234_00010"     # payment blocked
LARGE = "4507003300_00010"    # clean, above the clearing limit
OTHER_COMPANY = "ap-004"      # an AP clerk in MIS-02


@pytest.fixture()
def client(world_copy, monkeypatch):
    """The endpoint, against a throwaway world and a throwaway session store."""
    monkeypatch.setenv("MB_SESSIONS_DB", str(world_copy.parent / "sessions.db"))
    monkeypatch.setenv("MB_SESSION_SECRET", "test-secret")
    server_app._SESSIONS.clear()
    return TestClient(server_app.app)


# -- the identity the server established, not the one a message claims -------

def test_session_creation_rejects_a_role_the_world_disagrees_with(client):
    """SPEC AUTH-1. The claim loses to the record."""
    response = client.post("/sessions", json={"actor_id": "ap-003", "role": "controller"})
    assert response.status_code == 403
    assert "ap_clerk" in response.json()["detail"]


def test_session_creation_rejects_an_unknown_role_and_an_unknown_actor(client):
    assert client.post("/sessions", json={"actor_id": "ap-003", "role": "wizard"}).status_code == 400
    assert client.post("/sessions", json={"actor_id": "nobody", "role": "ap_clerk"}).status_code == 404


def test_a_token_cannot_authorize_a_different_session(client):
    first = client.post("/sessions", json={"actor_id": "ap-003", "role": "ap_clerk"}).json()
    second = client.post("/sessions", json={"actor_id": "ap-001", "role": "ap_clerk"}).json()
    assert first["session_id"] != second["session_id"]

    response = client.post(
        f"/sessions/{second['session_id']}/messages",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {first['token']}"},
    )
    assert response.status_code == 403


def test_a_message_needs_a_token_at_all(client):
    session = client.post("/sessions", json={"actor_id": "ap-003", "role": "ap_clerk"}).json()
    unauthenticated = client.post(
        f"/sessions/{session['session_id']}/messages", json={"message": "hello"}
    )
    assert unauthenticated.status_code == 401
    tampered = client.post(
        f"/sessions/{session['session_id']}/messages",
        json={"message": "hello"},
        headers={"Authorization": "Bearer deadbeef.notasignature"},
    )
    assert tampered.status_code == 401


def test_the_session_context_comes_from_the_world(client):
    """The stored identity is the whole payload; nothing is taken from chat."""
    session = client.post("/sessions", json={"actor_id": "ap-003", "role": "ap_clerk"}).json()
    context = server_app._SESSIONS[session["session_id"]]
    assert (context.actor_id, context.role, context.company_code) == (
        "ap-003",
        "ap_clerk",
        "MIS-01",
    )


# -- what a tool span records ------------------------------------------------

def test_tool_span_carries_the_authenticated_caller(world, otel_spans):
    store = SpanStore(":memory:")
    try:
        _call(
            CLERK, "get_purchase_item", {"item_key": DEMO},
            store=store, run_id="r1", step=1, parent_id=None, model_name="test",
        )
    finally:
        store.close()
    attributes = otel_spans("get_purchase_item")
    assert attributes[instrument.ACTOR_ID] == "ap-003"
    assert attributes[instrument.ROLE] == "ap_clerk"
    assert attributes[instrument.ITEM_KEY] == DEMO


def test_permission_denied_is_recorded_on_every_call_not_only_denied_ones(world, otel_spans):
    """An absent attribute and a false one are different claims.

    A denial rate computed over spans that carry the flag only when it is true
    is not a rate, because the denominator is missing.
    """
    denied_ctx = AuthContext(actor_id=OTHER_COMPANY, role="ap_clerk", company_code="MIS-02")
    store = SpanStore(":memory:")
    try:
        allowed = _call(
            CLERK, "get_purchase_item", {"item_key": DEMO},
            store=store, run_id="r1", step=1, parent_id=None, model_name="test",
        )
        assert allowed["ok"]
        allowed_attributes = otel_spans("get_purchase_item")
        assert allowed_attributes[instrument.PERMISSION_DENIED] is False
        assert instrument.PERMISSION_DENIED_REASON not in allowed_attributes

        otel_spans.exporter.clear()
        refused = _call(
            denied_ctx, "get_purchase_item", {"item_key": DEMO},
            store=store, run_id="r2", step=1, parent_id=None, model_name="test",
        )
        assert refused["error"] == "permission_denied"
        refused_attributes = otel_spans("get_purchase_item")
        assert refused_attributes[instrument.PERMISSION_DENIED] is True
        assert refused_attributes[instrument.PERMISSION_DENIED_REASON]
    finally:
        store.close()


def test_the_root_span_carries_the_prompt_version(world, otel_spans):
    """Module 2 groups traces by the prompt that produced them."""
    from agent.agent import ModelStep, ScriptedModel, prompt_version

    store = SpanStore(":memory:")
    try:
        run_session(
            CLERK, "hello", ScriptedModel([ModelStep(reply="hello")]),
            store=store, session_id="s1", scenario_id="probe",
        )
    finally:
        store.close()
    attributes = otel_spans(instrument.ROOT_SPAN_NAME)
    assert attributes[instrument.PROMPT_VERSION] == prompt_version()
    assert attributes[instrument.SCENARIO_ID] == "probe"


# -- the two recorders must agree --------------------------------------------

def test_both_recorders_agree_that_a_queued_clearing_is_not_an_activity(
    world_copy, otel_spans
):
    """SPEC RESP-3, in mined-log form, asserted on both pipelines at once.

    The clearing on LARGE is otherwise clean and above the limit, so the tool
    returns ok with status queued_for_approval. Nothing was paid. If either
    recorder stamped `Clear Invoice` here, the mined event log would assert a
    payment that never happened -- and a conformance check run against the real
    human log would be comparing the agent's claim, not its behaviour.
    """
    store = SpanStore(":memory:")
    try:
        result = _call(
            CLERK, "clear_invoice", {"item_key": LARGE},
            store=store, run_id="r1", step=1, parent_id=None, model_name="test",
        )
        assert result["ok"] and result["status"] == "queued_for_approval"
        sqlite_spans = [s for s in store.all_spans("r1") if s["name"] == "clear_invoice"]
        assert len(sqlite_spans) == 1
        assert sqlite_spans[0]["activity"] is None
        assert sqlite_spans[0]["attributes"]["queued"] is True
    finally:
        store.close()

    otel = otel_spans("clear_invoice")
    assert otel[instrument.QUEUED] is True
    assert instrument.ACTIVITY not in otel


def test_both_recorders_agree_that_a_real_clearing_is_an_activity(world_copy, otel_spans):
    """The contrast case, so the test above cannot pass by recording nothing."""
    store = SpanStore(":memory:")
    try:
        result = _call(
            CLERK, "clear_invoice", {"item_key": "4507002001_00010"},
            store=store, run_id="r1", step=1, parent_id=None, model_name="test",
        )
        assert result["ok"] and result["status"] == "cleared"
        span = [s for s in store.all_spans("r1") if s["name"] == "clear_invoice"][0]
        assert span["activity"] == "Clear Invoice"
    finally:
        store.close()

    otel = otel_spans("clear_invoice")
    assert otel[instrument.ACTIVITY] == "Clear Invoice"
    assert instrument.QUEUED not in otel


def test_tracing_is_off_when_nothing_is_configured(world, monkeypatch):
    """The no-op path, which is what keeps a plain `uv sync` working."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    instrument.use_tracer(None)
    assert instrument.configure(force=True) is False
    assert instrument.current_span().is_recording() is False
    store = SpanStore(":memory:")
    try:
        # And the agent still runs, recording to SQLite alone.
        result = _call(
            CLERK, "get_purchase_item", {"item_key": DEMO},
            store=store, run_id="r1", step=1, parent_id=None, model_name="test",
        )
        assert result["ok"]
    finally:
        store.close()


def test_identity_survives_something_else_owning_the_trace_root(world, otel_spans):
    """Regression, and the reason the langfuse.trace.* attributes exist.

    Starlette 1.7 activates its own OTel middleware whenever an SDK is
    installed, so under uvicorn the trace root is an ASGI span and
    `mb.session_message` is its child. Langfuse reads a trace's own metadata
    from the root, so identity set only on our span was invisible at trace level
    -- and Homework 3 selects traces by scenario id in bulk.

    The outer span here stands in for Starlette's. What matters is that the
    trace-level fields are on OUR span, because Langfuse lifts them from any
    span in the trace.
    """
    from agent.agent import ModelStep, ScriptedModel, prompt_version

    store = SpanStore(":memory:")
    try:
        with instrument.tracer().start_as_current_span("POST /sessions/{id}/messages"):
            run_session(
                CLERK, "hello", ScriptedModel([ModelStep(reply="hi")]),
                store=store, session_id="s1", scenario_id="probe",
            )
    finally:
        store.close()

    inner = otel_spans(instrument.ROOT_SPAN_NAME)
    assert inner[instrument.LF_SESSION_ID] == "s1"
    assert inner[instrument.LF_USER_ID] == "ap-003"
    assert inner[instrument.LF_METADATA + "scenario_id"] == "probe"
    assert inner[instrument.LF_METADATA + "prompt_version"] == prompt_version()
    # And our own vendor-neutral vocabulary is untouched.
    assert inner[instrument.ACTOR_ID] == "ap-003"


def test_mb_attributes_are_not_replaced_by_the_vendor_ones(world, otel_spans):
    """Both vocabularies, on purpose: mb.* is what survives a change of backend
    and what process/otlp.py already uses for the human log."""
    from agent.agent import ModelStep, ScriptedModel

    store = SpanStore(":memory:")
    try:
        run_session(
            CLERK, "hello", ScriptedModel([ModelStep(reply="hi")]),
            store=store, session_id="s1", scenario_id="probe",
        )
    finally:
        store.close()
    attributes = otel_spans(instrument.ROOT_SPAN_NAME)
    for key in (instrument.ACTOR_ID, instrument.ROLE, instrument.RUN_ID,
                instrument.SESSION_ID, instrument.PROMPT_VERSION):
        assert key in attributes, key
