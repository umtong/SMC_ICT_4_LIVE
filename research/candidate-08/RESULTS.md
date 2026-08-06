# Candidate 08 executed results

## Status

**Not yet claimed successful.** The first valid fixed BTC week rejected the original acceptance
implementation. One economically motivated structural revision is now frozen and under the same
predeclared three-week screen.

## Baseline screen-01 — failed

GitHub Actions run `31078218914`, commit
`ea5669f05cb1b1ed28c5da695c120aeb5da501aa`, replayed 2024-04-08 through 2024-04-15 with the pinned
NautilusTrader environment, official checksum-verified Binance Vision data, 6 bp per fill, one-tick
adverse slippage, and NAV-based 3% planned loss sizing.

| metric | result |
|---|---:|
| closed trades | 11 |
| winning trades | 2 |
| win rate | 18.1818% |
| starting NAV | 100,000.00 USDT |
| final NAV | 82,611.57175032 USDT |
| total return | -17.388428% |
| daily geometric NAV growth | -2.691966% |
| maximum realized-equity drawdown | 26.4543% |
| profit factor | 0.19048 |
| execution failures / residual exposure | 0 / 0 |

All eleven trades came from the acceptance family; rejection setups failed the fixed cost-after
payoff gate. The baseline therefore failed before screen-02 and is not promoted.

## Failure diagnosis

The original acceptance state entered on the same candle that first retested and held a swept pool.
That reduced the intended causal scenario to a breakout/retest candle pattern. In the executed
trades, both winners had materially lower retest/confirmation volume than their displacement bar,
whereas most losses returned with equal or greater activity. One low-volume loser came from a pool
only four bars old, which was not meaningful external liquidity. This is a mechanism diagnosis, not
a profitable-subset performance claim.

## Frozen controlled revision

Commit `28f469a6ad0a9e854fdb943fe992f2fabcc09f19` makes one coherent sequence correction:

1. a single-touch pool must have been visible for at least 30 one-minute bars; a reinforced pool with
   at least two touches is already established;
2. an acceptance retest must hold the pool with volume no greater than 75% of the interaction
   displacement volume ratio;
3. that retest only changes state to `RETEST_HELD`; entry requires a separate later bar within three
   minutes to displace beyond the retest with directional body/close location and volume expansion
   relative to the retest.

Risk, costs, stop logic, target logic, screen dates, and the 1.20 cost-after payoff gate were not
changed. Ten causal/risk/timestamp tests passed in the pinned environment before the revision was
committed.

## Promotion decision

Pending the revised fixed three-week screen. No long-period or cross-symbol claim exists until every
predeclared BTC screen gate passes.
