# v92 final failure analysis

## Result

The prospectively locked 2024-03-11 BTC week was executed only by
NautilusTrader 1.230.0 with current-account-NAV 3% risk sizing and the locked
cost model.

An inherited maximum reward/risk value of 8.0 initially suppressed the only
fully completed scenario. This was an implementation/configuration error: the
scenario design imposed a minimum cost-after reward/risk but no performance
ceiling. Removing only that ceiling and rerunning the same week restored one
trade. It lost 3.05% after cost.

The one permitted core-variable ablation removed only the requirement that
aggressive flow at the sweep align with the swept boundary. Every downstream
state remained mandatory. It produced five trades across all three UTC cycles:

| Metric | Result |
|---|---:|
| Trades | 5 |
| Trades/day | 0.714 |
| Wins | 0 |
| Cost-after profit factor | 0.000 |
| Geometric growth/day | -1.802% |
| Maximum mark-to-market drawdown | 12.98% |
| Final NAV | 88,050.33 USDT |

The source generator verified an exact four-line diff: the two-line flow
rejection predicate was replaced by two explanatory comments. No other source
line changed.

## Dominant failure

The scenario conflated two different claims:

1. an external sweep failed and caused a local opposite reaction;
2. price would continue through the entire frozen eight-hour range to the
   opposite external pool.

The first claim had partial support. Three of five trades reached more than
+1.5R favorable excursion before exit. The second had none: no opposite range
boundary target was reached within 240 minutes; four trades later hit the
sweep-extreme stop and one expired at maximum holding time.

Thus the failure was not simply a bad entry minute. The entry often located a
real local rebalance, but the distant objective lacked an independently
confirmed continuation state.

## What remains useful

* Frozen dealing-range boundaries are causal and auditable.
* Sweep, reclaim, displacement, structure break, FVG and retrace were separate
  states rather than an undifferentiated candle pattern.
* The initial reaction timing had measurable value in several trades.
* Cycle-only runs showed the negative result across 00:00, 08:00 and 16:00 UTC,
  preventing a false single-session explanation.

## What is rejected

* Failed sweep + CHoCH/BOS + FVG is not sufficient evidence of full range
  traversal.
* Sweep-direction aggressive flow is not the missing discriminator: removing it
  increased frequency and made expectancy uniformly negative.
* Large nominal reward/risk to a distant liquidity pool must not be mistaken for
  target probability.

No second variable is removed. v92 is discarded and cannot advance to a second
week or long-horizon evaluation.
