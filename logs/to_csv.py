"""Convert the BPI 2019 XES log into DataFrame-ready CSV tables under `logs/frames/`.

Two tables, not one. A single denormalised table repeats the 16 case attributes on
every event: measured at 175,618 rows and scaled, ~1.92 GB in memory against
~0.36 GB for the event columns alone, because the case attributes are 81% of a
joined layout at ~6.3 events per case. `process/csvio.py` already made this call
for the committed snapshot and says why.

Plus a `dtypes.json` sidecar, which is what makes the conversion lossless. A CSV
has no schema, so without it `GR-Based Inv. Verif.` reads back as the string
'False' and `Item` '00001' reads back as the integer 1. With it, pandas restores
both exactly -- verified in `tests/test_frames.py`.

Nothing here needs pandas. The conversion is standard library (`process.csvio`);
pandas is the consumer's dependency, at read time. `--parquet` is the one
exception and imports lazily, so a clone without pandas still converts.

Measured on the full 728 MB source: no information is lost. Every timestamp is
`.000` milliseconds and UTC, so epoch seconds is exact; the only XES types present
are string/float/date/boolean, so nothing nested is skipped; and the EUR column
has at most one decimal place, so integer cents never rounds. What a table cannot
hold is XES *metadata* -- extension declarations, classifiers, `<global>` defaults
-- and that goes in the manifest instead.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from process import config, csvio, otlp  # noqa: E402

DEFAULT_SOURCE = "logs/raw/BPI_Challenge_2019.xes"
DEFAULT_OUT_DIR = "logs/frames"
LOG_ID = "bpic19"
DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
LICENSE = "CC BY 4.0"
ATTRIBUTION = (
    "van Dongen, Boudewijn (2019): BPI Challenge 2019. Version 1. 4TU.ResearchData. "
    f"https://doi.org/{DOI} Licensed CC BY 4.0."
)

# XES metadata a table cannot carry. Recorded rather than dropped silently.
XES_METADATA = {
    "extensions": ["Organizational (org)", "Concept (concept)", "Time (time)"],
    "classifiers": {"Event Name": ["concept:name"], "Resource": ["org:resource"]},
    "global_event_defaults": {
        "concept:name": "UNKNOWN",
        "time:timestamp": "1970-01-01T00:00:00.000Z",
        "org:resource": "UNKNOWN",
        "User": "UNKNOWN",
        "Cumulative net worth (EUR)": 0.0,
    },
    "note": (
        "The <global scope=\"event\"> block is a schema declaration, not an event. "
        "Counting it is why this dataset is sometimes described as having 1,595,924 "
        "events and 173 midnight timestamps; the log has 1,595,923 and 172."
    ),
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def convert(source: Path, out_dir: Path, *, max_cases: int | None = None) -> dict[str, Any]:
    """Stream the XES into the two CSV tables and return the measured counts."""
    traces = otlp.traces_of_xes(
        source,
        log_id=LOG_ID,
        activity_rank=config.activity_rank(),
        max_cases=max_cases,
    )

    def progress(stats: dict[str, Any]) -> None:
        print(f"  {stats['cases']:,} cases / {stats['events']:,} events", flush=True)

    return csvio.write_csv_tables(
        traces,
        out_dir / f"{LOG_ID}-events.csv.gz",
        out_dir / f"{LOG_ID}-cases.csv.gz",
        on_progress=progress,
    )


def write_dtypes(stats: dict[str, Any], destination: Path) -> Path:
    """The sidecar. Without this the CSV is not a lossless representation."""
    destination.write_text(
        json.dumps(
            {
                "_comment": (
                    "Feed these straight to pandas: pd.read_csv(path, dtype=<table>, "
                    "keep_default_na=False). Without them bool columns come back as "
                    "the strings 'True'/'False' and 'Item' 00001 becomes the integer 1."
                ),
                "events": stats["dtypes"]["events"],
                "cases": stats["dtypes"]["cases"],
                "parse_dates": {"events": ["timestamp"]},
                "mixed_type_columns": stats["mixed_type_columns"],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def write_manifest(stats: dict[str, Any], source: Path, destination: Path) -> Path:
    events = stats["events"] or 1
    span = [
        datetime.fromtimestamp(stats[key], tz=timezone.utc).isoformat()
        if stats[key] is not None
        else None
        for key in ("first_timestamp", "last_timestamp")
    ]
    destination.write_text(
        json.dumps(
            {
                "derivative_of": {
                    "title": "BPI Challenge 2019",
                    "citation": ATTRIBUTION,
                    "doi": DOI,
                    "license": LICENSE,
                    "source_file": str(source).replace(str(REPO_ROOT) + "/", ""),
                },
                "counts": {
                    "cases": stats["cases"],
                    "events": stats["events"],
                    "activities": len(stats["activities"]),
                    "resources": stats["resources_count"],
                    "case_columns": len(stats["case_columns"]),
                    "value_cents_total": stats["value_cents_total"],
                    "first_event": span[0],
                    "last_event": span[1],
                },
                "order_assumption": {
                    "rule": "facts.yaml activity_rank",
                    "tie_broken_events": stats["tie_broken_events"],
                    "tie_broken_share": round(stats["tie_broken_events"] / events, 4),
                    "note": (
                        "Timestamps are minute-precision, so same-minute order comes "
                        "from the declared rank, not from the data. Sort by "
                        "['case_id','seq'] -- the row order of the events table -- "
                        "never by timestamp."
                    ),
                },
                "bytes": {
                    "events_csv_gz": stats["events_bytes"],
                    "cases_csv_gz": stats["cases_bytes"],
                },
                "xes_metadata_not_representable_in_a_table": XES_METADATA,
                "withheld": [
                    "value_cents is a CASE-level figure repeated on every event of a "
                    "case; a three-way value match is not computable from this log",
                    "the CSV carries no schema; load with dtypes.json or the types are gone",
                ],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def write_parquet(out_dir: Path, stats: dict[str, Any]) -> list[Path]:
    """Optional schema-carrying copies. The only output that needs pandas."""
    import pandas as pd  # imported lazily: a clone without pandas still converts

    written = []
    for table, parse_dates in (("events", ["timestamp"]), ("cases", None)):
        frame = pd.read_csv(
            out_dir / f"{LOG_ID}-{table}.csv.gz",
            dtype=stats["dtypes"][table],
            parse_dates=parse_dates,
            keep_default_na=False,
        )
        destination = out_dir / f"{LOG_ID}-{table}.parquet"
        frame.to_parquet(destination, index=False)
        written.append(destination)
    return written


def _case_column(fieldnames: list[str]) -> str | None:
    """Find the case-id column in the organizers' CSV.

    Their columns are prefixed `case ` / `event `, so the id is
    `case concept:name`. A loose "does the name contain 'case'" test picks
    `case Spend area text` instead -- which has 21 distinct values and makes the
    log look like it has 21 cases. Match the known names in order of specificity.
    """
    lowered = {name.lower().strip(): name for name in fieldnames if name}
    for candidate in ("case concept:name", "concept:name", "case id", "caseid", "case_id"):
        if candidate in lowered:
            return lowered[candidate]
    return next(
        (lowered[k] for k in lowered if k.startswith("case") and "concept" in k), None
    )


def cross_check(out_dir: Path, official_zip: Path) -> dict[str, Any]:
    """Compare our conversion against the challenge organizers' own CSV.

    Theirs is not the reference. It is hosted on the conference webserver rather
    than the DOI record, and the challenge page warns it "requires special
    handling for literal columns containing commas within quoted text" -- so
    where the two disagree, ours may be the better file. The comparison is the
    finding either way, which is why disagreements are reported rather than
    resolved.
    """
    report: dict[str, Any] = {"official_zip": str(official_zip)}
    with zipfile.ZipFile(official_zip) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        report["files_in_zip"] = archive.namelist()
        if not names:
            report["error"] = "no .csv inside the archive"
            return report
        with archive.open(names[0]) as raw:
            text = (line.decode("utf-8", "replace") for line in raw)
            reader = csv.DictReader(text)
            report["their_columns"] = reader.fieldnames
            their_cases: Counter[str] = Counter()
            their_rows = 0
            case_column = _case_column(reader.fieldnames or [])
            for row in reader:
                their_rows += 1
                if case_column:
                    their_cases[row[case_column]] += 1
    report["their_events"] = their_rows
    report["their_cases"] = len(their_cases)
    report["their_case_column"] = case_column

    ours: Counter[str] = Counter()
    with gzip.open(out_dir / f"{LOG_ID}-events.csv.gz", "rt") as handle:
        for row in csv.DictReader(handle):
            ours[row["case_id"]] += 1
    report["our_events"] = sum(ours.values())
    report["our_cases"] = len(ours)

    report["events_agree"] = report["our_events"] == report["their_events"]
    report["cases_agree"] = report["our_cases"] == report["their_cases"]
    if case_column:
        overlap = set(ours) & set(their_cases)
        report["case_ids_in_both"] = len(overlap)
        report["case_ids_only_ours"] = len(set(ours) - set(their_cases))
        report["case_ids_only_theirs"] = len(set(their_cases) - set(ours))
        report["cases_with_differing_event_counts"] = sum(
            1 for case in overlap if ours[case] != their_cases[case]
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-cases", type=int, help="fast path while developing")
    parser.add_argument(
        "--parquet", action="store_true", help="also write schema-carrying parquet (needs pandas)"
    )
    parser.add_argument(
        "--cross-check",
        nargs="?",
        const="logs/raw/BPIChallenge2019CSV.zip",
        metavar="ZIP",
        help="compare against the organizers' own CSV (fetch: python -m logs.download "
        "--log bpic19-csv)",
    )
    parser.add_argument("--sha256", action="store_true", help="hash the outputs (slow)")
    args = parser.parse_args(argv)

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cross_check:
        official = REPO_ROOT / args.cross_check
        if not official.exists():
            raise SystemExit(
                f"{official} not found. Fetch it with:\n"
                "  python -m logs.download --log bpic19-csv"
            )
        print(json.dumps(cross_check(out_dir, official), indent=1, default=str))
        return 0

    source = REPO_ROOT / args.source
    if not source.exists():
        raise SystemExit(
            f"{source} not found. Fetch it with:\n  python -m logs.download --log bpic19"
        )

    print(f"converting {source.name} -> {args.out_dir}/")
    stats = convert(source, out_dir, max_cases=args.max_cases)

    written = [
        out_dir / f"{LOG_ID}-events.csv.gz",
        out_dir / f"{LOG_ID}-cases.csv.gz",
        write_dtypes(stats, out_dir / f"{LOG_ID}-dtypes.json"),
        write_manifest(stats, source, out_dir / f"{LOG_ID}-frames-manifest.json"),
    ]
    if args.parquet:
        written += write_parquet(out_dir, stats)
    for path in written:
        size = path.stat().st_size
        print(f"wrote {path.relative_to(REPO_ROOT)}  ({size:,} bytes)")
        if args.sha256:
            print(f"      sha256 {sha256_of(path)}")

    events = stats["events"] or 1
    ties = stats["tie_broken_events"]
    print()
    print(
        f"{stats['cases']:,} cases / {stats['events']:,} events / "
        f"{len(stats['activities'])} activities / {stats['resources_count']} resources"
    )
    print(
        f"{ties:,} of {stats['events']:,} events ({ties / events:.1%}) were ordered by the "
        "declared facts.yaml activity_rank rather than by recorded time."
    )
    print(
        "Sort by ['case_id','seq'] -- the row order of the events table -- never by "
        "timestamp: minute precision means tied events would be reshuffled."
    )
    if stats["mixed_type_columns"]:
        print(
            f"\nWARNING: {len(stats['mixed_type_columns'])} case column(s) hold more than one "
            f"type and fell back to str: {', '.join(stats['mixed_type_columns'])}"
        )
    print(
        f"\nLoad with the sidecar or the dtypes are lost:\n"
        f"  d = json.load(open('{args.out_dir}/{LOG_ID}-dtypes.json'))\n"
        f"  events = pd.read_csv('{args.out_dir}/{LOG_ID}-events.csv.gz', dtype=d['events'],\n"
        f"                       parse_dates=['timestamp'], keep_default_na=False)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
