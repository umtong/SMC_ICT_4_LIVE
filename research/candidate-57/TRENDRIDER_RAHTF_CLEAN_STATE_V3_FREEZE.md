# TrendRider + external RAHTF clean-state v3 freeze

## Conditional purpose

This experiment is eligible only when the exact-public-MTF TrendRider v2 diagnostic is mechanically valid but does **not** earn `SOURCE_FIDELITY_SUPPORTED_POLICY_FRESH_REQUIRED`.  It is not a reaction to a new attractive strategy.  It addresses the specific v1 failure that the same 1-hour bull/pullback geometry produced repeated ROI/trailing winners in November 2024 but only early-loss exits in June 2025.

The exact-public-MTF TrendRider source remains the baseline.  V3 asks whether an independently published clean-trend state model distinguishes an expanding bull auction from a mature/choppy bull state before the unchanged TrendRider pullback entry is allowed.

## External component reused

Source: `richkuo/go-trader`, MIT licensed, public `regime_adaptive_htf.py`.

The repository's shipped fade strategy is **not** imported; its own fee audit found that complete strategy weak.  Only the source's higher-timeframe composite classification and clean-trend drift confirmation are reused as a state component:

- native frame: completed 1-hour TrendRider candles;
- epoch-aligned higher-timeframe buckets: 6 completed 1-hour candles;
- composite period: 14 closed 6-hour buckets;
- ADX threshold: 20;
- absolute return efficiency threshold: 0.05;
- range efficiency threshold: 0.03;
- Kaufman efficiency threshold: 0.5;
- state change requires two consecutive closed higher-timeframe buckets;
- required effective label: `trending_up_clean`;
- required native slow drift: `(close - close[100h ago]) / (ATR20 * 100) >= 0.10`.

These are the public source defaults and the public trend-entry confirmation.  They are not searched.  The external transition-entry, breakout, pullback, fade, short, z-score and position-management rules are not imported.

## Frozen account policy

Control:

- exact-public-MTF TrendRider v2 source semantics: completed daily EMA200 direct gate and completed pair 4-hour trend/ADX confidence;
- otherwise the unchanged public `trend_pullback` branch, one-slot arbitration, 6% stop, public ROI/trailing, indicator exits and 2h/4h/8h/16h/24h lifecycle.

Candidate:

- identical control policy;
- after the exact public source signal is valid, require the external confirmed clean-uptrend label and slow-drift agreement above;
- no candidate can be created by the RAHTF component; it can only reject a source-valid entry.

Both accounts retain ten unscored startup days, two close-runoff days, realistic fees/slippage/funding, current-NAV 3% planned-loss sizing, and one pending entry or position across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.

## Eligibility decisions

Run only when the exact-MTF diagnostic decision is either:

- `SOURCE_STATE_INFORMATIVE_BUT_STANDALONE_STILL_WEAK`, or
- `EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING`.

Skip on missing/invalid exact-MTF evidence or when exact source fidelity already earns its own policy-fresh test.

## Consumed causal diagnostics

The same already-consumed TrendRider intervals are used because this is a state explanation, not performance evidence:

1. `2024-11-01` through `2024-11-14` — known ROI/trailing expansion engine.
2. `2025-06-01` through `2025-06-28` — known early-loss lifecycle.

## Predeclared transaction predictions

The clean-state model is supported only if:

- at least 75% of the exact-control November positive ROI/trailing episodes remain positive in the candidate;
- the best November control winner remains positive;
- the candidate removes more June early-loss episodes than November positive ROI/trailing episodes;
- the June exact-control best positive trade remains positive unless its entry-time RAHTF label was explicitly not `trending_up_clean` or its slow drift was below 0.10;
- June after-cost expectancy and profit factor improve relative to exact control;
- the improvement is attributable to source-valid entries rejected by the frozen state gate, not to one unrelated account-slot outlier;
- both confirmed label rejection and slow-drift rejection are counted separately;
- if the gate merely lowers trade count, removes winners and losses similarly, or leaves the early-loss cohort intact, the hypothesis is rejected without changing factor, period, thresholds, confirmation buckets or drift lookback.

A positive candidate that preserves the mechanism but is too sparse may be retained only as a state component; it does not become a standalone strategy by passing an aggregate gate.

## Policy-fresh interval frozen before diagnostic result

If and only if the transaction predictions are supported, run the unchanged candidate once on:

- entries `2025-10-01` through `2025-10-28` UTC;
- ten preceding startup days;
- two subsequent close-runoff days.

This interval is available only when exact-MTF v2 did not qualify for its own October test.  A mechanically valid informative negative October result closes the state gate without another interval or any tuning.

## Non-authorization

Consumed-diagnostic improvement does not authorize integration or medium/long evaluation.  One policy-fresh success grants component status only, followed by one-account opportunity-overlap analysis against surviving short/event families.
