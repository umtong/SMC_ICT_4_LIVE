# Ichi source-exit profit-buffer v4 freeze

## Research question

The frozen public `report_short_level` IchiV2 account earned all 13 ROI exits and lost on 17 of 19 source-signal exits in the June 2025 lifecycle audit.  The broad hypothesis “ignore the source exit” was already falsified: only 5 of 17 source-exit losses later recovered to the original ROI and the aggregate shadow improvement was negligible.  The source exit therefore remains the default thesis-failure action.

Two source-signal exits, however, were already profitable after realistic account costs and both later reached the unchanged public ROI when the exit was observed but not traded.  This suggests a narrower causal distinction:

- a source crossover while the position is already non-positive after expected round-trip costs is a genuine failed trend state and must exit immediately;
- a first source crossover while the position is already positive after expected round-trip costs can be an ordinary profitable pullback rather than a completed trend failure.

The experiment tests only that distinction.  It does not search thresholds, ROI, stop, fan settings, hold time, symbols, or dates.

## External reuse

The public MIT-licensed TrendRider strategy uses trade-age and current-profit dependent loss cuts instead of treating every indicator deterioration identically.  We reuse only that lifecycle principle.  We do not copy its entries, confidence score, parameters, or claimed performance.

## Frozen candidate policy

Source control is the verified finite-history public IchiV2 `report_short_level` policy without changes.

Candidate policy keeps the same entry, one-slot arbitration, stop, ROI, 480-minute horizon, costs, funding handling, and current-NAV 3% planned-loss sizing.  Only source-exit handling changes:

1. On the first completed five-minute source-exit crossover, estimate the position return at the completed minute close after the already frozen 0.21% round-trip cost allowance.
2. If estimated after-cost return is `<= 0`, execute the public source exit immediately and identically.
3. If estimated after-cost return is `> 0`, defer that first crossover and arm a profit buffer.  Zero is an economic break-even boundary, not a tuned threshold.
4. While armed, exit immediately if the mark falls back to estimated after-cost break-even or below.
5. At the next distinct completed five-minute candle, exit if the public source-exit state remains active.  If the state has recovered, disarm the buffer and continue the unchanged source ROI/stop/horizon lifecycle.
6. A source ROI or structural stop can still close the trade before the confirmation decision.  No second position or shadow order is created.

## Predeclared transaction-level predictions

On the consumed June 2025 forensic interval:

- the two source-exit trades that were already positive after costs should be the only source-exit cohort materially delayed;
- both should preserve or improve their realized R and should be capable of resolving at the unchanged source ROI;
- source-exit trades that were non-positive after costs should remain immediate and should not acquire additional adverse excursion;
- if negative source-exit losses are delayed, or improvement comes from unrelated new entries rather than the predicted two episodes, the implementation or hypothesis is rejected.

On the policy-fresh February 2025 interval:

- non-positive source exits must remain immediate;
- any profitable first-cross cohort must show better account contribution without increasing full-stop losses or causing a worse one-slot opportunity set;
- the candidate must beat the source control after costs with positive expectancy to earn component status;
- a higher aggregate return caused by one unrelated outlier while the predicted cohort is unchanged is a failed hypothesis;
- no retuning follows a failure.

## Frozen evaluation interval

`2025-02-01` through `2025-02-28`, selected before running this policy and not used by the IchiV2 source tournament or June lifecycle forensic.

This is a policy-fresh diagnostic, not a long evaluation.  Long or integrated evaluation is unauthorized regardless of the result until the causal predictions and one-slot account mechanics are verified.

## Invariants

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- maximum one pending entry or open position across the universe
- NautilusTrader matching/accounting
- current NAV based 3% planned loss
- realistic entry/exit fees, slippage and funding safety
- completed candles only
- no future information
- source control and candidate use the same data and execution assumptions
- no parameter grid
- no automatic long-stage escalation
