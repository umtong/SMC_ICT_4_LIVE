# TrendRider exact public MTF v2 freeze

## Why v2 is an implementation/source-fidelity repair

The first public TrendRider pullback adapter deliberately used the source's documented no-data-provider fallback.  After its policy-fresh June failure, direct source-path review established that the normal public data-provider path contains two additional semantics for the same `trend_pullback` branch:

1. the entry is rejected unless the pair's close is above the completed daily EMA200;
2. the confidence score earns the 4h alignment point only when the pair's completed 4h state is bullish and 4h ADX is above 20.

The fallback adapter instead made the daily gate effectively always true and approximated the 4h point with the local 1h trend.  V2 repairs only those source differences.  It does not change the external branch, indicators, confidence threshold, management, costs, risk, symbols, or account constraints.

## Exact public information contract

For each decision:

- public Binance USD-M 4h and 1d klines are timestamped by their close time;
- the router may read only the latest row whose close timestamp is not later than the completed 1h decision candle;
- daily EMA200 uses 200 completed daily closes;
- 4h state uses EMA50, EMA200 and ADX14 exactly as visible in the public source;
- no forward-filled row can become available before its informative candle closes;
- BTC 1h RSI remains the actual completed BTC 1h context already used in v1;
- private FNG/funding/OI layers remain the public neutral defaults.

## Frozen account policy

Unchanged from v1:

- only the public MIT `trend_pullback` long branch;
- completed 1h bars and public 210-candle startup;
- EMA9/16, EMA50/200, RSI16, ADX14, volume EMA20, DMI, OBV/EMA20, BTC RSI14 and source confidence >=5;
- 6% structural/source stop;
- ROI 22.9% / 13.6% / 4.4% / 0 at 0 / 124 / 290 / 764 minutes;
- 3% trailing after +5%;
- same indicator exits and 2h/4h/8h/16h/24h lifecycle;
- current-NAV 3% planned-loss sizing;
- realistic fees, slippage and funding reserve;
- one pending entry or open position across BTC, ETH, SOL and XRP;
- ten unscored startup days and two close-runoff days.

No parameter or date grid is permitted.

## Consumed diagnostic intervals

These intervals are already development data for the branch and are used only to test whether the omitted source semantics explain the observed lifecycle difference:

1. `2024-11-01` through `2024-11-14` — v1 repeated large ROI/trailing winners.
2. `2025-06-01` through `2025-06-28` — v1 policy-fresh failure dominated by 4h early-loss exits.

For each interval run the fallback source control and exact-public-MTF candidate in otherwise identical one-slot continuous accounts.

## Predeclared episode predictions

The source-fidelity explanation is supported only if the exact MTF policy changes the predicted lifecycle:

- the November ROI/trailing winner engine must remain broadly intact; a filter that removes winners and losses indiscriminately is not useful source fidelity;
- the June BTC indicator-exit winner of roughly +0.50R should remain unless the public daily/4h source state explicitly shows it was not a valid public signal;
- June early-loss episodes should be rejected more often than November ROI/trailing winners;
- daily-EMA rejection and 4h-confidence rejection must be separately counted before account arbitration;
- account improvement must come from the source-rejected episode cohort, not an unrelated new outlier exposed by freed account time;
- the exact candidate should improve after-cost expectancy and profit factor relative to fallback in June without destroying the November payoff engine;
- if exact MTF only reduces trade count, removes a similar share of winners, or leaves the early-loss lifecycle intact, the explanation is falsified and this branch is closed without retuning.

## Conditional next interval frozen before diagnostic result

If and only if the exact candidate satisfies the transaction-level predictions and remains mechanically valid, one unchanged policy-fresh interval is authorized:

- entries `2025-10-01` through `2025-10-28` UTC;
- ten preceding startup days;
- two subsequent close-runoff days.

This interval was never consumed by the earlier conditional TrendRider workflow because June produced 18 trades.  It is predeclared here before the exact-MTF diagnostic result is known.  A negative informative October result cannot be rescued by another interval or by tuning.

## Non-authorization

Neither consumed-diagnostic improvement nor a single policy-fresh success authorizes integration, medium/long evaluation, or production.  The branch must first establish a portable long-side mechanism and then be evaluated for account-slot interaction against surviving short/event families.
