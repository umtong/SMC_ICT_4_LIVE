# Opening Drive Acceptance Continuation V1 — terminal result

## Decision

`SESSION_OPENING_DRIVE_ACCEPTANCE_CONTINUATION` is rejected after an implementation-clean first BTC
week failed the minimum opportunity and growth requirements.

## Frozen first-week result

For 2024-04-08 through 2024-04-15 UTC:

- 66 complete thirty-minute initial balances;
- 61 first displaced closes outside an IB edge;
- 40 immediate second outside closes;
- 19 projected one-IB extensions consumed before a separate retest;
- 14 accepted drives that re-entered the IB;
- 11 first breaks not accepted by the immediate second close;
- 3 accepted drives with no later boundary retest;
- 2 final signals and 2 closed trades;
- 1 target, 1 structural stop;
- +4.3086% cost-after NAV return;
- +0.6044% daily geometric NAV growth;
- 2.2285% maximum realized NAV drawdown.

All order, fill, planned-loss, fill-adjusted-loss, realized-loss, funding, liquidation, causality and
residual-exposure contracts passed.

## Why positive return is not enough

The week contained only two trades, below the frozen minimum of three. Win rate was 50%, daily
geometric growth was below 1%, and the single winning trade supplied all positive PnL. The result does
not demonstrate repeated independent opportunity or durable compounding. Relaxing the separate
retest, immediate second-close or target-consumption conditions would turn this into post-result
threshold fitting and is prohibited.

## Next independent state

The next candidate does not use the opening IB as its directional alpha. It tests whether the actual
volume-weighted trading center migrates beyond the completed prior-day value range:

```text
complete previous UTC value profile
→ first M15 close outside value
→ immediate second M15 close outside
→ cumulative session VWAP also outside
→ separate M5 value-edge retest held
→ one completed value-range extension
```

This is `SESSION_PREVIOUS_DAY_VALUE_MIGRATION_CONTINUATION`, a distinct auction state rather than an
ablation of opening-drive acceptance.
