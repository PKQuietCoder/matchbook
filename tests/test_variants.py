"""Variant, coverage and rework measurement."""

from __future__ import annotations

from process import variants


def test_coverage_curve_is_monotonic(helpdesk_log):
    curve = variants.coverage_curve(helpdesk_log)
    counts = [curve[point] for point in sorted(curve)]
    assert counts == sorted(counts)
    assert curve[0.5] == 1  # one variant covers 57% of helpdesk cases


def test_rework_counts_repeats_beyond_the_first(tiny_log):
    assert variants.rework(tiny_log.trace("c1")) == {}
    assert 0.0 <= variants.rework_ratio(tiny_log) < 0.1


def test_collapse_repeats_removes_stutter():
    assert variants.collapse_repeats((1, 1, 2, 2, 2, 3)) == (1, 2, 3)


def test_cases_covering_is_reproducible(snapshot_log):
    first = variants.cases_covering(snapshot_log, 0.5)
    second = variants.cases_covering(snapshot_log, 0.5)
    assert first == second
    assert 0 < len(first) <= snapshot_log.case_count


def test_real_log_has_a_long_tail(snapshot_log):
    summary = variants.summary(snapshot_log)
    # The shape that forces discovery onto a coverage sublog.
    assert summary["variants"] > 100
    assert summary["coverage"][0.5] < summary["coverage"][0.95]


def test_order_sensitivity_measures_a_real_effect(snapshot_log):
    """The declared tie-break changes the process, and the figure must not
    silently regress to a reassuring zero.

    Two earlier versions of this measurement did exactly that: one took its
    baseline from a log already loaded out of the store, the other from the
    ranked log it had just built. Both compared the ranked order against
    itself. The baseline has to be an unranked read of the source, which is why
    the committed snapshot preserves the source file's event order.
    """
    from process import config

    rows = variants.events_of(snapshot_log)
    measured = variants.order_sensitivity(rows, activity_rank=config.activity_rank())
    assert measured["tie_broken_events"] > 2000
    # Most ties are between two events of the same activity and cannot matter.
    assert 0 < measured["consequential_ties"] < measured["tie_broken_events"]
    # The ranking collapses variants, so it is not cosmetic.
    assert measured["variants_collapsed"] > 0
    assert measured["cases_whose_sequence_changed"] > 0


def test_no_ranking_means_no_change(snapshot_log):
    measured = variants.order_sensitivity(variants.events_of(snapshot_log), activity_rank=None)
    assert measured["variants_collapsed"] == 0


def test_same_activity_ties_are_never_consequential():
    """Reordering two identical labels cannot change a sequence."""
    from process.log import EventLogBuilder
    from tests.conftest import moment

    builder = EventLogBuilder("t")
    for _ in range(3):
        builder.add(case_id="c", activity="A", timestamp=moment(1, 9, 0))
    rows = variants.events_of(builder.build())
    measured = variants.order_sensitivity(rows, activity_rank={"A": 1})
    assert measured["tie_broken_events"] == 2
    assert measured["consequential_ties"] == 0
