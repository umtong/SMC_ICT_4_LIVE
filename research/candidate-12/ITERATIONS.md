# Candidate 12 research iterations

Only NautilusTrader account-NAV output is performance evidence. Pure state replay is diagnostic and never a success claim.

## I3 — symmetric session auction: rejected at W1

Workflow `31182807014`, job `92879958481`, commit `ec2cb5c0a139adb2520df325f737dc46749f41ac`: 9 trades, 0 wins, final NAV 77,719.27061424 USDT, daily geometric growth −3.53689321%. W2/W3 were not run.

## I4 — London-high raid reversal

W1 passed at commit `be3a8fcc1517e8f8d52ed45acbedd848deef3689`, workflow `31185042882`, job `92887348615`: 5 trades, 5 wins, final NAV 120,444.47572152 USDT, daily geometric growth +2.69303368%, no liquidation or risk-budget breach.

Sequential W2 confirmation failed at commit `4a6e08a9eb15b07193fdf720d3769c19301f957e`, workflow `31185251437`, job `92888057236`: 2 closed trades, 0 wins, final NAV 94,587.05266912 USDT, daily geometric growth −0.79184233%. W3 was not run.

Both W2 losses came from weak reclaims: reclaim bodies were 0.72 and 0.28 ATR, after which price displaced above the raid extremes. The old logic forced a short instead of classifying boundary acceptance. A third W2 high-rejection plan had strong upper-range context, missed its resting boundary entry, and then reached its structural target.

## I5 — completed-range rejection/acceptance bifurcation

I5 replaces the forced-reversal rule with mutually exclusive completed-auction outcomes. Rejection uses market entry only after causal confirmation; weak rejection remains flat until price either accepts beyond the raid extreme or expires. Low-side rejection and deep-discount low acceptance use the same completed London range and structural objectives.

W2 is diagnostic because it informed I5. W3 is not used. The validation order was reset to W1 first, followed only by previously untouched W4 after a W1 pass.

### I5 W1 design gate — passed

Authoritative evidence: commit `5b8c2689dfc755c59c9d583ba238b57d8d536b27`, workflow `31188233176`, job `92898142209`.

| Measure | Result |
|---|---:|
| Starting NAV | 100,000 USDT |
| Ending NAV | 118,126.23511324 USDT |
| Net return | +18.12623511% |
| Daily geometric growth | +2.40830892% |
| Closed trades | 5 |
| Winners / losers | 5 / 0 |
| Win rate | 100% |
| Closed-trade drawdown | 0% |
| Liquidation | No |
| Risk-budget breach | No |
| Global-slot violation | No |
| Event chronology error | No |

All five executed plans were `LONDON_HIGH_REJECTION`. The W1 gate passed frequency, win-rate, post-cost growth, risk, liquidation, global-slot, and event-order requirements. This justifies exactly one next action: untouched W4 confirmation. W2 is not rerun and W3 remains unused.
