# Candidate 09 v6 — discarded complete candidate

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31112601100`.
Result commit: `edeabdf555a1ec306676088c1152df1b5c1cf2d3`.

## Hypothesis

V6 preserved the accepted-liquidity-failure detector from v5, added a causal
240-minute completed-auction regime, waited for a failed-level retest/rejection,
and changed the objective from the source-range opposite edge to its midpoint.
The intended path was:

```text
external auction breach -> outside acceptance -> accepted-level failure
-> failed-level retest/rejection -> completed-regime alignment
-> midpoint rotation
```

## Frozen-week result

- implementation status: clean in all three weeks and all ablations
- baseline trades: **0**
- pooled daily geometric growth: **0.000000%**
- pooled NAV multiple: **1.000000x**
- `no-regime`, `no-failure-retest`, and `opposite-edge-target`: all **0 trades**

## State-path diagnosis

The detector was active rather than dead. Baseline diagnostics contained:

- 1,576 neutral liquidity breaches
- 543 outside acceptances
- 348 accepted-level failures
- 75 failed-level retest/rejections
- 75 final scenario rejections

Of the 75 retest/rejection events, 25 aligned with the completed 240-minute
regime. Eighteen also placed the midpoint in the intended trade direction.
However, round-trip composite cost alone made the expected midpoint reward
non-positive in 16 of those 18 cases. Even using a favorable lower bound for the
stop distance, the remaining two had maximum net reward-to-risk estimates of
approximately **0.232** and **0.081**, below the frozen 1.20 minimum.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for v6 as a complete candidate.**

The dominant failure was not a missing market event or an implementation defect.
It was incompatible trade geometry: waiting through acceptance, failure, and
retest left the invalidation outside the full excursion while the midpoint was
already too close to the eventual entry after realistic cost.

The three one-variable ablations did not create executable events, so v6 is not
revised by threshold tuning. It is discarded.

## Valid parts retained

- completed-bar, completed-auction causal observation contract
- explicit breach/acceptance/failure/retest state transitions
- event-level rejection diagnostics
- NAV-based full-cost 3% risk sizing and Nautilus accounting reconciliation

V7 changes the scenario rather than loosening v6. It uses the immediately prior
UTC activity-session range, requires sweep/reclaim plus opposite displacement and
a causal fair-value-gap mitigation, enters near the swept edge after retracement,
keeps invalidation outside the observed sweep, and targets the opposite edge of
the same completed session range. This directly changes risk geometry while
preserving a structural liquidity objective.
