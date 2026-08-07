# Candidate 01 v41 — Cross-asset delivery-failure rotation

## Decision

**STOP after the frozen first week.** The predeclared confirmation weeks were
not opened.

- frozen week: `2026-04-27` through `2026-05-04` UTC
- frozen seed: `4101`
- authoritative run: `31188193009`
- artifact: `8997903816`
- artifact digest: `sha256:a6e4d2a23421a158caae2b83783f8f25afe41b91ca6c3322cb907f1ba8beb3c5`
- engine: NautilusTrader 1.230.0 on official Binance Vision USD-M aggregate trades as TradeTicks
- shared current-NAV planned risk: 3%
- cost: 7 bp per side
- one global pending entry or open position

## Frozen question

When two peer markets, including BTC or ETH, retain accepted delivery beyond the
same-side preceding-hour liquidity but a laggard leaves both hourly edges
unconsumed and completes an opposite five-minute structure displacement with
opposite aggressive flow, does the laggard rotate to its opposite frozen hourly
liquidity?

The control retained the identical two-peer state and execution contract but
traded aligned laggard assimilation toward the same-side hourly edge.

## Authoritative result

| Metric | Failure rotation | Assimilation control |
|---|---:|---:|
| selected plans | 7 | 0 |
| closed positions | 6 | 0 |
| wins / losses | 3 / 3 | 0 / 0 |
| win rate | 50.00% | undefined |
| total return | **-0.6707%** | 0.0000% |
| geometric daily return | **-0.0961%** | 0.0000% |
| profit factor | **0.9273** | undefined |
| maximum drawdown | **-6.2138%** | 0.0000% |
| trades per day | 0.857 | 0.000 |

The six completed trades were:

- ETH short, exactly two peers: positive four-hour exit, about +3.80% NAV;
- SOL short, all three peers: structural stop, about -3.13% NAV;
- ETH long, exactly two peers: structural stop, about -3.02% NAV;
- XRP long, exactly two peers: target, about +4.18% NAV;
- BTC short, exactly two peers: positive four-hour exit, about +0.57% NAV;
- ETH long, all three peers: structural stop, about -3.08% NAV.

One contemporaneous XRP short plan was correctly rejected while the shared
account already held the ETH short. All operational invariants passed and the
run ended flat.

## Interpretation

Opposite hourly routing solved v40's destination-distance failure and restored
nearly one trade per day, but raw failure rotation had no positive cost-after
expectancy. It is rejected as a complete scenario.

A structurally distinct breadth observation survived. Both trades formed while
all three other markets shared the delivery direction, and both stopped. The
four trades formed under exactly two peer leaders produced three wins and one
loss. This is not accepted as evidence by itself and is not fitted on the same
week. It motivates a new independently frozen hypothesis:

- three-peer agreement represents a systemic market shock and should not be
  faded through a laggard reversal;
- exactly two peer leaders can represent selective capital rotation, where a
  laggard's opposite displacement is economically distinct.

V42 tests exactly-two-peer failure rotation against the unchanged at-least-two
control on a fresh random week. No v41 threshold, stop, target, risk, cost or
hold rule is changed.
