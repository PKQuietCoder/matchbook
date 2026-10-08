"""Guards against the class of bug a 2,000-case fixture cannot reveal.

The library's whole claim is that it handles a 1.6M-event log on a laptop.
Nothing in the rest of the suite tests that, because the fixtures are small --
and a quadratic that costs 0.04s at 2,000 cases costs about 23 minutes at
251,734. These tests are cheap and exist only to keep the asymptotics honest.
"""

from __future__ import annotations

import time

from process import otlp
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

    # Warm up and take the best of three, because the thing being measured is
    # smaller than the noise around it. At these sizes `build()` takes single-
    # digit milliseconds, so one cold measurement against one warm one is
    # dominated by interpreter warmth and GC timing rather than by complexity:
    # run on its own this test saw 2.0x, and run after the rest of the suite --
    # which leaves the interpreter warm, shrinking the FIRST timing -- it saw
    # 3.1x on the same unchanged code. Best-of-N filters scheduler noise in the
    # only direction it can go, and a genuine quadratic cannot hide in it: the
    # regression this guards against ran ~340x slow at 2,000 cases.
    def best_of(count: int, repeats: int = 3) -> float:
        _build(count)  # discarded
        timings = []
        for _ in range(repeats):
            start = time.perf_counter()
            _build(count)
            timings.append(time.perf_counter() - start)
        return min(timings)

    small_seconds = best_of(small)
    large_seconds = best_of(large)

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


def test_otlp_export_is_linear_in_events(tmp_path):
    """Doubling the cases must not quadruple the export.

    The export walks every case and every event once. The way that stops being
    true is an accidental per-span scan of something already emitted -- the
    span-id uniqueness set is per case for exactly that reason, and a repo-wide
    one would look fine on the 50-case fixture and quadratic on the real log.
    """
    small, large = 2_000, 4_000

    def export(case_count, name):
        """Best of three, after a warm-up.

        The work here takes ~50ms, so a single GC pause or a busy core is a
        bigger effect than the thing being measured. Best-of-N keeps the guard
        sensitive to an asymptotic change -- which shows up in every run --
        while ignoring a one-off spike, which is what made the first version of
        this test flaky. Measured ratio on an idle machine is 1.9 to 2.1.
        """
        log = _build(case_count)
        header = otlp.header_of(log)
        best = float("inf")
        for attempt in range(4):
            start = time.perf_counter()
            otlp.write_otlp(otlp.traces_of_log(log), tmp_path / f"{name}{attempt}.ndjson", header)
            elapsed = time.perf_counter() - start
            if attempt:  # the first pass is the warm-up
                best = min(best, elapsed)
        return best

    small_seconds = export(small, "small")
    large_seconds = export(large, "large")

    ratio = large_seconds / max(small_seconds, 1e-6)
    assert ratio < 3.0, (
        f"doubling the case count took {ratio:.1f}x longer "
        f"({small_seconds:.3f}s -> {large_seconds:.3f}s); the export is not linear"
    )
