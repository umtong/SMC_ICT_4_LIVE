# Candidate 14 v10 failure analysis

## Decision

`candidate-14-v10-failure-leg-leadership` is rejected as a complete candidate. The corrected causal measurement boundary is retained as a research principle, but the confirmed accepted-auction-failure branch did not show repeatable positive expectancy.

The strategy was evaluated from `2026-05-11` through `2026-08-03` in one continuous NautilusTrader account with no weekly reset.

- starting NAV: `100,000 USDT`
- final NAV: `109,480.18511670 USDT`
- daily geometric growth: `+0.1078836%`
- closed trades: `14`
- wins / losses: `5 / 9`
- win rate: `35.7143%`
- Wilson 95% lower win-rate bound: `16.3447%`
- payoff ratio: `2.3873`
- continuous realized drawdown: `14.3501%`
- active calendar weeks: `11 / 12`
- maximum positive-week log-growth share: `37.4426%`

All source provenance, metric recalculation, exact current-NAV 3% planned-loss budget, global one-slot, partial-fill protection, liquidation and engine audits passed. The result is admissible evidence against the frozen v10 strategy logic.

## What v10 changed

V9 had produced 29 fully ordered accepted-auction-failure reversals but rejected every one because the generic FAR market gate measured from the original source sweep. That window contained the completed opposite-direction acceptance leg.

V10 changed only the cross-market measurement origin for plans carrying `acceptance_failure_ts_ns`:

```text
market-leadership start = accepted-auction failure observation
market-leadership end   = later reversal initiative / plan observation
```

Every ordinary exclusive-rejection FAR and Session I7 plan retained the original anchor. No detector, semantic threshold, entry, stop, target, cost, risk or execution rule changed.

## New failure-leg branch

Nine confirmed accepted-auction-failure plans passed the unchanged market-semantic gate and executed.

| UTC | Symbol | Direction | Submitted net R | Realized PnL |
|---|---|---:|---:|---:|
| 2026-05-14 12:25 | ETH | long | 2.5723 | -3,009.24 USDT |
| 2026-05-21 01:19 | ETH | short | 1.8344 | +5,649.15 USDT |
| 2026-05-26 02:51 | BTC | long | 2.3979 | -3,253.37 USDT |
| 2026-06-01 08:34 | SOL | long | 4.6641 | -3,165.68 USDT |
| 2026-06-25 11:12 | XRP | long | 4.4248 | -3,108.37 USDT |
| 2026-07-20 07:20 | XRP | long | 5.7294 | +17,405.06 USDT |
| 2026-07-22 06:33 | ETH | long | 3.7584 | -3,610.39 USDT |
| 2026-07-30 11:40 | BTC | short | 3.7584 | -3,495.33 USDT |
| 2026-07-31 13:36 | BTC | long | 2.1545 | -3,388.22 USDT |

Branch-only result:

- trades: `9`
- wins / losses: `2 / 7`
- win rate: `22.2222%`
- gross winners: `23,054.21 USDT`
- gross losses: `-23,030.61 USDT`
- net PnL: `+23.60 USDT`
- branch profit factor: approximately `1.0010`
- average-winner / average-loser: approximately `3.5036`

The branch was economically flat only because the single `2026-07-20 XRP long` earned `+17,405.06 USDT`. Removing that one trade leaves approximately `-17,381.46 USDT`. This is incompatible with the project requirement that growth accumulate through many materially independent positive-expectancy trades rather than one outlier.

## Why another numeric filter is not justified

Strong measured reversals also lost:

- SOL long: event-path efficiency `1.0`, standardized displacement `2.474`, loss;
- ETH long: efficiency `1.0`, displacement `2.257`, loss;
- BTC short: efficiency `1.0`, displacement `3.054`, loss;
- BTC long: efficiency `0.929`, displacement `2.599`, loss.

The large XRP winner had event rank `3`, so requiring a top-two rank would remove the dominant winner. Both a winning short and a losing short could have event rank `1`, efficiency `1.0`, and large standardized displacement. Net structural R also did not discriminate: losses occurred with submitted values from roughly `2.15R` to `4.66R`.

Therefore event rank, efficiency, displacement, terminal impulse, net R, symbol or direction cannot be tightened without fitting the inspected L1 outcomes. The missing variable is not another threshold inside the same price/aggressor-flow representation.

## Retained conclusions

1. **Exclusive auction-origin ownership is valid.** Ordinary FAR must not silently relabel an acceptance-origin event.
2. **Acceptance completion must precede failure.** V8 proved that reversing acceptance possibilities creates a catastrophic pseudo-failure population.
3. **The failure observation and later initiative must be separate completed events.** The same bar cannot both reveal failure and own reversal.
4. **Market semantics must be measured on the economic leg being traded.** Original-sweep anchoring was causally wrong for a later accepted-auction-failure reversal.
5. **These corrections are necessary but not sufficient.** After all four were repaired, the new branch remained 2 wins and 7 losses and depended on one outlier.

## Research decision

The Candidate 14 price/OHLCV/aggressor-flow family is terminated. Do not add rank, efficiency, displacement, session, symbol or direction filters to v10. Do not retain only the three inspected exclusive-FAR winners or remove the two I7 losses; that would be direct L1 subset selection and would leave an unusably sparse system.

The next independent candidate must add an orthogonal state variable which can distinguish durable inventory transfer from synchronized price reaction. The admissible directions are position/open-interest change, spot-versus-futures ownership, actual quote replenishment/depletion, depth resilience, liquidation origin, or another independently observed inventory state. The same inspected L1 interval can be development data only and can never again serve as a holdout or success claim.
