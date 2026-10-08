# Logs: provenance, licensing, and the exclusion list

Every event log in Matchbook is recorded here with its DOI, its license, and the
reason it is present. This file is the compliance artifact that makes the repo
usable for commercial demos, paid teaching, and customer solicitation. Read it
before adding a dataset.

The rule: **a log is committed only if its license grants redistribution and
commercial use.** Everything else is fetched by DOI into `logs/raw/`, which is
gitignored.

## Committed

### `logs/helpdesk/helpdesk.csv` -- the fast fixture

| | |
| --- | --- |
| Source | Verenich, Ilya (2016): *Helpdesk*, Mendeley Data, v1 |
| DOI | [10.17632/39bp3vv62t.1](https://doi.org/10.17632/39bp3vv62t.1) |
| License | **MIT** (verified against the Mendeley API `data_licence` field) |
| Scale | 3,804 cases, 13,710 events, 9 activities |
| SHA-256 | `1306e06414481a1debd845ea760b5ce8331d087ddc67d7f7ec300b4bea3f41b7` |

A ticketing process at an Italian software company. Committed because it is MIT
and small: 13,710 events make a usable test fixture where 1.6M events do not.

Note the shipped file is the **anonymized** variant: three columns
(`CaseID,ActivityID,CompleteTimestamp`), activity names replaced by numeric ids,
and no resource or seriousness attributes. The richer attribute version lives on
4TU under the legacy general terms of use and is therefore *not* committable --
do not substitute it.

### `logs/snapshot/` -- the pinned BPI 2019 sample

| | |
| --- | --- |
| Source | van Dongen, Boudewijn (2019): *BPI Challenge 2019*, 4TU.ResearchData, v1 |
| DOI | [10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1) |
| License | **CC BY 4.0** (verified via the figshare API `license` field) |
| Full scale | 76,349 purchase documents, 251,734 item-level cases, 1,595,923 events, 42 activities, 627 resources (607 human + 20 batch) |
| Full file | `BPI_Challenge_2019.xes`, 728,558,522 bytes, MD5 `4eb909242351193a61e1c15b9c3cc814` |

CC BY 4.0 permits redistribution, derivatives and commercial use with
attribution, so a **sampled subset is committed** and a fresh clone works with no
network. Attribution is in `NOTICE` and in the snapshot's own header. Sampling is
deterministic and reproducible: see `logs/snapshot/README.md`.

#### Measured, not inherited

Everything below was computed from the file itself, over all 251,734 cases. Two
widely repeated claims about this log do not survive the measurement, which is
itself the first lesson of the dataset: **measure the log, do not cite the
description.**

| Claim in the literature / challenge description | What the file actually contains |
| --- | --- |
| "60 subsidiaries" | The `Company` attribute has **4** distinct values, and they are wildly skewed: `companyID_0000` holds 250,686 of 251,734 cases (99.6%), `companyID_0003` holds 1,044, and `companyID_0001` and `companyID_0002` hold **2 cases each**. Any per-company analysis is really a single-company analysis. |
| "day-granularity timestamps" | Timestamps are **minute**-precision: 99.99% of 1,595,927 events have `seconds == 00`, and only 173 sit at exact midnight. The real ordering problem is not date-only values -- it is **same-minute ties**. In the snapshot they affect 17.2% of events, but only **3.0% are consequential**: a tie between two events of the *same* activity cannot change a sequence, and most of them are. |

Confirmed as described:

- **42 activities**, led by `Record Goods Receipt` (314,098), `Create Purchase Order Item` (251,736), `Record Invoice Receipt` (228,760), `Vendor creates invoice` (219,920), `Clear Invoice` (194,394) and `Record Service Entry Sheet` (164,975). That last one is substantial and is missing from the commonly cited activity list.
- **629 distinct resources: 607 `user_*` + 20 `batch_*`**, exactly the published human/batch split, plus two sentinels (`UNKNOWN`, `NONE`). The naming convention is what `process.xes.classify_resource` reads, so no committed batch-user list is needed. Note there are no `vendor_*` resources -- the `Vendor creates invoice` events are not attributed to a vendor principal, so the vendor resource kind only ever appears in the synthetic and agent logs.
- **11,973 distinct variants** over 251,734 cases, with the **top 20 variants covering 70.5%** of cases. So the process has a clear spine and a very long tail: discovery must be run on an explicit coverage sublog, or it will return a flower model.
- **The tie-break is not cosmetic.** Applying the declared `activity_rank` from `facts.yaml` collapses the snapshot from **378 variants to 373** and changes the event sequence of **76 cases**. A discovered model or a fitness number is therefore partly a consequence of that declared ordering, and a result should say which ordering it used. Reproduce with `python -m process ingest logs/snapshot/bpic19-sample-events.csv.gz --log-id bpic19-sample --sensitivity`; the committed snapshot deliberately preserves the source file's event order so the baseline survives.
- **1,975 vendors**; the four matching flows split 87.8% / 6.0% / 5.8% / 0.4% (`3-way match, invoice before GR` / `after GR` / `Consignment` / `2-way match`). The flow strings above are the exact `Item Category` values and are what `facts.yaml` must match.

### Known data-quality traits -- curriculum, not defects to hide

Same-minute ties (see above); events for vendor-side activities with no vendor
principal; monetary values anonymized by a linear translation, so the scale is
internally consistent but **not** real currency and must be rescaled rather
than inherited; cancelled and deleted items (`Delete Purchase Order Item`,
`Cancel Goods Receipt`, `Cancel Invoice Receipt`); duplicate and subsequent
invoices; and items whose flow type contradicts their event sequence. Each
should be documented as intentional data with an expected handling, so nobody
mistakes corruption for ground truth.

#### Upstream availability

`data.4tu.nl/ndownloader` currently returns **503 Service Unavailable** with
`Retry-After: 3600` while 4TU storage is under maintenance, and the landing page
says files are temporarily unavailable. The 4TU records are figshare-backed, and
the **figshare mirror serves the file normally**:

```
https://api.figshare.com/v2/articles/12715853     -> metadata, license, md5
https://ndownloader.figshare.com/files/24072995   -> BPI_Challenge_2019.xes
```

`logs/download.py` tries 4TU first, falls back to the figshare mirror, verifies
against `logs/manifest.json`, and reports which source it used. The committed
snapshot is the insurance if both are down.

## Fetched on demand, never committed

### Process Discovery Contest, 2016-2025 -- the answer key

**CC0 1.0** (public domain dedication). The only process-mining data with a
published ground truth, and therefore Matchbook's correctness harness rather
than a curiosity:

| Year | Contents |
| --- | --- |
| 2016 | 10 training logs, 10 test logs, 10 ground-truth logs; per-trace boolean `pdc:isPos` |
| 2020 | 192 training, 192 test, 192 ground-truth logs + **96 original workflow nets (PNML)** |
| 2021 | 480 training, 96 test, 96 ground-truth logs + 96 models |

Logs are IEEE XES, models are ISO PNML. Because the generating nets are
published, `tests/pdc/` can check discovered *structure* against the true model,
not only replay fitness. Use it from the start; a hand-written miner has nothing
else to be measured against.

## Candidates kept for later

| Dataset | License | Why it is on the list |
| --- | --- | --- |
| OCEL 2.0 Order Management, [10.5281/zenodo.8337463](https://doi.org/10.5281/zenodo.8337463) | CC BY 4.0 | The second domain for object-centric mining; ships a CPN simulation model as true generative ground truth. |
| Event Graph of BPI Challenge 2019, [10.4121/14169614](https://doi.org/10.4121/14169614) | CC BY 4.0 | An independent object-centric derivation of our own flagship log (1.93M nodes, 15.1M relationships; Neo4j dump + GraphML). Not a dependency -- a cross-check for `process/ocel.py`. |
| City of Seattle SDCI Plan Review + Plan Comments | **Public domain** | A permit plan-review process with real free-text reviewer correction letters. The option to reach for if Matchbook ever needs a natural-language-rich domain, which BPI 2019 is not. |
| tau-bench retail | MIT | An already-agentified process with a written policy document; a capstone comparison. |

## Excluded on purpose -- do not re-add

Recorded with reasons so this decision is not quietly reversed later.

| Dataset | License | Reason |
| --- | --- | --- |
| **All five BPI Challenge 2020 logs** (Domestic Declarations, International Declarations, Request For Payment, Prepaid Travel Costs, Travel Permit Data) | **CC BY-NC 4.0** | Non-commercial. Verified on the Domestic Declarations landing page. Painful, because the travel-reimbursement approval chain is otherwise the ideal teaching process. |
| **CRMArena / CRMArena-Pro** | CC BY-NC | Non-commercial. |
| BPI 2012, 2013, 2014, 2015, 2017; Sepsis Cases; Road Traffic Fine Management; Hospital Billing; WABO | legacy **"4TU General Terms of Use"** (`https://doi.org/10.4121/resource:terms_of_use`) | Grants download and analysis but **no explicit redistribution or commercial grant**; only the *metadata* is CC0. Reference by DOI in prose if useful; never bundle the file, and never build a paid demo on one. |

A note on why this matters more than it looks: the canonical teaching logs are
mostly in that last row. Matchbook is built on BPI 2019 **because** it is one of
the few real, large, well-documented logs that is unambiguously CC BY 4.0.
