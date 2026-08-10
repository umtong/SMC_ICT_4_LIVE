# Candidate 57 — frozen jump direction × market-state policy fresh experiment

## Why this experiment exists

Two structural observations were established only on already-consumed data:

1. the June factor map showed an interaction between peer taker state and
   cross-symbol arbitration: source max-z was superior on 3-of-4 aligned
   boundaries, while least qualifying z was superior when the taker filter was
   absent. Replaying the resulting causal conditional rule on that consumed
   interval produced 10 completed trades, PF 2.62 and 1.041% geometric growth
   per day;
2. all-candidate anatomy across December 2025, April 2026 and June 2026 showed
   that long reversals after downward jumps were the persistent loss engine in
   the two 2026 samples, while short reversals after upward jumps carried the
   stronger payoff. This matches an external liquidation-reversal failure mode:
   a downside liquidation and OI reset can continue lower rather than rebound.

Neither observation is fresh evidence for the composed policy. This document
freezes a new factor map before reading the interval below.

## Fresh interval

- scored entry interval: **2026-07-15 through 2026-07-28 UTC**;
- Binance metrics sidecar begins 2026-07-11 for strict as-of availability;
- this interval was not used to choose the side state, peer majority or
  arbitration mapping;
- all cells use the same four-symbol data and continuous one-slot account;
- the interval becomes development data immediately after the first result is
  observed.

## Four cells

| cell | causal side state | market-state/arbitration policy |
|---|---|---|
| `conditional_both` | long and short reversals | 3-of-4 aligned: source max-z; otherwise least-z |
| `conditional_short_only` | short reversals only | 3-of-4 aligned: source max-z; otherwise least-z |
| `aligned_max_both` | long and short reversals | require 3-of-4 alignment, then source max-z |
| `aligned_max_short_only` | short reversals only | require 3-of-4 alignment, then source max-z |

This is a 2×2 causal factor map, not a threshold tournament. The first axis asks
whether downside-jump recovery and upside-jump fade are one mechanism. The
second asks whether opportunity-preserving conditional arbitration or the
higher-quality aligned-only specialist is the better role for the jump family.

## Side definition

- completed upward four-hour source jump → proposed short reversal;
- completed downward four-hour source jump → proposed long reversal;
- `short_only` rejects the second state before one-slot arbitration;
- no asset name, realized outcome, OI sign or future candle enters this state.

## Market-state definition

At the completed source boundary, join the latest Binance USD-M futures metrics
row at or before the boundary for BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.

- proposed long reversal: a peer aligns when taker long/short volume ratio > 1;
- proposed short reversal: a peer aligns when the ratio < 1;
- all four peers must be available and no snapshot may be older than ten
  minutes;
- 3 or 4 aligned peers define the aligned state.

For `conditional_*`, aligned boundaries use source max absolute z and all other
resolved boundaries use least absolute qualifying z. For `aligned_max_*`,
non-aligned boundaries are rejected and aligned boundaries use source max-z.

## Frozen source and execution contract

Unchanged in all cells:

- completed four-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed four-hour returns for volatility;
- entry opposite the completed impulse;
- whole-impulse structural stop with the existing causal buffer;
- original 240-minute source horizon;
- transient protection armed at +0.4R and escaped at +1.0R;
- no symbol-specific rule;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- realistic fees, adverse slippage, funding safety and NautilusTrader matching;
- simultaneous symbol candidates at one four-hour boundary count as one causal
  event, not independent opportunities.

## Interpretation

The actual continuous one-slot account is authoritative. Short-only can be a
valuable specialist even with lower frequency, but it is not allowed to inherit
trades or NAV from another cell. The conditional policy is promoted only if the
new interval preserves positive cost-after expectancy without an unacceptable
NAV path. The aligned-only policy is promoted only if its higher quality is not
entirely one-event concentration.

If all four cells fail, do not tune the z threshold, peer majority, stop or
transient management on this interval. Return to a delayed post-cascade state
transition—price no longer extending, OI no longer deteriorating and basis/taker
flow stabilizing—or import a structurally different scenario family.
