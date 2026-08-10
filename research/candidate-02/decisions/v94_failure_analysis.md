# v94 final failure and ablation analysis

## Prospective first week

BTCUSDT 2025-11-03 through 2025-11-10 UTC was locked before futures or spot
collection. The strategy evaluated four-hour, eight-hour and previous-day
completed high/low levels. Each level could be consumed only once. A trade
required perpetual and basis-adjusted spot acceptance, limited basis expansion,
directional displacement, a three-candle FVG and a later retest holding the old
level from the accepted side.

The central 20-minute retrace configuration produced three losses:

| Source | Trades | Wins | Comment |
|---|---:|---:|---|
| FOUR_HOUR | 2 | 0 | both stopped almost immediately |
| EIGHT_HOUR | 1 | 0 | moved more than +5R favorably but missed a distant selected target and reversed |
| PREVIOUS_DAY | 0 | 0 | no complete scenario |

The central portfolio lost 9,080.82 USDT after cost, compounded at -1.351% per
day and reached 12.54% mark-to-market drawdown.

## Single core-variable ablation

The selector originally walked outward through intact pre-event pivots until a
target paid at least 1.10 cost-after reward/risk. This allowed it to skip the
nearest real liquidity pool and select a much farther one. The first eight-hour
short, for example, skipped 100,873.2 and targeted 98,909.7. Price traveled more
than +5R from entry but reversed before the distant objective.

The one allowed ablation removed only the lower reward/risk predicate from the
pivot selector. Its verified source diff changed one line. All acceptance,
spot, basis, displacement, FVG, retest, invalidation, costs and 3% current-NAV
risk rules stayed unchanged.

The nearest 100,873.2 pivot was reached one minute after the eight-hour short,
producing +865.74 USDT after cost. The two four-hour trades remained unchanged
losses. Central ablation results were therefore:

| Metric | Result |
|---|---:|
| Trades | 3 |
| Trades/day | 0.429 |
| Wins | 1 |
| Win rate | 33.33% |
| PF after cost | 0.140 |
| Geometric growth/day | -0.780% |
| MDD | 6.15% |
| Final NAV | 94,662.79 USDT |

The ablation established that target skipping caused one avoidable failure. It
did not establish a structural route to the project target because frequency,
direction quality, profit factor and growth remained far below their gates.

## Dominant logical failure

Mechanically fresh four-hour range boundaries were treated as external
liquidity levels with the same authority as older levels. Common spot-perpetual
acceptance beyond them was not sufficient: both resulting trades failed almost
immediately even though the complete state sequence was present.

The accepted-breakout mechanism had one useful eight-hour example, but one
trade is not independent repeated evidence. It cannot justify a second week.

## Retained evidence

* Cross-market common acceptance is more informative than perpetual flow alone.
* The old boundary holding after an FVG retest is a coherent continuation state.
* The nearest intact pre-event pivot is a better natural objective than skipping
  liquidity solely to manufacture a larger reward/risk ratio.
* Source-separated diagnostics prevented the profitable eight-hour trade from
  hiding the negative four-hour component.

v94 is discarded. No second v94 variable is removed and no second week or
long-horizon evaluation is allowed.
