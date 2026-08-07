# Candidate-09 v19 — Failed-boundary role-reversal retest

## Why v18 is not enough

The frozen v18 baseline passed the three-week screen but ended the unchanged three-year BTC
interval at 8.86 USDT from 100,000 USDT. It made 663 trades, lost 536, produced mean
−0.466R, and reached 99.991% drawdown. The direct `ACCEPTED` failure path still contributed
544 trades and −83,196 USDT. A close back inside therefore detects outside-acceptance loss
but does not establish that the old boundary now contains replenished opposite liquidity.

## Causal sequence

1. Establish outside acceptance exactly as in v18.
2. Observe the exact v18 failure close back inside with opposite displacement and flow.
3. Do not enter or chase price toward equilibrium.
4. Within the existing six-bar post-resolution window, price must revisit the failed
   boundary from inside using the existing retest tolerance.
5. The completed retest bar must close back inside with the existing failure displacement
   and order-flow polarity. This confirms the boundary has changed role.
6. Enter at that completed close. Invalidation is beyond the boundary and every adverse
   extreme observed from the first failure through the retest. The target remains the
   source auction's pre-observed equilibrium.
7. If the outside auction is reaccepted, restore its prior state and process that same bar.
   If equilibrium is reached before entry, or no qualified retest appears in the existing
   window, expire without chasing.

## Exact controls

| Variant | Role-reversal retest requirement |
|---|---|
| `baseline` | Both direct `ACCEPTED` and defended `RETESTED` failures |
| `v18-control` | None; exact v18 state-strength timing |
| `direct-only` | Direct `ACCEPTED` failures only; `RETESTED` keeps exact v18 persistence |
| `retested-only` | `RETESTED` failures only; direct failure keeps exact v18 timing |

No numerical threshold, detector, auction horizon, cost, target, risk fraction, data period,
or NautilusTrader execution rule changes.

## External checks on the mechanism

- Degryse, de Jong, van Ravenswaaij, and Wuyts (2005) document partial reversal and gradual
  limit-order-book recovery after aggressive orders; an initial price shock alone is not a
  sufficient statement about restored liquidity.
- Bechler and Ludkovski (2017) find that limit-order additions/cancellations and deeper book
  shape carry more meso-scale information than trade imbalance alone.
- Taranto, Bormetti, and Lillo (2014) show that persistent signed order flow can coexist with
  weak price progress because liquidity adapts asymmetrically.
- Perry (2026, independent SSRN pre-registration) separately specifies failed-auction
  reversion as return inside, retest boundary, then revert toward point of control. Its
  cross-market replication is not conclusive, so it is corroborating structure rather than
  proof for this candidate.
