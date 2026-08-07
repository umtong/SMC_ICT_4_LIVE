# Candidate 01 v39 — Cross-asset consensus laggard transfer

## Decision

**STOP after the frozen first week.** Do not open a second week, third week or
long evaluation and do not relax the two-leader requirement.

- branch: `research/candidate-01`
- frozen week: `2023-04-17` through `2023-04-24` UTC
- frozen seed: `3901`
- authoritative corrected workflow: `31185282150`
- workflow head: `f9f8187a4a1a4f207bdf062d6f5aad7c0b17d78d`
- artifact id: `8996724910`
- artifact digest: `sha256:57da1300eb4a72f6abb1cc78a661f2255d46da8bcac1ba5f512b9d39d88e6a88`
- execution: NautilusTrader 1.230.0 using official Binance Vision USD-M
  aggregate trades as one-for-one TradeTicks
- shared current-NAV risk: 3%
- execution cost: 7 bp per side
- pending entry plus open position across all four symbols: one

## Frozen scenario

The primary required two independent peer markets, including BTCUSDT or
ETHUSDT, to close beyond their immediately preceding completed-hour external
liquidity with aligned aggressive flow and range expansion. In the same
completed minute, a laggard had to leave its corresponding hourly target
unconsumed while breaking its preceding five-minute internal structure with
aligned flow and range expansion. The first later own-symbol TradeTick entered;
local displacement invalidated the trade and the frozen hourly boundary was the
target.

The single control changed only the peer count from two to one.

## Authoritative result

| Metric | Primary: two peers | Control: one peer |
|---|---:|---:|
| selected plans / closed positions | 1 / 1 | 4 / 4 |
| wins | 1 | 1 |
| win rate | 100.00% | 25.00% |
| cost-after total return | **+4.7412%** | **-4.6823%** |
| geometric daily return | **+0.6639%** | **-0.6827%** |
| profit factor | undefined, no loss | 0.4954 |
| maximum drawdown | -0.2789% | -6.3817% |
| minimum submitted net reward/risk | 1.5855 | 1.5487 |

The primary trade was a SOLUSDT long led by simultaneous BTCUSDT and ETHUSDT
hourly acceptance. It reached the frozen SOL hourly high and earned 4.74% of
shared account NAV after costs. The control added three one-leader trades; all
three lost, while retaining the same winning two-leader trade.

Both variants processed all 10,080 synchronized evaluation minutes, ended flat,
and had zero global-entry violations, protective-order failures or liquidation
markers. The earlier SOL/XRP quantity-metadata error was repaired without
changing the strategy, week, signal, stop, target, risk or cost.

## Interpretation

Two-peer consensus materially discriminated direction from the one-peer
population in this week. However, exact same-minute consensus produced only one
independent trade in seven days. Its 100% win rate and strong single-trade return
therefore do not establish a day-trading system, and geometric daily growth
remained below 1%.

This is an opportunity-structure failure, not a reason to weaken peer consensus.
V40 changes one structural variable: a peer's completed external-liquidity
acceptance remains an active delivery state until that peer closes back through
its frozen boundary. The laggard may then respond after the leader minute,
while all direction, target, invalidation, geometry, cost, risk and shared-account
rules remain unchanged.
