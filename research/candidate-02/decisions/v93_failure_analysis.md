# v93 final failure and component analysis

## Prospective evidence

The first BTC week, 2025-08-18 through 2025-08-25 UTC, was locked before direct
futures and spot collection. Five configurations were executed in separate
NautilusTrader 1.230.0 processes with current-account-NAV 3% risk, the fixed
cost model, no nominal cap and no score multiplier.

| Variant | Trades | Trades/day | Win | PF after cost | Growth/day | MDD |
|---|---:|---:|---:|---:|---:|---:|
| Portfolio, 15-minute retrace | 2 | 0.286 | 0.00% | 0.000 | -0.903% | 6.15% |
| Portfolio, 20-minute retrace | 3 | 0.429 | 33.33% | 0.653 | -0.314% | 3.63% |
| Portfolio, 25-minute retrace | 3 | 0.429 | 33.33% | 0.653 | -0.314% | 3.63% |
| Local reversion only | 2 | 0.286 | 0.00% | 0.000 | -0.903% | 6.15% |
| Common breakout only | 1 | 0.143 | 100.00% | infinity | +0.594% | 0.82% |

The central portfolio emitted two `LOCAL_REVERSION` states and one
`COMMON_BREAKOUT_CONTINUATION` state across two UTC cycles.

## Single core-state ablation

The variant matrix was fixed before the week was revealed. Because the two
states are mutually exclusive, the prelocked common-breakout component is the
exact result of removing the local-reversion state. Its only scenario ID is
identical to the continuation scenario in the central portfolio:

`v93-common_breakout_continuation-1755918900000000000`

Thus the ablation did not alter event thresholds, the week, target selection,
orders, costs or risk. It removed one state branch only.

The retained branch won 4,232.41 USDT after cost, but produced only one trade,
0.143 trades/day and +0.594% geometric growth/day. It therefore did not meet the
frequency or 1% growth gate and cannot advance to a second week.

## Dominant failure

Both local-reversion trades stopped quickly. The local-reversion branch erased
the profitable continuation trade and made the portfolio negative. This is a
logic error, not a timing or engine error: the same locked local-reversion state
was negative in its isolated component run.

The failed inference was that a close back inside the old range, followed by
opposite displacement and an FVG retrace, was enough to trade toward a nearby
internal pivot. It was not. Cross-market acceptance was more informative than
reclaim in this week.

## Valid part

The common-breakout state did several things correctly:

* perpetual and basis-adjusted spot both accepted beyond the old range;
* basis expansion was limited, so the move was not predominantly a perpetual
  dislocation;
* same-direction displacement and a later FVG retest held outside;
* the objective was the nearest intact external pivot known before the event;
* the identical signal won in both portfolio and isolated-state execution.

This is evidence for a component, not proof of a complete strategy. One event
is not enough to generalize or promote.

## Consequence

v93 is discarded as a complete candidate. A successor may preserve the common
breakout state but must create more independent structural event clocks without
loosening the acceptance logic. Applying the state to a causal registry of
prior 4-hour, 8-hour and previous-day liquidity levels is the next structural
path; repairing the losing local-reversion branch is not.
