"""The directly-follows graph, against a log whose shape is known exactly."""

from __future__ import annotations

from process import dfg


def test_edges_counts_and_endpoints(tiny_log):
    graph = dfg.build(tiny_log)
    named = graph.named_edges()
    assert named[("Create Purchase Order Item", "Record Goods Receipt")].count == 3  # c1, c3, c4
    # c1, c2, c3, c5 -- c2 clears before its goods receipt, so it has this edge
    # too; c4 interposes a Remove Payment Block and so does not.
    assert named[("Record Invoice Receipt", "Clear Invoice")].count == 4
    assert graph.case_count == 5
    create = tiny_log.activities.id_of("Create Purchase Order Item")
    assert graph.starts[create] == 5
    assert graph.ends[tiny_log.activities.id_of("Clear Invoice")] == 4  # c2 ends on the GR


def test_durations_are_measured_per_edge(tiny_log):
    graph = dfg.build(tiny_log)
    stats = graph.named_edges()[("Record Invoice Receipt", "Clear Invoice")]
    assert stats.min_seconds == 86400  # one day in the fixture
    assert stats.mean_seconds == 86400
    # Quantiles come from a log-spaced bucket histogram, so they carry a stated
    # tolerance rather than being exact.
    assert abs(stats.median_seconds - 86400) <= 86400 * stats.QUANTILE_TOLERANCE


def test_quantiles_stay_inside_the_observed_range(tiny_log):
    stats = dfg.build(tiny_log).named_edges()[("Record Invoice Receipt", "Clear Invoice")]
    for q in (0.0, 0.1, 0.5, 0.9, 1.0):
        assert stats.min_seconds <= stats.quantile_seconds(q) <= stats.max_seconds


def test_keep_fraction_retains_the_heaviest_edges(tiny_log):
    graph = dfg.build(tiny_log)
    reduced = graph.filter_edges(keep_fraction=0.5)
    assert 0 < len(reduced) < len(graph)
    assert max(s.count for s in reduced.edges.values()) == max(
        s.count for s in graph.edges.values()
    )


def test_diff_matches_by_name_and_flags_exclusives(tiny_log):
    left = dfg.build(tiny_log.select(["c1"]))
    right = dfg.build(tiny_log.select(["c2"]))
    rows = {(r["from"], r["to"]): r for r in dfg.diff(left, right)}
    assert rows[("Clear Invoice", "Record Goods Receipt")]["only_in"] == "right"
    assert rows[("Record Goods Receipt", "Record Invoice Receipt")]["only_in"] == "left"


def test_real_snapshot_has_the_expected_spine(snapshot_log):
    graph = dfg.build(snapshot_log)
    named = graph.named_edges()
    # The purchase-to-pay spine must be present in real data.
    assert named[("Record Invoice Receipt", "Clear Invoice")].count > 500
    assert named[("Record Goods Receipt", "Record Invoice Receipt")].count > 300
