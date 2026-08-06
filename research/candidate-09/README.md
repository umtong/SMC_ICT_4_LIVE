# Candidate-09 — accepted-auction failure research

Candidate-09 implements SMC/ICT auction logic as causal state transitions and routes every
order, fill, position and account calculation through NautilusTrader. It is not a custom
backtest engine and it is not a parameter search program.

## Current status

**No final project success is claimed.**

The strongest completed result is v13's controlled `boundary-stop-all` ablation. v14 promotes
that causal invalidation rule to the frozen baseline, corrects the evaluation semantics to the
predeclared whole period, and automatically runs a three-year BTC evaluation only if the pooled
screen passes.

The complete fixed v14 workflow is:

```text
.github/workflows/candidate-09-v14-fixed.yml
GitHub Actions run 31124534428
trigger commit 9f92d85d16d791bba7c9776408fccc23d9ce9485
```

The run is pending because of the repository-external GitHub Actions incident which began on
2026-08-06. A pending run is neither implementation evidence nor strategy evidence. The branch
must not be described as successful until the native Nautilus result is committed.

## Research question

A completed auction extreme is a neutral liquidity event. The engine asks:

```text
where was external liquidity formed?
→ was the boundary merely breached or accepted outside?
→ did acceptance persist or fail?
→ what observable boundary invalidates the failure thesis?
→ which already-observed source-auction equilibrium is the causal target?
```

Pattern detection is separate from the trading scenario:

- The detector confirms completed 15-minute, 60-minute and daily auction ranges, approach
  pressure, breach, outside closes, displacement, volume participation and aggregate taker flow.
- The scenario consumes those states only after the configured sequence has completed.
- The Nautilus adapter exclusively handles orders, fills, protective brackets, positions,
  commissions and account NAV.

No FVG, BOS, CHoCH, order block, premium/discount label or session name is accepted as a
standalone entry signal.

## Current v14 causal scenario

```text
completed 15m / 60m / daily auction extreme
→ directional approach pressure
→ meaningful breach excursion
→ two completed closes accepted outside the source auction
→ displacement, participation and aligned taker flow
→ accepted boundary lost with opposite displacement and opposite failure-bar flow
→ market reversal at the completed failure close
→ invalidation beyond the failed boundary and completed failure-bar extreme
→ source-auction midpoint, or the causal opposite source edge when midpoint geometry is invalid
```

The v13 controlled ablation established that, after acceptance has failed, renewed acceptance
beyond the failed boundary is a more coherent invalidation than retaining the extreme of the
entire prior accepted excursion. v14 changes no entry observation time or target.

## Fixed BTC screen

The same three BTCUSDT one-minute UTC weeks are preserved:

```text
week-a: 2022-02-07 through 2022-02-13
week-b: 2023-06-12 through 2023-06-18
week-c: 2024-10-14 through 2024-10-20
```

Positive, negative and inactive subperiods are permitted. The 21-day pooled screen requires:

- implementation and account reconciliation OK;
- cost-after pooled daily geometric NAV growth at least 1%;
- at least 15 closed trades across the 21 calendar days;
- all three fixed weeks to contain at least one trade;
- the largest winning trade to contribute at most 35% of total positive PnL across the whole
  screen, not separately inside each week.

The prior `all weeks positive` requirement was removed because it contradicted the project-wide
whole-period geometric-growth definition. The aggregate opportunity burden was not reduced:
`5 trades × 3 weeks` became `15 trades across 21 days`.

## Predeclared long BTC evaluation

A pooled-screen pass automatically evaluates one frozen continuous period:

```text
BTCUSDT 1m
start:         2022-01-01 00:00 UTC
end-exclusive: 2025-01-01 00:00 UTC
```

Success requires all of the following after costs:

- daily geometric NAV growth at least 1%;
- at least 0.5 closed trades per calendar day;
- trades in at least 30 distinct calendar months;
- largest winning trade at most 35% of total positive PnL;
- maximum drawdown at most 30%;
- no implementation or accounting error.

Only after that result is credible should untouched BTC periods and then ETHUSDT, SOLUSDT and
XRPUSDT be evaluated without symbol-specific optimization. The multi-instrument implementation
must preserve the global maximum of one pending new-entry order or one position.

## Risk and cost contract

For every candidate order:

```text
loss budget = current full-account NAV × 3%

expected loss per unit
  = |expected entry fill - expected stop fill|
  + entry fee / slippage / impact reserve
  + stop fee / slippage / impact reserve
  + any explicitly modeled funding reserve

quantity
  = floor(loss budget / expected loss per unit, exchange quantity increment)
```

The active research configuration uses a composite taker cost of 7.5 bps per fill. Full costs
are included in both net reward-to-risk and quantity sizing. No separate nominal cap, leverage
cap or model-score risk multiplier is added.

## Completed controlled results

| generation | structural question | pooled cost-after daily geo | trades | decision |
|---|---|---:|---:|---|
| v6 | regime-aligned accepted-failure retest to midpoint | 0.0000% | 0 | discard: target/stop geometry produced no opportunity |
| v7 | prior-session sweep, MSS/FVG and mitigation | -0.2897% | 2 | discard: sweep/displacement did not imply durable reversal |
| v8 | outside session acceptance then failure | -0.6234% | 8 | discard: first internal return was not a stable reversal |
| v9 | failure-of-failure continuation | -0.4342% | 3 | discard: fixed session boundary failed in both directions |
| v10 | accepted-breakout failure reversal only | +0.6806% | 7 | positive core, but inactive week and concentration |
| v11 | failed-boundary market-retest salvage | +0.6806% | 7 | discard salvage: no additional executable edge |
| v12 | native GTC boundary-limit salvage | +0.5347% | 8 | implementation valid; economic logic weaker |
| v13 baseline | mixed accepted-extreme / boundary stop | +0.8720% | 16 | improved, below pooled target |
| v13 `boundary-stop-all` | failed-boundary invalidation for every reversal | **+1.3617%** | **16** | strongest completed control; promoted to v14 |

Exact v13 `boundary-stop-all` fixed-week results:

| segment | total return | daily geo | trades | win rate | max DD |
|---|---:|---:|---:|---:|---:|
| week-a | +19.5617% | +2.5852% | 9 | 55.56% | 3.0009% |
| week-b | -5.9111% | -0.8667% | 2 | 0.00% | 5.9111% |
| week-c | +18.0920% | +2.4041% | 5 | 60.00% | 2.9999% |

The negative 2023 week is retained. It is a known failure sample, not a week to be replaced.

## Success and failure factors carried forward

### Preserved

- flow-confirmed accepted-breakout failure reversal;
- 15m, 60m and daily completed source auctions;
- source-auction equilibrium target;
- failed-boundary reacceptance as the causal invalidation;
- current-NAV full-cost 3% sizing;
- explicit event-state diagnostics and native account reconciliation.

### Discarded

- generic continuation after defended retest;
- fixed prior-session sweep families;
- market chasing after a later boundary retest;
- passive boundary-limit salvage;
- restoring the 240-minute source level merely to add trades;
- using `all weeks positive` as a substitute for whole-period geometric growth.

### Newly identified state-definition issue

Outside acceptance requires consecutive closes, while the completed v14 failure detector accepts
one strong inside close. Four of the seven losses in the completed v13 trade set reached the
boundary stop within one or two minutes, whereas no winner closed that quickly. This outcome does
not define a fitted timing threshold; it exposed a semantic asymmetry. A dormant local v17
contract therefore tests consecutive inside acceptance as a single structural change, but it is
not promoted or economically evaluated before v14 finishes.

## Dormant proposals

Dormant proposals do not alter the active v14 files or claim performance.

- **v15 — failed-auction impact classification.** Separates passive absorption from an active
  liquidity flip using event-relative price impact per unit flow. Post-analysis showed that
  absorption alone does not explain the two week-b losses, so v15 is not automatically preferred.
- **v16 — unaccepted sweep absorption.** Adds an independent scenario only if v14 fails primarily
  on opportunity rate or active-month coverage. Its completed-event diagnostic found nine causal
  candidates distributed 7/1/1 across the fixed weeks; that concentration is a warning, not
  robustness evidence. Source is under `proposals/v16/`.

## Reproduction

Use the repository's prebuilt environment. Do not install or replace NautilusTrader.

```bash
smc4 doctor
bash research/candidate-09/run_v14_fixed_ci.sh
```

The GitHub workflow uses the pinned research image, restores the external Binance Vision cache,
runs doctor/compile/contracts, runs the fixed pooled screen, conditionally runs the three-year
BTC evaluation, uploads exact evidence and commits the compact result back to this branch.

## Known limitations

1. Binance Vision one-minute klines provide aggregate taker-buy volume, not historical L2
   replenishment, cancellation, queue position or hidden liquidity.
2. Native Nautilus bar matching must choose an OHLC path when both protective prices occur in one
   minute; later trade-tick replay can alter ambiguous fills.
3. The explicit composite cost is linear. Nonlinear market impact and capacity must be revisited
   only after genuine cost-after alpha is demonstrated.
4. The static test instrument does not reproduce every Binance margin tier or liquidation rule.
   Rejected orders and account mismatches are reported, never silently resized.
5. A strong fixed-week screen is not evidence of multi-year or cross-market persistence. The
   frozen long evaluation and untouched instruments remain mandatory.
