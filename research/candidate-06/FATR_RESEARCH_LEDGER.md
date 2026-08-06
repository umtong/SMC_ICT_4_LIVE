# Candidate 06 v1.9 — Failed-Acceptance Trap Resiliency (FATR)

## Status before execution

`PREDECLARED / NOT YET EVALUATED`

This ledger freezes the causal hypothesis, implementation invariants, comparison, and discard rules before the first NautilusTrader campaign result is known.

## Why this experiment exists

The latest normalized DLVR evidence supersedes the earlier stale positive price-only report. In the valid normalized run:

| DLVR variant | cost-after geometric NAV/day | trades | wins | profit factor | max NAV drawdown |
|---|---:|---:|---:|---:|---:|
| full depth vacuum + replenishment | -4.5913% | 11 | 2 | 0.2049 | 28.68% |
| vacuum continuation | -4.4277% | 11 | 2 | 0.2428 | 27.81% |
| price-only ablation | -8.3317% | 22 | 1 | 0.0412 | 45.61% |

Depth reduced damage but did not create positive expectancy. The causal error was temporal: passive depth observed before a sweep was connected to a materially later reclaim/response as though both belonged to one stable liquidity event.

The most useful surviving entry family is the existing completed-auction failed-acceptance trap:

`completed 30m auction -> SAC acceptance/retest -> next completed 1m bar fails defense -> opposite directional body + opposite taker flow -> reverse trap bracket`

Its frozen first-week reference produced approximately `+0.0663% geometric NAV/day`, `7 trades`, `2 wins`, and `PF 1.029`. This is far below the project gate and is not a selected system, but unlike the dominant losing entry families it retained approximately break-even cost-after expectancy. FATR tests whether passive-liquidity behavior measured during the exact completed failed-defense bar separates genuine trapped positioning from a temporary candle reversal.

## Structural hypothesis

A failed acceptance is tradable only when the completed failure bar is supported by both:

1. **source-side passive resiliency** — passive liquidity on the side defending the new trap direction either returns toward its pre-event level after depletion or finishes at/above that level; and
2. **relative target-path opening** — the source-side depth improves more than target-side depth over the same causal interval.

For a LONG trap, source is bid depth and target is ask depth. For a SHORT trap, source is ask depth and target is bid depth.

This is not a directional signal by itself. It is a pattern detector applied only after the existing price-and-flow failed-acceptance scenario has completed.

## Detector / scenario separation

### Detector

`evaluate_failed_acceptance_depth(...)` consumes only normalized official Binance Vision `bookDepth` observations:

- anchor: timestamp of the original SAC signal;
- pre-event window: `[anchor - 120 seconds, anchor]`;
- event window: `(anchor, completed failed-defense bar timestamp]`;
- minimum records: 2 pre-event and 2 event;
- maximum latest-observation age: 90 seconds;
- pre-event level: median, to reduce single-snapshot noise;
- decision level: latest causal observation, never an average containing earlier event states;
- no observation after the completed decision timestamp is admissible.

Let source and target refer to the trap direction as defined above:

```text
source_recovery = (source_final - source_trough) / (source_pre - source_trough)
path_asymmetry = log(source_final / source_pre) - log(target_final / target_pre)
```

The full gate passes only when:

```text
(source_final >= source_pre OR source_recovery >= 0.50)
AND path_asymmetry > 0
```

Missing, stale, non-positive, or insufficient observations cause abstention rather than imputation.

### Trading scenario

The existing `FailedAuctionTrapRelayEngine`, `failed_acceptance_trap` state transition, structural stop, structural target, and Nautilus execution path remain unchanged. The detector can only permit or reject an already completed FAT entry.

## Controlled matrix

Only two predeclared variants are allowed:

| Variant | Price/flow FAT scenario | Synchronous passive-depth gate | Selection eligible |
|---|---|---|---|
| `fatr_synchronous_depth` | unchanged | enabled | yes |
| `fatr_price_flow_reference` | unchanged | removed | no; diagnostic ablation only |

The following must be identical between variants:

- BTCUSDT data and frozen week;
- completed 30-minute auction construction;
- SAC acceptance/retest;
- next-completed-bar failed-defense trigger;
- opposite directional body and taker-flow requirement;
- structural stop and objective;
- NautilusTrader native orders, fills, positions, fees, and NAV accounting;
- one-tick slippage model and probabilistic limit-touch fill model;
- whole-account NAV sizing with fixed 3% planned-loss risk;
- one global position/order constraint;
- cooldowns and favorable-drift guard.

No threshold search is permitted. The ablation removes exactly one conceptual variable: synchronous passive-depth resiliency confirmation.

## Frozen evaluation order

The pre-existing deterministic weekly sample remains unchanged:

1. `2024-02-26` through `2024-03-04` UTC — first-week logic gate;
2. `2024-09-23` through `2024-09-30` UTC — sealed holdout;
3. `2024-04-22` through `2024-04-29` UTC — sealed holdout.

The full variant alone may advance. Weeks 2 and 3 run only if week 1 passes every gate without parameter or code changes to the trading contract.

## Fixed pass gate

Per week, the selectable full variant must satisfy all of:

- cost-after geometric NAV growth/day `>= 1.00%`;
- trades `>= 10`;
- win rate `>= 45%`;
- positive trades `>= 5`;
- maximum NAV drawdown `<= 25%`;
- largest positive trade share `<= 40%`;
- positive cost-after expectancy and valid Nautilus evidence.

Long evaluation is unauthorized unless the unchanged full variant passes all three weeks.

## Implementation-error rules

The following are implementation failures, not market-logic evidence:

- registration script fails to patch the exact execution anchor;
- import, syntax, or unit-test failure;
- missing/invalid normalized depth file;
- non-causal access to observations after the decision timestamp;
- missing Nautilus metrics/evidence, order ownership error, or result-integrity failure;
- mismatch in reference results caused by accidental changes to the parent FAT scenario.

An implementation failure is corrected by changing only the defective implementation and rerunning the same first week with identical logic and thresholds.

## Logic-error and discard rules

The full candidate is discarded after the predeclared one-variable ablation when any of the following occurs with valid implementation:

- no executable trades after causal confirmation;
- negative geometric NAV growth or profit factor below 1;
- insufficient independent opportunities for the fixed gate;
- direction/timing reliability below the win-rate gate;
- loss clustering above the drawdown gate;
- full does not materially improve cost-after expectancy relative to the price/flow reference;
- any unchanged sealed holdout fails after a first-week pass.

No rescue threshold, session exclusion, direction switch, stop/target retuning, or extra score is allowed after observing outcomes.

## What remains useful even if FATR fails

The following may be retained separately from candidate selection if evidence supports them:

- normalized passive-depth timestamps as causal event measurements;
- a missing/stale-data abstention contract;
- explicit source-versus-target path diagnostics;
- the distinction between failed acceptance and ordinary candle reversal;
- the near-break-even parent FAT price/flow sequence as a diagnostic scenario rather than a finished strategy.

## Research basis

The experiment follows the market-microstructure result that short-horizon price changes are more directly related to order-flow imbalance than to raw trade volume, while treating that relationship as event context rather than a standalone direction oracle. It also follows the project requirement to separate a pattern detector from a complete trading scenario and to diagnose expected liquidity path, timing, invalidation, and cost-after NAV rather than optimize a backtest number.
