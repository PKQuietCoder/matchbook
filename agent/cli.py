"""`python -m agent` -- run a session and write spans locally.

Two modes, and `--script <name>` selects the *session*, not the model.

  --model scripted   (default) replays the script's fixed steps. No API key, no
                     budget, byte-for-byte reproducible. This is what the
                     repo's own demonstration and CI use.
  --model claude-sonnet-5
                     sends the script's message to the live model and lets it
                     choose its own tool calls. Needs ANTHROPIC_API_KEY and
                     spends real tokens.

Both write spans to the same store, so the bridge, the mined log and the cost
report treat a live run and a scripted one identically. Keeping the scripted
path as the default is deliberate: an unconfigured checkout must still be able
to demonstrate the whole pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.agent import ScriptedModel, auth_context_for, banner, run_session
from agent.scripts import SCRIPTS
from observability import instrument
from observability.spans import SpanStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPANS = REPO_ROOT / "build" / "spans.db"
SCRIPTED = "scripted"


def build_model(choice: str, script_name: str, script: dict):
    """Pick the model for one run. The only place the live adapter is named."""
    if choice == SCRIPTED:
        return ScriptedModel(script["steps"], name=f"scripted:{script_name}")
    # Imported lazily so the scripted path never needs the SDK installed.
    from agent.model_anthropic import AnthropicModel

    return AnthropicModel(choice)


def _ad_hoc(args, parser, prompt_template: str | None) -> int:
    """One ad-hoc request, for Homework 1's recorded conversations.

    `--script` chooses a session; this chooses a *request*. The identity still
    comes from the world through `auth_context_for`, so an ad-hoc run is no
    less authorized than a scripted one -- what it loses is reproducibility,
    which is why the scripted path remains the default everywhere else.
    """
    if not (args.actor and args.message):
        parser.error("--actor and --message go together")
    if args.model == SCRIPTED:
        parser.error(
            "--message needs a live model: the scripted model replays fixed steps "
            "and cannot answer an arbitrary request. Pass --model claude-sonnet-5."
        )

    if args.reset:
        from seed.generate import generate_world

        generate_world(quiet=True)
        print("world re-seeded")

    instrument.configure()
    print(banner())
    ctx = auth_context_for(args.actor)
    store = SpanStore(args.spans)
    try:
        from agent.model_anthropic import AnthropicModel

        print(f"\n=== ad hoc ({ctx.actor_id}, {ctx.role}) ===")
        print(f"user: {args.message}")
        result = run_session(
            ctx,
            args.message,
            AnthropicModel(model=args.model),
            store=store,
            session_id=args.session,
            prompt_template=prompt_template,
        )
        _report(store, result, debug=args.debug)
    finally:
        store.close()
        instrument.shutdown()
    print(f"\nspans written to {args.spans}")
    return 0


def _report(store: SpanStore, result, *, debug: bool) -> None:
    """Print one run's tool calls, reply, and what reached the event log."""
    for call in result.tool_calls:
        status = "ok" if call["result"].get("ok") else call["result"].get("error")
        detail = call["result"].get("status") or call["result"].get("decision") or ""
        print(f"  tool {call['name']:<22} -> {status} {detail}")
        if debug:
            print(f"       {json.dumps(call['result'], default=str)[:400]}")
    print(f"agent: {result.reply}")
    print(f"  ({result.steps} steps, run {result.run_id[:8]})")
    # What the run contributed to the event log, which is not what it said it
    # did. `activities_recorded` in the homework session records comes from here.
    activities = [s["activity"] for s in store.all_spans(result.run_id) if s.get("activity")]
    print(f"  activities recorded: {activities or '(none)'}")
    spent = _tokens_of(store, result.run_id)
    if spent:
        print(
            f"  tokens in {spent['input_tokens']:,} out {spent['output_tokens']:,}"
            f"  cache read {spent['cache_read_tokens']:,}"
            f"  ${spent['usd']:.4f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", help="a scripted session name")
    parser.add_argument(
        "--actor",
        help="run one ad-hoc request as this actor (ap-003, buy-001, ctl-001, ...). "
        "The role comes from the world, never from the request.",
    )
    parser.add_argument(
        "--message",
        help="the request to send. Needs --actor and a live --model, because the "
        "scripted model replays fixed steps and cannot answer an open question.",
    )
    parser.add_argument(
        "--prompt-file",
        help="use this file as SYSTEM_PROMPT_TEMPLATE instead of the built-in one. "
        "Changes prompt_version, which is the point: it makes a prompt comparison "
        "a measurement rather than a memory.",
    )
    parser.add_argument("--list-scripts", action="store_true")
    parser.add_argument("--all-scripts", action="store_true", help="run every script in order")
    parser.add_argument("--spans", default=str(DEFAULT_SPANS))
    parser.add_argument("--session", default="cli")
    parser.add_argument("--debug", action="store_true", help="print each tool call and result")
    parser.add_argument(
        "--model",
        default=SCRIPTED,
        help="'scripted' (default, no key, reproducible) or a live model id such as "
        "claude-sonnet-5, which spends real tokens",
    )
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

    prompt_template = None
    if args.prompt_file:
        prompt_template = Path(args.prompt_file).read_text()

    if args.message or args.actor:
        return _ad_hoc(args, parser, prompt_template)

    names = list(SCRIPTS) if args.all_scripts else ([args.script] if args.script else [])
    if not names:
        parser.error(
            "pass --script <name>, --all-scripts, --list-scripts, "
            "or --actor <id> --message '<request>'"
        )
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
            model = build_model(args.model, name, script)
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
            spent = _tokens_of(store, result.run_id)
            if spent:
                print(
                    f"  tokens in {spent['input_tokens']:,} out {spent['output_tokens']:,}"
                    f"  cache read {spent['cache_read_tokens']:,}"
                    f"  ${spent['usd']:.4f}"
                )
    finally:
        store.close()
    print(f"\nspans written to {args.spans}")
    return 0


def _tokens_of(store: SpanStore, run_id: str) -> dict | None:
    """Totals for one run, or None when nothing spent tokens (the scripted path)."""
    from bridge.cost import run_totals

    totals = run_totals(store.path, run_id)
    return totals or None


if __name__ == "__main__":
    sys.exit(main())
