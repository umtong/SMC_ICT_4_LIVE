# Candidate 10 v22 — External Liquidity Target Hierarchy

## Structural hypothesis

A five-minute confirmed pivot can represent **internal liquidity** suitable for detecting a raid, displacement or local acceptance. It should not automatically become the economic objective of the trade.

After a valid v21 liquidation-auction confirmation, price is expected to seek the nearest unconsumed **external liquidity** in the direction of the confirmed auction result. In v22, external liquidity is operationally restricted to the high or low of an already completed eight-hour funding session.

```text
pre-existing internal or external source pool interaction
→ impulse + executed-flow + OI state
→ second completed 5m rejection or acceptance confirmation
→ first later raw aggregate trade
→ entry
→ completed 8h funding-session external liquidity target
```

## Full variant

`full-external-session-target`

- source pool: unchanged v21 pool universe
- target pool: nearest directionally valid, unconsumed, unreserved `FUNDING_SESSION` pool
- source pool cannot also be reused as target
- OI state remains required

## Exact ablation

`ablation-nearest-any-pool`

Only the target hierarchy is removed. The ablation uses v21's nearest active pool of the required side, including five-minute confirmed pivots.

The following remain identical:

- BTC fixed weeks and seed
- source pools
- raid and impulse definitions
- executed-flow thresholds
- OI state
- second-bar confirmation
- first-later-TradeTick entry
- stop
- taker fees
- size-dependent impact
- current whole-account NAV sizing
- 3% maximum planned loss
- funding/day flattening
- NautilusTrader order, position and NAV accounting

## Falsification

The hypothesis is rejected if a clean first-week run produces no meaningful opportunity, weak cost-after expectancy, or losses that are not materially improved relative to the nearest-any-pool ablation. It is not rescued by relaxing confirmation thresholds, optimizing session duration or adding symbol-specific filters.

If the full variant passes the first gate, the same frozen logic automatically runs the two remaining preselected BTC weeks. Only a three-week pass permits longer BTC evaluation.
