# Matchbook — an open-data agentic process-mining repo

*(Working title. It evokes the three-way match at the centre of the process and a book of records. Swap freely.)*

## Context

You need a second repo that does for a **purchase-to-pay process** what `matchbook` does for customer support — same conventions, same homework mechanics, same tracing and failure-analysis workflow — but built on **openly licensed real data** so it can serve three audiences at once: a business demo you show prospects, teaching material for students, and the substrate for your own failure/error-analysis work.

Two things make this more than a Oakline clone:

1. **A real human event log as a normative model.** A public log and an agent's traces are two recordings of the *same* business process — one executed by people in a real company, one by an LLM agent. That gives conformance checking a reference model you did not invent, so "the agent released a payment before the goods receipt" becomes a measured deviation instead of an opinion.
2. **Every algorithm is yours.** No a third-party mining framework, no commercial mining tool. The repo hand-writes XES parsing, discovery, conformance and visualization. That costs more effort and buys three things: you own the IP outright; you can host the live chat + process-map app publicly (a third-party mining framework is AGPL-3.0-or-later, whose §13 network clause would otherwise oblige you to offer the whole application's source to every visitor); and the algorithms become the teaching content rather than a library call.

The Oakline repo already contains **`design.md`**, an earlier design for a sibling repo ("Switchboard": IT-helpdesk flagship, a third-party mining framework-based, data download-only). This plan supersedes it on three points — the domain, the mining library, and the data posture — and keeps the rest, which is still right: the central idea, the Oakline pattern-reuse table, the four variability sources, local SQLite span capture with no Docker, and the dataset exclusion list. **Fold `design.md` into the new repo's `design.md` rather than leaving two documents to drift.**

### Decisions already settled

| Decision | Choice |
| --- | --- |
| Process + dataset | **BPI Challenge 2019** purchase-to-pay (SAP procurement), CC BY 4.0 |
| Mining library | **Hand-written**; no a third-party mining framework or other process-mining framework |
| Data posture | Pinned sampled snapshot committed + full-log downloader with hash manifest + fitted synthetic twin |
| Repo relationship | **New standalone repo**, mirroring Oakline's structure and conventions |
| First milestone | **Thin end-to-end vertical slice** through every layer |

---

## The dataset dossier

Record this verbatim in `logs/README.md`; it is the compliance artifact that lets you use the repo commercially.

**BPI Challenge 2019** — van Dongen, Boudewijn (2019), 4TU.ResearchData.
DOI `10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1` · landing page `data.4tu.nl/articles/dataset/BPI_Challenge_2019/12715853`
**License: CC BY 4.0**, verified on the landing page — commercial use, redistribution and derivatives all permitted with attribution. That is what makes it usable for paid demos and customer solicitation, and it is *why this log and not the better-known ones*.

Scale and shape:

- 76,349 purchase documents → **251,734 item-level cases** → **1,595,923 events** across **42 activities**
- **627 resources: 607 human users + 20 batch (bot) users** — a built-in human/automation split, and a rare one
- A multinational coatings and paints company, 60 subsidiaries; IEEE-XES XML, ~728 MB uncompressed
- Case id is the pair (purchasing document, item), so the log is **naturally object-centric**: purchase document, item, goods receipt, invoice, vendor
- Case attributes: `Purchasing Document`, `Item`, `Item Type`, `Item Category`, `GR-Based Inv. Verif.`, `Goods Receipt`, `Source`, `Doc. Category name`, `Company`, `Spend classification text`, `Spend area text`, `Sub spend area text`, `Vendor`, `Name`, `Document Type`
- **Four documented matching flows** (`Item Category`) — a ground-truth categorisation you get for free:
  1. 3-way match, invoice **after** goods receipt — GR value, invoice value and PO creation value must agree
  2. 3-way match, invoice **before** goods receipt — invoice blocked until goods arrive and values verify
  3. 2-way match, no goods receipt — invoice must match creation value, in full or partially until the PO value is consumed
  4. Consignment — no invoices at PO level; handled in a separate process
- Frequent activities include `Create Purchase Order Item`, `Record Goods Receipt` (~19.7% of events), `Record Invoice Receipt`, `Clear Invoice`, `Remove Payment Block`, `Create Purchase Requisition Item`, `Receive Order Confirmation`, `Change Quantity`, `Change Price`, `Vendor creates invoice`, `Delete Purchase Order Item`, `Cancel Goods Receipt`

**Known data-quality traits — curriculum, not defects to hide.** Day-granularity timestamps on several activities, so intra-day order is ambiguous; events recorded by vendors rather than employees; monetary values anonymized by a linear translation (internally consistent, not real currency); cancelled and deleted items; duplicate invoice events; items whose flow type contradicts their event sequence. Oakline's `data_quality_cases` table is the precedent — document each defect as intentional data with an `expected_handling` so nobody mistakes corruption for ground truth.

**Also screened. Keep the exclusion list in `logs/README.md` so nobody re-adds them.**

| Dataset | License | Verdict |
| --- | --- | --- |
| All five BPI 2020 travel-reimbursement logs | **CC BY-NC 4.0** | Excluded — non-commercial. Confirmed on the Domestic Declarations landing page. The otherwise-ideal approval-chain process. |
| BPI 2012 / 2013 / 2014 / 2015 / 2017, Sepsis, Road Traffic Fines, Hospital Billing | legacy **"4TU General Terms of Use"** | No explicit commercial or redistribution grant. Reference by DOI only; never commit the file. |
| CRMArena / CRMArena-Pro | CC BY-NC | Excluded — non-commercial. |
| Process Discovery Contest logs (2016 … 2025) | **CC0** | Safe, and more useful than it looks. **PDC 2020** ships 192 training logs, 192 test logs, 192 ground-truth logs and **96 original workflow nets in PNML**; **PDC 2021** ships 480/96/96 plus 96 models; PDC 2016 ships 10/10/10 with the per-trace boolean `pdc:isPos` flag. This is the only process-mining data with a published answer key — make it the **correctness harness** for the hand-written miner and the alignment classifier (see Risks), not a footnote. |
| OCEL 2.0 Order Management | **CC BY 4.0** | Safe. The second domain when object-centric mining arrives; ships a CPN generative ground truth. |
| τ-bench retail | **MIT** | Safe. An already-agentified process with a written policy doc; a capstone comparison. |
| Helpdesk log, Mendeley mirror `10.17632/39bp3vv62t.1` | **MIT** | Safe; was `design.md`'s flagship. Keep as the **small fast fixture** — 3,804 cases beats 251,734 in a test suite. |
| Event Graph of BPI Challenge 2019, DOI `10.4121/14169614` | **CC BY 4.0** | Not a dependency; an independent object-centric derivation of the same log (1.93M nodes, 15.1M relationships, Neo4j/GraphML) — useful to cross-check your own object-centric view. |

**Operational notes.** The 4TU download endpoint currently returns 503 with `Retry-After: 3600` and the landing page reports files unavailable during storage maintenance. So: the downloader must fail with a clear, actionable message; `logs/manifest.json` pins expected sizes and SHA-256s; and the **committed snapshot is what keeps a fresh clone working** regardless of upstream availability. The CC BY attribution block belongs in `NOTICE`, in `logs/README.md`, and in the header of every derived artifact the repo ships.

### The one honest limitation, stated up front

**BPI 2019 contains no natural language and no conversations.** It constrains control flow, timing, resource behaviour, the human/bot split and the exception mix — exactly what process mining needs — but the conversational layer must be **authored, not derived**. Therefore:

- The policy manual (`facts.yaml` + rendered policy docs) is written from standard P2P controls — three-way-match tolerances, approval limits, payment terms, segregation of duties. It is the normative source the agent is judged against, and it is *your* IP, not a derivative of the log.
- The agent's users (buyer, AP clerk, controller, vendor) and their messages are synthetic, generated from the log's own case attributes and exception patterns, so the *distribution* of situations is real even though the wording is not.
- Say this in `SPEC.md` and in the handouts. Framed correctly it is a strength: real structural ground truth, language grounded in it, and a synthetic generator fitted to measured distributions rather than invented.

---

## Repo layout

```
AGENTS.md / CLAUDE.md      routing + shared invariants (CLAUDE.md a relative symlink)
README.md  SPEC.md  facts.yaml  NOTICE  design.md
logs/
  README.md                provenance, license dossier, exclusion list
  download.py              DOI fetch into logs/raw/ (gitignored), verifies manifest
  manifest.json            expected file names, sizes, SHA-256
  snapshot/                committed pinned sample + attribution header
  helpdesk/                the MIT log: the fast test fixture
process/                   THE MINING LIBRARY — no heavy deps, importable alone
  log.py store.py xes.py csvio.py
  dfg.py filters.py variants.py performance.py resources.py
  tree.py inductive.py petrinet.py
  replay_token.py align.py precision.py
  rules.py conformance.py
  ocel.py viz.py cli.py
bridge/
  spans_to_log.py          *** spans -> canonical event log: the missing converter
  activity_map.yaml        tool/span -> business activity alphabet
agent/                     agent.py tools.py(holes) auth.py db.py policy.py
                           guards.py killswitch.py approvals.py cli.py
server/app.py              authenticated session + message routes
observability/
  spans.py schema.sql instrument.py    OTel -> local SQLite, no Docker
seed/
  controls.py              the pure oracle (match, tolerance, approval, SoD)
  generate.py fit.py simulate.py policies.py validate.py
scenarios/ replay/ analysis/ explore/ eval_cases/ tests/ homework/module-1..N/
```

Keep every Oakline idiom the subagent catalogued: `facts.yaml` → rendered policy docs → `seed/validate.py` fails the seed on mismatch; `SPEC.md` requirement IDs plus a spec→implementation table and the statement that the app never reads it at runtime; the logic/wrapper tool split with one `_call()` dispatch seam that converts `NotImplementedError` into a structured result and records span attributes; `{"ok", "error", "reason"}` results; `AuthContext` injected by the server and never from chat; env-var path indirection (`$MB_DB`, `$MB_POLICIES_DIR`, `$MB_ANALYSIS_STATE`) so a deterministic reseed *is* the sandbox reset; a fixed `WORLD_ASOF` and never `now()`; a short hashed `prompt_version` stamped on traces; append-only file-backed state; three test tiers with `xfail` homework holes and the `world` / `world_copy` / `analysis_state` fixture trio.

---

## Technical core

### 1. Canonical event log — one schema for three logs

SQLite at `logs/eventstore.db`, serving the real log, the synthetic log and the agent's mined log identically.

| Table | Columns |
| --- | --- |
| `logs` | `log_id PK, source, doi, license, attribution, sha256, ingested_at, event_count, case_count` — the provenance table; every derived artifact cites a `log_id` |
| `events` | `event_id PK, log_id, case_id, activity_id, ts, ts_precision, seq, resource_id, resource_kind, lifecycle, value_cents, attrs JSON` |
| `cases` | `log_id, case_id, variant_id, start_ts, end_ts, event_count, attrs JSON` |
| `activities` | `log_id, activity_id, name` — interned, so the hot path compares ints |
| `resources` | `log_id, resource_id, name, kind` (`human` / `batch` / `vendor` / `agent` / `tool`) |
| `variants` | `log_id, variant_id, activity_sequence, case_count` |
| `objects` | `log_id, object_id, object_type, attrs JSON` — OCEL side |
| `event_objects` | `event_id, object_id, qualifier` |

Indexes: `(log_id, case_id, seq)`, `(log_id, activity_id)`, `(log_id, ts)`, `(log_id, resource_id)`, and both directions of `event_objects`. Ingestion is chunked with `executemany` inside one transaction, `PRAGMA journal_mode=WAL`, `synchronous=OFF` during load.

In memory, `process/log.py` holds a **columnar `Log`**: interned `activity_id` / `resource_id` as `array('i')` or numpy `int32`, timestamps as epoch ints, plus a `case_offsets` index so a case is a slice rather than a dict. A trace is a tuple of ints; a variant is that tuple. This is what makes 1.6M events tractable in pure Python — never a list of per-event dicts.

### 2. Ingestion

`process/xes.py` is a **streaming** reader built on `xml.etree.ElementTree.iterparse`, clearing elements as it goes so peak memory stays flat on a 728 MB file; it also writes XES so your logs round-trip into other tools (a credibility feature in a demo). `process/csvio.py` is the fast path and the format the committed snapshot ships in.

BPI19 mapping: `case_id = f"{Purchasing Document}-{Item}"`, with `Purchasing Document` also kept as an object so the PO-level grouping survives; `activity ← concept:name`; `ts ← time:timestamp`; `resource ← org:resource`, `resource_kind` resolved from a committed batch-user list; `value_cents ← the event's cumulative net worth attribute`; flow type ← `Item Category`.

**Timestamp ties are made auditable, not hidden.** Sort key `(ts, activity_rank, source_order)` where `activity_rank` comes from a declared ordering in `facts.yaml`; `ts_precision` records `day` vs `second`; and ingestion emits `logs/reports/tie_breaks.csv` naming every case whose order was decided by the tie-break. Any conformance result computed on tied events cites that file.

### 3. The mining library

**Graphs and statistics** — `dfg.py` (directly-follows counts plus duration aggregates per edge), `filters.py` (activity, path, variant, attribute, timeframe, case-length, and *flow-type* filters, all returning a new `Log` view over the same columns), `variants.py` (variant extraction, coverage curve, rework/self-loop and repetition metrics, trace clustering on variant-prefix or bag-of-activity distance), `performance.py` (service vs waiting time, per-edge sojourn quantiles, bottleneck ranking, throughput by flow type), `resources.py` (handover-of-work matrix, batch-user share per activity, resource-activity profile, and the segregation-of-duties matrix the controls need).

**Discovery** — an inductive-miner-style recursive miner:
- `tree.py`: `ProcessTree(operator, children)` with `->` sequence, `X` exclusive, `+` parallel, `*` loop, and `tau`.
- `inductive.py`: on each sublog build the DFG, drop edges below a frequency threshold (the IMf knob, exposed as `--noise`), then try cuts in order — **exclusive** (connected components of the undirected DFG), **sequence** (condense strongly connected components, topologically order, require one-directional reachability between groups), **parallel** (every pair connected in both directions, with each part containing a start and an end activity), **loop** (body partition containing all start/end activities, redo parts reachable only from body ends back to body starts). Base cases: empty log, single activity, single activity with repetition. Fallthroughs, in order: activity-once-per-trace, tau-loop, and finally a flower model — and `--noise` plus the fallthrough actually taken are recorded in the model artifact, because a flower model that nobody noticed is the classic way a fitness number becomes meaningless.
- `petrinet.py`: recursive block construction tree → Petri net, with markings and a PNML writer.

**Conformance, in two layers that coexist deliberately.**

*Layer A — declarative control rules (`rules.py`), the cheap, business-legible half, and the one that earns the demo.* Each rule is a pure function over one case's events plus attributes, returning violation records. Start with: no payment clearing before goods receipt for GR-based items; invoice value within tolerance of PO value; three-way match complete before payment for categories 1 and 2; segregation of duties across create / receive / release; no price change after invoice receipt without re-approval; payment block removed only by an authorised role; duplicate invoice within the lookback window; maverick buying (PO created after the invoice, no requisition). These same functions serve three roles at once — conformance metrics on the human log, the agent's own guardrails, and the machine checks in eval cases.

*Layer B — model-based conformance.* `replay_token.py` for token-based replay fitness (cheap, runs at full scale), `align.py` for alignments, `precision.py` for escaping-edges precision. Alignments are the expensive part, so scope them honestly: A* over the synchronous product with unit costs for log and model moves and zero for synchronous moves, an admissible remaining-work heuristic, **per-variant alignment with a cache** (variants, not cases, are the unit of work), a state-exploration cap that degrades to token replay with the degradation recorded, and a documented workflow of aligning a filtered sublog or the variants covering the top *n*% of cases. Do not promise exact alignments over all 251,734 cases.

**Visualization** — `viz.py` emits **DOT** as the primary artifact (graphviz optional, never imported by the mining core) and hand-rolled **SVG** for the DFG and the Petri net so the hosted explorer has zero system dependencies. Oakline's `monitoring/chart.py` is the precedent for hand-written SVG.

**Object-centric** — `ocel.py` derives objects (purchase document, item, goods receipt, invoice, vendor) and event-to-object relations from the same store, and computes an object-centric DFG. This is the lens that actually fits agent traces, where one conversation touches several business objects.

### 4. The bridge: spans → event log

The heart of the project, and the piece that does not exist anywhere as open source.

| Canonical field | Source |
| --- | --- |
| `case_id` | **`mb.case_id` = (purchasing document, item)** — the business object, *not* the trace id |
| `activity` | `bridge/activity_map.yaml`, lifting tool spans onto the human log's alphabet (`post_goods_receipt` → `Record Goods Receipt`, `remove_payment_block` → `Remove Payment Block`, `change_po_price` → `Change Price`) |
| `ts`, `lifecycle` | span start / end → `start` and `complete` |
| `resource`, `resource_kind` | `agent:<model>`, `tool:<name>`, `human:<role>` |
| `value_cents`, tokens, cost | `gen_ai.usage.*` and the tool result |

Two decisions the handouts must make the reader confront rather than hand them: **the case notion** (a conversation is not a case; picking the business object is what makes the two logs comparable, and is why the object-centric module later models conversation, item and invoice as separate objects) and **activity abstraction** (not every LLM call is a business activity). Model-deliberation spans go to a *separate* `agent-internal` log so the comparable log keeps a shared alphabet; mixing them is the most common way this comparison is silently broken.

### 5. The agentic system

**Matchbook AP exception desk.** Roles: `buyer`, `ap_clerk`, `controller`, `vendor` (narrow read-only scope). The work is genuine exception handling: an invoice is blocked, and someone must find out why and resolve it within policy.

Tools (five are homework holes; the shape mirrors Oakline's logic/wrapper split):

| Tool | Risk | Notes |
| --- | --- | --- |
| `get_purchase_item(doc, item)` | read | authorised item record with flow type and match state |
| `list_open_exceptions(filter)` | read | the work queue |
| `get_invoice(invoice_id)` / `get_goods_receipt(gr_id)` | read | |
| `search_policy(query)` / `get_policy(id)` | read | BM25 over the rendered policy corpus |
| `check_three_way_match(doc, item)` | read | delegates to the pure oracle in `seed/controls.py` |
| `explain_blocked_payment(doc, item)` | read | structured rule-engine output, not prose |
| `request_vendor_credit_note(...)` | write | |
| `remove_payment_block(doc, item, reason)` | write | authorisation + value threshold + SoD |
| `change_po_price(doc, item, new_value, reason)` | write | tolerance check, re-approval above it |
| `post_goods_receipt(...)` | write | **AP clerks may not** — the SoD lesson, enforced in code |
| `escalate_to_controller(summary)` | write | |
| `mine_process(question)` | read | the differentiator: the agent queries your own mining library about its own process |

`facts.yaml` holds every number: `match_tolerance_percent`, `match_tolerance_abs_usd`, `payment_release_auto_approve_usd`, `payment_terms_days`, `early_payment_discount_percent` / `_days`, `gr_required_item_categories`, `duplicate_invoice_lookback_days`, `segregation_of_duties` rules, `activity_rank` for tie-breaking. `seed/controls.py` is the pure oracle with no framework imports — the seeder's stamp source, the tool's rule, and the test oracle, exactly as `seed/eligibility.py` is in Oakline. Authorization lives in `agent/auth.py` and each tool, never in the prompt. The human-approval gate covers payment release above the threshold and price changes beyond tolerance, two-stage as in Oakline (a pre-execution `needs_approval` pause, then an `approvals` audit table whose row plus the item's state *is* the record). The kill switch is a three-rung ladder on one env var: `off` / `writes` / `readonly`.

**The failure modes this design makes reachable** — the reason the repo exists: releasing payment without a goods receipt; a miscomputed tolerance; the agent performing both sides of a segregated duty; accepting a duplicate invoice; trusting the vendor-supplied value over the PO; claiming an action succeeded before the tool confirmed it; a policy claim with no citation; assuming the wrong flow type (consignment handled as three-way); resolving an exception that required controller approval.

**Generating more synthetic data, two distinct ways.**
*Agent traces:* dimension-driven scenarios (flow type × exception type × value band × vendor behaviour × requester role × injected data defect), where the dimension *values and their frequencies come from the real log*, executed through the HTTP endpoint, captured as spans, normalized into Oakline's trace record shape so the Module 2 review-and-judge workflow applies unchanged.
*Synthetic event logs:* `seed/fit.py` measures the real log — activity alphabet, per-flow-type variant distribution, DFG transition probabilities, inter-event duration distributions per edge, resource assignment including the batch-user share, value distribution per spend area — and `seed/simulate.py` samples new cases from it. **Fidelity is reported, not asserted**: `build/fidelity.md` compares synthetic against real on variant-distribution distance, DFG edge-weight correlation and cycle-time quantiles. That is what makes "I can generate more data" defensible.

---

## Milestone 1 — the thin end-to-end slice

One vertical cut through every layer, in this build order.

| # | Layer | Files | Done when |
| --- | --- | --- | --- |
| 1 | Scaffolding | `pyproject.toml`, `AGENTS.md`, `CLAUDE.md` symlink, `README.md`, `NOTICE`, `logs/README.md` | `uv sync` works; the license dossier is written |
| 2 | Data in | `logs/download.py`, `logs/manifest.json`, `logs/snapshot/`, `logs/helpdesk/` | snapshot present; downloader fails *clearly* against the 503 |
| 3 | Canonical log | `process/log.py`, `process/store.py`, `process/xes.py`, `process/csvio.py` | helpdesk log and the BPI19 snapshot both ingest; `tie_breaks.csv` written |
| 4 | Minimal mining | `process/dfg.py`, `process/variants.py`, `process/filters.py`, `process/viz.py` | DFG SVG + variant table for both logs |
| 5 | Rule conformance | `seed/controls.py`, `process/rules.py` (3 rules), `process/conformance.py` | a violation report over the real log |
| 6 | Policy + world | `facts.yaml`, `SPEC.md`, `seed/policies.py`, `seed/validate.py`, `seed/generate.py` | deterministic seed; two runs byte-identical; validator fails on a planted mismatch |
| 7 | Agent | `agent/auth.py`, `agent/db.py`, `agent/killswitch.py`, `agent/agent.py`, `agent/tools.py`, `agent/cli.py` | 5 tools, one of them a write gated by authorization and threshold |
| 8 | Spans | `observability/spans.py`, `schema.sql`, `observability/instrument.py` | a CLI run writes spans to SQLite, no Docker |
| 9 | The bridge | `bridge/spans_to_log.py`, `bridge/activity_map.yaml` | the agent's run becomes a canonical log in the same store |
| 10 | The payoff | `process/cli.py` | rule conformance reported for the human log and the agent log **side by side** |
| 11 | Failure analysis | `analysis/` (port the normalizer + state layer) | 20 traces reviewed, 3 candidate failure modes, labels appended |

**Out of scope for milestone 1**, deliberately: the inductive miner and Petri nets, alignments and precision, the object-centric view, the synthetic-log fitter, the `explore/` chat + live map UI, LLM judges, `optimize/`, and the homework handouts. Each is a later milestone; none is needed to prove the pipeline.

### Verification

```bash
uv sync
uv run python -m logs.download --log helpdesk            # MIT copy, checksum verified
uv run python -m process ingest logs/helpdesk --log-id helpdesk
uv run python -m process ingest logs/snapshot --log-id bpic19-sample
uv run python -m process dfg bpic19-sample --out build/human.svg
uv run python -m process variants bpic19-sample --top 20
uv run python -m process conform bpic19-sample --rules all --out build/violations.csv

uv run python -m seed.generate                           # deterministic world
uv run pytest                                            # offline, no API key
uv run python -m seed.generate && uv run python -m seed.generate   # byte-identical

uv run python -m agent.cli --role ap_clerk --user 1 --debug        # writes spans.db
uv run python -m bridge.spans_to_log spans.db --log-id agent-run-1
uv run python -m process conform agent-run-1 --rules all
uv run python -m process compare bpic19-sample agent-run-1         # the payoff
```

Acceptance: the mining path runs with **no API key and no Docker**; the agent's run appears in the same event store as the human log under a shared activity alphabet; and at least one control rule reports a violation on each log, with the human log's violations traceable to real cases.

---

## Span export, and the one trap waiting in it

`process/otlp.py` exports any `EventLog` as OTLP/JSON spans -- one trace per
case, one zero-duration span per event -- so the human log and the bridged agent
log open in the same OTLP tooling. It is hand-written and stdlib-only, and
`tests/test_offline_mining.py` writes and re-reads spans with the
`opentelemetry` import blocked, which is the claim worth testing.

Not built, deliberately: a **`spans.db` -> OTLP** exporter that would emit model
spans as `gen_ai.*` generations carrying token usage and cost. That is the more
valuable artifact, and it has a trap in it. Matchbook's four token fields are
**disjoint** (the Anthropic convention: prompt tokens = input + cache_read +
cache_write, see `agent/agent.py` and `observability/schema.sql`), whereas the
OTel GenAI semantic convention treats cache counts as **subsets** of the input
total. An exporter must therefore sum them, not copy them across. Copying would
under-report input tokens on every cached call and the number would still look
plausible, which is the worst kind of wrong.

## Later milestones

| M | Delivers |
| --- | --- |
| 2 | Discovery: process tree, inductive cuts, Petri net, token replay, PNML — validated against the PDC corpus |
| 3 | Alignments + precision, the deviation taxonomy, and the **human-model vs agent-log** conformance report that is the project's headline |
| 4 | Variability engine: k-rollout harness, pass^k, unique-sequence count, step variance, first-divergence — variant analysis of the agent's own runs |
| 5 | `seed/fit.py` + `seed/simulate.py` + `build/fidelity.md`: the synthetic twin, measured |
| 6 | `explore/`: chat left, live process map right — the demo surface, hostable because nothing is AGPL |
| 7 | Object-centric: OCEL view, OC-DFG, and the second domain (OCEL 2.0 Order Management) |
| 8 | Course layer: homework handouts, `xfail` holes, reference patches, judges, `optimize/` — **Module 1 delivered**, see below |

## Risks

- **Correctness without a reference implementation.** Hand-written discovery and conformance can be subtly wrong with no a third-party mining framework to check against. This is the single biggest risk, and it has a real mitigation: build the **CC0 Process Discovery Contest** corpus into the test suite from the start. Discover a model from each training log, classify its test log, and score against the ground-truth log — PDC 2020/2021 give 96 original PNML nets per year, so you can also compare your discovered structure against the generating model directly. Cross-check variant and DFG counts against the published BPI 2019 challenge submissions, and the object-centric derivation against the CC BY 4.0 Event Graph of BPI 2019. Do this before the mining library grows, not after.
- **Alignment tractability.** A*-based alignments at this scale are a research workload. The per-variant cache, the exploration cap, and the token-replay fallback are not optional extras; they are the design. Promise filtered-sublog alignments only.
- **Pure-Python performance.** The columnar representation is the mitigation; if ingest or DFG construction misses target, add numpy (BSD) before considering anything heavier, and keep `process/` importable without it.
- **Upstream availability.** 4TU is mid-maintenance and returning 503. The committed snapshot is the insurance; take it as soon as a download succeeds and pin its hash.
- **Scope.** This repo's surface is several times Oakline's — a mining library, an agent, a bridge, and a course. The thin slice exists precisely to stop the mining library from absorbing all the effort before anything is demoable.
- **Synthetic-fidelity overclaiming.** Never present synthetic traces as real. `build/fidelity.md` with measured distances is the honest version, and it is a better demo artifact than a silent claim.
- **The authored language layer.** Repeated here because it will be the first question a sharp prospect or student asks: the process, timings and exception mix are real; the conversations and the policy manual are yours. Lead with that rather than being caught by it.

---

## Design refinements adopted after approval

From the detailed core-design pass. These change the plan; they are not restatements.

1. **Scope conformance asymmetrically.** Ship **rules + token replay** as the supported path. Run **alignments on the agent's log only** (short traces, ≤20 activities — entirely tractable) and treat alignments on the human log as an advanced module with documented caps. This removes the single largest technical risk and costs almost nothing pedagogically.
2. **The inductive miner needs a property test, not unit tests.** Random tree → generate a log from it → rediscover → assert language equivalence up to bounded length. A wrong cut implementation produces a *plausible-looking wrong model*, which is the worst failure kind. Build this in the same step as the miner.
3. **The synthetic log ships its own answer key.** `ground_truth` (per case: flow, generating variant, injected rule violations) and `ground_truth_model` (the generating tree + PNML). This is the PDC corpus's role, owned outright, and it makes every discovery and conformance exercise scoreable — precision/recall on the rule catalogue, discovered tree vs generating tree.
4. **Monetary values must be rescaled, not inherited.** BPI 2019's values are anonymized by a linear translation, so the fitted scale is meaningless. Fit the *shape*, rescale to the band declared in `facts.yaml`, and record the override in the params file. Writing approval limits against an anonymized scale would be nonsense.
5. **Segregation of duties makes authorization stateful.** Whether a caller may clear an invoice depends on what that caller already did *on that case*. This is the first authorization rule in either repo that is a **process** property rather than a record property — no prompt can enforce it, and it is the sharpest argument for the whole project.
6. **Approval decisions emit business events into the world's log** (`Remove Payment Block`, `Clear Invoice`, resource `human:controller`). Governance then becomes minable: queue waiting time is a bottleneck in the performance analysis, and the agent→human handover appears in the handover matrix. Same for the kill switch — run a scenario set with it off and on, diff the two DFGs, and the drill is a picture rather than an assertion.
7. **Three failure modes are findable only through the process view**: acting on the wrong item of a multi-item PO (needs the object graph), escalation avoidance (a *path*: `clear_invoice[paused] → remove_payment_block → clear_invoice`), and rework loops (a metric). A transcript reviewer reading the escalation-avoidance conversation sees a helpful assistant that eventually escalated. Demonstrate that asymmetry in the handouts by having a learner hunt it in transcripts first, then in a DFG.
8. **Soften the ownership claim on synthetic data.** "Derived statistics, attributed" — commit the fitted parameter file *with* the attribution block and never source rows. Do not claim no relationship to BPI 2019.
9. **Hand-written SVG only where it will look good**: the process tree, a filtered DFG of ≤15 nodes, and the charts. Emit DOT for everything else. A 42-activity unfiltered spaghetti DFG laid out by hand is days of work and an ugly result.
10. **Only four OCEL object types are real in BPI 2019** (purchase document, item, vendor, company). Goods receipts, invoices and approvals are real objects only in the synthetic and agent logs. Flag-gate any synthesis and record it. The honest lesson: object-centric mining is what you get when you *control the instrumentation*.
11. **Trim M1 further than planned**: 3 roles, 5 tools, exclusive + sequence cuts with a flower fallthrough only, 3 control rules. Steps for the mining half must pass with `openai-agents` uninstalled and no API key — enforce that with a test.
12. **Watch one circularity.** A case-history tool is needed for stateful SoD, but it must read the *world's business history*, never the agent's own trace log, or conformance against a log the agent can read becomes self-referential.
13. **Compare control flow across logs; compare performance only within a log.** BPI 2019's timing is ±1 day and its intra-day order is partly our own tie-break assumption, while the agent's spans are microsecond-precise.

### Resolved during implementation

- **Upstream availability is no longer a risk.** 4TU's `ndownloader` returns 503, but the records are figshare-backed and the mirror serves normally. `https://api.figshare.com/v2/articles/12715853` returns the license (`CC BY 4.0`) and the file MD5; `https://ndownloader.figshare.com/files/24072995` serves all 728,558,522 bytes. Both are in `logs/manifest.json` in preference order, and the full log is downloaded and verified (MD5 `4eb909242351193a61e1c15b9c3cc814`).
- **The Helpdesk log's MIT license is verified programmatically**, not inherited from a claim: the Mendeley API's `data_licence.description` is the verbatim MIT grant. Note the committed file is the *anonymized* three-column variant (activity names replaced by numeric ids, no resource attribute); the attribute-rich version is 4TU-GTU and not committable.
- **The real schema is read off the file, not from papers.** Trace: `concept:name` (already `<document>_<item>`), `Purchasing Document`, `Item`, `Item Type`, `Item Category`, `GR-Based Inv. Verif.`, `Goods Receipt`, `Source`, `Purch. Doc. Category name`, `Company`, `Spend classification text`, `Spend area text`, `Sub spend area text`, `Vendor`, `Name`, `Document Type`. Event: `concept:name`, `time:timestamp`, `org:resource`, `User`, `Cumulative net worth (EUR)`. Resources are named by convention (`batch_NN`, `user_NNN`), which is where the human/automation split comes from — no committed batch-user list needed.
- **Timestamps are minute-precision and ties are common** — the file's very first case has three events at `13:53:00`. The declared tie-break is load-bearing, and `EventLog.tie_broken` plus `tie_broken_cases()` make it auditable.

---

## Appendix: the superseded "Switchboard" design

An earlier design for this repo lives at
`matchbook/design.md` (working title *Switchboard*): an IT-helpdesk
flagship built on a third-party mining framework, with the data download-only. It is superseded on three
points — the domain (purchase-to-pay, not helpdesk), the mining library
(hand-written, not a third-party mining framework) and the data posture (a committed snapshot plus a
fitted synthetic twin, not download-only).

What it got right and this design keeps: the central idea that a public log and
an agent's traces are two recordings of one process; the table of patterns
reused from Oakline; the four sources of run-to-run variability (k repeats,
latitude designed into the policy, dimension-driven scenarios, free-form chat);
local SQLite span capture with no Docker; and the dataset exclusion list, which
this design extends with verified license findings.

---

## Corrections after milestone 1

A review of the milestone-1 code against the data and the specification found
the following. They are recorded rather than quietly patched, because several
of them are the kind of mistake that reads as a result.

### Wrong, and reported to the user as a finding

**The tolerance control could never fire, and its zero was mistaken for
compliance.** `CTRL-TOLERANCE` subtracted the value recorded at the goods
receipt from the value recorded at clearing. BPI 2019 carries a single
case-level figure (`Cumulative net worth (EUR)`) repeated on every event, so
the subtraction is structurally zero: measured over 40,000 cases the value
varies across events in 1.92% of them, and where it varies the values are exact
multiples (`[549, 1098]`, `[1009, 2018]`) — a second receipt, not a price
variance. In some long cases it decreases, so it is not even monotonic. Two
consequences: the rule was vacuous, and on a sample containing multi-receipt
cases it would have reported legitimate partial deliveries as tolerance
breaches. Rules can now raise `NotApplicable`, and a rule whose inputs are
absent declines to report rather than returning a clean zero. The rewritten
rule compares the receipt *total* against the invoice total, so a split
delivery is not a variance, and it runs normally on the synthetic and agent
logs where both values are recorded.

**The specification and the implementation disagreed about a money path.**
SPEC TOOL-6 said `clear_invoice` checks the amount third; the code checks it
last; and the docstring argued for the spec's order while the code did the
opposite. The implemented order is the right one — checking the amount earlier
queues an above-limit invoice that is also blocked or missing its goods
receipt, handing a controller a decision nobody can make — so the spec and the
docstring were corrected to match the code, and the order is now pinned by a
test instead of by prose.

### Would have invalidated evaluations built on it

**The pinned fixtures leaked their expected answer into the agent's input.**
Each pinned item used its fixture note as its item description, so the agent
asked to diagnose `4507001234_00010` read `"price variance outside tolerance;
the main demo item"` straight off the record. Descriptions are now plausible
and, for the multi-item trap, deliberately similar to the sibling's.

**The agent's log carried no `Item Category`,** so every flow-scoped control —
`CTRL-GR` among them, the most important one — had an in-scope population of
zero and silently found nothing. The bridge now resolves each item's business
attributes from the world, which is what makes "one rule definition judges the
humans and the agent alike" true rather than aspirational.

**A taxonomy entry cited a compliant run as a failure.**
`premature_success_claim` listed `above_limit` as an example; that run queues
the clearing and says "It has not been paid", which is exactly correct. A
taxonomy that mislabels a compliant run poisons every label and judge built on
it. It is now recorded as a counter-example, and the genuinely distinct mode it
was confused with (`queued_reported_as_done`) is its own entry.

### Would have broken on the full log

**A quadratic in `EventLogBuilder.build()`.** A membership set was rebuilt per
candidate inside a comprehension: 0.045s at 2,000 cases, 79s at 60,000, and
roughly 23 minutes at the full log's 251,734 — directly contradicting the claim
that the columnar design makes the full log tractable. `tests/test_scale.py`
now fails if doubling the case count takes more than 3x longer, and that guard
was checked against the reintroduced bug (it reported 6.1x).

**Duration buckets topped out below the log's own span.** 48 log-spaced buckets
reach 1.47 years; BPI 2019 spans about two, so the top bucket saturated and
every duration beyond 18 months was reported at the same quantile. Now 54
buckets, reaching about 15 years.

### Compliance, in a repo whose premise is compliance

**The committed MIT data shipped without the notice MIT requires.** The Helpdesk
log was redistributed with no copy of its licence or copyright notice, which the
MIT terms oblige. Added as `logs/helpdesk/LICENSE`. **The CC BY 4.0 subset had
its attribution only in `NOTICE` and `SAMPLE.md`;** it now also carries
`logs/snapshot/LICENSE` with the licence link and the explicit statement of
changes that CC BY 4.0 §3(a)(1)(B) requires. **The repository stated no licence
of its own,** so a student cloning it had no rights and no way to know; `LICENSE`
now states the position and flags the choice as pending and reversible.

### Smaller, but misleading

- The kill switch named three tools that do not exist, so `MB_KILL_SWITCH=payments` appeared to pause `remove_payment_block` and read as a stronger guarantee than it gave. The enforced set is now the real tools, with the planned ones documented separately and a test asserting the distinction.
- `facts.yaml` filed Consignment under "goods receipt not required", which made the matching controls treat it as a 2-way match. BPI 2019's documentation is explicit that consignment items carry no PO-level invoice at all; they are now excluded from invoice matching, and a test asserts the three flow buckets *partition* the observed categories.
- The `agent` optional-dependency group declared five packages and the `fast` group declared numpy, and **none of the six was imported anywhere**. Both removed; a dependency is declared when something imports it.
- `server` was listed as a wheel package before it existed or had an `__init__.py`. Removed, along with seven empty scaffolding directories that existed locally but not in a clone.
- `--sensitivity` lost the XES progress line in a refactor, leaving the 728 MB path silent for minutes.
- `process/__init__.py` claimed the core was importable with the standard library alone; `process.config` needs PyYAML.
- `case_offsets` was `[0, 0]` for an empty log where the invariant requires `[0]`.
- Functions encoding specified behaviour that no milestone-1 tool reaches — payment terms, early-settlement discounts, duplicate detection, and the role matrix for invoice recording, payment blocks and approvals — were untestable dead weight a reader could not distinguish from load-bearing code. They are now pinned by `tests/test_controls.py`.

---

## Milestone 8, part one: Module 1 of the course layer

Delivered: the three Module 1 handouts adapted from the sibling Oakline course,
generated `xfail` hole patches for both assignments that have them, the tracing
and HTTP layers the handouts assume, and the `scenarios/` package.

### What the adaptation actually changed

Most of it is substitution — five support tools become five purchase-to-pay
tools, three retail roles become buyer/ap_clerk/controller, three orders become
the five pinned items. Four things needed more than that:

1. **Oakline's fuzzy-search exercise has no analogue**, so HW1's centrepiece is
   `clear_invoice`'s nine ordered checks (SPEC TOOL-6) instead.
2. **`seed.validate` has no analogue** and now appears in Preparation, because a
   student should know before writing a tolerance check that they are
   implementing the same constant the policy document quotes and `process/rules.py`
   evaluates against 251,734 real cases.
3. **`activities_recorded` is a new field** in every record the handouts ask for.
   A reply is not evidence; a run claiming it cleared an invoice while
   contributing no `Clear Invoice` activity has said two things, and only one is
   checkable.
4. **Flow becomes a required scenario dimension**, because every control rule is
   flow-scoped and a dataset that is all 3-way exercises a quarter of the control
   logic while looking complete.

### Holes are not committed

Oakline ships its holes in the starter, which is why its suite is red on a
fresh clone. This repo cannot: `main` publishes a green suite and a README of
measured claims that depend on the code working. So each assignment ships a
generated patch pair, produced by an AST pass that empties a function body and
keeps its signature and docstring — the docstring *is* the contract, and
`clear_invoice`'s carries the nine-check order. Patches record the commit they
came from and are verified in both directions.

HW2's holes deliberately stop the agent running, unlike HW1's. The `_call` seam
absorbs a `NotImplementedError` from a *tool*, because a half-built agent that
still talks is useful. It does not absorb one from a *recorder*: instrumentation
that failed quietly would leave a partially recorded event log, and a log that is
wrong is worse than one that is missing.

### Measured while building it, not assumed

- **Langfuse v4 would have broken Homework 3.** v4 ingests our OTLP spans
  correctly — verified, 8 spans — but defaults to "events_only mode", where
  `/api/public/traces` refuses and the ClickHouse `traces` and `observations`
  tables stay empty while data lands in `events_full`. HW3 reads traces back out
  and its smoke report is SQL over those tables. Hence v3, which also matches the
  sibling course.
- **FastAPI was stealing the trace root.** Starlette 1.7 activates its own OTel
  middleware whenever an SDK is installed, so every trace arrived named
  `POST /sessions/{session_id}/messages` with `mb.session_message` as a child.
  Langfuse v3 reads a trace's name, session, user, tags, input and output from the
  ROOT span, so `sessionId`, `userId` and `tags` came back null and input/output
  empty — traces openable one at a time but not selectable in bulk, which is
  exactly what HW3's export and HW4's review need.
  `telemetry={"tracing": False}` fixes it.
- **A third-party cost figure understates this provider.** One live
  `diagnose_block` session: Langfuse $0.005544, `bridge.cost` $0.0106. Langfuse
  prices exactly what `gen_ai.usage.input_tokens` means and is missing the 1,867
  cache tokens, which sit under `mb.*` because Anthropic's four counts are
  disjoint and billed at three rates, so no single total can be priced correctly
  by a consumer seeing one field. Recorded in `record_usage` with both numbers.
- **`FINAL` is not optional on Langfuse's ClickHouse tables.** They are
  ReplacingMergeTree, which keeps superseded rows until a merge runs, so the
  smoke report counted one trace twice out of 26 before every read got `FINAL`.
  Exactly the size of error nobody notices.
- **A timing test was measuring interpreter warmth.**
  `test_build_is_not_quadratic_in_case_count` compared one cold 8ms measurement
  with one warm 26ms one: 2.0x alone, 3.1x after a full suite, on unchanged code.
  The 36 new tests in this milestone exposed it rather than caused it. It now
  warms up and takes the best of three.

### New third-party components, and their licences

The trace stack is the first time this repo acquires software it does not ship.
`NOTICE` records each dev-time container and its terms. Redis is pinned to 7.2
deliberately: 7.4 onward is RSALv2/SSPL, which is not OSI-approved, and the
upstream compose file's `redis:7` tag floats onto it. A repo that excludes
CC BY-NC datasets by written policy should not acquire an SSPL service by copying
a compose file unread. One image cannot be pinned — `cgr.dev/chainguard/minio`
publishes only a rolling tag on the free tier — and that is stated rather than
hidden.

### Still to do in milestone 8

Modules 2, 3 and 5 remain the sibling course's text, about a support agent, and
will not run here. They are rewritten when reached, and bring the judges,
`eval_cases/`, CI and `optimize/` with them. `analysis/review.py` still hardcodes
its state directory; `$MB_ANALYSIS_STATE` is named in this document but read
nowhere, and Module 2 will want it.
