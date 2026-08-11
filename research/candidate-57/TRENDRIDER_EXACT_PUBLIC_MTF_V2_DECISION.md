# TrendRider exact public MTF v2 decision

## Decision

`EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING`

The exact public daily-EMA200 and pair-4h confidence semantics are now implemented and reusable, but they do not repair the standalone `trend_pullback` family.  No daily/4h threshold, confidence, lookback, date, or lifecycle retuning is authorized from this result.

## Source-fidelity change tested

Relative to the public no-data-provider fallback control, the candidate changed only two visible public source semantics:

1. direct entry rejection unless close was above the latest completed daily EMA200;
2. removal of the local-1h fallback confidence bonus and replacement with the actual completed pair-4h bull state and ADX>20 bonus.

All other entry, one-slot arbitration, stop, ROI, trailing, indicator exit, lifecycle, costs and current-NAV 3% risk semantics were identical.  Public Binance USD-M 1d/4h archives were checksum verified and exposed strictly after informative-candle close.

## Account results

| consumed diagnostic | account | trades | W/L | PF | expectancy | signal-window geo/day | return | MDD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| November 2024 expansion | fallback | 21 | 9/12 | 3.0821 | +474.23 USDT | +0.6804% | +9.9589% | 3.3457% |
| November 2024 expansion | exact MTF | 20 | 8/12 | 2.2815 | +298.59 USDT | +0.4204% | +6.0615% | 3.3430% |
| June 2025 failure | fallback | 18 | 8/10 | 0.7065 | -47.76 USDT | -0.03083% | -0.8597% | 1.9909% |
| June 2025 failure | exact MTF | 17 | 7/10 | 0.5831 | -73.15 USDT | -0.04452% | -1.2391% | 2.2963% |

## Causal episode effects

The source filter did change entry state, so the experiment was informative.  It did not change it selectively in the predicted direction.

- November removed a profitable XRP source episode of roughly `+0.695R` and exposed a later near-flat loss.  The known ROI/trailing payoff engine was weakened rather than preserved.
- June removed several small early-loss or small-profit episodes, but it also exposed a later XRP loss of roughly `-0.094R` and did not preserve a positive account expectancy.
- June expectancy and profit factor both deteriorated relative to fallback.
- Daily and 4h source rejections occurred, but their net account effect was not a clean separation of expansion from mature/choppy bull conditions.

The result was mechanically valid and used no threshold search.  The failure therefore belongs to the market-state explanation, not to the MTF sidecar or account engine.

## Market-model update

A completed daily EMA200 gate and pair-4h bull/ADX confidence are **not sufficient** to establish that a local 1-hour EMA16 pullback still has unspent continuation space.  They describe broad directional context but do not distinguish:

- fresh trend expansion from mature trend exhaustion;
- persistent directional auction from high-timeframe trend with low short-horizon efficiency;
- a leader pullback from a lagging/idiosyncratic bounce;
- a pullback with renewed participation from one entering 4h early-loss decay.

The exact public MTF loader and strict-as-of informative contract remain reusable infrastructure.  The exact-public TrendRider pullback family is not promoted to October policy-fresh, integration, medium, or long evaluation.

The next justified hypothesis is not another EMA/ADX threshold.  It is an independently defined clean-trend/efficiency state that predicts, before entry, which source-valid pullbacks belong to persistent expansion.  That hypothesis is frozen separately in `TRENDRIDER_RAHTF_CLEAN_STATE_V3_FREEZE.md` and is tested only because exact public MTF was informative but insufficient.
