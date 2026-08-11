# Candidate 60 — source-faithful liquidity-vacuum V1 repair freeze

## Economic mechanism

This family begins from participant urgency and displayed-liquidity withdrawal, not from a chart pattern:

> completed one-hour balance
> → persistent, price-efficient perpetual aggressor flow breaks the balance
> → liquidity on the opposing one-percent book is cancelled while deeper book imbalance points with the move
> → independent spot price and spot aggressor flow accept the same repricing
> → next-minute entry
> → invalidation beyond the completed parent minute
> → one prior-balance-width auction objective

The proposed payer is the participant who must continue consuming increasingly thin opposing liquidity while spot confirms the information or inventory transfer.

## Immutable source

- source branch lineage: `research/external-leverage-flow-router`
- source commit: `d2856dc7c7805617b2d760fecf087cf8975ea884`
- source scenario: `research/external-leverage-flow-router/liquidity_vacuum_screen.py`
- source spot wrapper: `research/external-leverage-flow-router/spot_participation_contract.py`
- source feature builder: `research/candidate-05/features.py`
- universe: `BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT`
- development interval: `2023-06-01` through `2023-06-14` UTC
- costs: fixed 20 bp round-trip hurdle
- one-hour parent cooldown and one-hour maximum hold
- no source threshold, side, entry, invalidation, target, cooldown, cost or management value is changed

## Sole engineering repair

The original execution failed before producing any market result. Pandas 3 can retain timezone-aware datetimes using millisecond storage. The feature builder used:

```python
frame["observed_time_ns"] = frame["close_time_dt"].astype("int64")
```

and the spot wrapper later divided that integer by `60_000_000_000` as though it were nanoseconds. In the failed run, many 2023 rows therefore collapsed to the duplicate key `1680000000000`.

Candidate-60 changes only the clock representation:

- prove the original integer clock equals the returned kline-close clock under exactly one unit scale (`1`, `1,000`, or `1,000,000`);
- reconstruct `observed_time_ns` from the exact returned kline close timestamps using explicit `datetime64[ns]` conversion;
- reconstruct `minute_start_ns` from the same returned kline open timestamps;
- assert one-to-one uniqueness before the spot/perpetual join.

This repair changes no market observation, classifier or trading decision.

## Evaluation

The four source screens run unchanged. Their event streams are then evaluated in three forms:

1. per-symbol source paths;
2. global three-minute causal episodes, choosing only by entry-time cost-aware net reward/risk and fixed symbol priority;
3. one global slot held until the selected source trade's frozen exit.

A diagnostic continuous NAV uses the project risk fraction of 3% per planned loss. It is not a substitute for NautilusTrader fills or accounting. A coherent survivor must be implemented unchanged through NautilusTrader before any fresh-data claim.

## Non-binary interpretation

- A positive aggregate is not sufficient. It must not depend on one symbol, day or winner, and the event distribution, median, trimmed mean, geometry and one-slot path must agree with the economic mechanism.
- A negative aggregate does not automatically invalidate every observation. We inspect whether parents are abundant but geometry rejects them, whether accepted states are directionally right but costs consume the objective, or whether the state itself is wrong.
- Zero events does not justify lowering thresholds. The parent and geometry funnel determines whether the state was absent or the representation was defective.
- The exact opposite is not adopted after observing outcomes unless an independently specified economic mechanism predicts it.

No fresh interval is consumed by this study.