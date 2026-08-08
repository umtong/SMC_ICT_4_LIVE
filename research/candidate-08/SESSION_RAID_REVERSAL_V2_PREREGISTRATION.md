# Session Raid Reversal V2 — execution-correction preregistration

## Status

This document is frozen before the V2 replay. V2 is not a parameter search and does not modify the
market scenario. It repairs one deterministic execution-cost mismatch exposed by the V1 first BTC
week.

## V1 evidence that justifies an implementation correction

The V1 detector produced four complete day-trading signals and four native NautilusTrader entries.
Every position was then exited on the first later ten-second bar because the fill-adjusted expected
loss exceeded the three-percent shared-NAV budget by only 0.0048% to 0.0364%. In every case the
observed market-entry fill was exactly two BTC ticks adverse to the signal reference while V1 had
reserved only one entry tick. The recorded realized losses remained below the signal-time loss
budget, but the forced exits make the V1 return and win rate unusable as evidence about the scenario
edge.

This is an implementation failure, not permission to relax the risk budget. The correction sizes the
position for the pinned replay's full deterministic bar-market entry path before order submission:

1. one tick for crossing the synthetic executable side when native quotes are absent; and
2. one tick forced by `OneTickSlippageFillModel`.

The stop-slippage Q99 reserve, funding reserve, fees, structural stop and target are unchanged.

## Frozen V2 scenario

V2 keeps V1 exactly unchanged:

- direction: completed H4 displacement through a causally confirmed H4 swing;
- source inventory: completed Asia range for Europe and completed Europe range for the US route;
- confirmation: a completed destination-session fifteen-minute raid of the source boundary opposite
  the H4 draw followed by a close back inside;
- entry time: the first completed ten-second bucket strictly after that fifteen-minute confirmation;
- location: long entries in the lower half and short entries in the upper half of the completed source
  range;
- stop: the observed raid extreme plus a fixed 0.05 five-minute ATR buffer;
- target: nearest still-unconsumed source-opposite liquidity, otherwise the already-frozen HTF
  external target;
- maximum holding time: six hours;
- asset and first window: BTCUSDT, 2024-04-08 through 2024-04-15 UTC;
- current shared NAV risk fraction: exactly 3%;
- fees: 6 bp on every fill;
- no notional cap, leverage cap, model-score multiplier or asset-specific parameter.

## Frozen cost correction

Only the following quantity-sizing term changes:

```text
V1 expected entry execution reserve = 1 tick
V2 expected entry execution reserve = 2 ticks
```

At the actual entry fill, the fill-adjusted expected loss is recomputed from the realized fill price.
V2 must not classify a position as safe merely because the eventual realized loss was below budget.
The expected fill-to-stop loss including fees, stop reserve and funding must itself remain within the
three-percent signal-time budget.

## Promotion and termination

The first BTC week promotes unchanged only if all of the following hold:

- at least three closed trades;
- cost-after total return is positive;
- no entry, fill-adjusted loss, realized loss, funding, order, position, liquidation or residual
  exposure contract failure;
- all signal timestamps are processed.

If the first week passes, V2 runs the two already-frozen additional BTC weeks without any parameter
or rule change. If the corrected first week is implementation-clean but negative, the direct session
raid family is rejected rather than rescued by further threshold tuning. The only previously
registered diagnostic ablation remains removal of the source-half location condition, and it is
allowed only when that rejection reason is dominant; ablated evidence cannot promote directly.
