# Candidate 60 — metaorder lifecycle exhaustion V1 development freeze

## Why this family exists

The real micro-auction forensic showed two different failures:

- a one-minute efficient-flow balance break described price discovery after almost all immediate objective space had already been consumed;
- a sweep/reclaim absorption state had a small defending-side bias, but its typical remaining move was only a few basis points and could not pay 20 bp round-trip costs.

The missing variable is not another static threshold. It is the **lifecycle of persistent execution**:

> repeated aggressive child-order flow
> → cumulative price impact and opposing-liquidity consumption
> → run age and impact accumulation
> → marginal price impact and efficiency decay while flow is still directionally persistent
> → likely completion of the urgent/metaorder inventory transfer
> → temporary impact decay toward the run origin

This is the market mechanism tested by V1.

## Frozen data and source

- immutable feature lineage: Candidate-51 source commit `f7787095f98b27f31fa3766bda13a94ae350269d`
- exact feature builder: Candidate-05/16/51 at that commit
- universe: `BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT`
- consumed development interval: `2026-04-13` through `2026-04-19` UTC
- reserved policy-fresh interval: `2026-08-03` through `2026-08-09` UTC; **not consumed in V1 development**
- 1-minute completed observations; next-minute-open execution
- diagnostic round-trip cost: 20 bp
- no source threshold search or parameter matrix

## Persistent execution run

At each completed minute `t`, a directional run is active only when all are true:

1. `flow_60s` and `flow_3m` have the same non-zero sign;
2. `abs(flow_3m)` is at least the prior-only 240-minute median of `abs(flow_3m)` (minimum 60 prior observations);
3. `notional_burst >= 1.0`;
4. `trade_count_burst >= 1.0`;
5. the real-data feature row is ready.

A neutral or opposite row ends the run. No gap filling is allowed.

For an active run, V1 tracks causally:

- run direction;
- consecutive run age;
- run-start open;
- run high and low;
- cumulative direction-adjusted close return;
- running mean direction-adjusted one-minute return;
- running mean one-minute path efficiency.

## Exhaustion transition

Only the first completed minute in a run satisfying all conditions becomes a candidate:

1. run age is at least 7 completed minutes;
2. cumulative direction-adjusted run return is at least 20 bp;
3. current direction-adjusted one-minute return is below its running run mean;
4. current one-minute path efficiency is below its running run mean.

The proposed side is opposite the run direction. This does not require a later price/flow reversal candle; the purpose is to anticipate completion before the one-minute rejection move is consumed.

The age, displacement and decay conditions were selected on the consumed development interval. They are therefore development policy, not evidence. They may be changed only before any fresh evaluation and only from market-mechanism reasoning, never from a fresh outcome.

## Frozen auction geometry

- entry observation: next minute open;
- target: run-start open, representing decay of the temporary impact component;
- invalidation: run extreme plus `0.15 × ATR(30)` outside the run;
- geometry requires correct stop/entry/target ordering;
- target distance after 20 bp cost must be positive;
- cost-aware reward/risk must be at least 1.0;
- maximum hold: 60 minutes;
- if target and stop are touched in the same completed minute, stop is assumed first;
- otherwise exit at the first target or stop, or the 60-minute close.

The geometry is evaluated separately from the state so that state failure, objective exhaustion and invalidation failure are not conflated.

## Global account screen

- simultaneous events within three minutes are one causal episode;
- episode arbitration uses only entry-time cost-aware reward/risk, then fixed symbol priority;
- one global slot remains occupied through the selected event's frozen exit;
- diagnostic account risk is 3% of current continuous NAV per planned loss;
- this is a signal/geometry screen, not a substitute for NautilusTrader execution or accounting.

## Development interpretation

A positive aggregate is not enough. V1 requires examination of:

- raw candidate and geometry-eligible counts;
- four-symbol and directional coverage;
- target/stop/timeout composition;
- cost-after R mean, median, trimmed means and largest-event concentration;
- exact-opposite fixed-horizon attribution;
- one-slot continuous NAV;
- whether impact decay actually separates from non-decaying long-run controls.

A negative aggregate does not automatically invalidate the state. V1 must determine whether the failure is state direction, run-origin target, invalidation, confirmation timing or cost space.

Only a coherent development mechanism may be frozen for the reserved August interval.