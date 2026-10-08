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
