# Candidate 12 research iterations

Only NautilusTrader account-NAV output is performance evidence. Pure state replay is diagnostic and never a success claim.

## I0–I2 — CLAR liquidity acceptance/rejection

The original CLAR family failed W1: two trades, zero wins, final NAV about 94,092 USDT. A first-touch lifecycle repair removed a causality defect but did not create economic edge. Running W2/W3 after W1 had already failed was inefficient and is not repeated.

## I3 — symmetric session auction

**Authoritative W1:** workflow `31182807014`, job `92879958481`, commit `ec2cb5c0a139adb2520df325f737dc46749f41ac`.

| Measure | Result |
|---|---:|
| Starting NAV | 100,000 USDT |
| Ending NAV | 77,719.27061424 USDT |
| Net return | -22.28072939% |
| Daily geometric growth | -3.53689321% |
| Closed trades | 9 |
| Winners / losers | 0 / 9 |
| Liquidation | No |
| 3% risk-budget breach | No |

The family is rejected immediately; W2/W3 were not run. It produced enough opportunities, so frequency was not the bottleneck. The economic error was that accepted-auction entries arrived after the impulse and placed stops around shallow pullbacks instead of beyond scenario invalidation. Normal boundary retests therefore stopped every trade. Treating high-side and low-side interactions symmetrically was also unsupported.

## I4 — New-York raid of completed London buy-side liquidity

I4 replaces the failed family rather than adding filters. The only executable sequence is:

```text
completed 06:00–12:00 UTC London range
→ weekday New-York trade above London high
→ completed five-minute close back inside within three bars
→ one additional completed confirmation bar
→ 15-minute protected sell limit at London high
→ stop beyond the observed raid extreme plus ATR buffer
→ structural objective 60% through the completed London range
```

W1 causal state replay found this sequence once on each weekday, June 26–30, 2023. All five protected boundary entries were subsequently reached and all five structural objectives preceded their invalidations. The observation remained unchanged across stop buffers from 0.8 to 1.0 ATR and targets from London equilibrium to the 61.8% traversal. These are diagnostic robustness checks only. The next and only decision is the W1 NautilusTrader account-NAV run; no confirmation week is allowed unless W1 passes frequency, win rate, payoff, post-cost geometric growth, risk, and liquidation gates.
