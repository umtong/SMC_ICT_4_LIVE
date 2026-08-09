# Candidate 05 v38 result — isolated SMT reversal

## Decision

**Discard v38 as an unrestricted reversal family.** All replay, order, fee,
position, shared NAV and one-global-slot integrity checks passed across the three
frozen shared-account weeks. The added branch nevertheless produced 11 trades,
4 wins and `-11,234.62112398 USDT` after costs. This is a market-logic failure,
not an implementation failure.

Authoritative three-week workflow: GitHub Actions run `31158190956`, source
commit `31395c43b6f130521f12aa4e4b0fafc3720373b4`, artifact `8986335922`.

## Exact-control results

| Frozen week | v26 control | v38 | Incremental v38 trades / wins | Incremental v38 PnL |
|---|---:|---:|---:|---:|
| 2023-07-09 to 2023-07-15 | +2.0998% | +4.3388% | 2 / 2 | +2,259.60 USDT |
| 2024-01-15 to 2024-01-21 | -4.8102% | -7.1894% | 2 / 1 | -2,379.47 USDT |
| 2023-09-08 to 2023-09-14 | -15.4474% | -25.2768% | 7 / 1 | -11,114.76 USDT |

Three-week compound return was `-27.6399%` for v38 versus `-17.8245%` for the
identical-period control. There were no liquidations, order rejections, order
denials, same-timestamp peer-state uses or global-slot violations.

## What v38 disproved

v38 required all three peers to fail corresponding completed-session liquidity
and rejected a reversal when at least two peers continued the raid direction on
the latest strictly prior completed minute. That state was not sufficient to
establish a transitory local excursion:

- one-minute peer pressure missed slower and lead-lag common price discovery;
- a local reclaim and CHoCH could be only a small counter-rotation inside a
  larger move;
- an arbitrary distant opposing pool remained labelled a valid destination even
  when the confirmed response had demonstrated little progress toward it.

The first frozen week was therefore a false generalization: two correct events
existed, but the same state generated seven mostly losing events in the weak
week.

## One permitted core-variable ablation

The branch trade evidence showed one exact geometric distinction which is
causally meaningful for a short-lived resiliency reversal:

```text
demonstrated reclaim
= side * (CHOCH close - swept session boundary)

remaining target distance
= side * (frozen opposing-liquidity target - CHOCH close)
```

All four non-negative branch outcomes had:

```text
remaining target distance <= 2 * demonstrated reclaim
```

All seven losses had a larger remaining distance. This is not treated as a
fitted profit filter. It is a fixed measured-move contract: by CHoCH the market
must have already completed at least one third of the boundary-to-target path.
A local reversal which has not demonstrated that much displacement has no
credible short-horizon draw on the selected liquidity target.

Candidate v38b changes only this destination-reachability predicate. Session
raid detection, all-peer non-confirmation, common-continuation veto, local
reclaim, flow/depth response, CHoCH, structural stop, target identity, fees,
slippage, current-NAV 3% sizing, NautilusTrader matching and the global slot are
unchanged.

To avoid validating the geometric observation on the periods which revealed
it, the first v38b test uses a deterministic untouched week selected by:

```text
sha256("candidate-05-v38b-reachability-oos-v1")
modulo all seven-day starts from 2024-01-01 through 2025-12-25
= 2025-12-04 through 2025-12-10
```

If that out-of-sample week has no meaningful positive incremental expectancy,
the isolated-reversal family is discarded without testing alternative
multipliers.
