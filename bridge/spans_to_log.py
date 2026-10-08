"""Spans to event log: the converter that did not previously exist.

The agent emits spans; process mining needs an event log. Turning one into the
other is the whole bridge, and two decisions in it determine whether anything
downstream means anything.

**The case notion.** A conversation is not a case. The obvious choice --
`run_id`, one case per conversation -- produces a log whose cases are
conversations, which can never be compared with a log whose cases are purchase
order items. So the case identifier is the **business object**: the
purchase-order item the span acted on, carried as `item_key`. One conversation
touching two items contributes to two cases; two conversations about one item
contribute to one. That is what makes the agent's log and BPI 2019's log the
same kind of object.

**Activity abstraction.** Not every span is a business activity. A policy
lookup changes nothing in the purchase-to-pay process, and an LLM call is not a
process step at all. `bridge/activity_map.yaml` lifts only the state-changing
tools onto the human log's alphabet; everything else goes to a separate
`internal` layer. Mixing the two is the most common way this comparison gets
quietly broken.

Three layers come out of the same spans, and each answers a different question:

  business  -- what happened in the process. Comparable to the human log.
  attempts  -- business, plus refused/paused/queued attempts as distinct
               activities. This is where escalation avoidance becomes a path.
  internal  -- every span including lookups and model steps. The agent's own
               deliberation, for studying the agent rather than the process.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability.spans import SpanStore  # noqa: E402
from process import config, store  # noqa: E402
from process.log import AGENT, TOOL as TOOL_KIND, EventLog, EventLogBuilder  # noqa: E402

MAP_PATH = Path(__file__).resolve().parent / "activity_map.yaml"

BUSINESS = "business"
ATTEMPTS = "attempts"
INTERNAL = "internal"
LAYERS = (BUSINESS, ATTEMPTS, INTERNAL)


def load_map(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or MAP_PATH).read_text())


def _parse(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


def _microseconds(stamp: str) -> int:
    """The sub-second part, so same-second agent events keep their true order.

    BPI 2019's intra-minute order is genuinely unknown and is disclosed as
    assumed. The agent's is known to the microsecond, and reporting it as
    assumed would overstate the uncertainty in exactly the comparison this
    repo cares about.
    """
    return datetime.fromisoformat(stamp).microsecond


def item_flows(item_keys: set[str]) -> dict[str, dict[str, Any]]:
    """Look up each item's business attributes from the world.

    Without this the agent's log carries no `Item Category`, and every control
    rule scoped by matching flow -- CTRL-GR among them, the most important one
    -- silently has an in-scope population of zero. The claim that one rule
    definition judges the humans and the agent alike is only true if the
    agent's log carries the attributes those rules read.

    The world is the source, not the span: the flow is a property of the
    purchase-order item, not of the call that touched it.
    """
    if not item_keys:
        return {}
    try:
        from agent import db
    except ImportError:  # the mining half must not require the agent package
        return {}
    found: dict[str, dict[str, Any]] = {}
    try:
        with db.connection() as conn:
            for item_key in sorted(item_keys):
                item = db.get_item(conn, item_key)
                if item is None:
                    continue
                found[item_key] = {
                    "Item Category": item["flow"],
                    "Item Type": item["item_type"],
                    "Vendor": item["vendor_id"],
                    "Company": item["company_code"],
                    "Purchasing Document": item["po_number"],
                    "Item": item["item_no"],
                }
    except Exception as exc:  # a missing world is not a reason to lose the log
        print(
            f"warning: could not read item attributes from the world ({exc}); the agent's "
            "log will carry no Item Category, and flow-scoped control rules will report "
            "themselves unevaluable rather than silently finding nothing.",
            file=sys.stderr,
        )
        return {}
    return found


def _outcome(span: dict[str, Any]) -> str:
    """What became of a write attempt: done, queued, refused, or paused."""
    result = span["attributes"].get("result") or {}
    if span.get("ok"):
        return "queued" if result.get("status") == "queued_for_approval" else "done"
    if result.get("error") == "paused":
        return "paused"
    return "refused"


def convert(
    span_store: SpanStore,
    *,
    log_id: str,
    layer: str = BUSINESS,
    run_id: str | None = None,
    activity_map: dict[str, Any] | None = None,
    warn_if_empty: bool = True,
) -> EventLog:
    """Build one canonical event log from captured spans."""
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {', '.join(LAYERS)}")
    mapping = activity_map or load_map()
    tool_activities: dict[str, str] = mapping["tool_activities"]
    attempt_activities: dict[str, str] = mapping["attempt_activities"]
    queued_activities: dict[str, str] = mapping.get("queued_activities") or {}

    builder = EventLogBuilder(
        log_id,
        activity_rank=config.activity_rank(),
        source=f"spans:{span_store.path.name} layer={layer}",
        license="",
        attribution="Agent-generated; derived from Matchbook span capture.",
    )

    runs = {row["run_id"]: row for row in span_store.runs()}
    spans = span_store.all_spans(run_id)
    # Resolve the business attributes once, not per span.
    flows = item_flows({span["item_key"] for span in spans if span["item_key"]})
    kept = 0
    for span in spans:
        item_key = span.get("item_key")
        name = span["name"]
        attributes = span["attributes"]
        run = runs.get(span["run_id"], {})

        if layer == INTERNAL:
            # Everything, keyed by the business object where there is one and by
            # the run otherwise, so model steps are not silently dropped.
            case_id = item_key or f"run:{span['run_id'][:8]}"
            activity = name if span["kind"] != "model" else "Agent Deliberation"
            resource = f"{AGENT}:{run.get('model', 'unknown')}" if span["kind"] == "model" else f"{TOOL_KIND}:{name}"
        else:
            if not item_key:
                continue  # no business object: not a case in this view
            if span["kind"] != "tool":
                continue
            business = tool_activities.get(name)
            if business is None:
                continue  # a read tool: not a process step
            outcome = _outcome(span)
            if outcome == "done":
                activity = business
            elif layer == ATTEMPTS and outcome == "queued":
                activity = queued_activities.get(name, f"{business} Queued for Approval")
            elif layer == ATTEMPTS:
                activity = attempt_activities.get(name, f"Attempted {business}")
            else:
                continue  # business layer records only what actually happened
            case_id = item_key
            resource = f"{AGENT}:{run.get('model', 'unknown')}"

        builder.add_case_attributes(
            case_id,
            {
                **flows.get(item_key or "", {}),
                "source": "agent",
                "actor_id": run.get("actor_id", ""),
                "role": run.get("role", ""),
                "model": run.get("model", ""),
                "prompt_version": run.get("prompt_version", ""),
                "scenario_id": run.get("scenario_id") or "",
                "killswitch": run.get("killswitch", "off"),
            },
        )
        builder.add(
            case_id=case_id,
            activity=activity,
            timestamp=_parse(span["started_at"]),
            sort_hint=_microseconds(span["started_at"]),
            resource=resource,
            value_cents=int(span.get("value_cents") or 0),
            attributes={
                "span_id": span["span_id"],
                "run_id": span["run_id"],
                "tool": name,
                "ok": bool(span.get("ok")),
                "error": span.get("error"),
                "outcome": _outcome(span) if span["kind"] == "tool" else "",
                "step": span["step"],
            },
        )
        kept += 1

    log = builder.build()
    if not kept and warn_if_empty:
        print(
            "warning: no spans contributed events. For the business layer this means no "
            "write tool succeeded -- which is a finding, not necessarily a bug.",
            file=sys.stderr,
        )
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spans", help="path to the span database")
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--layer", choices=LAYERS, default=BUSINESS)
    parser.add_argument("--run")
    parser.add_argument("--store", default=str(config.store_path()))
    args = parser.parse_args(argv)

    span_store = SpanStore(args.spans)
    try:
        log = convert(span_store, log_id=args.log_id, layer=args.layer, run_id=args.run)
    finally:
        span_store.close()
    store.save(
        log,
        args.store,
        notes=f"Derived from {args.spans} (layer={args.layer}) by bridge/spans_to_log.py.",
        resource_kind=lambda name: AGENT if name.startswith("agent:") else TOOL_KIND,
    )
    import json

    print(json.dumps(log.summary(), indent=1, default=str))
    for trace in log.traces():
        print(f"  {trace.case_id:<24} {' -> '.join(trace.activities)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
