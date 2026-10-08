"""`python -m process <command>` -- the mining library's command line.

Every command here runs with no API key, no model, and no agent dependencies
installed. That is enforced by a test, because the moment the mining half needs
a key it stops being usable as teaching material.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from process import config, csvio, dfg, filters, rules, store, variants, viz, xes
from process.log import EventLog

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(args) -> EventLog:
    log = store.load(args.store, args.log_id)
    if getattr(args, "flow", None):
        log = filters.by_flow(log, args.flow)
    if getattr(args, "coverage", None):
        log = filters.by_variant_coverage(log, args.coverage)
    return log


def _read_source(args, rank: dict[str, int] | None, *, progress: bool = True) -> EventLog:
    """Read the source with a given ranking. Called twice for --sensitivity."""
    source = Path(args.path)
    attribution = args.attribution or ""
    if source.suffix in (".xes", ".gz") and ".csv" not in source.name:
        log = xes.read_xes(
            source,
            log_id=args.log_id,
            activity_rank=rank,
            license=args.license,
            attribution=attribution,
            max_cases=args.max_cases,
            # The full log is 728 MB and takes minutes; silence looks like a hang.
            on_progress=(
                (lambda seen, kept: print(f"  {seen:,} read / {kept:,} kept", flush=True))
                if progress
                else None
            ),
        )
    else:
        columns = dict(csvio.HELPDESK_COLUMNS) if args.columns == "helpdesk" else None
        log = csvio.read_csv(
            source,
            log_id=args.log_id,
            columns=columns,
            activity_rank=rank,
            license=args.license,
            attribution=attribution,
        )
        if args.case_attributes:
            csvio.read_case_attributes(log, args.case_attributes)
    return log


def cmd_ingest(args) -> int:
    rank = config.activity_rank()
    log = _read_source(args, rank)
    store.save(log, args.store, doi=args.doi, resource_kind=xes.classify_resource)
    print(json.dumps(log.summary(), indent=1))

    if args.sensitivity:
        # The baseline must come from an UNRANKED read of the source. Deriving
        # it from the log we just built would compare the ranked order against
        # itself and report a reassuring zero.
        baseline = _read_source(args, None, progress=False)
        measurement = variants.order_sensitivity(
            variants.events_of(baseline), log_id=args.log_id, activity_rank=rank
        )
        print("\nwhat the declared tie-break costs:")
        print(
            f"  tie-broken events              {measurement['tie_broken_events']:,} "
            f"({measurement['tie_broken_share']:.1%})\n"
            f"  ...of which consequential      {measurement['consequential_ties']:,} "
            f"({measurement['consequential_tie_share']:.1%}) -- a tie between two events of "
            f"the same activity cannot change anything\n"
            f"  variants under arrival order   {measurement['variants_arrival_order']:,}\n"
            f"  variants under declared order  {measurement['variants_declared_order']:,}\n"
            f"  collapsed by the ranking       {measurement['variants_collapsed']:,}\n"
            f"  cases whose sequence changed   {measurement['cases_whose_sequence_changed']:,}"
        )
        if measurement["tie_broken_events"] and not measurement["variants_collapsed"]:
            print(
                "\nNote: ties exist but the ranking changed nothing, which usually means "
                "this source is ALREADY in ranked order -- a file Matchbook wrote, rather "
                "than the original export. Measure against the original source to get a "
                "meaningful figure."
            )
        elif measurement["variants_collapsed"]:
            print(
                "\nThe ranking is not cosmetic: it changes the variant structure, so a "
                "discovered model or a fitness number is partly a consequence of it. Say "
                "which ordering a result used."
            )
    return 0


def cmd_logs(args) -> int:
    rows = store.list_logs(args.store)
    if not rows:
        print(f"no logs in {args.store}; ingest one with `python -m process ingest`")
        return 0
    for row in rows:
        print(
            f"{row['log_id']:<22} {row['case_count']:>8,} cases  {row['event_count']:>9,} events  "
            f"{row['license'] or '(license unrecorded)':<12}  tie-broken {row['tie_broken']:,}"
        )
    return 0


def cmd_summary(args) -> int:
    log = _load(args)
    payload = log.summary() | {"variants": variants.summary(log)}
    print(json.dumps(payload, indent=1, default=str))
    return 0


def cmd_dfg(args) -> int:
    log = _load(args)
    graph = dfg.build(log)
    if args.keep_fraction:
        graph = graph.filter_edges(keep_fraction=args.keep_fraction)
    if args.out:
        path = Path(args.out)
        if path.suffix == ".svg":
            viz.write_svg(graph, path, title=f"{log.log_id} ({args.annotate})")
        else:
            viz.write_dot(graph, path, annotate=args.annotate)
        print(f"wrote {path}")
    print(f"{len(graph.activities)} activities, {len(graph)} edges, {graph.case_count:,} cases")
    print("\ntop transitions")
    for source, target, stats in graph.top_edges(args.top):
        print(f"  {source:<34} -> {target:<34} n={stats.count:>7,}  median {stats.median_seconds / 86400:>7.1f}d")
    print("\nbottlenecks by total waiting time")
    for source, target, stats in graph.bottlenecks(args.top):
        print(f"  {source:<34} -> {target:<34} total {stats.total_seconds / 86400:>9,.0f}d  n={stats.count:,}")
    return 0


def cmd_variants(args) -> int:
    log = _load(args)
    rows = variants.variant_table(log)
    print(f"{len(rows):,} variants over {log.case_count:,} cases")
    print(json.dumps(variants.coverage_curve(log), indent=1))
    for row in rows[: args.top]:
        print(
            f"\n{row.case_count:>6,} cases ({row.case_share:>6.1%}, cum {row.cumulative_share:>6.1%})"
            f"  median {row.median_duration_seconds / 86400:.1f}d  e.g. {row.example_case_ids[0]}"
        )
        print("       " + " -> ".join(row.activities))
    return 0


def cmd_rules(args) -> int:
    log = _load(args)
    policy = config.load_facts()
    rule_ids = args.rule or None
    payload = rules.report(log, policy, rule_ids=rule_ids)
    print(json.dumps(payload, indent=1))
    if args.out:
        violations, inapplicable = rules.evaluate(log, policy, rule_ids=rule_ids)
        for rule_id, reason in sorted(inapplicable.items()):
            print(f"\n{rule_id} could NOT be evaluated on this log:\n  {reason}")
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["rule_id", "case_id", "summary", "at_seq", "order_assumed"]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for violation in violations:
                writer.writerow(violation.as_record())
        print(f"wrote {len(violations):,} violations to {path}")
    return 0


def cmd_tie_breaks(args) -> int:
    """Report where event order was decided by the declared tie-break."""
    log = _load(args)
    affected = log.tie_broken_cases()
    share = len(log.tie_broken) / log.event_count if log.event_count else 0
    print(
        f"{len(log.tie_broken):,} of {log.event_count:,} events ({share:.1%}) were ordered by "
        f"facts.yaml activity_rank rather than by recorded time"
    )
    print(f"{len(affected):,} of {log.case_count:,} cases affected")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["case_id"])
            for case_id in affected:
                writer.writerow([case_id])
        print(f"wrote {path}")
    print("\nAny conformance result over these cases depends on that declared ordering.")

    print(
        "To measure what that ordering costs, re-ingest the source with "
        "`process ingest <source> --sensitivity`: it cannot be measured from the store, "
        "whose stored order is already the ranked one."
    )
    return 0


def cmd_compare(args) -> int:
    """Diff two logs' DFGs -- the human process against the agent's."""
    left = store.load(args.store, args.left)
    right = store.load(args.store, args.right)
    left_graph = dfg.build(left)
    right_graph = dfg.build(right)
    rows = dfg.diff(left_graph, right_graph)
    print(f"{args.left}: {left.case_count:,} cases / {len(left_graph)} edges")
    print(f"{args.right}: {right.case_count:,} cases / {len(right_graph)} edges")
    shared = sum(1 for row in rows if not row["only_in"])
    print(f"shared edges: {shared}  only in {args.left}: "
          f"{sum(1 for r in rows if r['only_in'] == 'left')}  only in {args.right}: "
          f"{sum(1 for r in rows if r['only_in'] == 'right')}")

    # Guard the methodological trap: two logs that share no activity names
    # produce a tidy-looking table of 100% differences that means nothing. The
    # usual cause is comparing the agent's `attempts` layer -- which invents
    # activity names on purpose -- against the human log.
    left_names = {left.activities.name_of(a) for a in left_graph.activities}
    right_names = {right.activities.name_of(a) for a in right_graph.activities}
    overlap = left_names & right_names
    if not overlap:
        print(
            "\nWARNING: these logs share no activity names, so every row below is "
            "'only in' one side and the comparison carries no information. Compare the "
            "agent's BUSINESS layer against the human log -- the attempts layer adds "
            "activity names ('Attempted ...') that the human log cannot contain by design."
        )
    else:
        print(
            f"shared activity alphabet: {len(overlap)} of "
            f"{len(left_names | right_names)} names"
        )
    print()
    print(f"{'transition':<60} {args.left[:12]:>12} {args.right[:12]:>12}  delta")
    for row in rows[: args.top]:
        label = f"{row['from']} -> {row['to']}"
        marker = {"left": "  (human only)", "right": "  (agent only)"}.get(row["only_in"], "")
        print(
            f"{label[:60]:<60} {row['left_share']:>11.1%} {row['right_share']:>12.1%}"
            f"  {row['share_delta']:+.1%}{marker}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="process", description=__doc__)
    parser.add_argument("--store", default=str(config.store_path()))
    sub = parser.add_subparsers(dest="command", required=True)

    def with_log(p, *, filterable: bool = True):
        p.add_argument("log_id")
        if filterable:
            p.add_argument("--flow", help="restrict to one Item Category value")
            p.add_argument("--coverage", type=float, help="keep variants covering this fraction")
        return p

    ingest = sub.add_parser("ingest", help="read XES/CSV into the store")
    ingest.add_argument("path")
    ingest.add_argument("--log-id", required=True)
    ingest.add_argument("--license", default="")
    ingest.add_argument("--attribution", default="")
    ingest.add_argument("--doi", default="")
    ingest.add_argument("--columns", choices=["canonical", "helpdesk"], default="canonical")
    ingest.add_argument("--case-attributes", help="CSV of per-case attributes")
    ingest.add_argument("--max-cases", type=int)
    ingest.add_argument(
        "--sensitivity",
        action="store_true",
        help="measure how much facts.yaml activity_rank changes the variant structure",
    )
    ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("logs", help="list logs in the store").set_defaults(func=cmd_logs)

    with_log(sub.add_parser("summary", help="counts, variants, rework")).set_defaults(func=cmd_summary)

    graph = with_log(sub.add_parser("dfg", help="directly-follows graph"))
    graph.add_argument("--out", help=".dot or .svg")
    graph.add_argument("--annotate", choices=["frequency", "performance"], default="frequency")
    graph.add_argument("--keep-fraction", type=float)
    graph.add_argument("--top", type=int, default=10)
    graph.set_defaults(func=cmd_dfg)

    variant = with_log(sub.add_parser("variants", help="variant table and coverage"))
    variant.add_argument("--top", type=int, default=5)
    variant.set_defaults(func=cmd_variants)

    rule = with_log(sub.add_parser("rules", help="declarative control conformance"))
    rule.add_argument("--rule", action="append", help="repeatable; default is all")
    rule.add_argument("--out", help="write violations to CSV")
    rule.set_defaults(func=cmd_rules)

    ties = with_log(sub.add_parser("tie-breaks", help="audit assumed event order"))
    ties.add_argument("--out")

    ties.set_defaults(func=cmd_tie_breaks)

    compare = sub.add_parser("compare", help="diff two logs' process maps")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument("--top", type=int, default=15)
    compare.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
