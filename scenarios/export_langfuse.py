"""`python -m scenarios.export_langfuse <scenarios> <out.json>` -- pull the traces.

Exports the trace for every scenario in the dataset, and **fails when a scenario
has no trace**. That refusal is the feature. A silent partial export is how a
review ends up drawing conclusions from 180 traces while believing it saw 250,
and the missing ones are never the random ones -- they are the slow, the
erroring, and the ones whose spans were still in the batch exporter's buffer when
the server stopped.

Traces are selected by tag. `observability/instrument.py` tags every trace with
the caller's role and the scenario id, so one request per scenario is enough and
no local bookkeeping has to stay in step with the backend.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.model_anthropic import load_env  # noqa: E402
from observability import instrument  # noqa: E402
from scenarios import schema  # noqa: E402


def _auth_header() -> str:
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not public or not secret:
        raise SystemExit(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set in .env; "
            "the values for the local stack are in .env.example"
        )
    return "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{instrument.langfuse_host()}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url)
    request.add_header("Authorization", _auth_header())
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"GET {path} -> {error.code}. Is the stack up? "
            "docker compose -f observability/docker-compose.yml up -d"
        ) from error
    except urllib.error.URLError as error:
        raise SystemExit(
            f"cannot reach {instrument.langfuse_host()}: {error.reason}. "
            "docker compose -f observability/docker-compose.yml up -d"
        ) from error


def traces_for(scenario_id: str) -> list[dict[str, Any]]:
    """Every trace tagged with this scenario id, newest first.

    More than one is normal and not an error: a multi-turn scenario produces one
    trace per turn.
    """
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _get("/api/public/traces", {"tags": scenario_id, "limit": 50, "page": page})
        found.extend(payload.get("data", []))
        meta = payload.get("meta") or {}
        if page >= int(meta.get("totalPages") or 1):
            return found
        page += 1


def full_trace(trace_id: str) -> dict[str, Any]:
    return _get(f"/api/public/traces/{trace_id}", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", help="the scenario JSONL whose traces to export")
    parser.add_argument("out", help="where the export is written")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="write the export even when scenarios have no trace. Off by default, "
        "and leave it off: a partial export that looks complete is the problem.",
    )
    args = parser.parse_args(argv)

    load_env()
    scenarios = schema.read_jsonl(Path(args.scenarios))
    exported: list[dict[str, Any]] = []
    missing: list[str] = []

    for number, scenario in enumerate(scenarios, start=1):
        summaries = traces_for(scenario["id"])
        if not summaries:
            missing.append(scenario["id"])
            print(f"[{number:>4}/{len(scenarios)}] {scenario['id']:<12} NO TRACE")
            continue
        for summary in summaries:
            trace = full_trace(summary["id"])
            trace["mb_scenario_id"] = scenario["id"]
            exported.append(trace)
        print(
            f"[{number:>4}/{len(scenarios)}] {scenario['id']:<12} "
            f"{len(summaries)} trace(s)"
        )

    payload = {
        "scenario_count": len(scenarios),
        "trace_count": len(exported),
        "missing_scenario_ids": missing,
        "langfuse_host": instrument.langfuse_host(),
        "traces": exported,
    }

    if missing and not args.allow_missing:
        print(f"\n{len(missing)} scenario(s) have no trace:")
        for identifier in missing[:20]:
            print(f"  {identifier}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        print(
            "\nNothing was written. Rerun those scenarios and export again:\n"
            f"  python -m scenarios.runner {args.scenarios} --model <id> "
            f"--output <results> --ids {','.join(missing[:5])}\n"
            "A spans buffer not yet flushed is the usual cause: the batch exporter "
            "sends on a timer, so a server stopped immediately after a run loses "
            "its last traces."
        )
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str))
    print(f"\n{len(exported)} traces for {len(scenarios)} scenarios -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
