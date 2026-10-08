# `logs/otlp/` -- the event log as OpenTelemetry spans

Matchbook's human baseline is IEEE XES; agent telemetry everywhere else is
OpenTelemetry. This directory holds the human log in the agent world's format,
so both open in the same trace tooling. Written by
[`process/otlp.py`](../../process/otlp.py).

## What is here

| File | Committed? | Contents |
| --- | --- | --- |
| `bpic19-sample.ndjson.gz` | **yes**, 18 KB | 50 cases, 369 events, 419 spans |
| `bpic19-sample.disclosure.md` / `.manifest.json` | **yes** | what the conversion measured and withheld |
| `bpic19-full.ndjson.gz` | no, 57 MB | the whole log: 251,734 cases, 1,595,923 events, 1,847,657 spans |
| `agent.ndjson.gz` | no | the agent's own log through the same exporter |
| `*.disclosure.md` / `*.manifest.json` | no | one pair per generated export |

**Only the fixture is committed, and `.gitignore` allowlists this directory
rather than blocklisting it.** A full-log export is the DOI-fetched log in
another format, and this repo's rule (see [`../README.md`](../README.md)) is that
a log fetched by DOI is never committed. An allowlist makes "not committed" the
default for anything new that lands here, so a 57 MB export cannot be added by
accident. Adding a file to the allowlist is a deliberate act, which is the point.

## Rebuilding

```bash
# the committed fixture -- byte-reproducible, so this is also how to verify it
python -m process otlp bpic19-sample --max-cases 50 \
    --doi 10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 \
    --attribution "van Dongen, Boudewijn (2019): BPI Challenge 2019. Version 1. 4TU.ResearchData. https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 Licensed CC BY 4.0." \
    --out logs/otlp/bpic19-sample.ndjson.gz

# the full log, streamed off the 728 MB raw file (fetch it first)
python -m logs.download --log bpic19
python -m process otlp bpic19 --from-xes logs/raw/BPI_Challenge_2019.xes \
    --license "CC BY 4.0" --doi 10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 \
    --out logs/otlp/bpic19-full.ndjson.gz

# the agent's log, same exporter -- this is the comparison the repo exists for
python -m process otlp agent-attempts --out logs/otlp/agent.ndjson.gz

# conformance on the committed fixture (it is a 50-case slice, so there is
# nothing for --against to conserve against)
python -m process otlp-verify logs/otlp/bpic19-sample.ndjson.gz

# the full conservation check needs an unfiltered export of the same log
python -m process otlp bpic19-sample --out build/sample-all.ndjson.gz
python -m process otlp-verify build/sample-all.ndjson.gz --against bpic19-sample
```

Measured on the full log, not estimated: **1,847,657 spans in 59.6 s at 81 MB
peak resident memory**, from a 728 MB source, producing 1.43 GB of NDJSON or
57 MB gzipped. Memory is flat in the number of cases because `--from-xes` builds
one case at a time.

## The shape

One trace per case, one root span per case, one child span per event. Each
NDJSON line is a complete `ExportTraceServiceRequest`, so a line POSTs to any
OTLP/HTTP collector unchanged, Langfuse's `/api/public/otel/v1/traces` included;
nothing vendor-specific is emitted.

| | root span | event span |
| --- | --- | --- |
| `name` | the case's `Item Category` | the activity |
| `startTimeUnixNano` | first recorded event | the event's instant |
| `endTimeUnixNano` | last recorded event (**measured**) | identical to the start |
| attributes | `mb.case_id`, `mb.event_count`, `mb.tie_broken_events`, `mb.case.*` | `mb.activity`, `mb.seq`, `mb.resource`, `mb.resource.kind`, `mb.value_cents`, `mb.event.*` |

Attributes live in the `mb.*` namespace that
[`observability/spans.py`](../../observability/spans.py) already declares, plus
`service.name`. No pseudo-standard key is invented.

## What the conversion will not assert

The log does not record these, so the export does not claim them. Each
`.disclosure.md` repeats this with denominators.

- **Event durations.** BPI 2019 carries no `lifecycle:transition` attribute on
  any of its 1,595,923 events, so an event is an instant and
  `startTimeUnixNano == endTimeUnixNano`. Nothing was interpolated to give a UI
  a bar to draw. A root span's interval *is* measured, but it is calendar time
  including net-30 payment waits, not work time.
- **Span nesting.** The log records none, so every event span is a sibling under
  its case root. A call tree would be invention.
- **Per-event outcome.** No span carries a `status`. An error rate computed from
  these files is undefined, not zero. (`Cancel Goods Receipt` and
  `Cancel Invoice Receipt` are activities, not statuses.)
- **A case-level actor.** 242,457 of 251,734 cases (96.3%) involve more than one
  `org:resource`, so no single actor describes a case. Actors stay per event.
- **A per-event amount.** `mb.value_cents` is a case-level figure repeated on
  every event of a case. A three-way value match is not computable from this
  log, which is why `process/rules.py` declares `CTRL-TOLERANCE` not applicable.

## Order, which is the subtle one

Timestamps are minute-precision -- **all** 1,595,923 events fall on a whole
minute. Within a minute, order comes from the declared `activity_rank` in
`facts.yaml`, not from the data: 233,463 events (14.6%) share an instant with
their predecessor and carry `mb.tie_broken: true`.

So **sort by `mb.seq`, not by `startTimeUnixNano`.** A consumer ordering by
timestamp will reorder tied events arbitrarily. No timestamp was nudged to make
a waterfall render the declared order; that would trade a disclosed assumption
for a hidden one.

## Accuracy

`process/otlp.py` ships a reader as well as a writer, so the claim is testable
rather than reviewable. `tests/test_otlp.py` writes an export, reads it back, and
asserts the reconstructed log is identical to the source -- case ids, per-case
activity sequences, timestamps, resources, values, case attributes, and the
`tie_broken` set position for position.

Verified on the full log too: streaming the export and the raw XES side by side,
**251,734 of 251,734 cases and 1,595,923 of 1,595,923 events agree** on activity,
timestamp, sequence, resource, value and tie-break flag, with zero mismatches.
