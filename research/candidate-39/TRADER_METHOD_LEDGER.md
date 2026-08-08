# Candidate 39 — Trader Method Mining Ledger

This ledger records **concrete, non-scalping trading methods** inspected before Candidate 39 V4 was built. It is not a credibility report. A name, PnL claim, or attribution was not treated as evidence. Each source was used only as a hypothesis generator; the resulting policy must survive our own causal NautilusTrader replay.

## Selection rule

A source entered the implementation queue only when most of the following could be reconstructed:

`context/regime -> objective level -> interaction -> confirmation -> entry -> invalidation -> target/management -> no-trade`

Rejected by default: seconds/tick scalping, averaging-down or martingale, signal-only calls, isolated profit screenshots, generic psychology, daily journals without a reproducible setup, and methods whose target/stop could not be inferred without inventing them.

## Domestic sources mined

| Source | Concrete policy extracted | Decision |
|---|---|---|
| DCInside chart-analysis trader/topic index — https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=3564918 | Used as an **outbound discovery graph**, not as a strategy. Followed person/topic links into original posts and discarded entries that were only PnL, mindset, or scalping. | Discovery index |
| `Rounders` bull-market method — https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=1416997&page=1&search_head=10 | Trade only an established bullish regime; wait for a strong coin's first controlled pullback toward a moving value area; require the prior low/value to hold and a later bullish response; invalidate below the pullback low; do not chase; watch high-volume upper-tail/distribution behavior for exit. | **Implemented in V4 `FIRST_PULLBACK_CONTINUATION`** |
| `조던벨퍼트` trend/channel method — https://gall.dcinside.com/mgallery/board/view/?id=electronicmoney&no=855992 | Do not draw a two-point trendline and call it structure. Require repeated contact/established channel, a completed higher-timeframe break, then a retest with volume/response; invalidate on structural reclaim; use the next structural expansion rather than a tiny fixed scalp target. | Geometry and retest logic reused; standalone family reserved |
| `도동퍄` 1h/4h candle-close practice — https://gall.dcinside.com/mgallery/board/view/?id=electronicmoney&no=1035391&page=74&search_head=10 | If the state is unclear, wait for the 1h/4h candle to complete. Treat uncertainty as `UNRESOLVED`, not permission to anticipate the close. | **Implemented as completed-15m event/confirmation separation and NO TRADE** |
| DCInside/FMKorea/Coinpan/Cobak searches for `전일고가 전일저가`, `돌파 실패`, `눌림목`, `추세매매`, `시간봉 지지저항` | Many hits contained chart calls or after-the-fact screenshots but no complete invalidation/target policy. Only rules that could be tied back to a complete original post were retained. | Screened; most rejected |

## International named traders / educators mined

| Source | Concrete policy extracted | Decision |
|---|---|---|
| CryptoCred — *How to Trade Reversals Using Candlestick Highs and Lows* — https://www.youtube.com/watch?v=nPDk5qPbf08 (transcript mirror: https://glasp.co/youtube/nPDk5qPbf08) | Use only obvious higher-timeframe or time-anchored extremes (session/day/week/month), not every minor swing. A sweep alone is not an entry; distinguish acceptance from a failed break/trapped-trader reversal. | **Implemented in V4 `FAILED_LEVEL_REACCEPTANCE`** |
| CryptoCred — *How to Trade Breakouts and Retests Effectively* — https://www.youtube.com/watch?v=inqHN8q57X4 (transcript mirror: https://glasp.co/youtube/inqHN8q57X4) | Match the confirmation close to the level's timeframe; do not chase a range poke; a rounded retest can improve geometry but may never occur; consume the episode rather than recycling it later. | **Implemented: completed event, later retest, passive order, one-shot episode** |
| Linda Raschke / Connors — `Holy Grail` first pullback | Strong momentum precedes price continuation; wait for the first clean pullback into dynamic value, then enter only when price resumes; invalidate beneath the pullback structure. Reference summary: https://www.forex.in.rs/linda-raschke-strategy/ | **Combined with Rounders and Shannon in V4 first-pullback family** |
| Linda Raschke / Connors — `Turtle Soup` false breakout | Attack an old 20-bar extreme, then react only when price reclaims it; structural stop beyond the attack extreme; cancel if reclaim never occurs. Rule reference: https://www.tradingview.com/script/28gN99Tw-Turtle-Soup-Indicator/ | **Combined into V4 failed-level family** |
| Brian Shannon — Anchored VWAP and multiple timeframes — https://www.moneyshow.com/expert/40434b4a4d3711d69af60001031c36ab/brian-shannon-cmt/ | Anchor participant cost to a meaningful impulse/event, align trend across timeframes, and use dynamic value for low-risk continuation rather than buying an already extended breakout. | **Implemented as impulse-anchored VWAP + fast/slow trend value** |
| Mark Fisher — ACD opening-range framework — https://www.investopedia.com/articles/technical/04/032404.asp | Opening range, acceptance outside it, failed A move through the opposite side (C), and end-of-day constraint form a complete session-state machine. | Reserved as an independent family if V4 frequency is insufficient |
| Trader Dale — failed breakdown reclaim — https://www.trader-dale.com/failed-breakdown-reclaim-trade-order-flow-delta-4th-feb-26/ | Key psychological level breaks, aggressive initiative exhausts, price reclaims and breaks the micro downtrend, then patiently retests the reclaimed level; no entry if the retest never comes. | Price-only skeleton implemented; delta/OI enhancements deferred until causal data contract is available |

## International community, channel, and non-name sources mined

| Source | Concrete policy extracted | Decision |
|---|---|---|
| `Dax Analysis from DaxTrader` Telegram — https://t.me/s/thedaxtrader | Example policy was fully specified: false breakout/rejection, broken support becomes resistance, sell the retest, stop above reclaim, structural pivot/S1 target, bias remains until the level is reclaimed. | **Direct support for V4 failed-level retest geometry** |
| `Mindfully Trading FX Analysis` Telegram/YouTube — https://t.me/s/emilymindfullytrading | Top-down daily level, failed breakout definition, separate entry confirmation, structural stop, target/RR planning, London-session context. | Used as a completeness check; not copied verbatim |
| `Trade With Abhinav` Telegram — https://t.me/s/tradewith_abhinav/1 | Existing uptrend, pause/tight base near breakout, enter only on expansion; long-wick “squat”/failed breakout means exit with a small loss. | Base-breakout family reserved; long-wick invalidation reused conceptually |
| Japanese `KZ Trader` ETH/USD case study (search trail) | Daily context, 4h higher lows/value support, 1h neckline/counter-line break, reduced size while 4h state unresolved, add only after confirmation, target at the next 4h reference. | Reserved; not yet implemented because source-specific partial adding conflicts with current single-risk bracket design |
| Japanese 4h/1h market-structure commentary | A small lower-timeframe bounce is not a reversal while the 4h structure remains intact; wait for the decisive high/low and completed close. | Reused as higher-timeframe state discipline |
| Chinese `方方土` price-action video curriculum / community summaries | Market cycle, background, signal bar, stop and management are taught as a full decision chain rather than a pattern label. | Mined for scenario decomposition; no rule copied until one episode can be specified exactly |
| Reddit r/algotrading discussions | Regime and timeframe matter; private claims are irrelevant without tests; multiple independent strategies should be run through long demo/evaluation before promotion. | Research-process input only, not alpha |
| Public Telegram signal channels and auto-indicator feeds | Many provide entry/SL/TP but no stable context/state distinction, or explicitly advertise scalp signals. | Rejected as strategy sources |

## V4 policy assembled from the mined methods

### 1. `FIRST_PULLBACK_CONTINUATION`

- **Context:** established multi-hour trend; fast value above/below slow value; slow value has meaningful slope; cross-asset breadth is supportive.
- **Initiative:** a 1.8+ ATR, reasonably efficient multi-hour impulse with participation.
- **Interaction:** first 2–6 completed 15m pullback retraces 18–70% into impulse-anchored VWAP / trend value while the slow structural value remains intact.
- **Confirmation:** a *different completed 15m bar* resumes direction and closes strongly back through dynamic value.
- **Entry:** passive retest limit, never market-chasing the confirmation close.
- **Invalidation:** beyond the pullback extreme plus an ATR buffer.
- **Objective:** same-leg impulse extension; reject if structural target does not clear the family reward floor after costs.
- **No trade:** deep pullback, weak/late impulse, value failure, poor breadth, poor geometry, or stale causal episode.

### 2. `FAILED_LEVEL_REACCEPTANCE`

- **Context/objective:** prior completed UTC day or prior completed 8h session high/low.
- **Interaction:** price attacks the level by a bounded ATR amount and closes back inside.
- **Confirmation:** a later completed 15m bar retests and rejects the level; the event bar cannot confirm itself.
- **Entry:** passive retest limit near the reclaimed level.
- **Invalidation:** beyond the attack extreme plus an ATR buffer.
- **Objective:** honest opposite value/midpoint reference from the same auction; no synthetic farther target is invented to rescue bad R.
- **No trade:** acceptance outside, excessive sweep, no later retest, mixed global direction, insufficient target space, or repeated episode.

## Explicit rejects

- Seconds/tick scalping, 0.x% repeated targets, order-book clicking.
- Martingale, rescue averaging, multi-stage loss adding.
- `코인충`-style daily journal entries without a complete reproducible method.
- PnL screenshots or “this trader is real” identity verification.
- Any source whose rule needs future candle information or whose stop/target must be invented after seeing the outcome.
