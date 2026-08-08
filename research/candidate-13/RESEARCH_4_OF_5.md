# Candidate 13 — Research 4/5

## Verdict

**Trade frequency is no longer the bottleneck. The current bottleneck is pre-entry market-state classification.**

Three independent scenario constructions generated abundant causal opportunities, but neither synchronized continuation nor delayed continuation nor immediate failed-auction reversal produced robust cost-positive alpha across the six exposed development intervals.

All results below are development-only. The intervals were already exposed and each weekly run restarted at 100,000 USDT, so neither the pooled NAV multiple nor the daily geometric rate is continuous-account validation or a success claim.

## Cumulative result

| Version | Market interpretation | Trades | Wins / losses | Win rate | Payoff | Pooled weekly NAV | Daily geometric growth |
|---|---|---:|---:|---:|---:|---:|---:|
| V9 | common-flow burst -> follower continuation | 143 | 53 / 90 | 37.06% | 1.640 | 0.4472x | -1.8977% |
| V10 | two common-flow events -> persistent initiative -> fresh MSS/FVG continuation | 114 | 22 / 92 | 19.30% | 4.009 | 0.5602x | -1.3703% |
| V11 | two-event initiative -> majority origin reacceptance -> failed-auction reversal | 94 | 24 / 70 | 25.53% | 1.697 | 0.1083x | -5.1556% |

The high payoff ratios do not rescue these systems. Losses are too frequent, and several invalid unprotected tails inflate both payoff and NAV dispersion.

## V9 — frequency solved, direct continuation rejected

V9 used the first completed five-minute interval of each UTC quarter-hour as the recurring event. At least three markets had to agree in body direction, inherited displacement magnitude, and taker-flow sign. The ATR-standardized leader was treated as the information owner; only already-confirmed followers received passive midpoint-retest orders.

The strategy produced 203 quarter-hour submissions and 143 closed portfolio trades across 42 exposed days. Exact removal of the six positions whose protective child was rejected still left the quarter-hour route at:

- 126 trades;
- 42 wins / 84 losses;
- 33.33% win rate;
- 1.544 payoff ratio;
- -64,792.63 USDT realized PnL.

The shortest-lived trades were the worst:

- <=5 minutes: 60 trades, 23.33% win rate, -80,824.14 USDT;
- 6-15 minutes: 43 trades, 37.21% win rate, -13,693.36 USDT;
- 16-60 minutes: 17 trades, 58.82% win rate, +33,424.79 USDT.

Conclusion: the periodic common-flow event exists, but the burst itself is not a complete continuation scenario.

## V10 — delayed continuation also rejected

V10 no longer traded the event. One common-flow event created only a candidate state; a second distinct same-direction event activated a four-hour initiative. Tradable entries required a new post-activation five-minute MSS, directional displacement, strict FVG and first consequent-encroachment retrace toward a live external 4H or previous-day pool.

This produced:

- 2,944 observed common-flow events;
- 1,015 initiative activations;
- 1,012 terminations;
- 813 continuation-plan confirmations;
- 99 continuation fills.

The V10 continuation module itself closed 99 trades with 15 wins and 84 losses. Two same-direction quarter-hour events therefore did not identify persistent price discovery. The state activated too often and terminated quickly:

- 611 initiatives ended through majority origin reacceptance, median lifetime 15 minutes;
- 401 ended through an opposite common-flow event, median lifetime 30 minutes.

Conclusion: repeated direction is not an independent discriminator between information-driven initiative and synchronized noise/pullback.

## V11 — immediate failed-auction reversal also rejected

V11 treated the dominant V10 termination path as a separate scenario family rather than flipping V10 outcomes after the fact.

```text
first common-flow impulse
-> second same-direction common-flow impulse
-> no entry
-> majority of second-event markets reaccept their own second origin
-> passive reversal at the second origin
-> stop beyond the second impulse extreme
-> target the first impulse origin
```

The system observed 519 market-wide failure confirmations, created 243 cost-qualified reversal plans and filled 79 V11 plans. The full portfolio result was 94 trades, 24 wins and 70 losses.

Isolating the V11 module from the preserved SCDAM core:

- V11 failed-initiative module: 79 trades, 17 wins / 62 losses, 21.52% win rate, -138,532.45 USDT;
- preserved SCDAM core: 15 trades, 7 wins / 8 losses, +28,533.71 USDT.

After excluding the one exact residual-unprotected XRP outlier described below, the V11 module still had:

- 78 trades;
- 17 wins / 61 losses;
- 21.79% win rate;
- 2.145 payoff ratio;
- -68,329.37 USDT realized PnL.

Both directions remained negative. Failed-long short reversals lost more than failed-short long reversals, but neither side supplied a valid standalone scenario.

Duration after exact outlier removal:

- <=5 minutes: 19 trades, 0 wins, -50,864.49 USDT;
- 5-15 minutes: 19 trades, 4 wins, -23,666.84 USDT;
- 15-30 minutes: 11 trades, 3 wins, -6,033.54 USDT;
- 30-60 minutes: 16 trades, 7 wins, +9,561.95 USDT;
- 60-120 minutes: 6 trades, 2 wins, -967.51 USDT;
- 120-240 minutes: 3 trades, 1 win, +14,713.33 USDT.

Conclusion: majority reacceptance is not sufficient to distinguish a genuine market-wide failed auction from a normal pullback, balance formation, or continued inventory transfer.

## Safety finding — callback-time flatten is not sufficient

V11 added fail-close handling for denied/rejected protective children. It improved visibility but exposed a deeper same-timestamp partial-fill race.

In E03, plan `QHF-PLAN-XRPUSDT-1658902200000000000-000025-SHORT` behaved as follows at one event timestamp:

1. the passive parent sold 819,011 XRP;
2. its stop child was rejected because the trigger was already in the market;
3. the contingent target was then rejected because the stop child had closed;
4. each rejection callback called `close_all_positions`, creating two market buys of 819,011 each;
5. after those callbacks, the parent filled another 2,407,267 XRP at the same timestamp;
6. the existing close orders covered only the earlier observed quantity, leaving a residual short open until the weekly forced flatten;
7. the residual position lasted 6,777 minutes and realized -70,203.09 USDT.

The correct fail-close contract must be fill-aware, not callback-only:

- cancel the unfilled parent remainder immediately;
- enter a persistent `FAIL_CLOSING` state;
- after every later fill, recompute residual position and offset only the new residual;
- prevent duplicate close orders for the same quantity;
- retain the global slot until the account is confirmed flat;
- keep any such run classified as implementation/evidence failure.

This invalid tail does not explain the alpha failure: removing it leaves the V11 module materially negative.

## What 4/5 establishes

1. **Opportunity occurrence is abundant.** V9-V11 generated 94-143 trades over the same 42 exposed days. Adding looser pattern thresholds is neither necessary nor justified.
2. **The periodic event is a detector, not a scenario.** It marks recurring cross-market activity but does not determine continuation versus reversal.
3. **Direction flipping is not state classification.** V9/V10 continuation and V11 reversal both fail, especially in the first 15 minutes.
4. **The missing variable must be independent of the outcome path.** A viable router needs causal information available before entry that distinguishes information-driven repricing, liquidation/inventory unwind, and balance/noise.
5. **Price-only one-minute Binance futures klines do not yet supply that discriminator.** The next research step must either add causally justified observable data such as spot-versus-perpetual leadership, open-interest/liquidation response, basis/funding, or depth/impact response, or discover a different scenario family whose state is identifiable from the existing data.
6. **V9-V11 must not be rescued by threshold tuning.** Their market-state hypotheses are rejected on the exposed development set.

## Contract for 5/5

Before any untouched validation:

1. implement the fill-aware fail-close state machine;
2. select one independent pre-entry state variable from an actually available, causally timestamped data source;
3. use the quarter-hour event only as a context trigger;
4. define mutually exclusive `CONTINUATION`, `FAILED_AUCTION`, and `UNRESOLVED` transitions before entry;
5. require entry, invalidation and target to belong to the new post-transition auction leg;
6. test mechanism occurrence and execution safety on exposed data;
7. freeze source and evaluate one continuous untouched account path only if the exposed mechanism survives.

No current V9, V10 or V11 result supports deployment or a success claim.
