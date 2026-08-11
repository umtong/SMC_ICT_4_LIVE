# Candidate 60 — real micro-auction state forensic V1 freeze

## Purpose

The Candidate-51 real-data micro-auction system lost money in its development account. That aggregate result does **not** by itself establish that every underlying market-state observation was useless. The executable policy combined three different claims:

1. the state classifier identified continuation or absorption correctly;
2. the next-open entry still had enough unconsumed price space;
3. the event-extreme stop and measured-move/midpoint target remained viable after 20 bp round-trip costs.

V1 separates those claims without changing any classifier threshold.

## Frozen source and data

- immutable source commit: `f7787095f98b27f31fa3766bda13a94ae350269d`
- exact router: `research/candidate-51/router_microauction.py` from that commit
- router SHA-256: `4ad9a1694ba5daab637b8fe51c5c36d9859218534116864bdec269c78d8903b9`
- exact consumed development interval: `2026-04-13` through `2026-04-19` UTC
- universe: `BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT`
- observations: checksum-verified Binance futures 1-minute klines, aggregate trades and book-depth archives through the exact Candidate-05/51 ingestion path at the frozen commit
- round-trip diagnostic cost: `20 bp`
- no policy-fresh interval is consumed
- no parameter or threshold search is permitted

## Frozen hypotheses

### H1 — directional state information may survive policy failure

If the exact continuation or absorption state contains useful information, its rising-edge forward return should separate from:

- the exact opposite direction;
- the corresponding first-failure/near-state families;
- one-symbol or one-observation concentration.

A positive mean alone is insufficient. Separation must be visible across symbols, medians and trimmed summaries without one event dominating.

### H2 — geometry may be the primary failure

The original router validates gross reward/risk at the event close. The actual order is submitted only after the completed minute and can fill at a later price. A state can therefore be directionally informative yet untradeable because:

- next-open objective distance is less than the 20 bp round-trip cost;
- the actual next-open geometry is invalid;
- cost-inclusive reward/risk is poor;
- confirmation consumed the auction objective.

For each exact edge V1 records actual next-open objective space, stop distance, cost-aware reward/risk, target/stop touch paths, MFE and MAE.

### H3 — continuation and absorption are distinct mechanisms

They are not pooled into a single positive/negative score. Each family is diagnosed separately, then the exact frozen arbitration is reconstructed only for global episodes and non-overlapping one-slot paths.

## Causal timing

At minute `t` the classifier receives only:

- the kline completed at `t`;
- aggregate-trade and book-depth features whose `observed_time_ns <= t`;
- historical bars complete by `t`.

Entry observation is the next minute open. Future closes/highs/lows are labels only.

## Outputs

- exact actionable rising-edge events;
- first-failure-stage rising edges for causal diagnostics;
- three-minute cross-asset episode collapse;
- non-overlapping one-slot paths for 1, 5, 15, 30, 60 and 120 minutes;
- exact-opposite attribution;
- actual next-open geometry and 20 bp cost-aware objective space;
- family, symbol, median, trimmed and concentration summaries.

## Interpretation contract

The exact state classifier is preserved only if the directional separation is coherent even when the old execution policy is removed. The old geometry is preserved only if next-open objective space exceeds costs with coherent cost-inclusive reward/risk.

Possible conclusions are deliberately non-binary:

1. **state and geometry both fail** — retire the exact family;
2. **state survives, geometry fails** — preserve the state observation and redesign entry/objective before any fresh test;
3. **geometry survives, state direction fails** — preserve only execution lessons, not the direction policy;
4. **both appear coherent** — freeze an executable one-slot policy before touching fresh data.

No result from this consumed interval is itself a promotion claim.
