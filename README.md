# Matchbook

A purchase-to-pay agent, a hand-written process-mining library, and a real
human event log of the same process — so an agent's behaviour can be measured
against how people actually ran the work.

## The idea

A public event log and an agent's traces are two recordings of the same business
process — one executed by people in a real company, one by an LLM agent. That
gives conformance checking a reference model nobody had to invent:

```
   BPI 2019: 251,734 real cases              agent runs: k repeats per case
                │                                        │
         measure the process  ──────────────▶   compare against it
         (DFG, variants, controls)              (same activity alphabet)
                │                                        │
                ▼                                        ▼
      "how people ran this process"          "how the agent runs it"
```

"The agent cleared an invoice before the goods receipt" stops being an opinion
and becomes a measured deviation.

## Why the algorithms are hand-written

No third-party mining framework, no commercial mining tool. XES parsing, the log
representation, discovery, conformance and rendering are all original code here.
That costs effort and buys three things: the IP is ours; the live process-map app
can be hosted publicly (the dominant open-source mining library is
AGPL-3.0-or-later, whose §13 network clause would otherwise oblige offering the
whole application's source to every visitor); and the algorithms become the
teaching content rather than a library call.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/). Run commands from the repo root.

```bash
uv sync
uv run python -m process ingest logs/snapshot/bpic19-sample-events.csv.gz \
    --log-id bpic19-sample --license "CC BY 4.0" \
    --case-attributes logs/snapshot/bpic19-sample-cases.csv.gz
uv run pytest
```

A fresh clone works with no network: the BPI 2019 snapshot and the Helpdesk
fixture are committed, both under licenses that permit it. The **mining half
needs no API key, no model and no agent dependencies** — that is enforced by
`tests/test_offline_mining.py`, not merely intended.

For the full 251,734-case log:

```bash
uv run python -m logs.download --log bpic19     # 728 MB, verified against the manifest
uv run python logs/sample.py                    # re-derive the snapshot + SAMPLE.md
```

## Try it

```bash
uv run python -m process logs                              # what is in the store
uv run python -m process summary bpic19-sample             # counts, variants, rework
uv run python -m process dfg bpic19-sample --out build/human.dot
uv run python -m process dfg bpic19-sample --annotate performance
uv run python -m process variants bpic19-sample --top 5
uv run python -m process rules bpic19-sample --out build/violations.csv
uv run python -m process tie-breaks bpic19-sample          # audit assumed order
uv run python -m process dfg bpic19-sample --flow "Consignment" --out build/consignment.svg
```

### Getting the log into pandas

```bash
uv run python -m logs.download --log bpic19
uv run python logs/to_csv.py                      # -> logs/frames/, ~35 s
uv run python logs/to_csv.py --max-cases 2000     # fast path
```

```python
import json, pandas as pd
d = json.load(open("logs/frames/bpic19-dtypes.json"))
events = pd.read_csv("logs/frames/bpic19-events.csv.gz", dtype=d["events"],
                     parse_dates=["timestamp"], keep_default_na=False)
cases  = pd.read_csv("logs/frames/bpic19-cases.csv.gz", dtype=d["cases"],
                     keep_default_na=False)
```

Two tables — 1,595,923 event rows and 251,734 case rows, 14.8 MB gzipped, which is
slightly smaller than the gzipped XES. A joined layout would repeat the 16 case
attributes on every event: ~1.92 GB in memory against ~0.36 GB, so join on
`case_id` when you actually need it.

**Pass the dtypes sidecar.** A CSV has no schema, so without it
`GR-Based Inv. Verif.` reads back as the string `'False'` and `Item` `00001` as the
integer `1`. With it the conversion is lossless, and `tests/test_frames.py` proves
that against the real XES rather than asserting it. `keep_default_na=False` matters
too, or pandas turns the empty strings into `NaN`.

**Row order is the order** — sort by `['case_id', 'seq']` if you reindex, never by
`timestamp`: every event falls on a whole minute and 233,463 of them (14.6%) share
an instant with their predecessor, so a timestamp sort reshuffles them.

### Exporting the log as OpenTelemetry spans

The human log and the agent's log are both `EventLog`s, so one exporter opens
both in any OTLP tool. One trace per case, one span per event.

```bash
uv run python -m process otlp bpic19-sample --out logs/otlp/bpic19-sample.ndjson.gz
uv run python -m process otlp-verify logs/otlp/bpic19-sample.ndjson.gz --against bpic19-sample
#   ^ compares only when the export is unfiltered; a --max-cases slice is
#     reported as not-applicable rather than as six conservation failures
uv run python -m process otlp agent-attempts --out logs/otlp/agent.ndjson.gz

# the whole 1.6M-event log, streamed straight off the 728 MB raw file
uv run python -m logs.download --log bpic19
uv run python -m process otlp bpic19 --from-xes logs/raw/BPI_Challenge_2019.xes \
    --license "CC BY 4.0" --doi 10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 \
    --out logs/otlp/bpic19-full.ndjson.gz
```

Measured on the full log, not estimated: **1,847,657 spans** (1,595,923 events +
251,734 case roots) in **59.6 s** at **81 MB peak resident memory**, producing
1.43 GB of NDJSON or 59.6 MB gzipped. Memory is flat because `--from-xes` builds
one case at a time; reading the whole log into an `EventLog` first costs ~1.4 GB.

The export is hand-written JSON with no `opentelemetry` dependency —
`tests/test_offline_mining.py` blocks that import and then writes and re-reads
spans anyway, which is the claim. Plain OTLP is POST-able unchanged to any
OTLP/HTTP collector, Langfuse's `/api/public/otel/v1/traces` included; nothing
vendor-specific is emitted.

What the conversion will not assert, because the log does not record it: event
durations (BPI 2019 has no `lifecycle:transition`, so every event span has
`start == end`), span nesting, per-event outcome (no span carries a `status`, so
an error rate from this file is undefined, not zero), and a case-level actor
(96.3% of cases involve more than one resource). Order lives in `mb.seq`, not in
the timestamps — 233,463 events share an instant with their predecessor. Each
export writes a `.disclosure.md` and a `.manifest.json` saying so, with
denominators.

What that reports on the committed sample of real data:

- `Record Invoice Receipt → Clear Invoice` has a **~25-day median** and is the
  largest bottleneck by total waiting time — net-30 payment terms, visible in the data.
- `Remove Payment Block → Clear Invoice` is the third-largest bottleneck: payment
  blocks are a leading cause of delay.
- **19% of cases remove a payment block that was never recorded as set.** Read the
  caveat the report prints before concluding anything: the full log holds 57,137
  `Remove Payment Block` events against 124 `Set Payment Block`, so on the human log
  this rule measures *log completeness*, not misconduct. It is the best teaching rule
  in the catalogue for exactly that reason.
- **The tolerance control reports itself unevaluable on this log, and that is the
  honest answer.** BPI 2019 carries a single case-level value (`Cumulative net worth
  (EUR)`) replicated onto every event, so there is no separate goods-receipt value and
  invoice value to compare. An earlier version of the rule subtracted one event's value
  from another's, reported zero violations, and that zero was mistaken for a finding.
  A rule whose inputs are absent now declines to report rather than returning a clean
  bill of health. It runs normally on the synthetic and agent logs, where the
  instrumentation records both values.

## The agent, end to end with no API key

The agent half runs from scripted sessions, so the whole pipeline — world,
tools, spans, the bridge, the mined log, conformance, failure analysis — is
reproducible with no model key and no budget. This is the default, and it is
what CI and the demo use.

```bash
uv run python -m seed.generate                       # deterministic world + policy corpus
uv run python -m seed.validate                       # the corpus must agree with facts.yaml
uv run python -m agent --list-scripts
uv run python -m agent --all-scripts --reset         # 10 sessions -> build/spans.db

uv run python -m bridge.spans_to_log build/spans.db --log-id agent-business --layer business
uv run python -m bridge.spans_to_log build/spans.db --log-id agent-attempts --layer attempts
uv run python -m process compare bpic19-sample agent-business
uv run python -m analysis.review list --sort retries
```

## The same pipeline on a live model

`--script` chooses the *session*; `--model` chooses who answers it. The scripted
model replays fixed steps; `claude-sonnet-5` decides its own tool calls. Both
write to the same span store, so the bridge, the mined log and the cost report
cannot tell them apart.

```bash
cp .env.example .env            # then put ANTHROPIC_API_KEY in it
uv sync --extra agent           # the only model dependency is `anthropic`
uv run python -m agent --script clean_receipt_then_clear --reset     --model claude-sonnet-5
```

The live adapter is `agent/model_anthropic.py`. It implements the one-method
`Model` protocol in `agent/agent.py` and nothing else changes — there is no
agent framework in this repo, and the session loop is Matchbook's own.

### What the process cost

Mining says which paths the agent takes. The cost report says what each one is
worth, which is the argument for looking at them together.

```bash
uv run python -m bridge.cost --by run        # per session
uv run python -m bridge.cost --by activity   # per business activity
uv run python -m bridge.cost --by case       # per purchase-order item
uv run python -m bridge.cost --by variant --log-id agent-business
```

Tokens are recorded on model spans; activities belong to tool spans. The two
are joined on `(run_id, step)`, because the tool calls at step N are the ones
the model call at step N requested — so a step's tokens are the price of
deciding to do what that step did. Steps that only looked things up, and the
final reply step, are reported in their own buckets rather than smeared over
the activities: deliberation-versus-action is usually the first thing worth
seeing. When one step requests two activities its tokens are divided equally
between them, and every report counts how many steps were split, the same way
the ingest reports how many events were tie-broken. A scripted run spends
nothing, and the report says *unmeasured* rather than showing a column of
zeros, because a free run and an unpriced one are not the same claim.

#### Measured, not estimated

One live `clean_receipt_then_clear` session on `claude-sonnet-5`, 2026-10-08:

| step | what it did | input | cache write | cache read | output |
| --- | --- | --- | --- | --- | --- |
| 1 | looked the item up | 112 | 1,867 | 0 | 106 |
| 2 | recorded the goods receipt | 509 | 0 | 1,867 | 290 |
| 3 | wrote the reply | 857 | 0 | 1,867 | 92 |
| | **run** | **1,478** | **1,867** | **3,734** | **488** |

$0.0133 for the session. Three things in that table are worth keeping:

- **Prompt caching pays for itself inside a single session.** The tools-plus-
  system prefix is 1,867 tokens, written once on step 1 and read back on steps
  2 and 3. Billed uncached it would be 5,601 full-rate input tokens ($0.0112);
  billed as one write plus two reads it is $0.0054. The remaining opportunity
  is *across* sessions, and `render_system_prompt` currently blocks it by
  interpolating the actor, role and company code into the system prompt, which
  gives every actor a different prefix. Moving that context into the first user
  message would make the prefix shared. Not done yet, because the saving should
  be measured rather than asserted.
- **The cheapest step is the one that changed the world.** Recording the
  receipt cost less than looking the item up beforehand and less than writing
  the reply afterwards. Deliberation, not action, is where the money goes --
  which is why the report keeps those buckets separate.
- **Output tokens dominate per-token cost.** 488 output tokens at $10/MTok cost
  more than 1,478 input tokens at $2/MTok. Reply length is a cost lever.

### What the bridge decides, and why it matters

**The case is the business object, not the conversation.** Keying the agent's
log on the run id would produce a log whose cases are conversations, which can
never be compared with a log whose cases are purchase-order items. Keying on
`item_key` means two conversations about one item form **one** case — which is
how three clearing attempts on a blocked item, spread across two separate
sessions, show up as a single three-step case.

**Three layers, three questions.** `business` records only what actually
happened and uses only activity names the human log can contain, so it is the
comparable one. `attempts` adds refused, paused and queued attempts as distinct
activities — this is where escalation avoidance becomes a visible path, and it
is deliberately *not* comparable to the human log. `internal` keeps every span
including lookups and model steps, for studying the agent rather than the
process.

### Findings from the ten scripted sessions

- **Escalation avoidance is a self-loop, not a sentence.** The run that chases a
  blocked payment produces `Attempted Clear Invoice → Attempted Clear Invoice`
  in the attempts-layer map. Its final reply reads like a helpful assistant that
  escalated appropriately. `analysis.review list --sort retries` puts it first;
  reading transcripts in arrival order would not.
- **The same run quietly commits a second failure.** It quotes a €120.00
  variance it derived itself, with no `get_three_way_match` call anywhere in the
  run (SPEC TOOL-7). The number happens to be right, which is the point: the
  failure is detectable from the tool sequence, not from the wording.
- **A well-guarded agent produces an almost empty business log.** Ten sessions
  yielded 21 tool calls and just **2 business events**, because the controls
  correctly refused nearly everything. That is a real methodological result: for
  a guarded agent on exception work, model-based conformance has little to chew
  on, and the governance signal lives in the attempts layer. Conformance
  checking needs runs that legitimately complete work.
- **Separation of duties is stateful, and it bites.** The pinned purchase orders
  were created *and* released by the same buyer, so no buyer could legitimately
  receipt them — correct behaviour that made the clean path undemonstrable until
  the world gained a buyer who had not released them. Authorization that depends
  on case history cannot be checked by reading a record.

## Repo map

```
AGENTS.md / CLAUDE.md     instructions for a coding agent (CLAUDE.md is a symlink)
SPEC.md                   the prescriptive process, with requirement IDs
facts.yaml                every policy number, plus the declared tie-break ordering
NOTICE                    attribution for every third-party dataset
logs/
  README.md               provenance, licenses, measured facts, exclusion list
  download.py             fetch by DOI, verify against manifest.json
  sample.py               derive the committed snapshot + SAMPLE.md disclosure
  snapshot/               the pinned CC BY 4.0 BPI 2019 sample (committed)
  otlp/                   the same log as OTLP spans; only the 50-case fixture is committed
  frames/                 the same log as CSV tables for pandas; nothing committed but its README
  to_csv.py               XES -> two CSV tables + a dtypes sidecar, streamed
  helpdesk/               the MIT Helpdesk log: the fast fixture (committed)
process/                  THE MINING LIBRARY -- PyYAML is its only dependency
  log.py                  the columnar EventLog; the tie-break audit
  store.py schema.sql     the SQLite event store; every log side by side
  xes.py csvio.py         streaming IEEE-XES and CSV, both directions
  otlp.py                 EventLog <-> OTLP/JSON spans, both directions
  dfg.py filters.py       directly-follows graph; sublog selection
  variants.py             variants, coverage curve, rework
  rules.py                declarative P2P control conformance
  viz.py                  DOT for real graphs, hand-written SVG for small ones
  cli.py                  python -m process <command>
seed/
  controls.py             the pure control oracle: match, tolerance, approval, SoD
  generate.py             deterministic world; pinned demo items
  policies.py             the policy corpus, rendered from facts.yaml
  validate.py             fails the seed if a document disagrees with the facts
agent/
  auth.py                 the access matrix, including the stateful duty rule
  tools.py                the five tools; clear_invoice's check order is spec'd
  agent.py                prompt, prompt_version, the _call() seam, the loop
  model_anthropic.py      the live Claude Sonnet adapter behind the Model protocol
  scripts.py              scripted sessions, including deliberate failure fixtures
  killswitch.py           off / clearing / payments / readonly
observability/
  spans.py schema.sql     OTel-shaped spans into local SQLite; AUTHORITATIVE
  instrument.py           real OTel spans -> local Langfuse; no-ops with no SDK
  docker-compose.yml      Langfuse v3 for reading traces; ports shifted by one
server/
  app.py                  POST /sessions, POST /sessions/{id}/messages
  sessions.py             conversation history; a session is not a case
bridge/
  spans_to_log.py         *** spans -> event log: the case notion and the alphabet
  activity_map.yaml       which tools are business activities, and which are not
  cost.py                 tokens -> money, per activity / case / variant
analysis/
  normalize.py            one normalized trace record, with process features
  review.py               open coding, sorted so the interesting traces come first
  state/                  append-only annotations, labels, and the mode taxonomy
scenarios/
  schema.py validate.py   the scenario record, checked against the world
  runner.py               runs a dataset through the HTTP endpoints
  export_langfuse.py      pulls the traces back; fails on a missing one
  skill/SKILL.md          how to write a scenario whose answer key is grounded
reports/smoke.sql         what a run contained, in ClickHouse SQL
homework/module-1/        the handouts, and the xfail hole patches
tests/                    offline; no API keys; 188 tests
```

### Reading the agent's traces

Everything above runs with no Docker. For a trace UI, the agent also exports
real OpenTelemetry spans to a local Langfuse:

```bash
docker compose -f observability/docker-compose.yml up -d
#   http://localhost:3001  ->  student@example.com / matchbook-dev-pass
uv run uvicorn server.app:app --port 8010
```

Ports are shifted by one from upstream so this stack can coexist with another
Langfuse instance on the same machine. With the stack down, or the `LANGFUSE_*` variables
unset, `instrument.py` records nothing and every command above still works --
`tests/test_offline_mining.py` blocks `opentelemetry` and `fastapi` by name and
mines a real log anyway.

Two recorders, one decision: the SQLite span store stays authoritative, because
the bridge mines the event log from it and `bridge/cost.py` prices it. Both go
through `record_tool_result` and `record_activity`, so they cannot disagree
about what reached the log.

One number that disagrees on purpose. On a live `diagnose_block` run Langfuse
priced the session at **$0.005544** and `bridge.cost` at **$0.0106**. Langfuse
is pricing exactly what `gen_ai.usage.input_tokens` means; it is missing the
1,867 cache tokens, which sit under `mb.*` because Anthropic reports its four
counts as disjoint sets billed at three different rates. Summing them into the
standard field would publish a number no provider reported. **`bridge.cost` is
authoritative for money; the trace UI is authoritative for shape.**

## What is not built yet

Milestone 1 is the vertical slice. Still to come, in order: discovery (process
tree, inductive cuts, Petri nets, token replay) validated against the CC0
Process Discovery Contest corpus; alignments and precision; the k-rollout
variability engine; the fitted synthetic twin with its own ground-truth answer
key; the live chat + process-map explorer; and the object-centric view. See
`design.md`.

The course layer (milestone 8) is partly built: `homework/module-1/` holds the
three Module 1 handouts with generated `xfail` hole patches, and `scenarios/`
runs a dataset through the endpoints. Modules 2, 3 and 5 are still the sibling
course's text and are rewritten when reached -- judges and `optimize/` with
them.

## Data and licensing

Read [logs/README.md](logs/README.md) before touching anything under `logs/`. In
short: **BPI Challenge 2019** (van Dongen 2019, 4TU.ResearchData,
doi:10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1) is **CC BY 4.0**, which
permits commercial use, redistribution and derivatives with attribution. That is
why Matchbook is built on it and not on the better-known logs — most of those
carry the legacy 4TU general terms, which grant no redistribution, and every
BPI 2020 log is CC BY-NC.

One limitation, stated up front: **BPI 2019 contains no natural language.** It
grounds the activity alphabet, control flow, timing, the human/batch split and
the exception mix. It cannot ground a single utterance, so the policy corpus and
the users' messages are authored from `facts.yaml` and `SPEC.md`. The process
realism is real; the conversational realism is ours. Claim validity only for the
first.
