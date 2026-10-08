"""`python -m scenarios.runner <file> --model <id> --output <file>` -- run scenarios.

Every request goes through the Homework 2 endpoints rather than calling
`run_session` directly, and that is the point rather than an inconvenience: the
endpoint is what establishes identity from the world, so a dataset run through it
exercises the same authorization path a real caller would. A runner that imported
the agent and built its own `AuthContext` would be testing a different system
from the one being evaluated.

urllib rather than a client library, because nothing else here needs one and a
dependency is declared when something imports it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios import schema  # noqa: E402

DEFAULT_ENDPOINT = "http://localhost:8010"


def _post(url: str, payload: dict[str, Any], *, token: str | None = None,
          timeout: float = 300.0) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(detail)
        except json.JSONDecodeError:
            return error.code, {"detail": detail}


def run_one(scenario: dict[str, Any], *, endpoint: str, model: str) -> dict[str, Any]:
    """Run one scenario's conversation and record what happened.

    A status other than `completed` is recorded rather than raised, so one bad
    scenario does not end a 250-scenario run. `--resume` reruns exactly those.
    """
    started = time.monotonic()
    record: dict[str, Any] = {
        "scenario_id": scenario["id"],
        "scenario_group": scenario["scenario_group"],
        "model": model,
        "status": "completed",
        "error": None,
        "turns": [],
        "run_ids": [],
        "tool_order": [],
        "activities_recorded": [],
        "expected": scenario["expected"],
        "duration_s": None,
    }

    status, session = _post(
        f"{endpoint}/sessions",
        {"actor_id": scenario["tuple"]["actor_id"], "role": scenario["tuple"]["role"]},
    )
    if status != 200:
        record["status"] = "session_failed"
        record["error"] = f"POST /sessions -> {status}: {session.get('detail')}"
        record["duration_s"] = round(time.monotonic() - started, 3)
        return record

    messages = [scenario["opening_message"], *scenario.get("followups", [])]
    for message in messages:
        status, reply = _post(
            f"{endpoint}/sessions/{session['session_id']}/messages",
            {"message": message, "model": model, "scenario_id": scenario["id"]},
            token=session["token"],
        )
        if status != 200:
            record["status"] = "message_failed"
            record["error"] = f"turn {len(record['turns']) + 1} -> {status}: {reply.get('detail')}"
            break
        record["turns"].append({"user": message, "agent": reply["reply"]})
        record["run_ids"].append(reply["run_id"])
        record["tool_order"].extend(reply["tool_order"])
        record["activities_recorded"].extend(reply["activities_recorded"])

    record["duration_s"] = round(time.monotonic() - started, 3)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="a validated scenario JSONL file")
    parser.add_argument("--model", required=True, help="the model id every scenario runs on")
    parser.add_argument("--output", required=True, help="where results are written")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep every completed record in --output and rerun only the scenarios "
        "whose record is missing or has another status",
    )
    parser.add_argument(
        "--ids",
        help="rerun only these scenario ids, comma separated; their records replace "
        "the earlier ones. A rerun does NOT reset the world.",
    )
    args = parser.parse_args(argv)

    scenarios = schema.read_jsonl(Path(args.path))
    output = Path(args.output)
    existing: dict[str, dict[str, Any]] = {}
    if output.exists() and (args.resume or args.ids):
        existing = {
            record["scenario_id"]: record for record in schema.read_jsonl(output)
        }

    if args.ids:
        wanted = {identifier.strip() for identifier in args.ids.split(",") if identifier.strip()}
        unknown = wanted - {scenario["id"] for scenario in scenarios}
        if unknown:
            parser.error(f"unknown scenario id(s): {sorted(unknown)}")
        todo = [scenario for scenario in scenarios if scenario["id"] in wanted]
    elif args.resume:
        todo = [
            scenario
            for scenario in scenarios
            if existing.get(scenario["id"], {}).get("status") != "completed"
        ]
    else:
        todo = scenarios

    status, health = _post(f"{args.endpoint}/sessions", {"actor_id": "", "role": ""})
    if status not in (400, 404):
        print(
            f"the endpoint at {args.endpoint} did not answer as expected "
            f"(got {status}). Start it with `uv run uvicorn server.app:app --port 8010`."
        )
        return 1

    print(f"{len(todo)} of {len(scenarios)} scenarios to run on {args.model}\n")
    results = dict(existing)
    for number, scenario in enumerate(todo, start=1):
        record = run_one(scenario, endpoint=args.endpoint, model=args.model)
        results[scenario["id"]] = record
        flag = "" if record["status"] == "completed" else f"  !! {record['status']}"
        print(
            f"[{number:>4}/{len(todo)}] {scenario['id']:<12} "
            f"{record['duration_s']:>6.1f}s  tools={len(record['tool_order']):<2} "
            f"activities={len(record['activities_recorded'])}{flag}"
        )
        # Written after every scenario, not at the end: a 250-scenario run that
        # dies on scenario 200 must not lose the first 199.
        schema.write_jsonl(
            [results[s["id"]] for s in scenarios if s["id"] in results], output
        )

    done = sum(1 for record in results.values() if record["status"] == "completed")
    print(f"\n{done}/{len(scenarios)} completed; results in {output}")
    if done < len(scenarios):
        print("rerun the rest with --resume, after fixing the cause")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
