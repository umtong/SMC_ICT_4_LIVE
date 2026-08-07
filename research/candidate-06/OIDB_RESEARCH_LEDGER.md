# OIDB Research Ledger

## Hypothesis

A completed extreme open-interest contraction with aligned price displacement
and aggressive taker flow is a forced inventory shock. It is not traded
immediately. A later completed response classifies exhaustion/reclaim versus
continued deleveraging/price discovery.

## Fixed comparison

1. Full bifurcation — eligible.
2. Reversal branch only — attribution.
3. Remove OI contraction while retaining price/flow and response — core
   ablation.

Fixed BTC weeks: 2024-02-26, 2024-09-23, 2024-04-22. Long evaluation was
forbidden unless all three passed unchanged.

## Terminal result

|week|geom/day|trades|wins|win rate|PF|max DD|gate|
|---|---:|---:|---:|---:|---:|---:|---|
|1|1.3481%|14|10|71.43%|1.7632|6.47%|pass|
|2|-0.4674%|2|0|0.00%|0.0000|4.46%|fail|
|3|0.9327%|10|7|70.00%|1.6964|9.02%|fail: growth|

The no-OI ablation lost 8.4651% per day with 61 trades, 32.79% win rate and
46.16% drawdown. OI contraction therefore removed a large false-event set and
is retained as a useful market-state primitive.

The full candidate is discarded. Locked week 2 showed that price reclaim after
deleveraging was not sufficient evidence of durable counter-inventory: both
trades were exhaustion reversals and both lost. Week 3 showed the mechanism can
work, but not at the fixed target across regimes. No parameter or directional
rescue was performed and no long evaluation was authorized.

## Implementation closure

Entry context and order-ledger scenario IDs were separated so the context could
continue producing invalidations while the order ledger followed
`ENTRY_ARMED → ORDER_SUBMITTED → POSITION`. Official metrics timestamps that
occur seconds after nominal five-minute slots are used only after the raw
timestamp, at the next completed minute; they are never shifted earlier.

## Preserved contracts

NautilusTrader 1.230.0 only; realistic fees and one-tick adverse slippage;
current total NAV; 3% planned loss; one global new-order/position slot; no
score-based risk multiplier or arbitrary notional cap.
