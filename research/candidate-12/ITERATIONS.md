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

The family was rejected immediately; W2/W3 were not run. Frequency was not the bottleneck. The economic error was that entries arrived after the impulse and placed stops around shallow pullbacks instead of beyond scenario invalidation. Normal boundary retests therefore stopped every trade. Treating high-side and low-side interactions symmetrically was unsupported.

## I4 — New-York raid of completed London buy-side liquidity

I4 replaced the failed family rather than adding filters. The only executable sequence is:

```text
completed 06:00–12:00 UTC London range
→ weekday New-York trade above London high
→ completed five-minute close back inside within three bars
→ one additional completed confirmation bar
→ 15-minute protected sell limit at London high
→ stop beyond the observed raid extreme plus ATR buffer
→ structural objective 60% through the completed London range
```

### W1 design gate — passed

**Authoritative evidence:** commit `be3a8fcc1517e8f8d52ed45acbedd848deef3689`, workflow `31185042882`, job `92887348615`.

| Measure | Result |
|---|---:|
| Starting NAV | 100,000 USDT |
| Ending NAV | 120,444.47572152 USDT |
| Net return | +20.44447572% |
| Daily geometric growth | +2.69303368% |
| Closed trades | 5 |
| Winners / losers | 5 / 0 |
| Win rate | 100% |
| Closed-trade drawdown | 0% |
| Liquidation | No |
| 3% risk-budget breach | No |
| Event chronology error | No |

All five entries and all five structural targets were executed by NautilusTrader. Three resting entries filled as maker orders; two marketable protected limits filled as taker orders, while sizing had reserved taker entry cost for every trade. W1 passed both the diagnostic and project target gates. This is sufficient to advance to W2 alone; W3 remains unexecuted until W2 is judged.
