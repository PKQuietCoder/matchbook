"""Filters return new logs and never mutate the original."""

from __future__ import annotations

import pytest

from process import filters


def test_flow_filter_selects_by_item_category(tiny_log):
    subset = filters.by_flow(tiny_log, "2-way match")
    assert subset.case_ids == ["c5"]
    assert tiny_log.case_count == 5  # unchanged


def test_variant_coverage_keeps_the_common_shape(tiny_log):
    subset = filters.by_variant_coverage(tiny_log, 0.4)
    assert 0 < subset.case_count < tiny_log.case_count


def test_containing_and_excluding_are_complements(tiny_log):
    with_block = filters.containing_activity(tiny_log, "Remove Payment Block")
    without_block = filters.excluding_activity(tiny_log, "Remove Payment Block")
    assert with_block.case_ids == ["c4"]
    assert set(with_block.case_ids) | set(without_block.case_ids) == set(tiny_log.case_ids)


def test_case_length_bounds(tiny_log):
    assert filters.by_case_length(tiny_log, minimum=5).case_ids == ["c4"]
    assert filters.by_case_length(tiny_log, maximum=3).case_ids == ["c5"]


def test_timeframe_mode_must_be_chosen_explicitly(tiny_log):
    with pytest.raises(ValueError):
        filters.by_timeframe(tiny_log, mode="whatever")


def test_timeframe_modes_differ(tiny_log):
    from tests.conftest import moment

    window_end = moment(3, 23, 59)
    contained = filters.by_timeframe(tiny_log, end=window_end, mode="contained")
    intersecting = filters.by_timeframe(tiny_log, end=window_end, mode="intersecting")
    # A long case is dropped by "contained" but kept by "intersecting" -- the
    # bias that makes duration statistics wrong if the mode is chosen silently.
    assert set(contained.case_ids) < set(intersecting.case_ids)
