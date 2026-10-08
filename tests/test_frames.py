"""The CSV DataFrame tables: what they conserve, and what the sidecar is for.

The claim worth testing is not "we wrote some CSV". It is that the two tables plus
`dtypes.json` are a *lossless* representation of the XES -- including the two bool
columns and `Item`'s leading zeros, which a plain CSV read destroys. So the tests
start from the real XES, not from the committed CSV snapshot: a round trip
CSV -> CSV would be circular, because both sides would already be strings.
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_LOG = REPO_ROOT / "logs" / "raw" / "BPI_Challenge_2019.xes"

from process import config, csvio, otlp, xes  # noqa: E402

pandas = pytest.importorskip("pandas", reason="pandas is the consumer's dependency, not ours")

needs_raw = pytest.mark.skipif(
    not RAW_LOG.exists(),
    reason="full log not fetched; run `python -m logs.download --log bpic19`",
)

CASES = 300


def _shape(log):
    return (
        log.case_ids,
        [t.activities for t in log.traces()],
        list(log.timestamp),
        [t.resources for t in log.traces()],
        list(log.value_cents),
        log.tie_broken,
    )


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    """The two tables plus the source log, from the real XES."""
    if not RAW_LOG.exists():
        pytest.skip("full log not fetched")
    out = tmp_path_factory.mktemp("frames")
    rank = config.activity_rank()
    stats = csvio.write_csv_tables(
        otlp.traces_of_xes(RAW_LOG, log_id="bpic19", activity_rank=rank, max_cases=CASES),
        out / "events.csv.gz",
        out / "cases.csv.gz",
    )
    source = xes.read_xes(RAW_LOG, log_id="bpic19", activity_rank=rank, max_cases=CASES)
    return {"dir": out, "stats": stats, "source": source}


# -- the accuracy guarantee -----------------------------------------------------


@needs_raw
def test_event_table_round_trips_exactly(converted):
    """Every event column, and the tie-break audit, survive the CSV."""
    back = csvio.read_csv(
        converted["dir"] / "events.csv.gz",
        log_id="bpic19",
        activity_rank=config.activity_rank(),
    )
    assert _shape(back) == _shape(converted["source"])


@needs_raw
def test_event_attributes_round_trip(converted):
    """The regression test for the bug this work found.

    `write_csv` emitted an `attributes` column holding `{"User": ...}` as JSON and
    `read_csv`'s default mapping omitted it, so it was written faithfully and
    ignored on read -- 0 of 5,536 recovered on a 200-case slice. Every event in
    BPI 2019 carries a `User`, so this silently dropped 1.6M attributes.
    """
    back = csvio.read_csv(
        converted["dir"] / "events.csv.gz",
        log_id="bpic19",
        activity_rank=config.activity_rank(),
    )
    source = converted["source"]
    assert len(back.event_attributes) == len(source.event_attributes) > 0
    assert back.event_attributes == source.event_attributes
    assert "User" in back.event(0)["attributes"]


@needs_raw
def test_case_attributes_round_trip_with_their_types(converted):
    """The sidecar's whole purpose: bool stays bool, '00001' stays a string."""
    stats, source = converted["stats"], converted["source"]
    cases = pandas.read_csv(
        converted["dir"] / "cases.csv.gz",
        dtype=stats["dtypes"]["cases"],
        keep_default_na=False,
    )
    assert len(cases) == source.case_count
    assert cases["GR-Based Inv. Verif."].dtype == bool
    assert cases["Goods Receipt"].dtype == bool

    indexed = cases.set_index("case_id")
    for case_id in source.case_ids:
        expected = source.case_attributes[case_id]
        row = indexed.loc[case_id]
        for key, want in expected.items():
            got = row[key]
            assert bool(got) == bool(want) if isinstance(want, bool) else got == want, (
                f"case {case_id} attribute {key!r}: {got!r} != {want!r}"
            )
            assert isinstance(want, bool) == (cases[key].dtype == bool)


@needs_raw
def test_leading_zeros_survive_only_with_the_sidecar(converted):
    """Document the loss the sidecar prevents, rather than asserting it away."""
    path = converted["dir"] / "cases.csv.gz"
    typed = pandas.read_csv(path, dtype=converted["stats"]["dtypes"]["cases"],
                            keep_default_na=False)
    naive = pandas.read_csv(path)

    assert typed["Item"].iloc[0] == "00001"
    # Without the sidecar pandas infers a number and the leading zeros are gone.
    assert naive["Item"].iloc[0] == 1
    assert naive["GR-Based Inv. Verif."].dtype == bool  # this one it happens to get right
    assert typed["Purchasing Document"].iloc[0] == "2000000000"


@needs_raw
def test_conservation(converted):
    stats, source = converted["stats"], converted["source"]
    assert stats["cases"] == source.case_count == CASES
    assert stats["events"] == source.event_count
    assert stats["tie_broken_events"] == len(source.tie_broken)
    assert stats["value_cents_total"] == sum(source.value_cents)
    assert set(stats["activities"]) == set(source.activity_frequency())
    assert len(stats["case_columns"]) == 16
    assert stats["mixed_type_columns"] == []


@needs_raw
def test_row_order_is_the_declared_order(converted):
    """Row order carries the order; the timestamps cannot, at minute precision."""
    source = converted["source"]
    with gzip.open(converted["dir"] / "events.csv.gz", "rt") as handle:
        rows = list(csv.DictReader(handle))
    by_case: dict[str, list[str]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row["activity"])
    for case_id in source.case_ids:
        assert tuple(by_case[case_id]) == source.trace(case_id).activities


# -- the sidecar itself ---------------------------------------------------------


def test_dtypes_sidecar_is_pandas_ready(tmp_path):
    """Every declared dtype must be one pandas actually accepts."""
    from process.log import EventLogBuilder

    builder = EventLogBuilder("typed")
    builder.add_case_attributes("c1", {"flag": True, "code": "007", "n": 3, "ratio": 1.5})
    builder.add(case_id="c1", activity="A", timestamp=1_600_000_000,
                resource="user_001", attributes={"User": "u1"})
    log = builder.build()
    stats = csvio.write_csv_tables(
        log.traces(), tmp_path / "e.csv.gz", tmp_path / "c.csv.gz"
    )
    assert stats["dtypes"]["cases"] == {
        "case_id": "str", "flag": "bool", "code": "str", "n": "int64", "ratio": "float64",
    }
    cases = pandas.read_csv(tmp_path / "c.csv.gz", dtype=stats["dtypes"]["cases"],
                            keep_default_na=False)
    assert cases["flag"].iloc[0] is True or cases["flag"].iloc[0] == True  # noqa: E712
    assert cases["code"].iloc[0] == "007"
    assert cases["n"].iloc[0] == 3 and cases["ratio"].iloc[0] == 1.5


def test_a_mixed_type_column_falls_back_and_says_so(tmp_path):
    """Two types in one column: fall back to str and report it, never pick a winner."""
    from process.log import EventLogBuilder

    builder = EventLogBuilder("mixed")
    builder.add_case_attributes("c1", {"odd": True})
    builder.add_case_attributes("c2", {"odd": "yes"})
    for case in ("c1", "c2"):
        builder.add(case_id=case, activity="A", timestamp=1_600_000_000)
    stats = csvio.write_csv_tables(
        builder.build().traces(), tmp_path / "e.csv.gz", tmp_path / "c.csv.gz"
    )
    assert stats["mixed_type_columns"] == ["odd"]
    assert stats["dtypes"]["cases"]["odd"] == "str"


def test_write_csv_and_the_streaming_writer_agree(snapshot_log, tmp_path):
    """One row builder, so the two writers cannot drift apart."""
    whole = tmp_path / "whole.csv.gz"
    streamed = tmp_path / "streamed.csv.gz"
    csvio.write_csv(snapshot_log, whole)
    csvio.write_csv_tables(snapshot_log.traces(), streamed, tmp_path / "cases.csv.gz")
    assert gzip.decompress(whole.read_bytes()) == gzip.decompress(streamed.read_bytes())


# -- the guarantees that must not regress ---------------------------------------


def test_the_mining_half_does_not_import_pandas():
    """pandas is the consumer's dependency. `process/` must not reach for it.

    pyproject declares PyYAML and nothing else, and
    `tests/test_offline_mining.py` imports every mining module in a subprocess.
    A stray `import pandas` in `process/` would make the mining half need a
    dependency the repo does not declare.
    """
    import subprocess

    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "import process.csvio, process.otlp, process.cli, process.log\n"
        "assert 'pandas' not in sys.modules, 'process/ imported pandas'\n"
        "assert 'pyarrow' not in sys.modules, 'process/ imported pyarrow'\n"
        "print('CLEAN')\n" % str(REPO_ROOT)
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "CLEAN" in result.stdout


@needs_raw
def test_conversion_memory_stays_flat(tmp_path):
    """The streaming writer is the thing under test, so measure what it costs.

    A whole-log `read_xes` of BPI 2019 costs ~1.4 GB. Converting a 20,000-case
    slice must not approach that: if this starts failing, something began holding
    every case instead of one.
    """
    import resource

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    csvio.write_csv_tables(
        otlp.traces_of_xes(
            RAW_LOG, log_id="bpic19", activity_rank=config.activity_rank(), max_cases=20_000
        ),
        tmp_path / "e.csv.gz",
        tmp_path / "c.csv.gz",
    )
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    scale = 1 if sys.platform == "darwin" else 1024
    grew_mb = (after - before) * scale / 1e6
    assert grew_mb < 400, f"peak RSS grew {grew_mb:.0f} MB converting 20,000 cases"


# -- the cross-check against the organizers' own CSV ----------------------------


def test_case_column_detection_prefers_the_id_over_a_lookalike():
    """Regression: a loose "contains 'case'" match found the wrong column.

    The organizers' CSV prefixes every column `case ` or `event `, so a test for
    "case" in the name matched `case Spend area text` -- 21 distinct values --
    and reported the 251,734-case log as having 21 cases. The comparison looked
    like a disagreement when the only thing wrong was the column.
    """
    from logs.to_csv import _case_column

    theirs = [
        "case Spend area text", "case Company", "case Document Type",
        "case Purchasing Document", "case Item", "case concept:name",
        "event concept:name", "event time:timestamp",
    ]
    assert _case_column(theirs) == "case concept:name"
    assert _case_column(["CaseID", "Activity"]) == "CaseID"
    assert _case_column(["case_id", "activity"]) == "case_id"
    assert _case_column(["nothing", "relevant"]) is None
