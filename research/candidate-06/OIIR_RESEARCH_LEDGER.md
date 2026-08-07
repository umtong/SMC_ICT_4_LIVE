# OIIR Research Ledger

## Hypothesis

Directional moves with extreme completed OI expansion were treated as fresh
inventory, while extreme OI contraction was treated as deleveraging. Later
completed OI and price/flow responses selected retained-inventory continuation,
persistent unwind, or counter-inventory reversal.

## Terminal first-week result

|variant|geom/day|trades|wins|win rate|PF|max DD|
|---|---:|---:|---:|---:|---:|---:|
|full inventory regime|0.2453%|11|6|54.55%|1.1210|6.56%|
|new-inventory BUILD only|-1.6522%|6|1|16.67%|0.1634|12.66%|
|reversal without counter-inventory rebuild|-0.2103%|23|13|56.52%|0.9486|12.77%|

Full branch attribution:

- `OIIR_B`: 6 trades, 2 wins, -5,333.11 USDT.
- `OIIR_UC`: 2 trades, 2 wins, +5,611.34 USDT.
- `OIIR_UR`: 3 trades, 2 wins, +1,451.47 USDT.

The full system failed the first-week 1% growth gate and is discarded. Fresh OI
expansion continuation was structurally negative. Removing completed
counter-inventory rebuild from reversal also made the combined system negative,
so rebuild confirmation is informative.

The next independent hypothesis, OIUT, removes BUILD event creation while
retaining the unchanged unwind continuation and counter-inventory reversal
contracts. No threshold, direction, session, stop, target, cost, risk or date is
adjusted from OIIR.
