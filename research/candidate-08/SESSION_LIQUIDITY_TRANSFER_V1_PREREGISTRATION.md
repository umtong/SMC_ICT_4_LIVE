# Session Liquidity Transfer V1 — Preregistration

## Research question

After a completed four-hour displacement establishes a draw on liquidity, does a completed destination-session raid and reclaim of the opposite source-session boundary initiate a repeatable intraday transfer toward the first still-unconsumed liquidity on the other side of that completed source range?

This is a day-trading hypothesis.  The expected holding period is tens of minutes to several hours.  Ten-second data is used only to place the next market order after a completed five-minute execution-location confirmation and to replay fills, fees, slippage, funding, and account NAV in NautilusTrader.

## Why this successor exists

Day Liquidity Delivery V1 produced one risk-valid positive trade but only one trade in the frozen BTC week.  Removing only standard three-bar FVG non-overlap produced the same one trade.  The redundant five-minute structure classifier, not FVG representation alone, was the binding frequency constraint.

The preserved market logic was:

1. a completed H4 displacement established direction;
2. a completed source-session boundary concentrated visible intraday liquidity;
3. the destination session raided the boundary opposite the H4 draw and closed back inside;
4. the sole executed trade moved profitably with the draw under realistic costs and risk accounting.

The new candidate therefore treats the completed 15-minute raid/reclaim as directional confirmation.  A later five-minute boundary retest supplies execution location only; it is not another direction classifier.

## Frozen state sequence

### Long

1. A completed H4 bullish displacement closes through a causally confirmed H4 swing high.
2. A completed Asia or Europe source range exists.
3. During Europe or US respectively, a completed 15-minute bar trades below the source low by at least `0.05 × causal 15m ATR` and closes back above the source low.
4. The H4 draw is unchanged and its active external context has not been consumed.
5. The first later completed five-minute bar that retests the reclaimed source low must close above the boundary and close bullish.  A first touched bar that closes back outside consumes the setup.
6. Entry is the first completed ten-second bucket strictly after that five-minute retest.
7. Entry must remain in the lower half of the completed source-session range.
8. Stop is below the observed raid extreme plus the fixed `0.05 × causal 5m ATR` structural buffer.
9. Target is the nearest still-unconsumed known liquidity above entry: first the completed source-session high if it remains untraded, otherwise the already-known completed day/week/H4 target selected at the raid.  No fitted-R target is permitted.
10. Cost-after reward/risk must be at least 1.2 before ordering.

### Short

The sequence is symmetric: bearish H4 draw, raid and reclaim of the completed source high, first later five-minute retest from inside closing bearish, entry in the upper half of the source range, stop above the raid extreme, target the first still-unconsumed source low or nearer active completed HTF target.

## Invalidation

The setup is cancelled before entry if any of the following occurs:

- the H4 draw changes;
- the active HTF context target is consumed;
- price consumes every eligible intraday/HTF target before entry;
- the first later boundary touch closes outside the reclaimed range;
- the destination route window ends without a valid retest;
- entry is outside the required half of the source range;
- cost-after reward/risk is below 1.2.

After entry, the structural stop, external/session target, funding avoidance, event-time timeout, native liquidation, and end-of-window flattening are handled by the existing NautilusTrader execution layer.

## Fixed risk and execution

- starting NAV: 100,000 USDT;
- planned loss budget: current shared NAV × 3%;
- effective fee: 6 bp per fill;
- entry reserve: one adverse tick;
- stop reserve: shifted 99th percentile of completed ten-second true range over the preceding hour, minimum one tick;
- checksum-verified official funding and mark price;
- native liquidation enabled;
- no arbitrary notional cap, leverage cap, or score-based risk multiplier;
- across all test assets, pending new entries plus open positions may never exceed one.  V1 is BTC-first.

## Frozen evaluation order

1. BTC `2024-04-08T00:00:00Z` to `2024-04-15T00:00:00Z`.
2. Only if the first gate passes unchanged:
   - `2025-06-09T00:00:00Z` to `2025-06-16T00:00:00Z`;
   - `2025-09-29T00:00:00Z` to `2025-10-06T00:00:00Z`.
3. Only if all three BTC weeks pass: longer BTC evaluation.
4. Only after BTC logic is frozen and durable: unchanged normalized logic on ETH, SOL, and XRP, then one shared-account global-position scheduler.

First gate:

- at least 3 closed trades;
- positive after-cost total return;
- no execution, causality, risk-contract, funding, liquidation, or residual-exposure failure.

Three-week gate:

- at least 3 trades each week;
- every week after-cost positive;
- positive-trade share at least 45%;
- no single positive trade contributes more than 50% of positive PnL;
- combined daily geometric growth at least 1%;
- no execution or residual-exposure failure.

## One allowed ablation

Only if the implementation is clean and the first window fails predominantly because the first five-minute boundary retest must close in the trade direction, remove **only the candle-direction requirement** while retaining boundary touch, close inside the reclaimed range, H4 draw, target, source-range half-location, stop, costs, funding, and risk.  The ablation is diagnostic-only and cannot be promoted directly.

No other threshold search or same-week modification is permitted.
