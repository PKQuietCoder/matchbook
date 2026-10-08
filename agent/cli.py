"""`python -m agent` -- run a session and write spans locally.

Two modes. `--script <name>` replays a fixed session and needs no API key, no
model and no budget; that is the mode the repo's own demonstration and CI use.
A live model adapter plugs into the same `Model` protocol (see agent/agent.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.agent import ScriptedModel, auth_context_for, banner, run_session
from agent.scripts import SCRIPTS
from observability.spans import SpanStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPANS = REPO_ROOT / "build" / "spans.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", help="a scripted session name")
    parser.add_argument("--list-scripts", action="store_true")
    parser.add_argument("--all-scripts", action="store_true", help="run every script in order")
    parser.add_argument("--spans", default=str(DEFAULT_SPANS))
    parser.add_argument("--session", default="cli")
    parser.add_argument("--debug", action="store_true", help="print each tool call and result")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="re-seed the world first. Scripted outcomes depend on world state -- a "
        "clearing that succeeded on the previous run finds no open invoice on the next "
        "-- so a reproducible run needs this. Re-seeding IS the sandbox reset.",
    )
    args = parser.parse_args(argv)

    if args.list_scripts:
        for name, script in SCRIPTS.items():
            print(f"{name:24s} {script['actor']:8s} {script['comment']}")
        return 0

    names = list(SCRIPTS) if args.all_scripts else ([args.script] if args.script else [])
    if not names:
        parser.error("pass --script <name>, --all-scripts, or --list-scripts")
    unknown = [name for name in names if name not in SCRIPTS]
    if unknown:
        parser.error(f"unknown script(s) {unknown}; known: {', '.join(SCRIPTS)}")

    if args.reset:
        from seed.generate import generate_world

        generate_world(quiet=True)
        print("world re-seeded")

    print(banner())
    store = SpanStore(args.spans)
    try:
        for name in names:
            script = SCRIPTS[name]
            ctx = auth_context_for(script["actor"])
            model = ScriptedModel(script["steps"], name=f"scripted:{name}")
            print(f"\n=== {name} ({script['actor']}, {ctx.role}) ===")
            print(f"user: {script['message']}")
            result = run_session(
                ctx,
                script["message"],
                model,
                store=store,
                session_id=f"{args.session}-{name}",
                scenario_id=name,
            )
            for call in result.tool_calls:
                status = "ok" if call["result"].get("ok") else call["result"].get("error")
                detail = call["result"].get("status") or call["result"].get("decision") or ""
                print(f"  tool {call['name']:<22} -> {status} {detail}")
                if args.debug:
                    print(f"       {json.dumps(call['result'], default=str)[:400]}")
            print(f"agent: {result.reply}")
            print(f"  ({result.steps} steps, run {result.run_id[:8]})")
    finally:
        store.close()
    print(f"\nspans written to {args.spans}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
