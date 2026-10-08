"""The OTLP export: what it conserves, what it encodes, and what it withholds.

The round-trip tests are the accuracy guarantee. An export that cannot be read
back into the log it came from is not a representation of that log, it is a
lossy picture of one, and no amount of eyeballing the JSON would show the
difference.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from process import otlp

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_LOG = REPO_ROOT / "logs" / "raw" / "BPI_Challenge_2019.xes"
FIXTURE = REPO_ROOT / "logs" / "otlp" / "bpic19-sample.ndjson.gz"
# CC BY 4.0 requires attribution, so the committed artifact carries it.
ATTRIBUTION = (
    "van Dongen, Boudewijn (2019): BPI Challenge 2019. Version 1. "
    "4TU.ResearchData. https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 "
    "Licensed CC BY 4.0."
)


def _shape(log):
    """The same shape `tests/test_store.py` compares, for the same reason."""
    return (
        log.case_ids,
        [t.activities for t in log.traces()],
        list(log.timestamp),
        [t.resources for t in log.traces()],
        list(log.value_cents),
        log.tie_broken,
        log.case_attributes,
    )


def _canonical_snapshot():
    """The committed snapshot as `process ingest` builds it: ranked.

    The `snapshot_log` fixture reads the committed CSV *without* an activity
    rank, because `logs/sample.py` writes that file in source order on purpose.
    Ingesting it applies `facts.yaml activity_rank`, which reorders same-minute
    ties -- so a log built the two ways is genuinely two different orderings of
    the same events, and only the ranked one matches the committed export.
    That difference is the whole reason `tie_broken` exists.
    """
    from process import config, csvio

    log = csvio.read_csv(
        REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-events.csv.gz",
        log_id="bpic19-sample",
        license="CC BY 4.0",
        activity_rank=config.activity_rank(),
    )
    csvio.read_case_attributes(
        log, REPO_ROOT / "logs" / "snapshot" / "bpic19-sample-cases.csv.gz"
    )
    return log


def _export(log, tmp_path, name="log.ndjson", **header_kwargs):
    header = otlp.header_of(log, **header_kwargs)
    path = tmp_path / name
    stats = otlp.write_otlp(otlp.traces_of_log(log), path, header)
    return path, stats, header


def _spans(path):
    return [span for _resource, _scope, span in otlp.iter_spans(path)]


def _attrs(span):
    return {entry["key"]: otlp._plain_value(entry["value"]) for entry in span["attributes"]}


# -- the accuracy guarantee -----------------------------------------------------


def test_round_trip_is_exact(tiny_log, tmp_path):
    path, _stats, _header = _export(tiny_log, tmp_path)
    assert _shape(otlp.read_otlp(path, log_id="tiny")) == _shape(tiny_log)


def test_round_trip_is_exact_on_real_data(snapshot_log, tmp_path):
    """2,117 real cases and 13,679 real events, not a hand-built fixture."""
    path, stats, _header = _export(snapshot_log, tmp_path, name="snapshot.ndjson.gz")
    back = otlp.read_otlp(path)
    assert _shape(back) == _shape(snapshot_log)
    assert stats.events == snapshot_log.event_count
    assert stats.cases == snapshot_log.case_count


def test_the_tie_break_audit_survives_and_is_not_reinvented(snapshot_log, tmp_path):
    """17.2% of this log's order is assumed. The export must say so, exactly.

    Both halves matter: an event must not lose its tie flag, and must not gain
    one. A reader that re-ranked the file would quietly do the latter.
    """
    path, _stats, _header = _export(snapshot_log, tmp_path)
    back = otlp.read_otlp(path)
    assert back.tie_broken == snapshot_log.tie_broken
    assert len(back.tie_broken) == 2354


def test_order_is_restored_not_recomputed(snapshot_log, tmp_path):
    """`read_otlp` must not re-rank a file that has already been ranked once."""
    path, _stats, _header = _export(snapshot_log, tmp_path)
    back = otlp.read_otlp(path)
    for case_id in snapshot_log.case_ids[:200]:
        assert back.trace(case_id).activities == snapshot_log.trace(case_id).activities


def test_any_value_round_trips_every_type():
    """`AnyValue` is the only typed wrapper in the repo; all branches must hold.

    The array and kvlist branches are not emitted by this log, so they would
    otherwise be dead and untested.
    """
    for value in (
        "text",
        True,
        False,
        0,
        -7,
        1234567890123,
        1.5,
        ["a", 1, True],
        {"k": "v", "n": 2},
    ):
        wrapped = otlp._any_value(value)
        assert len(wrapped) == 1
        assert otlp._plain_value(wrapped) == value
        assert type(otlp._plain_value(wrapped)) is type(value)


def test_attribute_types_survive_the_round_trip(tmp_path):
    """`AnyValue` keeps a bool a bool, which the committed CSV path cannot.

    Built here rather than from `snapshot_log` on purpose: reading the snapshot
    back through `csvio.read_case_attributes` already returns every attribute as
    a string, so that fixture could not show the difference this test is about.
    """
    from process.log import EventLogBuilder

    builder = EventLogBuilder("typed")
    builder.add_case_attributes("c1", {"Goods Receipt": True, "Item": "00001", "Lines": 3})
    builder.add(
        case_id="c1",
        activity="Record Goods Receipt",
        timestamp=1_600_000_000,
        resource="user_001",
        attributes={"flagged": False, "retries": 2},
    )
    log = builder.build()

    path, _stats, _header = _export(log, tmp_path, name="typed.ndjson")
    back = otlp.read_otlp(path, log_id="typed")

    assert back.case_attributes["c1"] == {"Goods Receipt": True, "Item": "00001", "Lines": 3}
    assert back.case_attributes["c1"]["Goods Receipt"] is True
    assert back.event(0)["attributes"] == {"flagged": False, "retries": 2}


def test_verify_reports_no_problems_on_a_good_export(snapshot_log, tmp_path):
    path, _stats, _header = _export(snapshot_log, tmp_path)
    report = otlp.verify(path, snapshot_log)
    assert report["problems"] == []
    assert report["ok"] is True
    assert report["traces"] == snapshot_log.case_count
    assert report["events"] == snapshot_log.event_count


def test_verify_names_what_failed(tiny_log, tmp_path):
    """A conservation failure must name what failed, not return False."""
    path, _stats, _header = _export(tiny_log, tmp_path)
    lines = path.read_text().splitlines()
    broken = json.loads(lines[0])
    spans = broken["resourceSpans"][0]["scopeSpans"][0]["spans"]
    for span in spans:
        for entry in span["attributes"]:
            if entry["key"] == otlp.ATTR_VALUE_CENTS:
                entry["value"]["intValue"] = "999999"
    lines[0] = json.dumps(broken, separators=(",", ":"))
    damaged = tmp_path / "damaged.ndjson"
    damaged.write_text("\n".join(lines) + "\n")

    report = otlp.verify(damaged, tiny_log)
    assert report["ok"] is False
    assert any("value_cents_total did not conserve" in problem for problem in report["problems"])


def test_verify_refuses_to_compare_a_slice_against_the_whole_log(snapshot_log, tmp_path):
    """A filtered export must not produce seven "did not conserve" failures.

    The real cause would be the filter, not a broken exporter, and six or seven
    conservation errors read as the latter. A check that cannot run says so --
    the discipline `process/rules.py` applies to a rule it cannot evaluate.
    """
    import itertools

    header = otlp.header_of(snapshot_log, filters={"max_cases": 10})
    path = tmp_path / "slice.ndjson"
    otlp.write_otlp(itertools.islice(otlp.traces_of_log(snapshot_log), 10), path, header)

    report = otlp.verify(path, snapshot_log)
    assert report["problems"] == []
    assert report["ok"] is True
    assert report["compared"]["evaluated"] is False
    assert "filtered slice" in report["compared"]["not_applicable_because"]


def test_verify_does_compare_an_unfiltered_export(snapshot_log, tmp_path):
    """The other half: an unfiltered export must actually be compared."""
    path, _stats, _header = _export(snapshot_log, tmp_path)
    report = otlp.verify(path, snapshot_log)
    assert report["compared"]["evaluated"] is True
    assert report["compared_against"] == snapshot_log.log_id
    assert report["ok"] is True


# -- OTLP/JSON conformance ------------------------------------------------------


def test_otlp_json_conformance(snapshot_log, tmp_path):
    path, _stats, _header = _export(snapshot_log, tmp_path)
    hex_digits = set("0123456789abcdef")
    allowed = {"stringValue", "boolValue", "intValue", "doubleValue", "arrayValue", "kvlistValue"}

    for span in _spans(path):
        assert len(span["traceId"]) == 32 and set(span["traceId"]) <= hex_digits
        assert len(span["spanId"]) == 16 and set(span["spanId"]) <= hex_digits
        assert set(span["traceId"]) != {"0"} and set(span["spanId"]) != {"0"}
        # Enums are integers in OTLP/JSON, never the enum name strings.
        assert span["kind"] == 1
        # Absent, not UNSET: this log records no per-event outcome.
        assert "status" not in span
        assert "droppedAttributesCount" not in span
        for key in ("startTimeUnixNano", "endTimeUnixNano"):
            assert isinstance(span[key], str), f"{key} must be a decimal string"
            assert int(span[key]) % 1_000_000_000 == 0
        for entry in span["attributes"]:
            value = entry["value"]
            assert len(value) == 1 and set(value) <= allowed
            if "intValue" in value:
                assert isinstance(value["intValue"], str), "int64 is a JSON string"


def test_every_line_is_one_postable_request(snapshot_log, tmp_path):
    """A line must stand alone, and a case must never be split across lines."""
    path, stats, _header = _export(snapshot_log, tmp_path)
    seen: set[str] = set()
    lines = 0
    for line in path.read_text().splitlines():
        lines += 1
        payload = json.loads(line)
        assert set(payload) == {"resourceSpans"}
        assert len(line.encode()) < 4 * 1024 * 1024, "a line must be POST-able"
        cases = set()
        for resource_spans in payload["resourceSpans"]:
            assert "resource" in resource_spans
            for scope_spans in resource_spans["scopeSpans"]:
                assert scope_spans["scope"]["name"] == otlp.SCOPE_NAME
                for span in scope_spans["spans"]:
                    cases.add(_attrs(span)[otlp.ATTR_CASE_ID])
        assert not (cases & seen), "a case was split across two lines"
        seen |= cases
    assert lines == stats.lines


def test_structure_is_one_trace_per_case(snapshot_log, tmp_path):
    path, stats, _header = _export(snapshot_log, tmp_path)
    roots = [s for s in _spans(path) if "parentSpanId" not in s]
    children = [s for s in _spans(path) if "parentSpanId" in s]
    assert len(roots) == snapshot_log.case_count
    assert len(children) == snapshot_log.event_count
    assert len({s["traceId"] for s in roots}) == snapshot_log.case_count
    assert stats.spans == len(roots) + len(children)

    root_of = {s["traceId"]: s["spanId"] for s in roots}
    for span in children:
        assert span["parentSpanId"] == root_of[span["traceId"]]


def test_children_are_zero_duration_and_the_root_is_measured(snapshot_log, tmp_path):
    path, _stats, _header = _export(snapshot_log, tmp_path)
    by_trace: dict[str, list[dict]] = {}
    for span in _spans(path):
        by_trace.setdefault(span["traceId"], []).append(span)

    for spans in by_trace.values():
        root = next(s for s in spans if "parentSpanId" not in s)
        children = [s for s in spans if "parentSpanId" in s]
        for child in children:
            assert child["startTimeUnixNano"] == child["endTimeUnixNano"]
        starts = [int(c["startTimeUnixNano"]) for c in children]
        assert int(root["startTimeUnixNano"]) == min(starts)
        assert int(root["endTimeUnixNano"]) == max(starts)


def test_name_agrees_with_the_activity_attribute(snapshot_log, tmp_path):
    """`name` is for a UI; `mb.activity` is the record. They must not drift."""
    path, _stats, _header = _export(snapshot_log, tmp_path)
    for span in _spans(path):
        if "parentSpanId" in span:
            assert span["name"] == _attrs(span)[otlp.ATTR_ACTIVITY]


def test_attribute_names_are_declared(snapshot_log, tmp_path):
    """A typo'd attribute name must fail here rather than ship unqueryable."""
    path, _stats, _header = _export(
        snapshot_log, tmp_path, doi="10.4121/x", attribution="van Dongen, B. (2019)"
    )
    declared = set(otlp.ROOT_ATTRIBUTES) | set(otlp.EVENT_ATTRIBUTES)
    seen: set[str] = set()
    resource_seen: set[str] = set()

    for resource, _scope, span in otlp.iter_spans(path):
        resource_seen |= {entry["key"] for entry in resource["attributes"]}
        for key in _attrs(span):
            seen.add(key)
            assert key in declared or key.startswith(
                otlp.ATTRIBUTE_PREFIXES
            ), f"undeclared attribute {key!r}"

    # Every declared constant is actually emitted: catches a dead constant left
    # behind after a rename, which the check above cannot see.
    assert declared <= seen, f"declared but never emitted: {sorted(declared - seen)}"
    assert resource_seen == set(otlp.RESOURCE_ATTRIBUTES)


def test_provenance_travels_with_the_artifact(snapshot_log, tmp_path):
    path, _stats, _header = _export(snapshot_log, tmp_path, doi="10.4121/uuid:d06aff4b")
    resource, _scope, _span = next(otlp.iter_spans(path))
    values = {e["key"]: otlp._plain_value(e["value"]) for e in resource["attributes"]}
    assert values["mb.log.license"] == "CC BY 4.0"
    assert values["mb.log.doi"] == "10.4121/uuid:d06aff4b"
    assert values["service.name"] == "bpic19-sample"
    assert values["mb.log.case_notion"] == "purchase-order item"
    # The measured log-level fact, not the per-event canonical value.
    assert values["mb.log.timestamp_precision"] == "minute"


def test_a_filtered_export_self_identifies(snapshot_log, tmp_path):
    """A filtered file carries the whole dataset's license. It must say so."""
    header = otlp.header_of(snapshot_log, filters={"flow": "Consignment"})
    path = tmp_path / "filtered.ndjson"
    otlp.write_otlp(otlp.traces_of_log(snapshot_log), path, header)
    resource, _scope, _span = next(otlp.iter_spans(path))
    values = {e["key"]: otlp._plain_value(e["value"]) for e in resource["attributes"]}
    assert values["service.name"] == "bpic19-sample+flow=Consignment"


# -- determinism and laziness ---------------------------------------------------


def test_export_is_byte_identical_across_runs(snapshot_log, tmp_path):
    """The committed fixture is diff-tested, so the bytes must be stable."""
    header = otlp.header_of(snapshot_log)
    first = tmp_path / "a.ndjson.gz"
    second = tmp_path / "b.ndjson.gz"
    otlp.write_otlp(otlp.traces_of_log(snapshot_log), first, header)
    otlp.write_otlp(otlp.traces_of_log(snapshot_log), second, header)
    assert first.read_bytes() == second.read_bytes()


def test_ids_are_deterministic_and_unique(snapshot_log, tmp_path):
    path, _stats, _header = _export(snapshot_log, tmp_path)
    spans = _spans(path)
    assert len({s["spanId"] for s in spans}) == len(spans)
    assert otlp.trace_id("bpic19-sample", "2000000000_00001") == otlp.trace_id(
        "bpic19-sample", "2000000000_00001"
    )
    # A different log id must not reuse another log's trace id.
    assert otlp.trace_id("a", "c1") != otlp.trace_id("b", "c1")
    # `\x00` separation: "a" + "bc" must not collide with "ab" + "c".
    assert otlp.trace_id("a", "bc") != otlp.trace_id("ab", "c")


def test_the_writer_is_lazy(snapshot_log, tmp_path):
    """Spans must reach the file before the trace iterator is exhausted.

    This is the streaming property, asserted deterministically rather than by
    measuring resident memory and hoping the number means something.
    """
    path = tmp_path / "lazy.ndjson"
    drained = {"all": False}

    def traces():
        for trace in snapshot_log.traces():
            yield trace
        drained["all"] = True

    sizes = []

    def on_progress(stats):
        sizes.append(path.stat().st_size if path.exists() else 0)
        assert not drained["all"] or stats.cases == snapshot_log.case_count

    otlp.write_otlp(traces(), path, otlp.header_of(snapshot_log), batch_spans=500,
                    on_progress=on_progress)
    assert any(size > 0 for size in sizes[:-1]), "nothing was written before the end"


# -- the committed fixture ------------------------------------------------------


def test_committed_fixture_round_trips():
    back = otlp.read_otlp(FIXTURE)
    assert back.case_count == 50
    assert back.event_count > 100
    report = otlp.verify(FIXTURE)
    assert report["problems"] == []


def test_committed_fixture_has_not_drifted(snapshot_log, tmp_path):
    """Regenerate it and compare bytes.

    The round-trip tests prove the writer and reader agree with each other. Only
    a golden file catches the two being edited together into a new on-disk
    format that still round-trips perfectly.
    """
    import itertools

    log = _canonical_snapshot()
    # Provenance pinned explicitly: `_canonical_snapshot` reads through an
    # absolute path, and an absolute path baked into a committed artifact would
    # make the bytes depend on where the repo happens to live.
    header = otlp.header_of(
        log,
        source="logs/snapshot/bpic19-sample-events.csv.gz",
        attribution=ATTRIBUTION,
        doi="10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1",
        filters={"max_cases": 50},
    )
    regenerated = tmp_path / "fixture.ndjson.gz"
    otlp.write_otlp(itertools.islice(otlp.traces_of_log(log), 50), regenerated, header)
    assert gzip.decompress(regenerated.read_bytes()) == gzip.decompress(FIXTURE.read_bytes()), (
        "the committed OTLP fixture no longer matches what the exporter produces; "
        "regenerate it with the command in logs/otlp/README.md if the change is intended"
    )


# -- the streaming XES path -----------------------------------------------------


@pytest.mark.skipif(
    not RAW_LOG.exists(),
    reason="full log not fetched; run `python -m logs.download --log bpic19`",
)
def test_streaming_matches_the_in_memory_build(tmp_path):
    """The per-case build must reproduce the whole-log build exactly.

    This is the claim the streaming path rests on: the builder's sort and its
    tie marking are case-local, so building one case at a time is lossless. If
    that ever stops being true, the export silently stops matching the log.

    Compared by case id, not by line order: the source file is not in case-id
    order, so the two paths emit the same cases in different places.
    """
    from process import config, xes

    rank = config.activity_rank()
    header = otlp.ExportHeader(log_id="bpic19", source=str(RAW_LOG))

    streamed = tmp_path / "streamed.ndjson"
    otlp.write_otlp(
        otlp.traces_of_xes(RAW_LOG, log_id="bpic19", activity_rank=rank, max_cases=200),
        streamed,
        header,
    )
    whole = xes.read_xes(RAW_LOG, log_id="bpic19", activity_rank=rank, max_cases=200)
    in_memory = tmp_path / "memory.ndjson"
    otlp.write_otlp(otlp.traces_of_log(whole), in_memory, header)

    def by_case(path):
        grouped: dict[str, list] = {}
        for span in _spans(path):
            attributes = _attrs(span)
            grouped.setdefault(attributes[otlp.ATTR_CASE_ID], []).append(
                (
                    span["name"],
                    span["startTimeUnixNano"],
                    span["endTimeUnixNano"],
                    attributes.get(otlp.ATTR_SEQ),
                    attributes.get(otlp.ATTR_TIE_BROKEN, False),
                    attributes.get(otlp.ATTR_RESOURCE),
                    attributes.get(otlp.ATTR_VALUE_CENTS),
                )
            )
        return grouped

    streamed_cases, memory_cases = by_case(streamed), by_case(in_memory)
    assert set(streamed_cases) == set(memory_cases)
    assert streamed_cases == memory_cases
