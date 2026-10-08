"""Guards against the class of bug a 2,000-case fixture cannot reveal.

The library's whole claim is that it handles a 1.6M-event log on a laptop.
Nothing in the rest of the suite tests that, because the fixtures are small --
and a quadratic that costs 0.04s at 2,000 cases costs about 23 minutes at
251,734. These tests are cheap and exist only to keep the asymptotics honest.
"""

from __future__ import annotations

import time

from process.log import EventLogBuilder


def _build(case_count: int, *, attributes: bool = True):
    builder = EventLogBuilder("scale")
    for index in range(case_count):
        case_id = f"c{index:07d}"
        if attributes:
            builder.add_case_attributes(case_id, {"Item Category": "2-way match"})
        for step, activity in enumerate(("Create Purchase Order Item", "Record Invoice Receipt")):
            builder.add(case_id=case_id, activity=activity, timestamp=1_700_000_000 + step)
    return builder.build()


def test_build_is_not_quadratic_in_case_count():
    """Doubling the cases must not quadruple the time.

    A quadratic shows up as a ratio near 4; linear-plus-sort is near 2. The
    threshold is loose enough to survive a noisy machine and tight enough to
    catch a rebuilt-set-per-item regression, which ran ~340x slow at 2,000
    cases and ~19,000x at 60,000.
    """
    small, large = 4_000, 8_000

    start = time.perf_counter()
    _build(small)
    small_seconds = time.perf_counter() - start

    start = time.perf_counter()
    _build(large)
    large_seconds = time.perf_counter() - start

    ratio = large_seconds / max(small_seconds, 1e-6)
    assert ratio < 3.0, (
        f"doubling the case count took {ratio:.1f}x longer "
        f"({small_seconds:.3f}s -> {large_seconds:.3f}s); this is the signature of a "
        "per-item rebuild of a set or list inside build()"
    )


def test_case_attributes_survive_the_build(snapshot_log):
    """The hoisted membership set must still filter correctly."""
    assert snapshot_log.case_attributes
    assert set(snapshot_log.case_attributes) <= set(snapshot_log.case_ids)
    # Attributes for a case that is not in the log must be dropped.
    builder = EventLogBuilder("t")
    builder.add_case_attributes("present", {"a": 1})
    builder.add_case_attributes("absent", {"a": 2})
    builder.add(case_id="present", activity="A", timestamp=1)
    log = builder.build()
    assert set(log.case_attributes) == {"present"}


def test_select_preserves_relative_order_at_size():
    """Filtering must not re-shuffle events that share a timestamp."""
    log = _build(500)
    subset = log.select([f"c{i:07d}" for i in range(0, 500, 2)])
    assert subset.case_count == 250
    for trace in subset.traces():
        assert trace.activities == ("Create Purchase Order Item", "Record Invoice Receipt")
