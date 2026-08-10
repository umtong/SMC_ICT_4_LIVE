# Candidate 57 — frozen jump peer-taker state × arbitration fresh experiment

## Decision being tested

Two components were independently frozen before this interval was selected:

1. **market-event state** — a completed 4-hour jump reversal is actionable only
   when Binance USD-M futures taker long/short volume ratios for at least three
   of BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT point in the proposed reversal
   direction;
2. **one-slot arbitration** — when more than one already-qualified symbol exists
   at the same completed 4-hour boundary, choose either the source maximum
   absolute causal z-score or the least absolute qualifying z-score.

The earlier 2026-04-01 through 2026-04-14 development experiments showed that
peer-taker state removed broad continuation boundaries while least-z improved
several collision choices. Their combination has not been tested on observed
outcomes. This experiment freezes the combination before reading a different
interval.

## Fresh interval

- scored entry interval: **2026-06-15 through 2026-06-28 UTC**;
- Binance metrics sidecar begins 2026-06-11 for strict as-of availability;
- all four cells use exactly the same market data and account contract;
- the interval becomes development data immediately after the first result is
  observed.

## Four factorial cells

| cell | market-event state | one-slot arbitration |
|---|---|---|
| `source_max_z__no_taker` | source control, no taker rejection | source max absolute z |
| `source_max_z__taker_3of4` | 3-of-4 peer taker alignment | source max absolute z |
| `least_z__no_taker` | source control, no taker rejection | least absolute qualifying z |
| `least_z__taker_3of4` | 3-of-4 peer taker alignment | least absolute qualifying z |

This is a causal factor map, not a pass/fail threshold tournament. The controls
remain in the account so the state and arbitration contributions can be
separated.

## Frozen source and execution contract

Unchanged in every cell:

- completed 4-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed 4-hour returns for volatility;
- entry opposite the completed impulse;
- whole-impulse structural invalidation with the existing terminal ATR buffer;
- 240-minute source horizon;
- transient protection armed at +0.4R and escaped at +1.0R;
- no symbol-specific rule or outcome-derived threshold;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- realistic fees, adverse slippage, funding safety and NautilusTrader matching;
- simultaneous symbol candidates at one 4-hour boundary count as one causal
  market event, not independent opportunities.

## External-state causality

For every completed event boundary, the metrics join must:

- use `bisect_right` as-of selection;
- reject any metric timestamp after the event boundary;
- reject a snapshot older than ten minutes;
- require snapshots for all four peers before the 3-of-4 decision;
- use only `sum_taker_long_short_vol_ratio > 1` for a proposed long reversal and
  `< 1` for a proposed short reversal.

Open interest and positioning fields are preserved only as diagnostics. No rule
may be selected from them after observing this interval and then called fresh.

## Interpretation rule

The combined policy is useful only if its actual continuous one-slot account
preserves meaningful winners and materially improves cost-after expectancy,
NAV path and drawdown relative to both controls. A small trade count or a
positive result concentrated in one event is not sufficient for long promotion.

If the combination remains negative, do not tune price thresholds, the taker
majority, the stop or transient management on this interval. Return to a
structurally different leverage/positioning transition or import another
observable market-state solution.
