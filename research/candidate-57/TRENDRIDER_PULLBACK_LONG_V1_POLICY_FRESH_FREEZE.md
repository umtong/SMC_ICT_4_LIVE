# TrendRider pullback-long v1 policy-fresh freeze

This specification is committed before the corrected startup/end-flat development result is read.

## Conditional eligibility

The policy-fresh account may run only if `trendrider-pullback-long-v1-warmup-endflat-v2/comparison.json` is mechanically valid and records `MECHANISM_PROMISING_POLICY_FRESH_REQUIRED`.  An implementation failure is repaired without changing the policy.  Any other alpha decision skips this campaign.

## Frozen policy

No rule changes are permitted from the development implementation:

- only the public MIT TrendRider `trend_pullback` long branch;
- completed 1-hour bars;
- public 210-candle-capable unscored startup;
- same bullish regime, pullback, RSI, ADX, volume, DMI, OBV, BTC context and public confidence logic;
- same 6% stop, ROI ladder, 3% trailing after +5%, indicator exits and 2h/4h/8h/16h/24h lifecycle;
- same one-slot four-symbol Nautilus account and current-NAV 3% planned-loss sizing;
- same realistic costs, funding and two-day close runoff;
- no daily informative/private source layers are silently added;
- no threshold, hold, stop, ROI, confidence, asset or date search.

## Primary policy-fresh interval

- entries: `2025-06-01` through `2025-06-28` UTC;
- ten preceding days are startup only;
- two subsequent days are close runoff only.

This interval was selected as a fixed calendar block before reading the corrected development result.  It is policy-fresh for this branch, not a project-wide final holdout.

## Underinformative fallback, not rescue

If and only if the primary interval produces fewer than seven completed independent trades while remaining mechanically valid, the unchanged policy may run once on `2025-10-01` through `2025-10-28` with the same startup/runoff contract.  A primary interval with seven or more completed trades and negative expectancy is a failed hypothesis and must not be rescued by the fallback.

## Predeclared predictions

- the source state should generate repeated pullback episodes across more than one symbol rather than one isolated outlier;
- after-cost expectancy and profit factor should be positive when the state is genuinely portable;
- profitable lifecycle should be explained by unchanged ROI/trailing or durable positive progression;
- losses should be bounded predominantly by the public early-loss/trend-failure lifecycle rather than repeated unexplained full 6% stops;
- no single winner should explain most of gross profit;
- opportunity density can be below the final system requirement, but it must be high enough to contribute as a long bull-regime family;
- failure of the exact policy closes this branch without confidence/threshold/hold retuning;
- success earns component status only.  It does not authorize long evaluation or integration until time overlap and arbitration against surviving short/event families are measured in one account.

## Required causal evidence

Retain every completed trade, source signal, selected and rejected symbol, exit family, planned loss, realized R, one-slot diagnostics, end-flat state, symbol distribution, active-day distribution and largest-winner concentration.  Aggregate improvement without the predicted lifecycle behavior is not sufficient evidence.
