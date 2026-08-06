# NT-LVCFR-v17 dual-inventory auction — development evidence

## Candidate status

V17 is not yet a completed candidate.  It passed only the first frozen BTC
development week.  The source and configuration are frozen by Git blob identity
before the second week.  No result from this document authorizes long evaluation
or live deployment.

## Causal architecture

V17 routes two economically distinct inventory processes into one native
NautilusTrader portfolio slot:

1. **OI contraction / deleveraging auction**
   - `FIRST_BREAK_CHOCH_REVERSAL`: a completed close traverses the event range
     opposite the original displacement.
   - `MEASURED_ACCEPTANCE_CONTINUATION`: a same-side break reaches one complete
     event-range measured extension before a full opposite-range failure.
   - midpoint-only failure is `NO_TRADE`.
2. **OI expansion / new-position auction**
   - two consecutive five-minute price and OI expansions;
   - futures and spot aggressive flow agree;
   - price breaks a pre-existing 30-minute external boundary;
   - the next completed minute holds outside and both markets retain directional
     aggressive flow.

The two families use the same current-native-NAV 3% planned-loss sizing, native
orders, fees, impact, funding, margin, positions, Portfolio accounting, and NAV.
Only one pending new entry or open position is allowed.

## First frozen BTC week — 2024-01-08

- GitHub Actions run: `31116621891`
- Artifact: `8973815477`
- Artifact digest: `sha256:a2a4b14f578a81968861823ccd49861a3b181f31994eadba40a68bf441b3bca0`
- Engine: NautilusTrader 1.230.0 `BacktestNode`
- Causal signals: 24
- Native independent episodes: 19
- Wins / losses: 10 / 9
- Win rate: 52.6316%
- Initial NAV: 100,000 USDT
- Final NAV: 118,460.87176315 USDT
- Net return: +18.46087%
- Daily geometric NAV growth: +2.449703%
- Mean episode PnL: +971.6248 USDT
- Mark-to-market maximum drawdown: 10.5193%
- Native orders: 44
- Native positions including snapshots: 22
- Rejected entries: 0
- End state: flat
- Gate: passed

## State contribution

| Scenario state | Executed episodes | Wins | Native PnL (USDT) |
|---|---:|---:|---:|
| `FIRST_BREAK_CHOCH_REVERSAL` | 5 | 3 | +3,711.372 |
| `MEASURED_ACCEPTANCE_CONTINUATION` | 5 | 2 | +5,093.135 |
| `SPOT_LED_OI_EXPANSION_ACCEPTANCE` | 9 | 5 | +9,656.365 |

All three states were positive.  The week is therefore not explained by a
single state branch.  This supports the inventory-regime separation hypothesis,
but one week is insufficient to establish invariance.

## Concentration and path dependence

The largest winning episode earned approximately 11,565.18 USDT, or 62.65% of
the week's net profit.  The next-largest winner earned approximately 10,032.95
USDT.  Removing only the largest winner from the arithmetic episode sum would
leave approximately +6.90% net profit; removing the two largest winners would
leave the week negative.  This is a diagnostic, not a counterfactual native NAV
backtest, because later position sizes depend on prior NAV.

Daily native episode PnL was also uneven:

| UTC day | Episodes | Episode PnL (USDT) |
|---|---:|---:|
| 2024-01-08 | 4 | +2,186.44 |
| 2024-01-09 | 1 | -3,097.08 |
| 2024-01-10 | 1 | +2,741.91 |
| 2024-01-11 | 4 | +10,242.33 |
| 2024-01-12 | 7 | +17,003.74 |
| 2024-01-13 | 0 | 0.00 |
| 2024-01-14 | 2 | -10,616.47 |

Therefore the second and third frozen weeks must establish that positive
expectancy is repeated across independent episodes and is not merely a January
2024 event cluster.  No risk, target, filter, or scenario change is allowed
before those weeks are evaluated.

## Frozen source identity

The following Git blobs define the candidate used for the first week and must
remain unchanged through the next frozen weeks:

```text
derive_nt_lvcfr_v17_signals.py  fcc05dd19bbfc621226250743979d341a7194bf7
nt_lvcfr_v17_config.json        64c7ef99cc076582ffff59c961208bc09d22cae7
nt_lvcfr_strategy.py            e4d00ae0c6fa1d24198c846bccb247baacdc0456
```

The next frozen week is `2025-06-23`.  Only if it passes the same complete gate
may `2022-05-16` be opened.
