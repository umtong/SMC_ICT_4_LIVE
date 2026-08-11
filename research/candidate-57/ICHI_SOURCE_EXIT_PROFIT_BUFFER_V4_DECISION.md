# Ichi source-exit profit-buffer v4 decision

## Decision

`POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING`

The exact lifecycle repair is closed.  No profit threshold, confirmation count, break-even offset or hold-time search is authorized.

## What was tested

The verified public IchiV2 `report_short_level` account remained the source control.  The candidate changed only the first source-exit crossover:

- non-positive after realistic round-trip cost: source exit remained immediate;
- positive after cost: defer once, protect after-cost break-even, and require the source-exit state to persist on the next distinct completed five-minute candle.

The economic boundary was zero after expected round-trip cost; it was not tuned.

## Policy-fresh account result

Interval: `2025-02-01` through `2025-02-28`.

| account | trades | W/L | PF | expectancy | geo/day | return | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| source control | 116 | 51/65 | 0.969144 | -17.45 USDT | -0.07300% | -2.0241% | 16.4823% |
| profit buffer | 116 | 49/67 | 0.956506 | -24.70 USDT | -0.10377% | -2.8653% | 17.2001% |

Candidate minus control:

- total return: `-0.8413 percentage points`;
- geometric daily growth: `-0.03077 percentage points`;
- expectancy: `-7.25 USDT/trade`;
- maximum drawdown: `+0.7178 percentage points`.

Mechanics were valid, no threshold search occurred, and all 116 account episodes were paired.

## Episode-level mechanism result

The candidate armed five profitable first crossovers:

- one state recovered on the next completed five-minute candle and was disarmed;
- three fell back to protected break-even exits;
- one persisted and exited after confirmation;
- zero reached the unchanged public ROI while buffered.

The materially changed paired episodes included:

- SOL: `+0.04583R -> +0.00298R`;
- ETH: `+0.05445R -> +0.09893R`;
- ETH: `+0.03509R -> -0.00983R`;
- SOL: `+0.11437R -> -0.12995R`.

Thus the candidate did not selectively preserve temporary profitable pullbacks.  It frequently converted valid small source exits into smaller gains or losses.  The aggregate deterioration came from the predicted cohort itself, not an unrelated outlier.

## Market-model update

A profitable first Ichi source crossover is **not** sufficient evidence that the short trend remains active.  In this family the public source exit is often a useful small-profit harvesting action, not merely a premature failure signal.

This also confirms the earlier broad lifecycle audit: ignoring source exits generally was wrong, and conditioning the delay only on current after-cost profit was still too weak.  Any future Ichi management hypothesis would need an independent state observed before or at the crossover—such as continued cross-asset trend leadership, unspent objective space, or renewed participation—not another exit-delay threshold.

The verified source entry, ROI-winner engine and immediate source-exit behavior remain reusable components.  This rejected repair is not carried into N-to-1 integration.
