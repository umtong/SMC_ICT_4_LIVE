# Quote-Resiliency Execution and Risk Contract

## Purpose

This file records the execution-price correction discovered before the first economically evaluable
quote-resiliency week. It is an implementation correction, not a strategy threshold change.

## Observed defect

The first native replay used the final aggregate-trade price of the confirmation bucket as
`entry_reference`, then added one adverse tick as the expected entry reserve. The native market
order, however, starts from the executable side of the market:

- buy market order: completed best ask;
- sell market order: completed best bid.

The incumbent bar/L1 simulation then applies the configured one adverse fill tick. When the final
trade was one tick inside the side-specific executable quote, the old accounting understated entry
cost by one tick. The resulting fills exceeded the current-NAV three-percent loss budget by roughly
0.1–0.2% of the planned loss budget even though the signal detector and structural stop were
unchanged.

## Official platform semantics

NautilusTrader documents market execution as filling at the current bid/ask and documents L1
`FillModel.prob_slippage` as moving each selected fill one tick against the order direction.

- https://nautilustrader.io/docs/latest/concepts/backtesting/
- https://nautilustrader.io/docs/nightly/concepts/backtesting/fill-prices-and-matching/
- https://nautilustrader.io/docs/nightly/concepts/backtesting/fill-models/

The corrected expected fill contract is therefore:

```text
long expected market fill reference
= completed best ask at confirmation
+ one adverse fill tick

short expected market fill reference
= completed best bid at confirmation
- one adverse fill tick
```

The quote itself is observed at the completed confirmation bucket. The adverse fill tick remains the
existing execution reserve. No model score, arbitrary risk multiplier, notional cap, or leverage cap
is introduced.

## Separation of market logic and execution logic

The market scenario remains unchanged:

- interaction, displayed-liquidity response, reclaim/hold, retest, and confirmation are decided from
  completed trade/quote states;
- the aggregate-trade close remains the price-progress confirmation;
- the side-specific completed best bid/ask is used only for executable entry cost geometry;
- the structural stop and completed external target remain scenario-derived;
- quantity remains `current shared NAV × 3% / expected loss per unit` after venue rounding.

This correction can change whether an otherwise identical signal has sufficient cost-after target
geometry. That is expected: the old version priced the same market order from a non-executable trade
print.

## Fill-adjusted emergency exit timing

If the realized entry fill still makes the expected stop loss exceed the three-percent budget, the
position is not allowed to close at the same native timestamp as `POSITION_OPENED`.

```text
permitted fill-adjusted forced exit time
> native position open event time
```

A synchronous callback request at or before the open timestamp is blocked and kept pending for the
first separately completed ten-second bar. The guard changes no ordinary stop, target, timeout, or
unexpected-close path.

## Evidence preservation

Before merged event-chain validation, every raw native execution callback is written to
`raw_execution_events.json`. This prevents an evidence-validator failure from destroying the exact
callback sequence needed to distinguish:

- native execution failure;
- adapter state-transition failure;
- fill-adjusted risk failure;
- strategy logic failure.

## Frozen-test rule

After this correction, the same frozen BTC week, same quote-response thresholds, same external
levels, same cost assumptions, and same 3% risk fraction are rerun. No result from a pre-correction
run is used as economic evidence.
