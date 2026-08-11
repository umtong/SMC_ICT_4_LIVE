# Candidate 57 — fresh 4h jump state/arbitration comparison

This specification is frozen before reading the 2024-09-02 through 2024-09-15
account result.  The interval is temporally separated from the known 2025-12,
2026-04 and 2026-06 jump-family studies.

## Purpose

The 4-hour jump family has repeatedly shown a high-payoff but sparse reversal
mechanism.  Its unresolved problem is not stop distance: completed abnormal
impulses sometimes reverse strongly and sometimes continue with almost no
favorable excursion.  The current reusable components solve separate roles:

- completed 4h two-sigma impulse: abnormal market event;
- Binance peer taker state: observable aggressive-flow regime at the boundary;
- cross-symbol arbitration: which already-qualified asset occupies the one
  global slot;
- whole-impulse invalidation and transient management: risk and exit geometry.

A consumed 2026-06 development replay found that using source max-z when at
least three of four peer taker ratios aligned with the reversal, otherwise
least qualifying z, produced 10 trades and 1.041% geometric daily growth.  That
is a discovery signal only.  This experiment tests the frozen rule on a
different interval.

## Unchanged source and execution

All cells retain:

- completed 240-minute candles only;
- prior-only volatility window 18;
- absolute jump threshold 2.0 sigma;
- reversal side opposite the completed impulse;
- whole-impulse structural stop;
- 240-minute source horizon;
- transient protection armed at +0.4R, break-even floor, +1.0R escape;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- project costs, adverse slippage, funding reserve and NautilusTrader account;
- strict as-of Binance Vision futures metrics, all four peers and maximum age
  ten minutes.

No price threshold, taker majority, stop, target, horizon, side, symbol or
management value may be changed after observing this interval.

## Frozen cells

1. `source_max_z`: no taker entry filter; source maximum absolute causal z.
2. `least_qualifying_z`: no taker entry filter; least absolute already-qualified
   z.
3. `taker_conditional`: no taker entry filter; source max-z when at least three
   of four peer taker ratios align with the proposed reversal, otherwise
   least-z.  Missing any peer makes the boundary unresolved.
4. `least_z_taker_3of4`: require at least three aligned peers, then choose
   least-z.  This is the lower-frequency state-filter composition retained as a
   contrast, not a threshold search.

## Fresh interval

- metrics and bar warm-up begin 2024-08-29 UTC;
- evaluated entries: 2024-09-02 through 2024-09-15 UTC;
- every cell starts from 100,000 USDT and ends flat;
- raw simultaneous symbol candidates at one 4h boundary count as one causal
  opportunity, not multiple independent trades.

## Predictions before the result

- The first three cells should observe the same source boundaries; differences
  arise only from collision symbol selection or a missing-metrics fail-close.
- Conditional arbitration is credible only if its improvement is distributed
  across multiple collision boundaries and preserves large winners; a single
  selected outlier is insufficient.
- The 3-of-4 filtered cell may reduce loss but is expected to trade fewer
  independent boundaries.  It is useful only if the quality gain can later be
  combined with other independent scenario families without weakening it.
- If all cells lose on the same boundaries, the missing variable is market-event
  state and the next work must use positioning/OI/spot-perpetual information,
  not z-score or stop tuning.
- If conditional arbitration fails while least-z or source max-z works, retain
  the better causal selector and reject the state-dependent switching rule.

## Required reading

Preserve every source candidate, independent boundary, collision set, selected
symbol, peer metrics snapshot, actual account trade, shadow path, slot-blocked
boundary, MFE/MAE, exit reason, R, cost, NAV, drawdown and end validity.

The project target is reported but not used to hide mechanism: after-cost
geometric daily growth at least 1%, independent completed trades at least the
calendar days, positive expectancy/PF, valid one-slot account, no liquidation
and no unrecoverable NAV damage.
