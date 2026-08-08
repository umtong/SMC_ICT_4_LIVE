# Candidate 39 V4 — Seven-Day Failure Analysis

## Verdict

The first trader-derived price-only router was **falsified**, not promoted.

Development replay: 2026-07-08 through 2026-07-14, BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, one continuous NautilusTrader account, one global pending/open slot, current NAV × 3% planned loss, passive LIMIT parents, configured fees/slippage/funding reserve.

| Metric | V4 result |
|---|---:|
| Ending NAV | 82,172.49 USDT |
| Total return | -17.83% |
| Daily geometric growth | -2.765% |
| Max drawdown | 17.94% |
| Completed trades | 9 |
| Wins / losses | 2 / 7 |
| Win rate | 22.22% |
| Profit factor | 0.0822 |
| Expectancy | -1,980.83 USDT/trade |
| Active days | 4 / 7 |

Trade count cleared the seven-day diagnostic minimum, so lack of opportunity was not the blocker. The alpha/state classification and one execution path were wrong.

## Family results

### `FIRST_PULLBACK_CONTINUATION`

Three filled trades: one +73.61 USDT, two losses (-2,784.18 and -2,628.05 USDT).

The implemented price shape accepted moves of roughly 4–7 ATR (and generated an unfilled 10 ATR candidate) as sponsored initiative. Feature inspection showed several were liquidation/climax or had aggressor flow opposing the alleged initiative. A large directional candle sequence is therefore not sufficient context for a continuation pullback.

Required repair: the initiative leg must show positioning build and aligned aggressor flow at the event, then renewed aligned flow at confirmation. Excessive impulse/climax bursts must be rejected.

### `FAILED_LEVEL_REACCEPTANCE`

Six filled trades: one +1,520.71 USDT, five losses (-2,913.01, -2,687.25, -2,511.09, -4,103.79, -1,799.47 USDT).

Most candidates were admitted with cross-asset breadth of 0.00–0.25. A wick through a prior-day/session level and a close back inside did not establish a positioning reset. The only profitable example exhibited a substantial OI flush followed by strongly aligned reclaim flow. Several losing examples had no OI flush, confirmation flow still aligned with the attack, or negligible confirmation efficiency.

Required repair: reversal requires event-time OI contraction, later flow flip and efficient reclaim, deeper reacceptance, and either cross-asset reversal breadth or relative isolation of the attacked symbol.

## Geometry failure

V4 failed-level stops were often only 0.41–0.66 ATR from entry, while reported raw structural R reached 6–10. This was not robust target space; it was an artifact of wick-tight invalidation. Reacceptance should be invalidated by renewed acceptance outside the attacked level, with a hard distance large enough for one-minute execution resolution. The honest midpoint/next value target must still clear costs after that wider stop, otherwise the trade is rejected.

## Execution failure

One SOL failed-level short submitted at 2026-07-14 13:14 UTC and filled at 13:20. At child activation, the protective stop was already inside the market spread (`stop 76.99`, `bid 76.85`, `ask 77.18`). Nautilus rejected the stop child, and the emergency flatten lost -4,103.79 USDT versus planned loss near 2,642 USDT.

This is not an engine issue to hide. A passive parent must be cancelled before fill when either:

1. its structural stop has already been touched, or
2. the accepted/reaccepted value state has been lost.

V5 implements both cancellations and uses wider state invalidation for failed-level/opening-range families.

## Non-scalping horizon failure

The final XRP short filled around 23:32 UTC and was flattened for funding protection at 23:45, producing only a 14-minute holding period. Although the signal itself was not a scalp, mandatory account management left no day-trading operating horizon. New entries must be rejected when less than 60 minutes remain to the next funding boundary/mandatory flatten window.

## V5 structural changes

V5 is not a threshold rescue of V4. It changes the latent-state definitions:

- `SPONSORED_FIRST_PULLBACK`: initiative OI build + event flow + later renewed flow/efficiency; rejects liquidation/climax.
- `LIQUIDATION_FAILURE_REACCEPTANCE`: OI flush + later flow flip + deeper reacceptance + relative/broad confirmation; hard invalidation outside the level.
- `OPENING_RANGE_ACCEPTANCE_RETEST`: independent 8-hour-session family reconstructed from ACD/opening-range practitioners; requires sponsored acceptance and later retest, not merely a breakout candle.
- Pending parent state invalidation and pre-funding operational-horizon rejection.

All V4 data is development data. Any surviving V5 structure must move promptly to untouched periods after a short causal/execution diagnostic.
