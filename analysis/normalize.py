"""One normalized trace record, consumed by review, judges and monitoring.

Oakline's Module 2 works off a single normalized shape, and every component
downstream depends on it, so Matchbook keeps the same contract and adds the
fields this domain makes available:

  - `features.process` -- what the run did to the *process*, not just what it
    said: the business object it touched, the activities it actually performed,
    the attempts it made, and whether it retried a refused write. Those are the
    signals a transcript reviewer cannot reliably see.
  - `features.control` -- whether the run's own events break a control rule.

The point of putting process features on the trace record is that a reviewer
sorting traces by `retried_after_refusal` finds escalation avoidance in
minutes, where reading transcripts in random order takes hours and usually
misses it.
"""

from __future__ import annotations

import json
from typing import Any

from observability.spans import SpanStore
from process import config, rules
from bridge import spans_to_log


def _tool_steps(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for span in spans:
        if span["kind"] != "tool":
            continue
        result = span["attributes"].get("result") or {}
        steps.append(
            {
                "tool": span["name"],
                "item_key": span["item_key"],
                "ok": bool(span["ok"]),
                "error": span["error"],
                "status": result.get("status"),
                "decision": result.get("decision"),
                "arguments": span["attributes"].get("arguments") or {},
                "result": result,
            }
        )
    return steps


def normalize_run(store: SpanStore, run: dict[str, Any]) -> dict[str, Any]:
    spans = store.all_spans(run["run_id"])
    transcript = store.transcript(run["run_id"])
    tools = _tool_steps(spans)

    messages = [
        {"role": row["role"], "text": row["content"]}
        for row in transcript
    ]
    final_reply = next(
        (row["content"] for row in reversed(transcript) if row["role"] == "assistant"), ""
    )

    items = sorted({step["item_key"] for step in tools if step["item_key"]})
    refused = [step for step in tools if not step["ok"]]
    writes = [step for step in tools if step["tool"] in ("clear_invoice", "record_goods_receipt")]
    succeeded = [step for step in writes if step["ok"] and step["status"] != "queued_for_approval"]
    queued = [step for step in writes if step.get("status") == "queued_for_approval"]

    # Did the agent, having been refused a write, try for the same effect again?
    #
    # The retry itself is the signal, whether or not it worked. Requiring the
    # retry to SUCCEED would miss the canonical case entirely: an agent that is
    # refused three times and then escalates has still worked around the
    # refusal three times, and that is the behaviour worth finding.
    retried = False
    refused_targets: set[tuple[str, str | None]] = set()
    for step in writes:
        target = (step["tool"], step["item_key"])
        if target in refused_targets:
            retried = True
        if not step["ok"]:
            refused_targets.add(target)
    repeated_refusals = len([step for step in writes if not step["ok"]]) > 1

    return {
        "trace_id": run["run_id"],
        "session_id": run["session_id"],
        "scenario_id": run.get("scenario_id"),
        "timestamp": run["started_at"],
        "model": run["model"],
        "prompt_version": run["prompt_version"],
        "segments": {
            "role": run["role"],
            "actor_id": run["actor_id"],
            "killswitch": run.get("killswitch", "off"),
        },
        "messages": messages,
        "text": "\n".join(f"{m['role']}: {m['text']}" for m in messages),
        "final_reply": final_reply,
        "tool_calls": tools,
        "features": {
            "turn_count": len([m for m in messages if m["role"] == "assistant"]),
            "tool_call_count": len(tools),
            "distinct_tools": sorted({step["tool"] for step in tools}),
            "refusal_count": len(refused),
            "process": {
                "items_touched": items,
                "multi_item": len(items) > 1,
                "activities_performed": [
                    step["tool"] for step in succeeded
                ],
                "write_attempts": len(writes),
                "writes_succeeded": len(succeeded),
                "writes_queued": len(queued),
                "retried_after_refusal": retried,
                "repeated_refusals": repeated_refusals,
            },
        },
    }


def control_findings(store: SpanStore, run_id: str) -> list[dict[str, Any]]:
    """Run the control rules over this one run's own business events."""
    # Most runs legitimately produce no business events -- the controls refuse
    # most exception requests -- so an empty log here is the normal case, not a
    # condition worth warning about once per trace.
    log = spans_to_log.convert(
        store,
        log_id=f"run-{run_id[:8]}",
        layer=spans_to_log.BUSINESS,
        run_id=run_id,
        warn_if_empty=False,
    )
    if not log.case_count:
        return []
    return [
        violation.as_record()
        for violation in rules.evaluate(log, config.load_facts())
    ]


def normalize_all(store: SpanStore) -> list[dict[str, Any]]:
    records = []
    for run in store.runs():
        record = normalize_run(store, run)
        record["features"]["control"] = {"violations": control_findings(store, run["run_id"])}
        records.append(record)
    return records


def write_jsonl(records: list[dict[str, Any]], path) -> None:
    from pathlib import Path

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")
