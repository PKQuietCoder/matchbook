"""What the agent's process cost in tokens, and where the money went.

Process mining says which paths the agent takes; this says what each path is
worth. Put together, an expensive rework loop in the mined map has a price next
to it, which is the whole argument for looking at the two together.

This lives in `bridge/` for the same reason `spans_to_log.py` does: it is the
only layer allowed to see both halves of the repo. Tokens are recorded on
spans, activities are a property of the mined log, and joining them is
boundary-crossing work. `process/` stays free of it, so the mining half keeps
running with no key and no agent dependencies.

**How tokens reach a business activity.** They do not, directly. Only model
spans carry tokens; only tool spans carry an activity. The two are joined on
`(run_id, step)`, because the tool calls at step N are exactly the ones the
model call at step N requested. So a step's tokens are the price of deciding to
do what that step did.

**The one assumption, stated rather than hidden.** When a single step requests
several business activities, its tokens are split equally between them. There
is no provider signal for "this activity cost more of the prompt than that
one", and inventing a weighting would be worse than dividing. Every report
counts how many steps were split, the same way the ingest reports how many
events were tie-broken -- if that count is zero the figures carry no assumption
at all. Steps that requested only lookups, and the final reply step that
requested nothing, are reported in their own buckets rather than smeared across
the activities, because deliberation-versus-action is exactly the split a cost
reader is looking for.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.spans import MODEL, TOOL, connect  # noqa: E402
from process import config  # noqa: E402

# Price per million tokens, in USD, by model id. Input and output are published
# rates; the cache multipliers are the standard Claude ones -- a cache read
# costs a tenth of the full input rate, a cache write costs 1.25x, because the
# write has to do the work of reading plus storing. Verify against
# https://www.anthropic.com/pricing before quoting a figure to a customer:
# these are constants in a repo, not a live feed.
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# Buckets for token spend that no business activity can claim.
LOOKUP_ONLY = "(lookup only)"
FINAL_REPLY = "(final reply)"
UNATTRIBUTED = "(unattributed)"

ZERO = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}


def price_of(model: str) -> tuple[float, float] | None:
    """Published rates for a model id, or None when it is not in the table.

    A scripted run's model is `scripted:<name>`, which has no price and should
    report no cost rather than a misleading zero-dollar one.
    """
    if model in PRICES:
        return PRICES[model]
    # LiteLLM-style and provider-prefixed ids resolve to the same model.
    tail = model.rsplit("/", 1)[-1]
    return PRICES.get(tail)


def usd(model: str, tokens: dict[str, int]) -> float | None:
    """Cost of a token bundle under one model's rates, or None if unpriced."""
    price = price_of(model)
    if price is None:
        return None
    input_rate, output_rate = price
    return (
        tokens.get("input_tokens", 0) * input_rate
        + tokens.get("cache_read_tokens", 0) * input_rate * CACHE_READ_MULTIPLIER
        + tokens.get("cache_write_tokens", 0) * input_rate * CACHE_WRITE_MULTIPLIER
        + tokens.get("output_tokens", 0) * output_rate
    ) / 1_000_000


def _add(target: dict[str, int], row: Any) -> None:
    for key in ZERO:
        target[key] = target.get(key, 0) + (row[key] or 0)


def _steps(connection: sqlite3.Connection, run_id: str | None) -> dict[str, Any]:
    """Model-call tokens and tool outcomes per (run, step), plus run models."""
    run_filter = " AND run_id = ?" if run_id else ""
    scope = [run_id] if run_id else []

    models: dict[str, str] = {
        row["run_id"]: row["model"]
        for row in connection.execute(
            "SELECT run_id, model FROM runs WHERE 1=1" + run_filter, scope
        )
    }

    def spans_of(kind: str):
        return connection.execute(
            "SELECT * FROM spans WHERE kind = ?" + run_filter, [kind, *scope]
        )

    spend: dict[tuple[str, int], dict[str, int]] = defaultdict(dict)
    for row in spans_of(MODEL):
        _add(spend[(row["run_id"], row["step"])], row)

    # Tool spans give each step its activities and its business objects. Only a
    # successful write carries an activity, which is what makes refused and
    # queued attempts visible as deliberation cost rather than as process steps.
    activities: dict[tuple[str, int], list[str]] = defaultdict(list)
    items: dict[tuple[str, int], list[str]] = defaultdict(list)
    run_items: dict[str, set[str]] = defaultdict(set)
    # Tracked on its own rather than inferred from the two dicts below, because
    # a tool can be neither: `get_policy` takes no item_key and is not a
    # business activity, so a step that only cited policy would otherwise look
    # like a step that called no tool at all -- and be priced as the final reply.
    tool_steps: set[tuple[str, int]] = set()
    for row in spans_of(TOOL):
        key = (row["run_id"], row["step"])
        tool_steps.add(key)
        if row["activity"]:
            activities[key].append(row["activity"])
        if row["item_key"]:
            if row["item_key"] not in items[key]:
                items[key].append(row["item_key"])
            run_items[row["run_id"]].add(row["item_key"])

    return {
        "models": models,
        "spend": dict(spend),
        "activities": dict(activities),
        "items": dict(items),
        "run_items": dict(run_items),
        "tool_steps": tool_steps,
    }


def run_totals(spans_path: str | Path, run_id: str) -> dict[str, Any]:
    """Token totals and cost for one run. Empty dict when it spent nothing."""
    connection = connect(spans_path)
    try:
        data = _steps(connection, run_id)
        totals: dict[str, int] = {}
        for tokens in data["spend"].values():
            for key, value in tokens.items():
                totals[key] = totals.get(key, 0) + value
        if not any(totals.values()):
            return {}
        model = data["models"].get(run_id, "")
        result: dict[str, Any] = {**ZERO, **totals, "model": model, "steps": len(data["spend"])}
        result["usd"] = usd(model, totals) or 0.0
        return result
    finally:
        connection.close()


def by_activity(spans_path: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """Token spend per business activity, with the split assumption disclosed."""
    connection = connect(spans_path)
    try:
        data = _steps(connection, run_id)
        rows: dict[str, dict[str, int]] = defaultdict(lambda: dict(ZERO))
        cost: dict[str, float] = defaultdict(float)
        split_steps = 0

        for (run, step), tokens in sorted(data["spend"].items()):
            model = data["models"].get(run, "")
            names = data["activities"].get((run, step))
            if names:
                if len(names) > 1:
                    split_steps += 1
                targets = names
            elif (run, step) in data["tool_steps"]:
                targets = [LOOKUP_ONLY]
            else:
                targets = [FINAL_REPLY]

            share = len(targets)
            for name in targets:
                for key, value in tokens.items():
                    rows[name][key] += value // share
                amount = usd(model, tokens)
                if amount is not None:
                    cost[name] += amount / share

        return {
            "rows": {
                name: {**counts, "usd": cost[name]}
                for name, counts in sorted(rows.items())
            },
            "split_steps": split_steps,
        }
    finally:
        connection.close()


def by_case(spans_path: str | Path, run_id: str | None = None) -> dict[str, Any]:
    """Token spend per business object -- the case identifier the bridge uses."""
    connection = connect(spans_path)
    try:
        data = _steps(connection, run_id)
        rows: dict[str, dict[str, int]] = defaultdict(lambda: dict(ZERO))
        cost: dict[str, float] = defaultdict(float)
        split_steps = 0

        for (run, step), tokens in sorted(data["spend"].items()):
            model = data["models"].get(run, "")
            touched = data["items"].get((run, step))
            if not touched:
                # A step that called no tool -- the final reply, or a refusal.
                # If the whole run only ever touched one item, it belongs to
                # that item; otherwise there is no honest way to assign it.
                whole_run = data["run_items"].get(run, set())
                touched = [next(iter(whole_run))] if len(whole_run) == 1 else [UNATTRIBUTED]
            elif len(touched) > 1:
                split_steps += 1

            share = len(touched)
            for item in touched:
                for key, value in tokens.items():
                    rows[item][key] += value // share
                amount = usd(model, tokens)
                if amount is not None:
                    cost[item] += amount / share

        return {
            "rows": {
                item: {**counts, "usd": cost[item]}
                for item, counts in sorted(rows.items())
            },
            "split_steps": split_steps,
        }
    finally:
        connection.close()


def by_variant(
    spans_path: str | Path,
    store_path: str | Path,
    log_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Token spend per mined variant: what each distinct path through the
    process actually costs to execute.

    Needs the event store because a variant is a property of the mined log, not
    of the spans. Cases the log does not contain are reported separately rather
    than dropped -- a case the agent touched but the business layer excluded
    (because every attempt was refused, say) is a real and interesting cost.
    """
    from process import store as event_store

    cases = by_case(spans_path, run_id)
    log = event_store.load(store_path, log_id)

    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {**ZERO, "usd": 0.0, "cases": 0})
    missing: dict[str, Any] = {**ZERO, "usd": 0.0, "cases": 0}

    for case_id, counts in cases["rows"].items():
        try:
            trace = log.trace(case_id)
        except (KeyError, ValueError):
            target = missing
        else:
            name = " -> ".join(log.named_variant(log.variant_of(trace))) or "(empty)"
            target = rows[name]
        for key in ZERO:
            target[key] += counts[key]
        target["usd"] += counts["usd"]
        target["cases"] += 1

    return {
        "rows": dict(sorted(rows.items(), key=lambda kv: -kv[1]["usd"])),
        "not_in_log": missing,
        "split_steps": cases["split_steps"],
    }


# -- command line ------------------------------------------------------------

def _table(title: str, rows: dict[str, dict[str, Any]], label: str) -> bool:
    """Print one table. Returns False when there was nothing to price.

    A store holding only scripted runs has rows but no tokens. Printing a
    column of zeros would suggest the runs were free rather than unmeasured,
    so say which it is.
    """
    print(f"\n{title}")
    spent = any(
        counts["input_tokens"] or counts["output_tokens"] or counts["cache_read_tokens"]
        for counts in rows.values()
    )
    if not rows or not spent:
        print(
            "  (no tokens recorded -- every run in this store is scripted. Run with"
            "\n   --model claude-sonnet-5 to spend real tokens and get real figures.)"
        )
        return False
    width = max(len(label), max(len(name) for name in rows))
    width = min(width, 60)
    print(
        f"  {label:<{width}}  {'in':>9} {'cached':>9} {'out':>9} {'USD':>10}"
    )
    for name, counts in rows.items():
        shown = name if len(name) <= width else name[: width - 1] + "…"
        print(
            f"  {shown:<{width}}  {counts['input_tokens']:>9,} "
            f"{counts['cache_read_tokens']:>9,} {counts['output_tokens']:>9,} "
            f"{counts['usd']:>10.4f}"
        )
    return True


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spans", default=str(repo_root / "build" / "spans.db"))
    parser.add_argument("--run", help="limit to one run id")
    parser.add_argument(
        "--by",
        default="activity",
        choices=("activity", "case", "variant", "run"),
        help="what to group by (default: activity)",
    )
    parser.add_argument("--store", default=str(config.store_path()))
    parser.add_argument("--log-id", default="agent-business", help="for --by variant")
    args = parser.parse_args(argv)

    if not Path(args.spans).exists():
        parser.error(f"no span store at {args.spans}; run `python -m agent --script ...` first")

    if args.by == "run":
        connection = connect(args.spans)
        try:
            run_ids = [row["run_id"] for row in connection.execute("SELECT run_id FROM runs ORDER BY started_at")]
        finally:
            connection.close()
        rows = {}
        for run_id in run_ids:
            totals = run_totals(args.spans, run_id)
            if totals:
                rows[f"{run_id[:8]} {totals['model']}"] = totals
        _table("token spend per run", rows, "run")
        return 0  # the helper already explains an empty result

    if args.by == "activity":
        report = by_activity(args.spans, args.run)
        priced = _table("token spend per business activity", report["rows"], "activity")
    elif args.by == "case":
        report = by_case(args.spans, args.run)
        priced = _table("token spend per case (purchase-order item)", report["rows"], "case")
    else:
        try:
            report = by_variant(args.spans, args.store, args.log_id, args.run)
        except KeyError as exc:
            parser.error(
                f"{exc.args[0]}\n"
                f"Build it first:  python -m bridge.spans_to_log {args.spans} "
                f"--log-id {args.log_id} --layer business"
            )
        priced = _table("token spend per mined variant", report["rows"], "variant")
        if priced and report["not_in_log"]["cases"]:
            print(
                f"\n  {report['not_in_log']['cases']} case(s) spent "
                f"${report['not_in_log']['usd']:.4f} but appear in no variant of "
                f"{args.log_id!r} -- the agent touched them without completing a "
                "business activity."
            )

    if not priced:
        return 0

    split = report["split_steps"]
    if split:
        print(
            f"\n  Assumption: {split} step(s) requested more than one thing, and their "
            "tokens were divided equally. There is no provider signal for a better split."
        )
    else:
        print("\n  No step requested more than one thing, so no tokens were split.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
