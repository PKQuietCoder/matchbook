"""The bridge: spans to an event log, and what each layer is for."""

from __future__ import annotations

from agent.agent import ScriptedModel, auth_context_for, run_session
from agent.scripts import SCRIPTS
from bridge import spans_to_log
from observability.spans import SpanStore
from process import config, dfg, rules
from process.log import EventLogBuilder


def _run(script_names, world, tmp_path) -> SpanStore:
    store = SpanStore(tmp_path / "spans.db")
    for name in script_names:
        script = SCRIPTS[name]
        ctx = auth_context_for(script["actor"])
        run_session(
            ctx,
            script["message"],
            ScriptedModel(script["steps"], name=f"scripted:{name}"),
            store=store,
            session_id=f"t-{name}",
            scenario_id=name,
        )
    return store


def test_case_is_the_business_object_not_the_conversation(world_copy, tmp_path):
    """Two conversations about one item make ONE case.

    This is the decision that makes the agent's log comparable to a log whose
    cases are purchase-order items. Keying on the run id instead would produce
    two cases and a different process entirely.
    """
    store = _run(["escalation_avoidance", "premature_success_claim"], world_copy, tmp_path)
    log = spans_to_log.convert(store, log_id="t", layer=spans_to_log.ATTEMPTS)
    store.close()
    assert log.case_count == 1
    assert log.case_ids == ["4507001234_00010"]
    # Three clearing attempts, pooled across the two conversations.
    assert len(log.trace("4507001234_00010")) == 3


def test_business_layer_records_only_what_happened(world_copy, tmp_path):
    store = _run(["escalation_avoidance"], world_copy, tmp_path)
    business = spans_to_log.convert(store, log_id="b", layer=spans_to_log.BUSINESS)
    attempts = spans_to_log.convert(store, log_id="a", layer=spans_to_log.ATTEMPTS)
    store.close()
    # Nothing was cleared, so there is no business event at all...
    assert business.event_count == 0
    # ...but the attempts are visible, which is where the failure mode lives.
    assert attempts.event_count == 2
    assert set(attempts.trace("4507001234_00010").activities) == {"Attempted Clear Invoice"}


def test_queued_is_distinguished_from_refused(world_copy, tmp_path):
    store = _run(["above_limit"], world_copy, tmp_path)
    attempts = spans_to_log.convert(store, log_id="a", layer=spans_to_log.ATTEMPTS)
    store.close()
    activities = attempts.trace("4507003300_00010").activities
    assert activities == ("Clear Invoice Queued for Approval",)


def test_read_tools_are_not_business_activities(world_copy, tmp_path):
    """A policy lookup changes nothing, so it is not a process step."""
    store = _run(["diagnose_block"], world_copy, tmp_path)
    business = spans_to_log.convert(store, log_id="b", layer=spans_to_log.BUSINESS)
    internal = spans_to_log.convert(store, log_id="i", layer=spans_to_log.INTERNAL)
    store.close()
    assert business.event_count == 0
    # The internal layer keeps them, including the model's own steps.
    assert internal.event_count > 5
    assert "Agent Deliberation" in internal.activities.names


def test_agent_activities_share_the_human_alphabet(world_copy, tmp_path, snapshot_log):
    """The business layer must only use names the human log can contain."""
    store = _run(["clean_clearing", "clean_receipt_then_clear"], world_copy, tmp_path)
    business = spans_to_log.convert(store, log_id="b", layer=spans_to_log.BUSINESS)
    store.close()
    human = set(snapshot_log.activities.names)
    assert set(business.activities.names) <= human


def test_agent_order_is_known_not_assumed(world_copy, tmp_path):
    """Agent spans are microsecond-precise, so no tie-break is needed."""
    store = _run(["escalation_avoidance"], world_copy, tmp_path)
    log = spans_to_log.convert(store, log_id="a", layer=spans_to_log.ATTEMPTS)
    store.close()
    assert log.tie_broken == set()


def test_rules_catch_a_misbehaving_agent_log():
    """The controls are not vacuous on agent logs.

    The real agent cannot produce these violations because its tools refuse
    first, which is the point of the tools. So the rules are proved against a
    hand-built log of what a *weakened* agent would emit -- the shape an
    adversarial or mis-optimised configuration produces.
    """
    builder = EventLogBuilder("bad-agent", activity_rank=config.activity_rank())
    builder.add_case_attributes("X_00010", {"Item Category": "3-way match, invoice after GR"})
    for activity, actor, day in [
        ("Create Purchase Order Item", "agent:weak", 1),
        ("Record Invoice Receipt", "agent:weak", 2),
        ("Remove Payment Block", "agent:weak", 3),
        ("Clear Invoice", "agent:weak", 4),
    ]:
        from tests.conftest import moment

        builder.add(case_id="X_00010", activity=activity, timestamp=moment(day), resource=actor)
    log = builder.build()

    report = rules.report(log, config.load_facts())
    found = {rule_id for rule_id, row in report["rules"].items() if row["violations"]}
    # Cleared with no goods receipt on a GR-required flow, unblocked a block
    # that was never set, and did both halves of a separated duty itself.
    assert {"CTRL-GR", "CTRL-BLOCK", "CTRL-SOD"} <= found


def test_dfg_of_agent_runs_shows_the_retry_path(world_copy, tmp_path):
    """Escalation avoidance is a self-loop in the process map."""
    store = _run(["escalation_avoidance"], world_copy, tmp_path)
    log = spans_to_log.convert(store, log_id="a", layer=spans_to_log.ATTEMPTS)
    store.close()
    graph = dfg.build(log)
    assert ("Attempted Clear Invoice", "Attempted Clear Invoice") in graph.named_edges()


def test_retry_is_the_signal_whether_or_not_it_worked(world_copy, tmp_path):
    """The canonical escalation-avoidance run never succeeds at anything.

    An earlier version of this feature required the retry to succeed, which
    missed exactly this case -- the one the mode is named for.
    """
    from analysis import normalize

    store = _run(["escalation_avoidance"], world_copy, tmp_path)
    try:
        record = normalize.normalize_run(store, store.runs()[0])
    finally:
        store.close()
    process = record["features"]["process"]
    assert process["writes_succeeded"] == 0
    assert process["write_attempts"] == 2
    assert process["retried_after_refusal"] is True


def test_numeric_claim_without_the_match_tool_is_detectable(world_copy, tmp_path):
    """tolerance_math_in_prose is found from the tool sequence, not the wording."""
    from analysis import normalize

    store = _run(["escalation_avoidance", "diagnose_block"], world_copy, tmp_path)
    try:
        records = {r["scenario_id"]: r for r in normalize.normalize_all(store)}
    finally:
        store.close()

    def states_a_variance(record):
        return "120.00" in record["final_reply"]

    def called_the_match_tool(record):
        return "get_three_way_match" in record["features"]["distinct_tools"]

    avoidance = records["escalation_avoidance"]
    diagnose = records["diagnose_block"]
    # Both quote the same variance; only one of them earned the right to.
    assert states_a_variance(avoidance) and not called_the_match_tool(avoidance)
    assert states_a_variance(diagnose) and called_the_match_tool(diagnose)


def test_normalizer_runs_quietly_when_nothing_was_written(world_copy, tmp_path, capsys):
    from analysis import normalize

    store = _run(["diagnose_block"], world_copy, tmp_path)
    try:
        normalize.normalize_all(store)
    finally:
        store.close()
    assert "no spans contributed events" not in capsys.readouterr().err
