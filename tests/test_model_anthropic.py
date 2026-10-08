"""The live adapter's translation layer, exercised without a key or a budget.

The risky part of `agent/model_anthropic.py` is not the HTTP call -- it is that
Matchbook's loop and the Messages API disagree about history. The loop never
appends the assistant turn that requested a tool, and it appends one message
per tool result; the API needs the assistant turn present and every result for
one turn grouped into a single user message. These tests pin that translation
against a stub client, so a regression shows up here rather than as a model
that mysteriously stops calling tools in parallel.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.agent import ModelStep, ToolCall, run_session
from agent.model_anthropic import AnthropicModel, TOOL_SCHEMAS, _usage_of
from bridge import cost
from observability.spans import SpanStore


# -- stub client -------------------------------------------------------------

def block(**fields):
    return SimpleNamespace(**fields)


def tool_use(identifier: str, name: str, arguments: dict):
    return block(type="tool_use", id=identifier, name=name, input=arguments)


def text(body: str):
    return block(type="text", text=body)


def thinking():
    """Sonnet 5 runs adaptive thinking, and its blocks must survive the round trip."""
    return block(type="thinking", thinking="")


def response(content, *, stop_reason="end_turn", usage=None, stop_details=None):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage
        or SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class StubClient:
    """Records every request and replays queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("the adapter made more calls than the stub expected")
        return self._responses.pop(0)


# -- translation -------------------------------------------------------------

def test_parallel_tool_results_become_one_user_message():
    """Two results for one assistant turn must arrive as a single user message.

    Splitting them is accepted by the API but teaches the model to stop
    requesting tools in parallel, which silently changes the process.
    """
    client = StubClient(
        [
            response(
                [
                    thinking(),
                    tool_use("tu_1", "get_purchase_item", {"item_key": "4507001234_00010"}),
                    tool_use("tu_2", "get_three_way_match", {"item_key": "4507001234_00010"}),
                ],
                stop_reason="tool_use",
            ),
            response([text("Both checks are clean.")]),
        ]
    )
    model = AnthropicModel(client=client)

    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "check 4507001234_00010"},
    ]
    first = model.step(messages)
    assert [call.name for call in first.tool_calls] == [
        "get_purchase_item",
        "get_three_way_match",
    ]

    # The loop appends one message per result, in dispatch order.
    messages.append({"role": "tool", "name": "get_purchase_item", "content": '{"ok": true}'})
    messages.append({"role": "tool", "name": "get_three_way_match", "content": '{"ok": true}'})
    second = model.step(messages)
    assert second.reply == "Both checks are clean."

    sent = client.requests[1]["messages"]
    # user, assistant(tool_use), user(tool_result x2)
    assert [entry["role"] for entry in sent] == ["user", "assistant", "user"]
    results = sent[2]["content"]
    assert len(results) == 2, "both results must share one user message"
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]


def test_assistant_turn_is_echoed_back_verbatim():
    """The loop never appends the tool-requesting assistant turn; we must.

    Keeping the blocks verbatim is also what preserves thinking blocks, which
    have to be replayed unchanged while continuing on the same model.
    """
    first_content = [thinking(), tool_use("tu_1", "get_policy", {"policy_id": "mb-goods-receipt"})]
    client = StubClient(
        [
            response(first_content, stop_reason="tool_use"),
            response([text("Cited.")]),
        ]
    )
    model = AnthropicModel(client=client)
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "cite it"}]
    model.step(messages)
    messages.append({"role": "tool", "name": "get_policy", "content": "{}"})
    model.step(messages)

    assistant = client.requests[1]["messages"][1]
    assert assistant["role"] == "assistant"
    # The list may be copied; the blocks inside it must be the very objects the
    # API returned, or a thinking block would be rebuilt and so invalidated.
    assert all(
        sent is original for sent, original in zip(assistant["content"], first_content)
    ), "blocks must be passed through, not rebuilt"
    assert len(assistant["content"]) == len(first_content)
    assert any(getattr(b, "type", None) == "thinking" for b in assistant["content"])


def test_system_prompt_is_hoisted_and_cached():
    client = StubClient([response([text("hi")])])
    model = AnthropicModel(client=client)
    model.step([{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "hi"}])

    request = client.requests[0]
    assert request["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}
    ]
    # The system prompt must not also appear as a message.
    assert [entry["role"] for entry in request["messages"]] == ["user"]


def test_caching_can_be_turned_off():
    client = StubClient([response([text("hi")])])
    model = AnthropicModel(client=client, cache_system=False)
    model.step([{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}])
    assert "cache_control" not in client.requests[0]["system"][0]


def test_request_omits_parameters_this_model_rejects():
    """Sonnet 5 returns 400 for temperature, top_p, top_k and budget_tokens."""
    client = StubClient([response([text("hi")])])
    AnthropicModel(client=client).step(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
    )
    request = client.requests[0]
    for rejected in ("temperature", "top_p", "top_k", "thinking"):
        assert rejected not in request
    assert request["output_config"] == {"effort": "medium"}


def test_refusal_becomes_a_reply_not_a_crash():
    """A declined request is HTTP 200 with no usable content."""
    client = StubClient(
        [
            response(
                [],
                stop_reason="refusal",
                stop_details=SimpleNamespace(type="refusal", category="cyber", explanation=""),
            )
        ]
    )
    step = AnthropicModel(client=client).step(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}]
    )
    assert step.tool_calls == ()
    assert "declined" in (step.reply or "")
    assert "cyber" in (step.reply or "")
    assert step.usage is not None and step.usage.input_tokens == 100


def test_max_tokens_without_text_is_reported():
    client = StubClient([response([thinking()], stop_reason="max_tokens")])
    step = AnthropicModel(client=client).step(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}]
    )
    assert "output limit" in (step.reply or "")


def test_tool_schemas_are_strict_and_closed():
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    from agent import tools as tool_module

    assert names == set(tool_module.TOOLS), "every tool must be declared, and no others"
    for schema in TOOL_SCHEMAS:
        assert schema["strict"] is True
        body = schema["input_schema"]
        assert body["additionalProperties"] is False
        # Strict mode wants a closed schema, so optional arguments are listed
        # too and "" stands for omitted.
        assert set(body["required"]) == set(body["properties"])


# -- usage -------------------------------------------------------------------

def test_usage_is_read_defensively():
    assert _usage_of(SimpleNamespace()).input_tokens == 0
    assert _usage_of(SimpleNamespace(usage=SimpleNamespace())).output_tokens == 0
    full = _usage_of(
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=7,
                output_tokens=3,
                cache_read_input_tokens=11,
                cache_creation_input_tokens=5,
            )
        )
    )
    assert (full.input_tokens, full.output_tokens) == (7, 3)
    assert (full.cache_read_tokens, full.cache_write_tokens) == (11, 5)


def test_tokens_land_on_model_spans_and_not_on_tool_spans(world, tmp_path):
    """The span store is where the cost report reads from, so check it there."""
    from agent.agent import Usage, auth_context_for

    class TokenModel:
        """A scripted model that also reports usage, so no key is needed."""

        name = "claude-sonnet-5"

        def __init__(self):
            self._steps = [
                ModelStep(
                    tool_calls=(ToolCall("get_purchase_item", {"item_key": "4507001234_00010"}),),
                    usage=Usage(input_tokens=1_000, output_tokens=100, cache_read_tokens=40),
                ),
                ModelStep(reply="done", usage=Usage(input_tokens=1_500, output_tokens=60)),
            ]
            self._index = 0

        def step(self, messages):
            current = self._steps[self._index]
            self._index += 1
            return current

    store = SpanStore(tmp_path / "spans.db")
    try:
        result = run_session(
            auth_context_for("ap-003"),
            "check it",
            TokenModel(),
            store=store,
            session_id="t",
        )
        spans = store.all_spans(result.run_id)
    finally:
        store.close()

    model_spans = [s for s in spans if s["kind"] == "model"]
    tool_spans = [s for s in spans if s["kind"] == "tool"]
    assert [s["input_tokens"] for s in model_spans] == [1_000, 1_500]
    assert [s["cache_read_tokens"] for s in model_spans] == [40, 0]
    assert all(s["input_tokens"] is None for s in tool_spans), (
        "a tool call spends no tokens of its own"
    )

    totals = cost.run_totals(tmp_path / "spans.db", result.run_id)
    assert totals["input_tokens"] == 2_500
    assert totals["output_tokens"] == 160
    assert totals["cache_read_tokens"] == 40
    # 2500 input @ $2 + 40 cached @ $0.20 + 160 output @ $10, per million.
    expected = (2_500 * 2.0 + 40 * 0.2 + 160 * 10.0) / 1_000_000
    assert totals["usd"] == pytest.approx(expected)


def test_scripted_runs_are_unpriced_not_free():
    assert cost.price_of("scripted:clean_receipt_then_clear") is None
    assert cost.usd("scripted:x", {"input_tokens": 10}) is None
    assert cost.price_of("anthropic/claude-sonnet-5") == (2.00, 10.00)


def test_migration_adds_token_columns_to_an_older_store(tmp_path):
    """A span store written before the token columns existed must still open.

    `CREATE TABLE IF NOT EXISTS` would leave it in its old shape and every
    token query would fail on it, so opening is the migration.
    """
    import sqlite3

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE spans (span_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,"
        " parent_id TEXT, name TEXT NOT NULL, kind TEXT NOT NULL,"
        " step INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, ended_at TEXT,"
        " ok INTEGER, error TEXT, item_key TEXT, activity TEXT, actor TEXT,"
        " value_cents INTEGER, attributes TEXT NOT NULL DEFAULT '{}');"
    )
    legacy.execute(
        "INSERT INTO spans (span_id, run_id, name, kind, started_at)"
        " VALUES ('s1','r1','model.step.1','model','2026-01-01T00:00:00')"
    )
    legacy.commit()
    legacy.close()

    store = SpanStore(path)
    try:
        rows = store.all_spans("r1")
    finally:
        store.close()
    assert rows[0]["input_tokens"] is None, "the column exists and is simply unknown"
    assert cost.run_totals(path, "r1") == {}, "an unmeasured run is not a free run"


# -- attribution -------------------------------------------------------------

def _span(store, run_id, *, kind, step, **fields):
    with store.span(run_id=run_id, name=f"{kind}.{step}", kind=kind, step=step, **fields):
        pass


def test_tokens_are_attributed_to_the_activity_their_step_requested(tmp_path):
    """The (run_id, step) join is what connects a price to a business activity.

    Step 1 looks things up, step 2 records a receipt, step 3 is the final reply.
    Each bucket must get its own step's tokens and nobody else's.
    """
    store = SpanStore(tmp_path / "spans.db")
    try:
        run = store.start_run(
            session_id="s", actor_id="buy-004", role="buyer",
            model="claude-sonnet-5", prompt_version="v1",
        )
        _span(store, run, kind="model", step=1, input_tokens=1_000, output_tokens=10)
        _span(store, run, kind="tool", step=1, item_key="PO_1")  # a lookup: no activity
        _span(store, run, kind="model", step=2, input_tokens=2_000, output_tokens=20)
        _span(store, run, kind="tool", step=2, item_key="PO_1", activity="Record Goods Receipt")
        _span(store, run, kind="model", step=3, input_tokens=3_000, output_tokens=30)
    finally:
        store.close()

    report = cost.by_activity(tmp_path / "spans.db")
    rows = report["rows"]
    assert rows["(lookup only)"]["input_tokens"] == 1_000
    assert rows["Record Goods Receipt"]["input_tokens"] == 2_000
    assert rows["(final reply)"]["input_tokens"] == 3_000
    assert report["split_steps"] == 0, "no step did more than one thing"

    # Deliberation and lookup are 4,000 of the 6,000 input tokens here. Keeping
    # them in their own buckets rather than folding them into the activity is
    # the point: it is the number that tells you where the money actually goes.
    assert sum(r["input_tokens"] for r in rows.values()) == 6_000


def test_a_step_doing_two_things_splits_and_says_so(tmp_path):
    store = SpanStore(tmp_path / "spans.db")
    try:
        run = store.start_run(
            session_id="s", actor_id="buy-004", role="buyer",
            model="claude-sonnet-5", prompt_version="v1",
        )
        _span(store, run, kind="model", step=1, input_tokens=1_000, output_tokens=100)
        _span(store, run, kind="tool", step=1, item_key="PO_1", activity="Record Goods Receipt")
        _span(store, run, kind="tool", step=1, item_key="PO_2", activity="Clear Invoice")
    finally:
        store.close()

    report = cost.by_activity(tmp_path / "spans.db")
    assert report["split_steps"] == 1, "the assumption must be counted, not hidden"
    assert report["rows"]["Record Goods Receipt"]["input_tokens"] == 500
    assert report["rows"]["Clear Invoice"]["input_tokens"] == 500

    # Two business objects in one step split by case as well.
    cases = cost.by_case(tmp_path / "spans.db")
    assert cases["rows"]["PO_1"]["input_tokens"] == 500
    assert cases["rows"]["PO_2"]["input_tokens"] == 500
    assert cases["split_steps"] == 1


def test_a_reply_only_step_joins_the_single_case_it_touched(tmp_path):
    """One-item runs can attribute the final reply; ambiguous ones must not."""
    store = SpanStore(tmp_path / "spans.db")
    try:
        single = store.start_run(
            session_id="s", actor_id="ap-003", role="ap_clerk",
            model="claude-sonnet-5", prompt_version="v1",
        )
        _span(store, single, kind="model", step=1, input_tokens=100, output_tokens=1)
        _span(store, single, kind="tool", step=1, item_key="PO_1", activity="Clear Invoice")
        _span(store, single, kind="model", step=2, input_tokens=900, output_tokens=9)
    finally:
        store.close()
    rows = cost.by_case(tmp_path / "spans.db")["rows"]
    assert rows["PO_1"]["input_tokens"] == 1_000
    assert "(unattributed)" not in rows

    store = SpanStore(tmp_path / "two.db")
    try:
        both = store.start_run(
            session_id="s", actor_id="ap-003", role="ap_clerk",
            model="claude-sonnet-5", prompt_version="v1",
        )
        _span(store, both, kind="model", step=1, input_tokens=100, output_tokens=1)
        _span(store, both, kind="tool", step=1, item_key="PO_1", activity="Clear Invoice")
        _span(store, both, kind="tool", step=1, item_key="PO_2", activity="Clear Invoice")
        _span(store, both, kind="model", step=2, input_tokens=900, output_tokens=9)
    finally:
        store.close()
    rows = cost.by_case(tmp_path / "two.db")["rows"]
    assert rows["(unattributed)"]["input_tokens"] == 900, (
        "a reply covering two cases cannot be assigned to either"
    )


def test_a_policy_only_step_is_a_lookup_not_the_final_reply(tmp_path):
    """`get_policy` carries neither an item_key nor an activity.

    Inferring "this step called a tool" from those two fields would price a
    policy citation as if it were the closing reply, which is the difference
    between "citing policy is expensive" and "answering is expensive".
    """
    store = SpanStore(tmp_path / "spans.db")
    try:
        run = store.start_run(
            session_id="s", actor_id="ap-003", role="ap_clerk",
            model="claude-sonnet-5", prompt_version="v1",
        )
        _span(store, run, kind="model", step=1, input_tokens=800, output_tokens=8)
        _span(store, run, kind="tool", step=1)  # get_policy: no item_key, no activity
        _span(store, run, kind="model", step=2, input_tokens=200, output_tokens=2)
    finally:
        store.close()

    rows = cost.by_activity(tmp_path / "spans.db")["rows"]
    assert rows["(lookup only)"]["input_tokens"] == 800
    assert rows["(final reply)"]["input_tokens"] == 200


def test_every_view_of_one_run_reports_the_same_total(tmp_path):
    """`--by run`, `--by case` and `--by activity` must not disagree.

    They did, in the last digit, when each row was rounded before being
    formatted. A cost report whose own views contradict each other is worse
    than no cost report, so rounding happens once, at display time.
    """
    store = SpanStore(tmp_path / "spans.db")
    try:
        run = store.start_run(
            session_id="s", actor_id="buy-004", role="buyer",
            model="claude-sonnet-5", prompt_version="v1",
        )
        # The shape of a real session: a cache write on the first call, cache
        # reads afterwards. These are the figures measured on 2026-10-08.
        _span(store, run, kind="model", step=1, input_tokens=112, output_tokens=106,
              cache_write_tokens=1_867)
        _span(store, run, kind="tool", step=1, item_key="4507001234_00020")
        _span(store, run, kind="model", step=2, input_tokens=509, output_tokens=290,
              cache_read_tokens=1_867)
        _span(store, run, kind="tool", step=2, item_key="4507001234_00020",
              activity="Record Goods Receipt")
        _span(store, run, kind="model", step=3, input_tokens=857, output_tokens=92,
              cache_read_tokens=1_867)
    finally:
        store.close()

    path = tmp_path / "spans.db"
    total = cost.run_totals(path, run)["usd"]
    by_case = sum(row["usd"] for row in cost.by_case(path)["rows"].values())
    by_activity = sum(row["usd"] for row in cost.by_activity(path)["rows"].values())

    assert total == pytest.approx(by_case)
    assert total == pytest.approx(by_activity)
    # 1478 input @ $2 + 3734 cached @ $0.20 + 1867 written @ $2.50 + 488 out @ $10.
    assert total == pytest.approx((1_478 * 2 + 3_734 * 0.2 + 1_867 * 2.5 + 488 * 10) / 1e6)
