# Quote Resiliency V2 — Event-Time Impulse-Response Preregistration

## Activation rule

This document is registered **before** the first economically evaluable V1 result. V2 is activated
only when:

1. V1 completes the frozen first BTC week with all implementation/evidence contracts intact;
2. V1 fails its economic/opportunity gate;
3. the one predeclared V1 confirmation-OFI ablation also fails or only identifies a new-base lesson;
4. no V1 threshold is retuned.

A V1 success proceeds to its frozen screen weeks instead. V2 is a structurally new measurement
family, not a V1 parameter search.

## Motivation from primary research

Jeremy Large, *Measuring the resiliency of an electronic limit order book* (Journal of Financial
Markets, 2007), formalizes resilience as a continuous-time impulse response and replenishment
probability after a large trade. Reported replenishment, when it occurs, is fast rather than a static
end-state property.

- https://doi.org/10.1016/j.finmar.2006.09.001

Xu et al., *Limit-order book resiliency after effective market orders: Spread, depth and intensity*,
measure the evolution of spread, depth, and order intensity after effective market orders and report
different response paths for more- and less-aggressive shocks.

- https://arxiv.org/abs/1602.00731

Bechler and Ludkovski, *Order Flows and Limit Order Book Resiliency on the Meso-Scale*, report
nonlinear trade-imbalance/price response and substantial information in relative limit-order
addition/cancellation rates.

- https://arxiv.org/abs/1708.02715

These sources motivate measuring the **trajectory and speed** of liquidity response rather than
classifying a scenario from one cumulative quote-add/remove ratio.

## Structural defect V2 is designed to address

V1 compresses at most three completed ten-second response buckets into cumulative bid/ask additions,
removals, quote OFI, and one terminal reclaim/hold state. That representation can lose:

- whether replenishment occurred immediately or only after price had already failed;
- whether displayed size survived or was cancelled one update later;
- whether price impact decayed after the shock or remained persistent;
- whether the same cumulative ratio came from one large update or many durable updates;
- the ordering of depletion, replenishment, spread recovery, and price response.

V2 therefore treats an external-liquidity interaction as an impulse and observes a bounded response
curve in venue event time.

## Frozen data and independent weeks

V2 uses the same checksum-verified Binance Vision `aggTrades` and `bookTicker` sources and the same
NautilusTrader native execution infrastructure. It does **not** reuse V1's three BTC research weeks.

Using seed `8812`, then rejecting starts within seven calendar days of an already selected start,
the independently frozen non-overlapping V2 BTC weeks are:

| order | start | end, exclusive |
|---|---|---|
| 1 | 2023-08-23T00:00:00Z | 2023-08-30T00:00:00Z |
| 2 | 2023-09-20T00:00:00Z | 2023-09-27T00:00:00Z |
| 3 | 2023-11-12T00:00:00Z | 2023-11-19T00:00:00Z |

The seed, selection procedure, and dates are frozen before V1 outcome inspection.

## Event-time response horizon

After a completed external-level interaction, V2 observes until the first of:

- 20 seconds of exchange transaction time;
- 20 best-price-changing `bookTicker` events;
- boundary reclaim/hold invalidation;
- data discontinuity.

Size-only events remain in flow and survival calculations but do not consume the 20 price-change
counter. The dual bound is taken from the empirical idea that resilience is fast and visible in best
limit updates, while preventing millions of size-only updates from defining a horizon.

## Causal pre-shock baseline

All normalization is shifted and uses only observations completed before the interaction:

- prior 60 minutes of aggressive signed quantity;
- prior 60 minutes of bid/ask add and remove flow;
- prior 60 minutes of spread and displayed best size;
- prior 60 minutes of price-changing event intensity.

No response observation contributes to its own baseline. Insufficient history makes the scenario
unobservable rather than substituting a fitted constant.

## Response-curve facts retained without a fitted score

For each shock, retain the full ordered facts and the following dimensionless summaries:

1. **Opposing depletion fraction** — displayed quantity removed at the opposing best price relative
   to the pre-shock opposing quantity.
2. **Opposing recovery fraction** — displayed opposing quantity restored at the same or more
   competitive price relative to the pre-shock quantity.
3. **Recovery half-life** — first exchange-time delay at which recovery fraction reaches 0.5;
   otherwise right-censored.
4. **Quote survival** — fraction of response time during which the restored or withdrawn quote state
   remains intact.
5. **Peak outward impact** — maximum directional mid-price progress in ticks after the shock.
6. **Terminal outward impact** — directional progress at the end of the response horizon.
7. **Impact retention** — terminal outward impact divided by peak outward impact, with zero-peak
   cases rejected.
8. **Marginal impact** — directional mid-price progress divided by normalized aggressive signed
   quantity over each phase.
9. **Same-side support migration** — whether the supporting best quote moves in the shock direction
   and remains there rather than appearing for a single update.
10. **Spread recovery** — whether spread returns to its shifted causal median before confirmation.

The ordered response curve is written to evidence. The summaries do not replace it.

## Scenario A — fast resilient failed auction

A reversal can arm only after this observable sequence:

1. completed external high/low interaction with unusual outward aggressive pressure;
2. opposing displayed liquidity is initially depleted;
3. opposing recovery fraction reaches at least 0.5 inside the frozen response horizon;
4. the recovered quote survives through the response phase;
5. terminal outward price impact is no more than half of peak outward impact;
6. price reclaims the external boundary;
7. a separate completed confirmation phase produces opposite aggressive flow whose marginal price
   impact exceeds the terminal outward marginal impact;
8. price closes through the frozen response extreme.

The structural stop is beyond the complete shock-response extreme. The target is the nearest still
active completed external liquidity in the reversal direction.

## Scenario B — persistent non-resilient acceptance

A continuation can arm only after this observable sequence:

1. completed external high/low interaction with unusual outward aggressive pressure;
2. opposing displayed liquidity is depleted or retreats;
3. opposing recovery remains below 0.5 throughout the frozen response horizon;
4. same-side displayed support migrates in the shock direction and survives;
5. terminal outward impact retains more than half of peak outward impact;
6. spread recovers and price remains beyond the external boundary;
7. a separate retest touches the boundary with lower marginal price impact and lower normalized
   aggressive pressure;
8. a separate confirmation phase restores outward marginal impact and closes through the frozen
   retest extreme.

The structural stop is beyond the frozen retest extreme. The target is the nearest still active
completed external liquidity in the continuation direction.

## Costs, sizing, and execution

V2 inherits unchanged:

- side-specific completed L1 executable entry reference;
- one adverse fill tick reserve;
- causal shifted stop-slippage reserve;
- taker fees on entry and stop/target as configured;
- official funding and mark-price state;
- current shared NAV three-percent planned-loss sizing;
- one global pending entry or position;
- NautilusTrader native orders, fills, position, account, liquidation, and post-run path evidence.

No custom simulator, score-based risk multiplier, notional cap, or fitted leverage limit is added.

## Single V2 ablation

If the exact V2 base cleanly fails, remove only the **impact-retention half condition** while retaining
all depletion/recovery, quote-survival, boundary, marginal-impact, retest, stop, target, cost, and
execution contracts. This is diagnostic only and cannot be promoted directly.

## Falsification and discard

Discard V2 when the clean base plus its single ablation show any of the following without a new
structural path:

- fast opposing recovery does not precede external-target reversals;
- persistent depletion does not precede external-target continuation;
- the response curve classifications generate independent losses rather than target arrivals;
- most signals depend on one update, one day, or one shock;
- cost-after performance is weak despite direction being correct;
- L1 quote survival is unstable to parser chunk size or equal-time event ordering;
- fill-adjusted risk repeatedly exceeds the three-percent budget;
- the ablation merely increases trade count or losses.

## Research-method contribution

V2 tests whether **liquidity response dynamics**, not a static chart pattern or terminal quote ratio,
explain the next auction. It preserves the project's distinction among:

- pattern detection;
- economic scenario state;
- executable confirmation;
- structural invalidation;
- target realization;
- account-level performance.
