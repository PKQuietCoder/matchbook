"""The store must reload a log byte-for-byte, tie-break audit included."""

from __future__ import annotations

from process import store


def _shape(log):
    return (
        log.case_ids,
        [t.activities for t in log.traces()],
        list(log.timestamp),
        [t.resources for t in log.traces()],
        list(log.value_cents),
        log.tie_broken,
        log.case_attributes,
    )


def test_round_trip_is_exact(tiny_log, tiny_store):
    assert _shape(store.load(tiny_store, "tiny")) == _shape(tiny_log)


def test_provenance_is_recorded(tiny_log, tmp_path):
    path = tmp_path / "s.db"
    store.save(tiny_log, path, doi="10.0/x", notes="a note")
    row = next(r for r in store.list_logs(path) if r["log_id"] == "tiny")
    assert row["doi"] == "10.0/x"
    assert row["notes"] == "a note"
    assert row["case_count"] == tiny_log.case_count
    assert row["event_count"] == tiny_log.event_count


def test_saving_twice_replaces_rather_than_duplicates(tiny_log, tmp_path):
    path = tmp_path / "s.db"
    store.save(tiny_log, path)
    store.save(tiny_log, path)
    assert len([r for r in store.list_logs(path) if r["log_id"] == "tiny"]) == 1
    assert store.load(path, "tiny").event_count == tiny_log.event_count


def test_unknown_log_names_what_is_available(tiny_store):
    try:
        store.load(tiny_store, "nope")
    except KeyError as exc:
        assert "tiny" in str(exc)
    else:
        raise AssertionError("expected a KeyError naming the available logs")
