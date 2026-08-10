# Candidate 57 — public Slope-is-Dope short-exit repair v3 freeze

## Failure isolated from v2

After the ROI schedule was mechanically repaired, the claim-profile short account completed 52 trades in fourteen days but only 4 were profitable. Fifty-one of the 52 trades exited through the public source signal, while only one reached the profitable trailing state.

The public short exit is asymmetric:

- long: `fast MA < slow MA OR close < prior rolling minimum low`;
- short: `fast MA > slow MA OR close > prior rolling minimum low`.

For a short position, close is normally above a recent rolling minimum. The second condition therefore requests an exit almost immediately and prevents the strategy's trailing engine from operating. This is a structural logic defect, not a threshold problem.

## Frozen experiment

The v2 claim-profile entries, risk, ROI, trailing, one-hour candle construction, four-symbol arbitration and account mechanics remain unchanged. Only the short source-exit interpretation changes.

### Cells

1. `symmetric_short`: short exit is `fast MA > slow MA OR close > prior rolling maximum high`.
2. `symmetric_both`: same repair with source long logic unchanged.
3. `ma_only_short`: short source exit is only `fast MA > slow MA`.
4. `ma_only_both`: same repair with source long logic unchanged.

The preexisting v2 literal short and both accounts are controls and are not rerun.

## Information-preserving selection

Development remains 2026-04-15 through 2026-04-28 because it is consumed diagnostic data. Every new cell persists every completed trade, R distribution, exit mix and winner-versus-loser source-state contrasts.

Up to two cells may consume the untouched interval 2025-10-13 through 2025-10-19 when they are mechanically valid and either:

- have positive development expectancy and growth; or
- materially improve the corresponding literal control's return while reducing the source-signal-exit share.

This is resource allocation, not a claim that a development-positive or improved cell is already valid alpha. A 30-day continuous run, 2025-08-01 through 2025-08-30, is consumed only for a positive untouched survivor.

The strict final project pass is unchanged: one continuous account, after-cost geometric daily growth at least 1%, at least one completed independent trade per calendar day on average, positive expectancy, profit factor above one or no losses, maximum drawdown at most 20%, no liquidation and valid one-slot mechanics.
