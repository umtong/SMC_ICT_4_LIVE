# Why v3 was not extended

## Diagnostic interval

- Development interval: 2024-02-01 through 2024-02-14.
- Warm-up: 30 calendar days.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- One continuous account and one global position/order slot.
- NautilusTrader orders, fills, fees, positions and account reports.

## Result

| Metric | v3 |
|---|---:|
| Starting NAV | 100,000.00 |
| Final NAV | 83,487.19 |
| Total return | -16.51% |
| Daily geometric growth | -1.2808% |
| Closed positions | 20 |
| Independent trades/day | 1.43 |
| Win rate | 35% |
| Sum actual net R | -5.7049R |

Risk/accounting validation passed.  The failure was therefore not dismissed as a matching-engine or NAV bug.

## Conditional evidence

| Higher/decision footprint pair | Trades | Wins | Sum net R |
|---|---:|---:|---:|
| FVG / FVG | 6 | 3 | +0.5329R |
| OB / OB | 6 | 4 | +1.4471R |
| FVG / OB | 2 | 0 | -1.9228R |
| OB / FVG | 6 | 0 | -5.7620R |

This tiny sample is not a stable estimate of pair profitability.  It did reveal a structural error: “different footprint labels overlap” had been promoted to market context and even used as an arbitration advantage.  That has no clear support in the source material.  OB and FVG were being asked to answer direction, range and auction-state questions which the material assigns to market structure.

## Failure decomposition

- **Understanding error:** overlap was treated as a reason to trade instead of evidence inside a pre-existing scenario.
- **State error:** ordinary touch/reaction and true rejection, acceptance or rotation were not separated strongly enough.
- **Geometry error:** some late confirmations left a narrow target space even when nominal RR was large from a tight stop.
- **Routing error:** the tie-breaker rewarded heterogeneous OB/FVG kinds, exactly the subset that failed most severely in the diagnostic.
- **Not established:** that OB is useless, that FVG is useless, or that EasyChart logic is unprofitable.

## v5 response

v5 preserves the validated Nautilus execution/risk foundation, removes footprint-kind diversity from arbitration, promotes causal structure to context, and makes OB/FVG event-local confirmation objects.  The same interval is used first as development data to check whether this semantic correction produces the intended trades—not as a holdout or final proof.
