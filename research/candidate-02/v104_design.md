# Candidate-02 v104 — External-Liquidity Common-Acceptance Retest Continuation

## Status

`LOCKED_BEFORE_FIRST_WEEK_COLLECTION`

This candidate is a trading scenario, not a candle-pattern detector and not a custom backtest engine. Its signal module produces causal trade intents only. NautilusTrader 1.230.0 remains the sole owner of orders, fills, fees, position accounting, liquidation behavior and account NAV.

## Why v103 is not advanced

v103 was executed successfully through NautilusTrader 1.230.0 on its prospectively locked BTC week (`2025-11-17` through `2025-11-24`). The central `u8` variant completed 19 trades but produced a 31.58% win rate, 0.4271 cost-after profit factor, -3.3358% geometric daily NAV growth, and -25.65% mark-to-market drawdown. The adjacent `u10` variant improved to a 43.75% win rate and 0.8449 profit factor but still lost -0.6246% per day geometrically. Removing the front-depth-refill ceiling increased activity and worsened NAV and drawdown.

The result is therefore a **scenario-logic failure**, not an execution-plumbing failure. Endogenous turnover packets remain useful event detectors, but they did not answer the trading-scenario questions:

1. Which already-existing external liquidity was sought?
2. Was the breach accepted jointly by spot and perpetual markets?
3. Where is the logical invalidation of the new dealing range?
4. What is the next naturally ordered liquidity objective?

The fixed `1.25 × first-packet move` target was synthetic rather than a market-structural destination. The central median planned cost-after RR was only 0.9485, while a median 0.2182% stop produced a median 7.93× effective notional through the required 3% risk sizing and 21,430 USDT of modeled commissions. This does not justify an arbitrary notional cap; it shows that direction, invalidation and natural target must be structurally stronger before risk sizing.

## Detector layer

The detector layer maintains an immutable-at-time liquidity registry. A level is not tradable merely because a line can be drawn on a chart.

### Previous UTC day high/low

The level becomes eligible only after all 1,440 one-minute bars of the UTC day have closed.

### Previous UTC week high/low

The level becomes eligible only after all 10,080 one-minute bars of the Monday–Sunday UTC week have closed.

### Mature confirmed 15-minute swing

A swing is confirmed only after the configured right-hand bars have completed. It must then survive for eight hours. In the central model it must also demonstrate market memory through an approach and rejection before it can become eligible.

### Equal-swing cluster

Two causally confirmed swings may form a cluster only when they are separated in time, remain within a lagged ATR tolerance, and neither level was traversed between confirmations. This approximate family is deliberately isolated because it is the single precommitted ablation.

A wick traversal consumes a level even when later confirmation fails. A failed trade setup never resurrects already-taken liquidity.

## Scenario state machine

```text
KNOWN_EXTERNAL_LIQUIDITY
    ↓ first wick traversal during above-median prior-regime turnover
FIRST_EXTERNAL_BREACH
    ↓ exact three completed minutes
COMMON_SPOT_PERP_ACCEPTANCE
    ↓ displacement search begins only after acceptance closes
POST_ACCEPTANCE_DISPLACEMENT_AND_FVG
    ↓ later FVG + old-boundary retest
COMMON_MARKET_DEFENSE
    ↓ one full completed-minute execution delay
ACTIVATED_ENTRY
    ↓ nearest still-intact external liquidity only
TARGET / STRUCTURAL_INVALIDATION / MAX_HOLD
```

### 1. First external breach

The previous close must remain on the old-range side of the level. The current wick must traverse the level by the minimum lagged-ATR distance. Both-side consumption in one minute is classified as indeterminate and is a no-trade state.

### 2. Common acceptance

Over an exact number of completed minutes:

- enough perpetual closes remain outside the old boundary;
- the final perpetual distance exceeds a lagged-ATR floor;
- spot also closes beyond its basis-adjusted equivalent boundary;
- spot participation is not negligible relative to perpetual displacement;
- basis expansion does not explain most of the apparent move.

Spot/perpetual agreement is confirmation, not an assumption that either venue permanently leads price discovery.

### 3. Post-acceptance displacement and FVG

The displacement search cannot begin before the acceptance segment is fully known. The directional candle must exceed a shifted prior body quantile and an ATR floor, close beyond the boundary, and leave a causal three-candle gap.

### 4. Defended retest

A later completed minute must:

- overlap the FVG;
- return close enough to the old external-liquidity boundary;
- reject from the FVG midpoint in the delivery direction;
- close on the accepted side in both perpetual and spot;
- avoid strongly opposite combined spot/perpetual aggressive flow;
- avoid wicking through the structural old-range invalidation.

The session label and volatility regime are recorded only for diagnostics. They do not filter trades and cannot become hidden optimization knobs.

### 5. Delayed execution and activation revalidation

The decision is known at the retest close. The order cannot activate on that same close. The scheduled signal is delayed by exactly one completed minute. At the activation callback, a v104-specific NautilusTrader adapter repeats the locked structural and economic checks using the actual activation close and the full just-completed activation-bar range. It rejects the signal before order submission when any of the following occurred:

- the close returned inside the old external-liquidity boundary;
- the structural stop/old-range/entry/target ordering is no longer valid;
- the activation bar already traversed the exchange-rounded stop, meaning the scenario failed before an order could exist;
- the activation bar already traversed the exchange-rounded natural target, meaning the scenario completed before an order could exist;
- the activation bar traversed the old-range structural invalidation even when the wider protective stop survived;
- the target was not already known at the decision close or is no longer active at activation;
- actual boundary-to-target delivery exceeds the locked first-half limit;
- actual cost-after reward/risk falls below the locked floor;
- the signal cost model differs from the execution cost model.

Before sizing, the adapter converts stop and target to the exact instrument price increment, reruns geometry and cost-after-RR with those executable prices, and only then delegates current-NAV sizing, orders, fills, costs, liquidation and NAV to NautilusTrader. This prevents tick rounding from increasing the executable stop loss after the 3% budget was calculated.

### 6. Structural invalidation

The stop is placed beyond both the retest swing and the old-range invalidation threshold, with only the explicit ATR buffer. No arbitrary leverage, notional or score-based risk cap is introduced.

### 7. Natural target

The target is the nearest unconsumed external-liquidity level which was already eligible by the decision close, remains active at the later activation timestamp, and lies beyond the entire event-to-decision path. A level confirmed only by the activation bar is future information for this one-minute-delayed decision and cannot be selected. The nearest objective is decisive:

- it may not be skipped to manufacture a higher reward/risk ratio;
- entry must remain in the first half of the boundary-to-target delivery range;
- if the nearest target cannot cover the locked cost-after-risk floor, the state is no-trade.

## Risk and execution contract

```text
planned loss budget = current NautilusTrader NAV × 0.03
quantity = planned loss budget ÷ expected per-unit loss
```

Expected per-unit loss uses the exchange-rounded executable stop and includes entry-to-stop distance, entry/stop fees, slippage, market impact and funding allowance. There is no independent maximum notional cap, arbitrary leverage cap or score risk multiplier. The first three prospective screens are BTC-only, so pending entry plus open position is necessarily at most one. Multi-asset evaluation is forbidden until the execution layer has one shared portfolio gate across BTC, ETH, SOL and XRP; per-strategy busy checks alone are not sufficient for that later stage.

## Research foundations and limits

- Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events* (arXiv:1011.6402), supports treating order-flow impact jointly with available depth rather than using trade volume alone; it does not validate an ICT setup or a profitable target.
- Bechler and Ludkovski, *Order Flows and Limit Order Book Resiliency on the Meso-Scale* (arXiv:1708.02715), motivates retaining limit-order additions/cancellations and deeper-book shape as diagnostics around impact persistence; it does not justify making refill a standalone direction rule.
- The Inner Circle Trader's *Elements Of A Trade Setup* (YouTube `0LhteuLVuDU`) is used only as a practitioner taxonomy: context and market condition precede the choice of a tool. Its claims are not treated as empirical proof.
- The same source family's impulse-swing and premium/discount material is used to make dealing-range and external-objective language explicit, not to replace prospective market testing.

These sources agree with the project's separation of detector facts from a causal trading scenario. v104 remains an unvalidated hypothesis until NautilusTrader produces cost-after NAV evidence.

## Prospective validation

The first BTC week was selected before collection:

- seed: `20260807104`
- period: `2025-12-08 00:00 UTC` to `2025-12-15 00:00 UTC`

No second week is allowed unless the central first-week model passes every gate. The preselected later weeks are:

- seed `20260807105`: `2025-02-03` to `2025-02-10`
- seed `20260807106`: `2024-07-15` to `2024-07-22`

Central first-week gates:

- at least 0.75 completed trades/day;
- win rate at least 50%;
- cost-after profit factor at least 1.50;
- NAV geometric daily growth at least 1.00%;
- maximum mark-to-market drawdown at most 25%;
- maximum planned loss no greater than the 3% budget;
- flat account at evaluation end;
- NautilusTrader 1.230.0, no custom backtest engine.

## Single precommitted ablation

If the baseline fails, remove only `EQUAL_SWING_CLUSTER` from the level registry. All other logic, timing, risk, costs, week and targets remain unchanged. The ablation diagnoses whether approximate equal-high/equal-low clustering adds noise. A retrospective ablation pass does not promote the candidate; the family remains rejected and any retained lesson must be prospectively locked in a new version.

## Required diagnostics

The output must permit attribution by:

- breached boundary family and member level IDs;
- acceptance failure reason;
- displacement/FVG availability;
- retest and invalidation failure reason;
- target family and distance;
- direction-correct/timing-wrong cases using MFE/MAE analysis;
- session and volatility regime, diagnostics only;
- scenario concentration by day and level family;
- scheduled, rejected, submitted and completed counts;
- current-NAV planned-loss utilization;
- fees, fills, mark-to-market NAV and liquidation status.
