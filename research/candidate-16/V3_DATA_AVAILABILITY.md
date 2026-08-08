# Candidate 16 v3 data-availability result

## Classification

**NO_ALPHA_RESULT — REQUIRED RAW DATA NOT AVAILABLE FOR THE PRE-REGISTERED PERIOD**

Candidate 16 v3 pre-registered `2022-12-12` through `2022-12-18` before any v3
outcome. Static causal, risk, and source-contract tests passed. The first market
data request then failed with HTTP 404 for:

```text
https://data.binance.vision/data/futures/um/daily/bookTicker/BTCUSDT/
BTCUSDT-bookTicker-2022-12-09.zip
```

No strategy observation, order, position, PnL, or NAV result was produced. The
period therefore contains no v3 performance information and is not a failed or
successful alpha screen.

## Corrected external data facts

The Candidate 03 implementation assumed daily Binance Vision `bookTicker`
archives. The current public archive does not provide daily files. Independent
inspection of an existing dataset built by streaming the official monthly
archives records the actual coverage as:

- monthly `bookTicker` only;
- first data: `2023-05-16 11:49 UTC`;
- last complete data: `2024-03-31 23:59 UTC`;
- eleven monthly archives, `2023-05` through `2024-03`;
- `2024-04` exists as a zero-byte/corrupt archive;
- `2024-05` onward returns HTTP 404;
- no daily `bookTicker` dumps.

The project-internal Candidate 03 daily URL is therefore an unexecuted source
assumption, not a reusable working historical data path.

## Existing solution found

The Torch-Trade/Mindbyte public Hugging Face dataset already performed the
expensive generic engineering task:

```text
official Binance monthly bookTicker ticks
→ streaming aggregation
→ one row per completed minute
→ raw ticks discarded
→ versioned Parquet
```

Frozen artifact facts:

- dataset commit: `2c8dce40261855c7b57113f5a157bbeb82280bb8`;
- file: `data/train-00000-of-00001.parquet`;
- size: `28,423,067` bytes;
- SHA-256: `274eb8e87c7d7185a0162271144b30a0e387ae496fe657c6af83833448f08624`;
- rows: `460,265`;
- cadence: one minute;
- completeness: `99.726%` versus the covered one-minute grid;
- license: MIT.

Available columns are completed-minute timestamp, close/TWAP spread, close bid
and ask size, close/TWAP imbalance, close microprice, close microprice premium,
and quote update count.

## Structural consequence

The preprocessed dataset cannot test v3's proposed event-by-event same-price
queue depletion and refill sequence. Pretending that it can would silently
replace the hypothesis. Downloading and reparsing an entire monthly raw archive
inside each experiment is also unnecessary generic engineering when the stable
one-minute aggregation already exists.

Therefore the event-level v3 hypothesis is suspended, not numerically relaxed.
The next independent candidate uses the information the available dataset
actually supports:

```text
whole-minute top-book pressure (TWAP)
→ end-of-minute top-book pressure (close)
→ persistent pressure, transient pressure, or sign reversal
→ completed failed-auction / true-acceptance state
→ later independent initiative
```

This is explicitly a pressure-persistence hypothesis, not a claim to observe L3
orders, add/cancel identities, or same-price queue replenishment.
