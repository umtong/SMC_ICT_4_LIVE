# TrendRider pullback-long v1 freeze

## Why this experiment exists

Candidate-57 currently has a reproducible short-side Ichi trend family and several reversal/event families, but no independently supported long-side bull-regime continuation specialist.  Adding another generic indicator strategy would be random search.  The missing decision is specific: when a mature bullish auction pulls back without breaking trend structure, can the system re-enter continuation with enough frequency and payoff to complement the short specialist?

## External mechanism reused

Source: `darkvolg/trendrider-strategy`, MIT licensed, public `TrendRiderStrategy.py`.

The public strategy contains six OR-connected long entry families and several private/neutralized external layers.  Importing all six at once would make a positive or negative result uninterpretable.  This experiment reuses only the source's explicitly named `trend_pullback` branch and its visible lifecycle management because that branch directly addresses the missing long continuation state.

The source branch is:

- completed 1-hour candles;
- bullish regime: close above EMA200 and EMA50 above EMA200;
- pullback interaction: low reaches within 2% above EMA16, then the candle closes above EMA16 and above its open;
- RSI16 between 30 and 65 and below 70;
- ADX14 above 18;
- volume above 0.7 times EMA20 volume;
- +DI above -DI;
- OBV above its EMA20;
- BTC 1-hour RSI14 above 35;
- source confidence at least 5/10 in the bullish regime.

The public no-data-provider fallback uses the local 1-hour trend for its 4-hour confidence bonus and neutral values for FNG/funding.  We preserve that public fallback rather than inventing unavailable private data.  Unlike the fallback, BTC RSI is computed causally from the actual completed BTC 1-hour series shared by the four-symbol account.

The source's daily EMA200 informative filter is not imported in v1 because the common one-minute execution shell intentionally retains a bounded event history and the public source explicitly defines a neutral fallback when informative data are unavailable.  This omission is declared before testing and no result is described as an exact reproduction of the source's private/live claim.

## Frozen management

- long only;
- structural/source stop: 6% below entry;
- public ROI ladder: 22.9% from entry, 13.6% after 124 minutes, 4.4% after 290 minutes, zero after 764 minutes;
- public trailing: activate after +5%, trail by 3%;
- public indicator exits: RSI16 > 78; EMA9 bearish cross with negative MACD histogram and RSI > 50; close crossing below 99% of EMA200; source early-warning state near EMA200 with RSI > 72 and falling MACD histogram;
- public lifecycle cuts: after 2h exit below -1.5%; after 4h exit below 0%; after 8h exit below +0.5%; after 16h exit below +1%; force exit after 24h;
- current-NAV 3% planned-loss sizing includes fees/slippage/funding assumptions;
- one pending entry or open position across BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.

No entry, confidence, stop, ROI, trailing, or lifecycle parameter is searched.

## Development predictions

Two predeclared diagnostic regimes are consumed as development data:

1. Intended bullish expansion: `2024-11-01` through `2024-11-14`.
2. Contrast regime: `2025-02-01` through `2025-02-14`.

Before seeing the account results, the mechanism predicts:

- the intended bullish regime should create repeated independent pullback episodes rather than one isolated outlier;
- profitable trades should generally reach ROI/trailing or remain positive long enough to avoid the early-loss cascade;
- failed entries should be concentrated in early-loss or trend-break lifecycle exits, not in unexplained full 6% stops;
- the contrast regime should naturally produce fewer signals or materially lower account exposure; if it trades frequently and loses, the bullish state model is not selective enough;
- if the intended regime has fewer than roughly one independent episode every two calendar days, this branch is too sparse to solve the current opportunity gap by itself even if accurate;
- if aggregate profit improves only because of one unrelated outlier while the early-loss cohort remains unchanged, the mechanism is not supported;
- a failed diagnostic closes this exact branch without threshold, hold-time, or confidence retuning.

## Promotion rule

The two development regimes are not holdouts.  A branch with coherent trade-level behavior and positive after-cost expectancy in its intended regime may earn exactly one separately frozen policy-fresh interval.  It does not earn integration or long evaluation from development performance alone.

## Invariants

- completed candles only;
- no future information;
- NautilusTrader matching/accounting;
- realistic fees, slippage, funding and continuous NAV;
- no DCA or position adjustment;
- one global account slot;
- no arbitrary nominal or leverage cap;
- no parameter grid;
- no automatic long-stage escalation.
