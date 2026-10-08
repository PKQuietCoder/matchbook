# `logs/frames/` -- BPI 2019 as DataFrame-ready CSV tables

Two gzipped CSVs plus a dtypes sidecar, built from the raw XES by
[`logs/to_csv.py`](../to_csv.py). Nothing here is committed except this file:
the tables are ~15 MB and derived from the DOI-fetched log, and this repo's rule
is that a log fetched by DOI is never committed. Rebuild them in ~35 seconds.

```bash
python -m logs.download --log bpic19          # the 728 MB source, if you lack it
python logs/to_csv.py                         # -> logs/frames/
python logs/to_csv.py --max-cases 2000        # fast path while developing
python logs/to_csv.py --parquet               # also write .parquet (needs pandas)
```

## What is here

| File | Size | Rows |
| --- | --- | --- |
| `bpic19-events.csv.gz` | 11,811,857 B | 1,595,923 |
| `bpic19-cases.csv.gz` | 3,027,944 B | 251,734 |
| `bpic19-dtypes.json` | 899 B | — |
| `bpic19-frames-manifest.json` | 1,933 B | — |

14.8 MB total, which is slightly *smaller* than the gzipped XES (16.9 MB).

## Loading it

**Use the sidecar.** It is not a convenience; it is what makes these files a
lossless representation. A CSV carries no schema, so without it
`GR-Based Inv. Verif.` reads back as the string `'False'` and `Item` `00001`
reads back as the integer `1`.

```python
import json, pandas as pd

d = json.load(open("logs/frames/bpic19-dtypes.json"))
events = pd.read_csv("logs/frames/bpic19-events.csv.gz", dtype=d["events"],
                     parse_dates=["timestamp"], keep_default_na=False)
cases  = pd.read_csv("logs/frames/bpic19-cases.csv.gz", dtype=d["cases"],
                     keep_default_na=False)

df = events.merge(cases, on="case_id", how="left")   # the one-table view, on demand
```

`keep_default_na=False` is load-bearing: without it pandas turns the empty
strings into `NaN` and the string columns quietly become `object`-with-holes.

Two tables rather than one because a joined layout repeats the 16 case attributes
on every event — measured at 175,618 rows and scaled, ~1.92 GB in memory against
~0.36 GB for the event columns alone, since the case attributes are 81% of the
joined table at ~6.3 events per case. Join when you need it.

## Schema

`bpic19-events.csv.gz` — **row order is the declared order**, see below.

| column | dtype | source |
| --- | --- | --- |
| `case_id` | `str` | trace `concept:name`, `<PurchasingDocument>_<Item>` |
| `activity` | `str` | event `concept:name` (42 values) |
| `timestamp` | datetime, UTC | event `time:timestamp` |
| `resource` | `str` | `org:resource` (628 values) |
| `value_cents` | `int64` | `Cumulative net worth (EUR)` × 100 |
| `precision` | `str` | `second`, or `day` for the 172 midnight events |
| `attributes` | `str` (JSON) | leftover event attributes — `{"User": "batch_00"}` |

`bpic19-cases.csv.gz` — `case_id` plus the 16 XES trace attributes.
`GR-Based Inv. Verif.` and `Goods Receipt` are **`bool`**; the rest are strings,
including `Item` (`'00001'`) and `Purchasing Document`.

## Fidelity

Nothing in the data is lost. Measured against the 728 MB source:

| check | result |
| --- | --- |
| timestamp sub-second part | all 1,595,923 events are `.000` → epoch seconds is exact (1,595,924 `time:timestamp` tags exist in the XML; the extra one is the `<global>` default) |
| timezone | all `Z` → no offset to drop |
| XES types present | `string`, `float`, `date`, `boolean` only — no `int`, `list`, `container`, `id` |
| EUR → integer cents | max 1 decimal place; **0** values round |

Proven, not asserted: `tests/test_frames.py` reads 300 cases of the real XES,
writes both tables, reads them back through pandas with the sidecar, and asserts
equality on case ids, per-case activity sequence, timestamps, resources,
`value_cents`, the `tie_broken` audit, every event attribute, and every case
attribute **including its type**.

What a table cannot hold is XES *metadata* — `<extension>` declarations,
`<classifier>` definitions, and the `<global>` attribute defaults. Those are
recorded in `bpic19-frames-manifest.json` instead.

## Read this before you sort

**Row order is the order. Sort by `['case_id', 'seq']` if you reindex — never by
`timestamp`.**

Timestamps are minute-precision: all 1,595,923 events fall on a whole minute. So
within a minute the order comes from the declared `activity_rank` in
`facts.yaml`, not from the data, and **233,463 events (14.6%)** share an instant
with their predecessor. A `sort_values("timestamp")` reshuffles all of them. The
events table is written in the declared order and the row index preserves it;
nothing was nudged to make a timestamp sort come out right.

Two more things the numbers will not tell you on their own:

- **`value_cents` is a case-level figure** repeated on every event of a case
  (it varies within a case in 1.92% of cases, and then by exact multiples). A
  three-way value match is **not** computable from this log, which is why
  `process/rules.py` declares `CTRL-TOLERANCE` not applicable.
- **320 of 1,595,923 events (0.02%) fall outside 2018–2019**, including 10 in
  **1948**, 9 in 1993, and 2 in 2020, against 1,550,468 in 2018. The dataset is
  described as a 2018 purchasing process; these look like data-entry artifacts.
  They are kept, not silently dropped — filter them yourself if your question
  needs it, and say that you did.

## Provenance

van Dongen, Boudewijn (2019): *BPI Challenge 2019*. Version 1.
4TU.ResearchData. DOI
[10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1).
Licensed **CC BY 4.0**. These tables are a derivative — reformatted, with a
documented deterministic tie-break — not the dataset. See
[`../README.md`](../README.md) and [`../../NOTICE`](../../NOTICE).

The challenge organizers also published their own CSV conversion (36,720,297 B,
still live on the conference webserver). It is registered in
`logs/manifest.json` as `bpic19-csv` and used only as an outside cross-check:

```bash
python -m logs.download --log bpic19-csv
python logs/to_csv.py --cross-check
```

**Cross-check result** (run 2026-10-08, `logs/to_csv.py --cross-check`): complete
agreement. Their CSV and these tables both hold **1,595,923 events across 251,734
cases**, all 251,734 case ids present in both, none unique to either, and **zero
cases with differing event counts**. Two independent conversions of the same XES
agree exactly.

It also settles a detail: their file has 1,595,923 rows, not 1,595,924 — the same
`<global scope="event">` off-by-one corrected in [`../README.md`](../README.md).

It is not the reference. It sits outside the DOI record, and the challenge page
warns it needs special parsing for commas inside quoted fields — so where the two
disagree, this conversion may be the better file. The comparison is reported
rather than resolved.
