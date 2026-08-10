# Candidate 57 — 4h jump-reversion repair freeze v1

## Why this family is being advanced

The original 4h/2σ causal jump-reversion run on 2026-01-01 through 2026-01-14 completed 14 independent account trades. It produced four wins and ten losses, yet the average winner was about 3.36 planned-risk units while losses were close to one unit. Gross profit was large enough to produce positive expectancy and 0.655% geometric NAV growth per calendar day despite the low win rate.

The important observation is not that this run narrowly missed or passed a gate. The system already contains a meaningful rare-event alpha engine. Its loss engine is structurally suspicious: many losses terminate within minutes even though profitable trades survive toward the four-hour source horizon. The stop was added around the terminal one-minute auction extreme merely to satisfy the risk contract; it was not part of the source alpha hypothesis. That makes entry/stop geometry a potentially high-leverage repair point.

## Frozen interval

All variants below are fixed before observing `2025-11-01` through `2025-11-14`. The interval is used once to compare mechanism-specific changes. Once results are inspected it becomes development evidence for subsequent work.

## Invariants shared by every variant

- Four-symbol universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- One global pending entry or position across the universe.
- NautilusTrader matching, portfolio and account accounting.
- Current NAV × 3% maximum planned loss per trade, with fees, slippage and funding reserve included by the reused execution shell.
- Completed four-hour return; causal z-score estimated from the preceding 18 completed four-hour returns only.
- Source threshold: absolute z-score at least 2.0.
- Reversion direction: opposite the completed jump.
- Exit horizon: the original jump boundary plus one equal four-hour period.
- No arbitrary nominal cap or score-based risk multiplier.

## Mechanism-isolating variants

1. `baseline_terminal_source`
   - Original source-score arbitration.
   - Stop beyond the terminal one-minute extreme plus the existing causal buffer.
   - Immediate entry after the completed jump.

2. `impulse_extreme_source`
   - Same entry and arbitration as baseline.
   - Only the invalidation geometry changes: stop beyond the entire completed four-hour impulse extreme.

3. `terminal_residual_rank`
   - Baseline stop and immediate entry.
   - Only arbitration changes: among simultaneous jump candidates, prioritize the largest idiosyncratic return residual relative to the other three assets rather than the largest absolute jump score.

4. `confirm5_source`
   - Source-score arbitration.
   - Wait for a completed five-minute post-jump bar to re-enter through the terminal minute extreme in the reversion direction, within fifteen minutes.
   - Entry occurs only after that state transition; stop is beyond the observed post-jump extension extreme. The original four-hour exit deadline is not extended.

5. `confirm5_residual_rank`
   - Combines idiosyncratic residual arbitration with the same post-jump rejection confirmation. It tests whether the two repairs solve distinct problems rather than simply adding filters.

## Interpretation rule

No variant is accepted or rejected from final PnL alone. The synthesis must report:

- gross winner engine in risk units and per day;
- gross loss engine and how much comes from immediate stop-outs;
- winner and loser holding signatures;
- opportunity density and source-candidate conversion;
- symbol, side and time concentration;
- baseline winner preservation and baseline loss avoidance by causal episode;
- whether a repair changes the intended subsystem while preserving the original alpha engine.

A low-frequency high-payoff variant may remain valuable as one scenario family. A high-frequency negative variant may remain valuable only if its loss engine is demonstrably separable. Neither conclusion follows from frequency, win rate, or net PnL in isolation.
