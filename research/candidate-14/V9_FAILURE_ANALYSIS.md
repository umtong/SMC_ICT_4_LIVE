# Candidate 14 v9 failure analysis

## Decision

`candidate-14-v9-confirmed-acceptance-failure` is not a complete candidate. Its completion-before-failure chronology is retained as a valid state representation, but the v9 result does not establish expectancy for the new reversal branch.

The strategy ran from `2026-05-11` through `2026-08-03` in one continuous NautilusTrader account with no weekly reset.

- final NAV: `110,123.22061424 USDT`
- daily geometric growth: `+0.114863%`
- closed trades: `5`
- wins / losses: `3 / 2`
- win rate: `60.0%`
- payoff ratio: `1.7448`
- continuous realized drawdown: `5.9095%`
- active calendar weeks: `5 / 12`
- maximum consecutive empty weeks: `3`

All source provenance, account arithmetic, exact current-NAV 3% planned-loss budget, global one-slot, partial-fill protection, liquidation and engine audits passed.

## State funnel

The v9 state machine produced:

```text
ACCEPTANCE_COMPLETION_OBSERVED                60
CONFIRMED_ACCEPTANCE_FAILURE_OBSERVED         52
CONFIRMED_ACCEPTANCE_FAILURE_RESCINDED        18
CONFIRMED_ACCEPTANCE_FAILURE_REVERSAL         29
new failure-reversal entries submitted         0
```

This is a material correction versus v8. V8 generated 126 SCDAM trades from acceptance possibilities and destroyed the account. V9 required the original frozen hold, pullback and reacceleration sequence before failure, reducing the population to 29 later-initiative resolutions.

## Why the five trades do not validate v9

The five submitted trades were not the new accepted-auction-failure branch:

- three preserved exclusive-rejection FAR trades; all three won;
- two preserved Session I7 trades; both lost.

Every one of the 29 new v9 reversals was rejected with:

```text
SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM
```

The result therefore measures the retained v6/I7 portfolio after suppressing AAC, not the expectancy of confirmed accepted-auction failures.

## Causal measurement defect

The new plan contains three timestamps:

```text
original source sweep
→ accepted-auction completion
→ accepted-auction failure observation
→ later reversal initiative / entry
```

The portfolio runner nevertheless called the generic FAR market-leadership gate with `sweep_ts_ns` from the original source sweep. For a confirmed accepted-auction failure, that window contains the complete original acceptance leg. It asks whether peers and the candidate moved in the reversal direction over a period whose economic content was primarily the opposite-direction accepted auction.

For example, a later long reversal after a completed bearish acceptance can still show negative `candidate_event_move`, negative event displacement and peers continuing the earlier bearish path when measured from the original sweep. The gate then correctly rejects the wrong window, but does not evaluate the new reversal leg.

This is not a reason to relax peer-alignment, trend, rank, displacement or efficiency thresholds. The measurements are anchored to the wrong causal event.

## Next controlled change

Preserve every v9 state, target, stop, cost, risk and semantic threshold. Change only the leadership measurement origin for plans whose entry model is:

```text
CONFIRMED_ACCEPTANCE_FAILURE_LATER_INITIATIVE_MARKET
```

For those plans:

```text
leadership measurement start = acceptance_failure_ts_ns
leadership confirmation       = later initiative / plan observed time
```

All ordinary exclusive-rejection FAR, Session I7 and any future scenario retain their existing anchors. The same inspected L1 interval remains diagnostic and cannot support a success claim. If the failure-leg anchor produces a coherent mechanism, the source must be frozen before a newly reserved continuous interval is collected.
