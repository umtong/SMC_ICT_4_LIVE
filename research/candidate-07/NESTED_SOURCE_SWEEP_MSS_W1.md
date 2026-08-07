# Nested 15S/30S/1M source liquidity — clean W1 failure

## Classification

`LOGIC / OPPORTUNITY EXTENSION FAILED`

The prior 15-second sweep→MSS→broken-level-retest route showed a real but sparse
W1 edge. This controlled successor changed only the source-liquidity clocks:

```text
baseline: 15S + 30S + 1M source pools
ablation: 15S source pools only
```

Both routes kept the same completed reclaim, distinct 15-second MSS pivot,
broken-level rejection retest, structural stop, 15S→30S→1M→5M target hierarchy,
30-minute horizon, current-NAV 3% risk, fees, adverse ticks, funding and
NautilusTrader MIT execution.

## Frozen BTC Week-1 result

Period: `2025-12-22` through `2025-12-29` exclusive.

The two variants produced exactly the same four trades and exactly the same NAV:

| Measure | Nested source clocks | 15S source only |
|---|---:|---:|
| Trades | 4 | 4 |
| Active days | 2 | 2 |
| Wins / losses | 3 / 1 | 3 / 1 |
| Win rate | 75.00% | 75.00% |
| Net return | +6.2513% | +6.2513% |
| Daily geometric growth | +0.8700% | +0.8700% |
| Profit factor | 3.3450 | 3.3450 |
| Maximum drawdown | 3.8476% | 3.8476% |

The nested detector materially enlarged the source population:

```text
15S pools: 5,848
30S pools: 3,829
1M pools:  2,380
selected first-touch events: 5,070
qualified sweeps: 62
```

But it still produced only eight completed MSS events, four valid broken-level
retests and four entries. Two existing trades were relabeled as one-minute
source sweeps; no new independent trade was created. Thirty-second sources
created no terminal entry.

## Failure cause

The bottleneck is not source-liquidity coverage. It is the use of a fifteen-
second execution structure after the sweep. The source population already
contained thousands of causally confirmed first touches and dozens of qualified
sweeps, yet the fifteen-second MSS/retest clock reduced them to four entries.
Adding higher source clocks cannot create an execution transition that the
coarse confirmation clock does not observe.

## Retained evidence

- broken-level retest remains materially superior to MSS-close entry;
- source and MSS pivots must be distinct physical pivots;
- nested sources neither improve nor damage the observed edge;
- the next controlled variable must be the lower-timeframe execution clock, not
  another source threshold or target adjustment.

## Next successor

```text
causal 15S source liquidity
-> literal first 5S touch and completed 5S reclaim state
-> distinct protected 5S opposing swing
-> 5S displacement MSS
-> first 5S broken-level rejection retest
-> unchanged 15S / 1M / 5M target hierarchy
```

The controlled ablation retains the proven fifteen-second execution clock. All
wall-clock history, confirmation and retest horizons are kept economically
constant by scaling bar counts three-for-one. This tests lower-timeframe entry
resolution without fitting the W1 outcomes.

## Evidence

- source commit: `7e0b99e4648942eed49b3430d1e98fdf6923760f`
- workflow run: `31181130203`
- artifact id: `8994902991`
- artifact SHA-256: `ae5278fbca4d145c39a7a6ea1fccadf376ef729ec7deb8c791dcb32f3273226c`
