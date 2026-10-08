# Matchbook

A purchase-to-pay agent, a hand-written process-mining library, and a real
human event log of the same process — so an agent's behaviour can be measured
against how people actually ran the work.

Matchbook is a sibling of the Oakline course repository. Oakline teaches how
to evaluate an agent's *answers*. Matchbook teaches how to see, measure and
govern the *process* an agent executes.

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

No a third-party mining framework, no commercial mining tool. XES parsing, the log representation,
discovery, conformance and rendering are all original code here. That costs
effort and buys three things: the IP is ours; the live process-map app can be
hosted publicly (a third-party mining framework is AGPL-3.0-or-later, whose §13 network clause would
otherwise oblige offering the whole application's source to every visitor); and
the algorithms become the teaching content rather than a library call.

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

## The agent, end to end with no API key

The agent half runs from scripted sessions, so the whole pipeline — world,
tools, spans, the bridge, the mined log, conformance, failure analysis — is
reproducible with no model key and no budget. A live model adapter plugs into
the same `Model` protocol in `agent/agent.py`.

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
  helpdesk/               the MIT Helpdesk log: the fast fixture (committed)
SPEC.md                   the prescriptive process, with requirement IDs
process/                  THE MINING LIBRARY -- stdlib only
  log.py                  the columnar EventLog; the tie-break audit
  store.py schema.sql     the SQLite event store; every log side by side
  xes.py csvio.py         streaming IEEE-XES and CSV, both directions
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
  scripts.py              scripted sessions, including deliberate failure fixtures
  killswitch.py           off / clearing / payments / readonly
observability/
  spans.py schema.sql     OTel-shaped spans into local SQLite; no Docker
bridge/
  spans_to_log.py         *** spans -> event log: the case notion and the alphabet
  activity_map.yaml       which tools are business activities, and which are not
analysis/
  normalize.py            one normalized trace record, with process features
  review.py               open coding, sorted so the interesting traces come first
  state/                  append-only annotations, labels, and the mode taxonomy
tests/                    offline; no API keys; 87 tests
```

## What is not built yet

Milestone 1 is the vertical slice. Still to come, in order: discovery (process
tree, inductive cuts, Petri nets, token replay) validated against the CC0
Process Discovery Contest corpus; alignments and precision; the k-rollout
variability engine; the fitted synthetic twin with its own ground-truth answer
key; the live chat + process-map explorer; the object-centric view; and the
course layer of handouts and judges. See `design.md`.

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
