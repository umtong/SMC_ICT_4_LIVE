# Candidate 39 V5 — Failure Analysis and V6 Redesign

## Result

Seven-day development replay, 2026-07-08 through 2026-07-14, one continuous four-asset NautilusTrader account:

| Metric | V5 |
|---|---:|
| Ending NAV | 97,637.68 USDT |
| Total return | -2.3623% |
| Daily geometric growth | -0.3409% |
| Max drawdown | 2.3623% |
| Completed trades | 1 |
| Wins / losses | 0 / 1 |
| Profit factor | 0 |
| Expectancy | -2,362.32 USDT/trade |

V5 removed the V4 execution defects: zero order rejections, zero emergency flattens, zero global-position violations. It also suppressed most false failed-level reversals. The remaining alpha and opportunity set were still unacceptable.

## The one filled trade exposed the shallow-value bug

The filled BTC short was classified as `SPONSORED_FIRST_PULLBACK` and stopped three minutes later. Its impulse was 5.49 ATR and the reported retrace was only 18.44%. The implementation selected the shallower of the impulse AVWAP and fast EMA for a short (`min(AVWAP, EMA)`), placing the passive entry around 62,017 while the deeper impulse AVWAP was around 62,255.

That is not a faithful first pullback into value. For a short, the deeper value is the higher reference; for a long, it is the lower reference. V6 therefore uses:

- long deep value = `min(impulse AVWAP, 20-bar trend value)`;
- short deep value = `max(impulse AVWAP, 20-bar trend value)`.

The pullback must actually touch that deeper value, the touch bar cannot confirm itself, and a later completed 15-minute bar must resume direction. This is a geometry/state correction, not a looser threshold.

## V5 opening-range family was not Fisher ACD

V5 generated nine opening-range candidates, but every candidate failed post-cost target space. The code used a generic boundary retest with a relatively local stop and nearby measured/prior-session target. That is not the A/B/C state machine described in Mark Fisher's method.

V6 replaces it with:

1. an objective A-distance beyond the completed opening range;
2. three consecutive completed one-minute closes to establish A;
3. a passive later retest of the A level;
4. B invalidation at the far side of the opening range;
5. C only after an established A subsequently fails through the opposite A/C level with persistent closes;
6. targets at the nearest prior-day/prior-session or typical-session-range objective, never an arbitrary farther price added to rescue reward/risk.

## Preserved family

`LIQUIDATION_FAILURE_REACCEPTANCE` produced no V5 survivor. It is retained without weakening because its causal definition—OI flush, later flow flip, deeper reacceptance, and relative/broad confirmation—directly addresses the V4 false-reversal failure. V6 audit counters will show whether price geometry or positioning transition is the bottleneck before any future change.

## V6 evidence status

V6 remains development work because its structure was designed after observing V4/V5 results on 2026-07-08 through 2026-07-14. It cannot be called holdout evidence. Only a structurally promising, executable V6 may move to a genuinely untouched period.
