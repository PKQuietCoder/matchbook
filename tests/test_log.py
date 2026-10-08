"""The columnar log: slicing, ordering, and the tie-break audit."""

from __future__ import annotations

from datetime import datetime, timezone

from process.log import DAY, EventLogBuilder


def test_cases_are_slices_not_copies(tiny_log):
    trace = tiny_log.trace("c1")
    assert trace.activities == (
        "Create Purchase Order Item",
        "Record Goods Receipt",
        "Record Invoice Receipt",
        "Clear Invoice",
    )
    assert len(trace) == 4
    assert trace.end - trace.start == 4
    # Every case's slice must tile the event columns exactly, with no gaps.
    assert sum(len(t) for t in tiny_log.traces()) == tiny_log.event_count
    assert tiny_log.case_offsets[-1] == tiny_log.event_count


def test_cases_are_sorted_and_addressable_by_id(tiny_log):
    assert tiny_log.case_ids == sorted(tiny_log.case_ids)
    assert tiny_log.trace("c3").case_id == "c3"


def test_activity_rank_breaks_ties_and_records_it():
    builder = EventLogBuilder("t", activity_rank={"early": 1, "late": 2})
    same = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    builder.add(case_id="c", activity="late", timestamp=same)
    builder.add(case_id="c", activity="early", timestamp=same)
    log = builder.build()
    # The declared rank wins over arrival order...
    assert log.trace("c").activities == ("early", "late")
    # ...and the fact that we chose it is on the record.
    assert len(log.tie_broken) == 1
    assert log.tie_broken_cases() == ["c"]


def test_untied_events_are_not_flagged():
    builder = EventLogBuilder("t")
    builder.add(case_id="c", activity="a", timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc))
    builder.add(case_id="c", activity="b", timestamp=datetime(2026, 5, 2, tzinfo=timezone.utc))
    log = builder.build()
    assert log.tie_broken == set()


def test_select_preserves_order_and_attributes(tiny_log):
    subset = tiny_log.select(["c4", "c1"])
    assert subset.case_ids == ["c1", "c4"]
    assert subset.trace("c1").activities == tiny_log.trace("c1").activities
    assert subset.trace("c4").attributes == tiny_log.trace("c4").attributes


def test_variants_group_identical_sequences(tiny_log):
    counts = tiny_log.variants()
    # c1 and c3 share a sequence; they differ only in resources.
    assert max(counts.values()) == 2


def test_day_precision_is_carried(tiny_log):
    builder = EventLogBuilder("t")
    builder.add(
        case_id="c",
        activity="a",
        timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        precision=DAY,
    )
    log = builder.build()
    assert log.event(0)["precision"] == DAY


def test_case_offsets_invariant_holds_including_when_empty(tiny_log):
    """len(case_offsets) == len(case_ids) + 1, always.

    An empty log carried [0, 0] rather than [0], which is harmless only for as
    long as nothing reads the last offset of an empty log. Invariants that are
    true "except in the empty case" are the ones that break later.
    """
    assert len(tiny_log.case_offsets) == tiny_log.case_count + 1

    empty = tiny_log.select([])
    assert empty.case_count == 0
    assert empty.event_count == 0
    assert len(empty.case_offsets) == 1
    assert list(empty.traces()) == []


def test_every_analysis_tolerates_an_empty_log(tiny_log):
    """A filter that matches nothing is routine, not exceptional."""
    from process import config, dfg, rules, variants, viz

    empty = tiny_log.select([])
    assert dfg.build(empty).edges == {}
    assert variants.summary(empty)["variants"] == 0
    assert rules.report(empty, config.load_facts())["total_violations"] == 0
    assert viz.dfg_to_dot(dfg.build(empty))  # renders, rather than raising
