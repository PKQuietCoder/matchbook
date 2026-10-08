"""Ingest: the real files, the published counts, and round-tripping."""

from __future__ import annotations

from pathlib import Path

import pytest

from process import csvio, xes

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_helpdesk_matches_its_published_counts(helpdesk_log):
    # Verenich (2016) reports 3,804 cases and 13,710 events.
    assert helpdesk_log.case_count == 3804
    assert helpdesk_log.event_count == 13710
    assert helpdesk_log.activity_count == 9


def test_snapshot_carries_its_license_and_flow_attribute(snapshot_log):
    assert snapshot_log.case_count > 2000
    flows = {
        snapshot_log.case_attributes[c]["Item Category"]
        for c in snapshot_log.case_ids
        if "Item Category" in snapshot_log.case_attributes.get(c, {})
    }
    assert flows == {
        "3-way match, invoice before GR",
        "3-way match, invoice after GR",
        "2-way match",
        "Consignment",
    }


def test_resource_kinds_follow_the_logs_naming_convention():
    assert xes.classify_resource("batch_07") == "batch"
    assert xes.classify_resource("user_120") == "human"
    assert xes.classify_resource("") == "unknown"
    assert xes.classify_resource("NONE") == "unknown"


def test_minute_precision_is_second_not_day():
    """BPI 2019 timestamps are minute-precision; only midnight reads as a day."""
    assert xes.parse_timestamp("2018-01-02T12:53:00.000Z")[1] == "second"
    assert xes.parse_timestamp("2018-01-02T00:00:00.000Z")[1] == "day"


def test_csv_round_trip_preserves_the_log(tiny_log, tmp_path):
    events = csvio.write_csv(tiny_log, tmp_path / "e.csv")
    attributes = csvio.write_case_attributes(tiny_log, tmp_path / "c.csv")
    back = csvio.read_csv(events, log_id="tiny")
    csvio.read_case_attributes(back, attributes)
    assert back.case_ids == tiny_log.case_ids
    assert [t.activities for t in back.traces()] == [t.activities for t in tiny_log.traces()]
    assert [t.resources for t in back.traces()] == [t.resources for t in tiny_log.traces()]
    assert back.case_attributes == tiny_log.case_attributes


def test_xes_round_trip_preserves_the_log(tiny_log, tmp_path):
    path = xes.write_xes(tiny_log, tmp_path / "log.xes")
    back = xes.read_xes(path, log_id="tiny", value_attribute=None)
    assert back.case_ids == tiny_log.case_ids
    assert [t.activities for t in back.traces()] == [t.activities for t in tiny_log.traces()]
    assert [t.timestamps for t in back.traces()] == [t.timestamps for t in tiny_log.traces()]


@pytest.mark.skipif(
    not (REPO_ROOT / "logs" / "raw" / "BPI_Challenge_2019.xes").exists(),
    reason="full log not fetched; run `python -m logs.download --log bpic19`",
)
def test_full_log_streams_without_loading_it_all():
    """Streaming must be streaming: read 50 cases out of 728 MB cheaply."""
    import itertools

    source = REPO_ROOT / "logs" / "raw" / "BPI_Challenge_2019.xes"
    traces = list(itertools.islice(xes.iter_traces(source), 50))
    assert len(traces) == 50
    attributes, events = traces[0]
    assert attributes["concept:name"] == "2000000000_00001"
    assert attributes["Item Category"] == "3-way match, invoice before GR"
    assert events[0]["concept:name"] == "SRM: Created"
