# Candidate 60 — metaorder resilience change-point V2 freeze

## Status

**Frozen consumed-development diagnostic. The reserved fresh interval remains untouched.**

This is not a parameter rescue of V1 and not a reversal of direction after
seeing outcomes. It holds the exact V1 parent-run detector fixed and replaces
only the invalid inference:

```text
first marginal impact/efficiency decay
→ immediate opposite entry
```

with a sequential stopping-time state model:

```text
persistent parent execution
→ provisional marginal decay
→ renewed extreme / unresolved transition / confirmed exhaustion
→ next-open reversal only after confirmation
```

## Reuse before build

The implementation directly imports and calls the immutable V1
`_build_symbol_events` function. The parent detector is neither copied nor
reimplemented. Candidate-51 data loading, returned one-minute klines, real-flow
features, symbol universe, cost convention, causal clocks, global episode
collapse, one-slot arbitration and the 3% planned-loss diagnostic account are
reused.

The new final-state detector combines established solutions from several
fields instead of inventing another trading threshold stack:

1. **Statistical process control / quickest change detection:** a one-sided
   standardized CUSUM detects a persistent negative shift in the parent move's
   marginal price force rather than reacting to one weak bar.
2. **Limit-order-book resilience:** confirmation requires opposite-side depth
   replenishment to dominate same-side replenishment over three completed
   minutes. Market orders alone do not identify whether the auction has become
   resistant.
3. **Transaction-cost analysis / execution benchmarking:** the natural target
   is the parent run's volume-weighted typical-price centroid, interpreted as a
   causal proxy for the inventory-transfer cost basis. It is not an arbitrary
   R multiple.
4. **Metaorder impact decay:** a partial reclaim from the final observed impact
   extreme is required, while every fresh parent-direction extreme resets the
   stopping-time evidence.

Useful source mechanisms include Page's continuous inspection/CUSUM,
Adams–MacKay Bayesian online change-point framing, empirical Bitcoin metaorder
impact and decay, and order-book-event studies showing that limit submissions
and cancellations contribute materially to price formation.

## Source and data freeze

- feature/data source commit: `f7787095f98b27f31fa3766bda13a94ae350269d`
- immutable V1 source SHA-256:
  `7dc2c1da597c518bc427ec2869d03623f363cbf212cf90eb319aca127885b71a`
- V2 source SHA-256:
  `b6e53f2f9f82e8ae972dbd81582c2050e5669762fc88a397e06fc8900730359b`
- consumed development interval: `2026-04-13` through `2026-04-19` UTC
- reserved fresh interval: `2026-08-03` through `2026-08-09` UTC
- the reserved fresh interval is not executed by this workflow
- universe: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`

The development interval was already consumed by V1. It is reused solely to
answer whether the proposed state correction changes the predicted V1 loss
mechanism. It is not holdout evidence.

## Exact V1 parent state retained

A parent run is generated only by the exact V1 implementation:

- `flow_60s` and `flow_3m` share a nonzero direction;
- absolute `flow_3m` is at least its strictly prior 240-minute rolling median,
  with 60 observations minimum;
- notional burst and trade-count burst are each at least 1.0;
- run age is at least 7 completed minutes;
- cumulative directional impact is at least 20 basis points;
- current directional one-minute return is below its running run mean;
- current efficiency is below its running run mean.

Every V1 event becomes one provisional V2 state. A mismatch in event count or
identity is an implementation failure.

## Exact stopping-time policy

### Parent force baseline

For the completed parent run, calculate causal robust centers and scales for:

- directional one-minute return;
- one-minute price efficiency.

The scale is the maximum of the sample standard deviation, `1.4826 × MAD`, and
an explicit numerical floor. The monitor combines the two robust z-scores with
equal weight.

### One-sided force-shift detector

For each completed post-provisional minute:

```text
S_t = min(0, S_(t-1) + force_z_t + 0.10)
```

A negative force shift is present only when:

- at least 3 post-provisional observations exist; and
- `-S_t >= 2.50`.

The detector is reset after every fresh parent-direction price extreme.

### Auction confirmation

A proposal exists only when all of the following are simultaneously true at a
completed minute close:

1. the force-shift detector has fired;
2. `flow_60s` and `flow_3m` are both opposite the parent direction;
3. over the latest 3 completed minutes, the sum of
   `opposite-side depth change - same-side depth change` is positive;
4. at least 2 completed bars have passed since the final observed parent
   extreme;
5. price has reclaimed at least 10% of the move from run-start open to the
   final observed extreme;
6. the parent execution-run volume-weighted typical-price centroid has not
   already been reached.

If these conditions do not align within 30 minutes, the state remains
`UNRESOLVED / NO TRADE`. If the natural objective is reached before
confirmation, it becomes `OBJECTIVE_CONSUMED / NO TRADE`.

### Entry, invalidation and objective

- direction: opposite the frozen V1 parent direction;
- decision: confirmation-bar close;
- entry: next minute open;
- natural target: parent run volume-weighted typical-price centroid;
- stop: final observed parent extreme plus `0.15 × ATR(30)`;
- same-bar target/stop: stop first;
- maximum hold: 120 minutes;
- round-trip friction floor: 20 basis points;
- minimum cost-aware reward/risk: 1.0;
- geometry that cannot meet the natural objective after costs is rejected;
- global causal-episode collapse: 3 minutes;
- one global open position at a time;
- diagnostic planned loss: current NAV × 3%.

The 120-minute timeout is not selected from a sweep. V1's fixed-horizon
attribution showed that the hypothesized reversal generally matured at 60 to
120 minutes while immediate brackets were often stopped first.

## Pre-result predictions

Before execution, V2 predicts the following trade-level changes:

1. V1 trades stopped before later positive 60- or 120-minute reversal should
   primarily become one of:
   - later entries after a newly observed final extreme;
   - `UNRESOLVED / NO TRADE`;
   - `OBJECTIVE_CONSUMED / NO TRADE`.
2. The stop boundary of any retained trade must be based on the last extreme
   observed before confirmation, not the earlier V1 provisional extreme.
3. Very fast reversals that reach the natural objective before three-bar
   sequential and depth confirmation may be deliberately missed.
4. Trade count should fall. This is acceptable only if the removed trades are
   the predicted premature-entry group rather than an arbitrary mixture.
5. Improvement caused by one unrelated large winner while the predicted V1
   early-stop group remains unchanged is a failed hypothesis.

## Falsification conditions

Retire the exact V2 policy if any of the following occurs:

- the directly reused V1 detector does not reproduce every V1 provisional;
- V2 continues to enter before post-provisional fresh extremes are complete;
- the early-stop/later-positive V1 group remains largely stopped for the same
  reason;
- confirmation merely reduces frequency without selectively changing the
  predicted loss mechanism;
- the natural VWAP-centroid objective is generally consumed before entry or
  cannot cover costs;
- accepted trades are still cost-negative as a group and no distinct causal
  child state explains the losses;
- positive performance depends on one event or one symbol.

Do not respond by sweeping the CUSUM threshold, reclaim fraction, depth window,
confirmation timeout, stop buffer, target, symbol or direction on this consumed
interval. A failed representation is replaced, not numerically massaged.

## Promotion boundary

This workflow writes only causal diagnostic evidence. It does not consume the
reserved August interval and does not create a production claim.

Only if the development evidence shows the predicted loss-group transformation,
multiple independent geometry-eligible events, and positive cost-aware behavior
without one-event concentration may a separate frozen test be authorized. Any
such test must then be executed through the existing NautilusTrader continuous
account with the four-asset one-slot constraint and realistic costs.
