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
process/                  THE MINING LIBRARY -- stdlib only
  log.py                  the columnar EventLog; the tie-break audit
  store.py schema.sql     the SQLite event store; every log side by side
  xes.py csvio.py         streaming IEEE-XES and CSV, both directions
  dfg.py filters.py       directly-follows graph; sublog selection
  variants.py             variants, coverage curve, rework
  rules.py                declarative P2P control conformance
  viz.py                  DOT for real graphs, hand-written SVG for small ones
  cli.py                  python -m process <command>
tests/                    offline; no API keys
```

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
