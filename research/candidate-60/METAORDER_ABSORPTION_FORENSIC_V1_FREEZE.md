# Candidate 60 — metaorder absorption forensic V1 freeze

## Status

**Frozen outcome-aware forensic study on consumed development data. This is not a trading policy. The reserved fresh interval remains untouched.**

V2 established that visible price confirmation changed the intended V1 loss
group but usually arrived after the natural objective was consumed or after the
remaining geometry became uneconomic. The next uncertainty is therefore not
which confirmation threshold scores best. It is whether a price-leading state
can be observed before the reclaim.

This study records the complete completed-minute path around all seven exact V1
provisional events. Outcome labels are attached only after the causal
observations are written so the known development groups can be compared.

## Reuse before build

- exact immutable V1 parent detector is imported and called directly;
- Candidate-51 returned one-minute klines and real-flow feature contract are
  reused;
- no backtest engine, matching engine, portfolio simulator or account engine is
  added;
- the script is a diagnostic trace and cannot place or authorize a trade.

The representation imports established mechanisms from outside the original
trading-pattern domain:

1. **Adaptive liquidity / asymmetric liquidity.** Predictable aggressive flow
   should have less marginal price impact when liquidity providers adapt to it.
   The trace therefore measures return per absolute aggressor flow relative to
   the completed parent run.
2. **Statistical process monitoring.** Return, efficiency and impact-per-flow
   are standardized with robust parent-run centers and scales rather than
   compared with arbitrary raw magnitudes.
3. **Order-book resilience.** Opposite-side replenishment is compared with
   same-side replenishment over completed one- and three-minute windows.
4. **Execution benchmarking and latent value.** The trace records the parent
   run VWAP and the 15-, 30- and 60-minute pre-run VWAP centroids. These are
   possible inventory/value references, not fitted take-profit levels.
5. **Competing-risks / stopping-time analysis.** Every event records which
   occurs first: renewed parent extreme, source stop, source target, parent-run
   VWAP, pre-run value, absorption under load, or counterflow resilience.

## Frozen source and interval

- immutable V1 source SHA-256:
  `7dc2c1da597c518bc427ec2869d03623f363cbf212cf90eb319aca127885b71a`
- forensic source SHA-256:
  `71f565fe2ca86463362c8a68a68ea6f3d45eb46c38a64b842926a791129f14ad`
- source feature/data commit:
  `f7787095f98b27f31fa3766bda13a94ae350269d`
- consumed development interval: `2026-04-13` through `2026-04-19` UTC
- reserved fresh interval: `2026-08-03` through `2026-08-09` UTC
- universe: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`
- trace horizon: 120 completed minutes after each provisional event

The development interval and its seven outcomes are already known. This study
is permitted to use those outcomes for causal forensic comparison, but no
result from it is holdout evidence.

## Outcome groups fixed before execution

Each exact V1 event receives one label:

1. `FAST_TARGET`: the V1 natural target was reached before its source stop;
2. `EARLY_STOP_LATER_POSITIVE`: V1 stopped, but the proposed reversal direction
   was positive after the 20 bp cost floor at 60 or 120 minutes;
3. `STOP_NO_LATER_POSITIVE`: V1 stopped and neither 60 nor 120 minutes was
   positive after costs;
4. `OTHER`: any residual shape, which would require separate inspection.

Expected counts from the already-recorded V1 evidence are:

- `FAST_TARGET`: 3
- `EARLY_STOP_LATER_POSITIVE`: 3
- `STOP_NO_LATER_POSITIVE`: 1

A mismatch is an implementation failure.

## Causal trace fields

At each completed minute from the V1 provisional through +120 minutes, record:

- renewed parent-direction extreme and bars since the dynamic extreme;
- parent-direction versus opposite-direction flow consensus;
- directional one-minute return and efficiency;
- robust z-scores versus the completed parent run;
- directional return per absolute `flow_60s` and its robust z-score;
- opposite-side and same-side depth change;
- one- and three-minute opposite-minus-same resilience;
- retrace fraction from the dynamic extreme;
- directional premium change and five-minute OI change;
- parent-run VWAP and 15/30/60-minute pre-run VWAP centroids;
- first touch of each value reference;
- next-open cost-aware geometry to each pre-run value reference using the
  dynamic extreme plus the existing 0.15 ATR(30) stop buffer.

## Diagnostic observations, not trade rules

The script marks three interpretable observations only to make event paths
comparable. They are not entry policies.

### Absorption under load

```text
parent-direction flow consensus
+ return z <= -1 versus the parent run
+ impact-per-flow z <= -1 versus the parent run
+ positive three-minute opposite-minus-same depth resilience
+ at least one completed bar since the dynamic parent extreme
```

### Deep absorption under load

The preceding observation plus:

- at least two consecutive parent-flow minutes; and
- efficiency z <= -0.5 versus the parent run.

### Counterflow resilience

```text
opposite-direction flow consensus
+ positive three-minute opposite-minus-same depth resilience
+ at least one completed bar since the dynamic parent extreme
```

These nominal standardized cutoffs are diagnostic landmarks, not a searched
parameter family. They must not be tuned against the seven labels.

## Pre-result predictions

A useful price-leading representation should create the following ordering:

1. The `FAST_TARGET` group may reverse too quickly for deep absorption under
   load. Missing some of these is expected.
2. The `EARLY_STOP_LATER_POSITIVE` group should show renewed parent extremes
   after the V1 provisional, then one of the absorption-under-load observations
   before visible 10% reclaim and before the later reversal matures.
3. The single `STOP_NO_LATER_POSITIVE` event should either lack absorption
   under load, show continued efficient parent impact, or lose pre-run-value
   geometry before the observation appears.
4. If absorption landmarks occur equally or earlier in the non-reversal event,
   the proposed representation is falsified.
5. If only outcome-selected pre-run value windows produce adequate geometry,
   the value-objective hypothesis is falsified. No window will be selected by
   highest PnL.

## Decision boundary

After the trace:

- preserve an observation only if it changes the predicted V1 loss group for a
  market-mechanism reason visible before outcome;
- do not build V3 if the seven paths do not separate causally;
- do not consume fresh data;
- do not tune standardized cutoffs, value windows, symbols or directions;
- if a coherent state survives, freeze a distinct V3 policy before any further
  performance run;
- any later promotion must use the existing NautilusTrader four-asset
  continuous account, one global slot, realistic costs and current-NAV 3%
  planned loss sizing.
