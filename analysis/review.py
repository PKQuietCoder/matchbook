"""`python -m analysis.review` -- the open-coding surface.

Deliberately minimal and file-backed: the state lives in inspectable JSON and
JSONL, appended never rewritten, so a review session is auditable and a
disagreement can be traced to the note that caused it.

What this adds over reading transcripts is the sort order. `--sort` ranks
traces by a process feature -- retries after a refusal, multiple items touched,
repeated refusals -- so the runs worth reading come first. Finding escalation
avoidance by reading transcripts in arrival order is luck; finding it by
sorting on `retried_after_refusal` is a method.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import normalize  # noqa: E402
from observability.spans import SpanStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).resolve().parent / "state"

SORT_KEYS = {
    "arrival": lambda r: r["timestamp"],
    "retries": lambda r: (
        not r["features"]["process"]["retried_after_refusal"],
        -r["features"]["process"]["write_attempts"],
    ),
    "multi-item": lambda r: (not r["features"]["process"]["multi_item"], r["timestamp"]),
    "refusals": lambda r: -r["features"]["refusal_count"],
    "violations": lambda r: -len(r["features"].get("control", {}).get("violations", [])),
}


def _load_state(name: str, default):
    path = STATE / name
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_state(name: str, payload) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(payload, indent=1, default=str))


def cmd_list(args) -> int:
    store = SpanStore(args.spans)
    try:
        records = normalize.normalize_all(store)
    finally:
        store.close()
    records.sort(key=SORT_KEYS[args.sort])
    print(f"{len(records)} traces, sorted by {args.sort}\n")
    for record in records[: args.limit]:
        process = record["features"]["process"]
        flags = []
        if process["retried_after_refusal"]:
            flags.append("RETRIED-AFTER-REFUSAL")
        if process["multi_item"]:
            flags.append("MULTI-ITEM")
        if process["repeated_refusals"]:
            flags.append("REPEATED-REFUSALS")
        if process["writes_queued"]:
            flags.append("QUEUED")
        violations = record["features"].get("control", {}).get("violations", [])
        if violations:
            flags.append(f"CONTROL:{','.join(sorted({v['rule_id'] for v in violations}))}")
        print(
            f"{record['trace_id'][:8]}  {record['scenario_id'] or '-':<26} "
            f"{record['segments']['role']:<10} "
            f"tools={record['features']['tool_call_count']:<2} "
            f"{' '.join(flags)}"
        )
    return 0


def cmd_show(args) -> int:
    store = SpanStore(args.spans)
    try:
        records = {r["trace_id"][:8]: r for r in normalize.normalize_all(store)}
    finally:
        store.close()
    record = records.get(args.trace_id[:8])
    if record is None:
        print(f"no trace starting {args.trace_id!r}; try `list`")
        return 1
    print(f"trace {record['trace_id']}  scenario {record['scenario_id']}")
    print(f"actor {record['segments']['actor_id']} ({record['segments']['role']})  "
          f"prompt {record['prompt_version']}\n")
    for message in record["messages"]:
        print(f"[{message['role']}] {message['text']}\n")
    print("process features:")
    print(json.dumps(record["features"]["process"], indent=1))
    violations = record["features"].get("control", {}).get("violations", [])
    print(f"control violations on this run's own events: {len(violations)}")
    for violation in violations:
        print(f"  {violation['rule_id']}: {violation['summary']}")
    return 0


def cmd_annotate(args) -> int:
    """Append one open-coding note. Append-only, so nothing is overwritten."""
    state = _load_state("annotations.json", {"annotations": []})
    state["annotations"].append(
        {
            "trace_id": args.trace_id,
            "note": args.note,
            "author": args.author,
            "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        }
    )
    _write_state("annotations.json", state)
    print(f"{len(state['annotations'])} annotation(s) recorded")
    return 0


def cmd_patterns(args) -> int:
    state = _load_state("patterns.json", {"modes": {}})
    if not state["modes"]:
        print("no failure modes recorded yet")
        return 0
    for name, mode in state["modes"].items():
        print(f"\n{name}  [{mode.get('status','candidate')}]  from: {mode.get('created_from','?')}")
        print(f"  {mode['definition']}")
        if mode.get("detect"):
            print(f"  machine signal: {mode['detect']}")
        if mode.get("example_scenarios"):
            print(f"  examples: {', '.join(mode['example_scenarios'])}")
    return 0


def cmd_export(args) -> int:
    store = SpanStore(args.spans)
    try:
        records = normalize.normalize_all(store)
    finally:
        store.close()
    normalize.write_jsonl(records, args.out)
    print(f"wrote {len(records)} normalized traces to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spans", default=str(REPO_ROOT / "build" / "spans.db"))
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="traces, sorted so the interesting ones come first")
    listing.add_argument("--sort", choices=sorted(SORT_KEYS), default="retries")
    listing.add_argument("--limit", type=int, default=30)
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="one trace in full")
    show.add_argument("trace_id")
    show.set_defaults(func=cmd_show)

    annotate = sub.add_parser("annotate", help="append an open-coding note")
    annotate.add_argument("trace_id")
    annotate.add_argument("note")
    annotate.add_argument("--author", default="human")
    annotate.set_defaults(func=cmd_annotate)

    sub.add_parser("patterns", help="the failure-mode taxonomy").set_defaults(func=cmd_patterns)

    export = sub.add_parser("export", help="normalized traces as JSONL")
    export.add_argument("--out", default=str(REPO_ROOT / "build" / "traces.jsonl"))
    export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
