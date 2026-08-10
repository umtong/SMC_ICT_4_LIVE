# MBE2 short leg — frozen fresh management comparison

This experiment is frozen before reading the 2026-05-04 through 2026-05-10
result.  It is not a binary gate.  It tests whether a reusable short-side alpha
component was obscured by the source trailing/ROI interaction.

## Development observation

Across the already inspected 2026-07-22 through 2026-07-28 and 2025-02-10
through 2025-02-16 intervals, the public RSI/TEMA short entry combined with the
ROI-only management ablation produced:

- 25 completed trades;
- 22 wins and 3 losses;
- approximately 88% win rate;
- approximately +0.0816R mean after-cost expectancy;
- R-based profit factor approximately 2.91.

The corresponding long leg was negative.  This does not justify a permanent
`short only` system; it justifies an independent fresh test of the observed
component before investing in more complex repairs.

## Fresh interval

- warm-up data: 2026-05-02 through 2026-05-03 UTC
- evaluated entries: 2026-05-04 through 2026-05-10 UTC
- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- one global pending entry or open position
- current-NAV planned-loss budget 3%
- NautilusTrader matching/accounting and project costs
- end-flat forced cutoff

This interval is fresh to the MBE2 short-management hypothesis.  It becomes
development data after the run.

## Entry held constant

Both cells use the exact public completed-five-minute short entry:

- previous RSI at or above 70 and current RSI below 70;
- TEMA above the Bollinger middle line;
- TEMA falling versus the previous completed candle;
- positive volume;
- public 140-candle startup;
- source effective leverage 6.46 used only to translate the public 22% profit
  stop into underlying price geometry;
- one independent RSI-cross episode per symbol and completed five-minute candle.

## Two frozen management cells

### `short_avg646_source`

Preserve the source ROI ladder and trailing interaction.

### `short_avg646_roi_only`

Preserve the same ROI ladder and source stop but disable trailing.  This is the
only management ablation selected from the prior anatomy.

No long cell, leverage change, ROI threshold change, stop change, session rule,
asset rule or third management policy will be chosen after seeing this interval.

## Required causal reading

For both cells preserve and inspect:

- every completed trade, entry feature state, MFE/MAE and exit reason;
- same-minute multi-asset collisions and which symbol occupied the account;
- source signals visible before execution filters;
- time in the global account slot;
- gross winner and loss engines, costs, R geometry, NAV and drawdown;
- whether ROI-only helps by preserving genuine winners or only by converting
  source-trailing exits into tiny high-win-rate outcomes;
- whether a loss is entry-state failure, stop/management failure, execution
  issue or normal probabilistic loss.

A good result is not enough by itself.  The short component is reusable only if
its causal episodes and code path explain why it remains positive.  A weak
result should be decomposed rather than reduced to `FAIL`.
