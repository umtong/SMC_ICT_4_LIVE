# Candidate 14 V5 frozen-holdout failure

## Verdict

`candidate-14-session-far-price-discovery` is rejected. It cannot support a
success claim or further parameter refinement.

The result is not a marginal threshold miss. Two independent frozen evaluations
show negative after-cost NAV growth, low win rate, insufficient opportunity and
structurally incorrect cross-market transfer ownership.

## Sixteen precommitted quarterly holdouts

The reservation selected one seven-day interval from every quarter of 2022Q1
through 2025Q4 before outcomes were inspected. Fifteen intervals completed; H04
had zero trades and stopped only because the evidence wrapper reused
`scenario_id="AMBIGUOUS"`. Treating H04 as flat cannot materially improve the
portfolio result.

| Interval | Trades | Wins | Losses | Final NAV | Daily geometric growth |
|---|---:|---:|---:|---:|---:|
| H01 | 0 | 0 | 0 | 100,000.00 | 0.0000% |
| H02 | 0 | 0 | 0 | 100,000.00 | 0.0000% |
| H03 | 2 | 1 | 1 | 101,100.05 | +0.1564% |
| H04 | 0 | 0 | 0 | 100,000.00 | 0.0000% (event-log wrapper failure) |
| H05 | 0 | 0 | 0 | 100,000.00 | 0.0000% |
| H06 | 2 | 1 | 1 | 106,180.68 | +0.8604% |
| H07 | 2 | 1 | 1 | 99,744.08 | -0.0366% |
| H08 | 0 | 0 | 0 | 100,000.00 | 0.0000% |
| H09 | 0 | 0 | 0 | 100,000.00 | 0.0000% |
| H10 | 1 | 1 | 0 | 106,453.50 | +0.8974% |
| H11 | 2 | 0 | 2 | 93,835.57 | -0.9048% |
| H12 | 1 | 1 | 0 | 104,941.46 | +0.6914% |
| H13 | 1 | 0 | 1 | 96,990.80 | -0.4355% |
| H14 | 2 | 1 | 1 | 101,493.44 | +0.2120% |
| H15 | 4 | 0 | 4 | 88,508.65 | -1.7287% |
| H16 | 3 | 1 | 2 | 100,681.58 | +0.0971% |

Pooled evidence:

- 112 observed calendar days
- 20 closed trades
- 7 wins, 13 losses
- 35.0% win rate
- compounded NAV multiple `0.98460662597`
- net return `-1.5393%`
- daily geometric growth approximately `-0.01385%`
- only 10 of 16 active weeks

Every aggregate gate failed except basic safety and positive ending NAV.

## Independent 84-day continuous frozen evaluation

The later single-account interval `2026-05-11` through `2026-08-03` independently
failed:

- 15 closed trades
- 3 wins, 12 losses
- 20.0% win rate
- final NAV `80,737.93761302`
- net return `-19.2621%`
- daily geometric growth `-0.254392%`
- continuous realized maximum drawdown `21.3861%`

This is a continuous NautilusTrader account path, not weekly-reset aggregation.

## Structural diagnosis

### 1. Session-I7 did not create holdout opportunity

All 20 trades across H01-H16 were `SCDAM_CORE`. The new `SESSION_I7` module
produced no executed holdout trade. It therefore did not solve the frequency
problem which motivated V5.

### 2. Synchronized movement was mistaken for price-discovery ownership

The preserved semantic FAR branch approved unanimous peer alignment while
allowing event-direction ranks one, two and three. Frozen holdouts separated the
categories:

| Event-direction rank | Trades | Wins | Losses | Profit factor | Net PnL |
|---|---:|---:|---:|---:|---:|
| 1 | 8 | 4 | 4 | 1.878 | +10,558 |
| 2 | 8 | 1 | 7 | 0.216 | -16,346 |
| 3 | 4 | 2 | 2 | 1.906 | +5,718 |

Rank two was not a weaker version of the same alpha. It was a different economic
category: a follower borrowed a synchronized peer move inside an unresolved
auction and was falsely labeled as marketwide transfer.

The strongest remaining FAR subset, event rank one with unanimous peers, had six
trades, four wins and profit factor about 3.75. That confirms useful directional
information but is far too sparse to meet the project opportunity and geometric
growth requirements by filtering alone.

### 3. Weak markets dominated the losses

- SOL: 6 trades, 1 win, 5 losses, profit factor about 0.439
- ETH: 2 trades, 0 wins
- SHORT: 11 trades, 3 wins, 8 losses, profit factor about 0.676

The correct response is not a symbol or direction blacklist. Those results show
that local candidate identity was being confused with ownership of the broader
auction.

## Research consequence

V5 is frozen as a failed experiment. Its useful inheritance is limited to:

- causally completed external-liquidity FAR/AAC scenarios;
- exact current-NAV 3% planned-loss sizing;
- one global pending-entry or open-position slot;
- realistic NautilusTrader orders, costs, fills and account accounting; and
- cross-market measurements of quote liquidity, trailing direction and event
  displacement.

V6 must not optimize V5 thresholds. It must change the state representation:

1. only actual event-direction ownership may transfer global initiative;
2. the initiative remains active until an observable structural invalidation or
   declared external delivery;
3. fresh lower-timeframe continuation episodes may accumulate opportunity in
   that direction; and
4. each continuation requires its own protected swing, displacement, FVG,
   invalidation and external target.
