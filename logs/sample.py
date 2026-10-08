"""Build the committed BPI 2019 snapshot, and disclose how it differs.

CC BY 4.0 lets us redistribute a subset with attribution, so a small sample is
committed and a fresh clone works with no network. But a 2,000-case sample of a
251,734-case log will not reproduce the original's variant distribution, and a
conclusion drawn on the sample may not hold on the full log. The mitigation is
disclosure, not cleverness: this script makes one streaming pass that both
*measures the full log* and *selects the sample*, then writes `SAMPLE.md` with a
side-by-side comparison so the distortion is on the record.

Selection is deterministic and reproducible from the manifest, not a mystery
file: a case is chosen by the rank of `sha256(salt + case_id)` within its flow
stratum. Re-running on the same input gives the same sample, and anyone can
check it.

Allocation is proportional to each flow's share of the log, with a floor so the
rare flows still appear. The floor over-represents them; `SAMPLE.md` says by
how much.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from process import csvio, store, xes  # noqa: E402
from process.log import EventLogBuilder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SALT = "matchbook-bpic19-snapshot-v1"
FLOW_ATTRIBUTE = "Item Category"
CASE_ATTRIBUTE = "concept:name"
UNKNOWN_FLOW = "(unrecorded)"


def case_hash(case_id: str) -> int:
    return int.from_bytes(hashlib.sha256((SALT + case_id).encode()).digest()[:8], "big")


def _variant_coverage(variants: Counter, total_cases: int, top: int = 20) -> float:
    covered = sum(count for _variant, count in variants.most_common(top))
    return covered / total_cases if total_cases else 0.0


def scan_and_select(
    source: Path,
    *,
    target_cases: int,
    floor_per_flow: int,
    candidates_per_flow: int,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], list[dict[str, Any]]]]]:
    """One pass: full-log statistics plus the candidate pool per flow."""
    activity_ids: dict[str, int] = {}
    stats: dict[str, Any] = {
        "cases": 0,
        "events": 0,
        "activities": Counter(),
        "flows": Counter(),
        "resource_kinds": Counter(),
        "variants": Counter(),
        "events_per_case": [],
        "day_precision_events": 0,
        "vendors": set(),
        "companies": set(),
    }
    # Per flow: a bounded max-heap of (-hash, case_id, trace attrs, events).
    pools: dict[str, list] = {}

    for trace_attributes, events in xes.iter_traces(source):
        stats["cases"] += 1
        stats["events"] += len(events)
        stats["events_per_case"].append(len(events))
        flow = str(trace_attributes.get(FLOW_ATTRIBUTE) or UNKNOWN_FLOW)
        stats["flows"][flow] += 1
        vendor = trace_attributes.get("Vendor")
        if vendor:
            stats["vendors"].add(str(vendor))
        company = trace_attributes.get("Company")
        if company:
            stats["companies"].add(str(company))

        signature = []
        for record in events:
            activity = str(record.get(xes.ACTIVITY_ATTRIBUTE, "")) or "UNKNOWN"
            stats["activities"][activity] += 1
            signature.append(activity_ids.setdefault(activity, len(activity_ids)))
            resource = str(record.get(xes.RESOURCE_ATTRIBUTE, "") or "")
            stats["resource_kinds"][xes.classify_resource(resource)] += 1
            raw_timestamp = record.get(xes.TIMESTAMP_ATTRIBUTE)
            if raw_timestamp is not None:
                _epoch, precision = xes.parse_timestamp(str(raw_timestamp))
                if precision != "second":
                    stats["day_precision_events"] += 1
        stats["variants"][tuple(signature)] += 1

        case_id = str(trace_attributes.get(CASE_ATTRIBUTE) or f"case-{stats['cases']}")
        pool = pools.setdefault(flow, [])
        entry = (-case_hash(case_id), case_id, trace_attributes, events)
        if len(pool) < candidates_per_flow:
            heapq.heappush(pool, entry)
        elif entry[0] > pool[0][0]:
            heapq.heapreplace(pool, entry)

    total_cases = stats["cases"]
    allocation: dict[str, int] = {}
    for flow, count in stats["flows"].items():
        proportional = round(target_cases * count / total_cases) if total_cases else 0
        allocation[flow] = min(count, max(floor_per_flow, proportional))

    selected: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    short_falls: dict[str, int] = {}
    for flow, pool in pools.items():
        ordered = sorted(pool, key=lambda item: -item[0])  # ascending hash
        wanted = allocation[flow]
        if len(ordered) < wanted:
            short_falls[flow] = wanted - len(ordered)
        for _negative_hash, _case_id, attributes, events in ordered[:wanted]:
            selected.append((attributes, events))

    stats["allocation"] = allocation
    stats["short_falls"] = short_falls
    stats["selected_cases"] = len(selected)
    return stats, selected


def build_log(selected, *, activity_rank: dict[str, int] | None = None):
    builder = EventLogBuilder(
        "bpic19-sample",
        activity_rank=activity_rank,
        source="BPI_Challenge_2019.xes (deterministic subset)",
        license="CC BY 4.0",
        attribution=(
            "van Dongen, Boudewijn (2019): BPI Challenge 2019. Version 1. "
            "4TU.ResearchData. https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 "
            "Licensed CC BY 4.0. Derived subset: deterministic stratified sample, "
            "converted to the Matchbook canonical schema."
        ),
    )
    for trace_attributes, events in selected:
        case_id = str(trace_attributes.get(CASE_ATTRIBUTE))
        builder.add_case_attributes(case_id, trace_attributes)
        for record in events:
            raw_timestamp = record.get(xes.TIMESTAMP_ATTRIBUTE)
            if raw_timestamp is None:
                continue
            timestamp, precision = xes.parse_timestamp(str(raw_timestamp))
            raw_value = record.get(xes.BPIC19_VALUE_ATTRIBUTE)
            builder.add(
                case_id=case_id,
                activity=str(record.get(xes.ACTIVITY_ATTRIBUTE, "")) or "UNKNOWN",
                timestamp=timestamp,
                resource=str(record.get(xes.RESOURCE_ATTRIBUTE, "") or ""),
                value_cents=int(round(float(raw_value) * 100))
                if isinstance(raw_value, (int, float))
                else 0,
                precision=precision,
            )
    return builder.build()


def write_disclosure(stats: dict[str, Any], sample_log, destination: Path) -> Path:
    total_cases = stats["cases"]
    total_events = stats["events"]
    sample_activities = sample_log.activity_frequency()
    sample_flows = Counter(
        str(sample_log.case_attributes.get(case_id, {}).get(FLOW_ATTRIBUTE) or UNKNOWN_FLOW)
        for case_id in sample_log.case_ids
    )
    sample_variants = sample_log.variants()

    lines = [
        "# The committed BPI 2019 snapshot: what it is and how it differs",
        "",
        "Generated by `logs/sample.py`. Do not edit by hand -- re-run the script.",
        "",
        "## Provenance",
        "",
        "van Dongen, Boudewijn (2019): *BPI Challenge 2019*. Version 1. 4TU.ResearchData.",
        "<https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1>",
        "Licensed **CC BY 4.0**, which permits this redistribution with attribution.",
        "",
        "**Changes made to the original:** a deterministic stratified subset of cases;",
        "conversion from IEEE XES to the Matchbook canonical schema; monetary values",
        "carried as integer cents; and deterministic tie-breaking of equal timestamps,",
        "audited per event. No field value was altered.",
        "",
        "## How the sample was chosen",
        "",
        f"A case is selected by the rank of `sha256(\"{SALT}\" + case_id)` within its",
        f"`{FLOW_ATTRIBUTE}` stratum. Allocation is proportional to each flow's share of the",
        "full log, with a floor so rare flows still appear. Deterministic: the same input",
        "yields the same sample, and the selection is checkable without this script.",
        "",
        "## Full log versus sample",
        "",
        "| | Full log | Sample |",
        "| --- | --- | --- |",
        f"| Cases | {total_cases:,} | {sample_log.case_count:,} |",
        f"| Events | {total_events:,} | {sample_log.event_count:,} |",
        f"| Activities | {len(stats['activities']):,} | {sample_log.activity_count:,} |",
        f"| Distinct variants | {len(stats['variants']):,} | {len(sample_variants):,} |",
        f"| Top-20 variant coverage | {_variant_coverage(stats['variants'], total_cases):.1%} | "
        f"{_variant_coverage(sample_variants, sample_log.case_count):.1%} |",
        f"| Vendors | {len(stats['vendors']):,} | - |",
        f"| Companies | {len(stats['companies']):,} | - |",
        f"| Events at day precision | {stats['day_precision_events'] / max(total_events, 1):.1%} | "
        f"{len(sample_log.precision) / max(sample_log.event_count, 1):.1%} |",
        f"| Tie-broken events | (not computed on the full log) | "
        f"{len(sample_log.tie_broken) / max(sample_log.event_count, 1):.1%} |",
        "",
        "### Flow mix -- where the floor distorts the sample",
        "",
        "| Flow | Full log cases | Full % | Sample cases | Sample % |",
        "| --- | --- | --- | --- | --- |",
    ]
    for flow, count in stats["flows"].most_common():
        sampled = sample_flows.get(flow, 0)
        lines.append(
            f"| {flow} | {count:,} | {count / total_cases:.1%} | {sampled:,} | "
            f"{sampled / max(sample_log.case_count, 1):.1%} |"
        )

    lines += [
        "",
        "### Activity frequency, top 15",
        "",
        "| Activity | Full log | Full % | Sample | Sample % |",
        "| --- | --- | --- | --- | --- |",
    ]
    for activity, count in stats["activities"].most_common(15):
        sampled = sample_activities.get(activity, 0)
        lines.append(
            f"| {activity} | {count:,} | {count / total_events:.2%} | {sampled:,} | "
            f"{sampled / max(sample_log.event_count, 1):.2%} |"
        )

    lines += [
        "",
        "### Resource kinds",
        "",
        "| Kind | Full-log events | Share |",
        "| --- | --- | --- |",
    ]
    for kind, count in stats["resource_kinds"].most_common():
        lines.append(f"| {kind} | {count:,} | {count / total_events:.1%} |")

    lines += [
        "",
        "## How to read this",
        "",
        "The sample is for development, tests and demos. Any claim meant to hold for the",
        "real process must be re-run against the full log, fetched with",
        "`python -m logs.download --log bpic19`. The rare flows are over-represented here",
        "by design, so flow-weighted aggregates computed on the sample are biased -- weight",
        "by the full-log shares in the table above, or filter to one flow and compare within it.",
        "",
    ]
    destination.write_text("\n".join(lines))
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="logs/raw/BPI_Challenge_2019.xes")
    parser.add_argument("--out-dir", default="logs/snapshot")
    parser.add_argument("--target-cases", type=int, default=2000)
    parser.add_argument("--floor-per-flow", type=int, default=120)
    parser.add_argument(
        "--candidates-per-flow",
        type=int,
        default=None,
        help="candidate pool per flow; defaults to --target-cases, which is the"
        " largest any single flow can be allocated",
    )
    parser.add_argument("--store", default="build/eventstore.db")
    args = parser.parse_args(argv)

    source = REPO_ROOT / args.source
    if not source.exists():
        raise SystemExit(
            f"{source} is missing. Fetch it first:\n"
            "  python -m logs.download --log bpic19"
        )
    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"scanning {source.name} ({source.stat().st_size / 1e6:.0f} MB) ...", flush=True)
    candidates_per_flow = args.candidates_per_flow or args.target_cases
    if candidates_per_flow < args.target_cases:
        raise SystemExit(
            f"--candidates-per-flow {candidates_per_flow} is below --target-cases "
            f"{args.target_cases}; the dominant flow would be truncated to the pool size"
        )
    stats, selected = scan_and_select(
        source,
        target_cases=args.target_cases,
        floor_per_flow=args.floor_per_flow,
        candidates_per_flow=candidates_per_flow,
    )
    print(
        f"full log: {stats['cases']:,} cases / {stats['events']:,} events / "
        f"{len(stats['activities'])} activities / {len(stats['variants']):,} variants",
        flush=True,
    )
    print(f"selected {stats['selected_cases']:,} cases: {stats['allocation']}", flush=True)
    if stats["short_falls"]:
        raise SystemExit(
            "the candidate pool could not satisfy the allocation for "
            f"{stats['short_falls']}; raise --candidates-per-flow"
        )

    log = build_log(selected)
    events_path = csvio.write_csv(log, out_dir / "bpic19-sample-events.csv.gz")
    attributes_path = csvio.write_case_attributes(log, out_dir / "bpic19-sample-cases.csv.gz")
    disclosure = write_disclosure(stats, log, out_dir / "SAMPLE.md")
    store.save(
        log,
        REPO_ROOT / args.store,
        doi="10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1",
        notes="Deterministic stratified subset built by logs/sample.py.",
        resource_kind=xes.classify_resource,
    )

    manifest = {
        "salt": SALT,
        "target_cases": args.target_cases,
        "floor_per_flow": args.floor_per_flow,
        "allocation": stats["allocation"],
        "candidates_per_flow": candidates_per_flow,
        "full_log": {
            "cases": stats["cases"],
            "events": stats["events"],
            "activities": len(stats["activities"]),
            "variants": len(stats["variants"]),
        },
        "sample": log.summary(),
    }
    (out_dir / "sample-manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    for path in (events_path, attributes_path, disclosure):
        print(f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
