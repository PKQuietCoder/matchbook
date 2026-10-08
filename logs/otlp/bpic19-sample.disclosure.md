# OTLP export of `bpic19-sample`

## Provenance

- Source: `logs/snapshot/bpic19-sample-events.csv.gz`
- License: CC BY 4.0
- Attribution: van Dongen, Boudewijn (2019): BPI Challenge 2019. Version 1. 4TU.ResearchData. https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1 Licensed CC BY 4.0.
- DOI: 10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1
- Case notion: purchase-order item
- Exporter: `matchbook.process.otlp`, attribute schema 1

## What is in the file

- 50 traces, one per case
- 419 spans: 369 event spans plus 50 case roots
- 24 distinct activities; largest case 37 events
- 1 NDJSON line, each a complete ExportTraceServiceRequest
- 299,832 bytes written; largest line 299,831 bytes

**This is a filtered export** (max_cases=50). The 50 cases here are a
subset; the license and DOI above describe the whole source dataset, not this slice.

## Measured

- Order assumed for 78 of 369 events (21.1%): they share an instant with the preceding event in their case and were ordered by the declared facts.yaml activity_rank. Those spans carry `mb.tie_broken`.
- 369 of 369 events (100.0%) fall on a whole minute, which is what the source's precision actually is.
- 0 events record no resource.
- 0 events carry a non-default precision.

## Derived, and how to read it

- **Event spans have zero duration.** The source records instants -- there is no
  `lifecycle:transition` attribute anywhere in it -- so no event duration exists to
  export and none was interpolated.
- **A root span's interval is calendar time**, first to last recorded event, including
  payment waits. It is not work time.
- **Spans are flat.** The source records no nesting; every event span is a sibling.
- **Sort by `mb.seq`, not by time.** Tied events share `startTimeUnixNano`, so a
  consumer ordering by timestamp will reorder them arbitrarily. No timestamp was
  altered to make a UI render the declared order.

## Withheld on purpose

- **No `status` on any span.** The source records no per-event outcome. An error rate
  computed from this file is undefined, not zero.
- **No case-level actor.** Resources are per event. In BPI 2019, 242,457 of 251,734
  cases (96.3%) involve more than one resource, so no single actor describes a case.
- **`mb.value_cents` is a case-level figure** repeated on every event of a case,
  not a per-event amount. A three-way value match is not computable from this log.
