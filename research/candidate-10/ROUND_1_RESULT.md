# Candidate 10 — Autonomous round 1 result

Status: **project target not achieved; no success claim**.

This round used NautilusTrader for all orders, fills, fees, positions, margin and
raw account NAV.  Every promoted experiment used the current all-cost NAV for
the fixed 3% loss budget, with size-dependent square-root impact solved jointly
with quantity and debited at actual fills.  Across all four instruments the
single global pending-entry/position invariant remained enforced.

## Clean conclusions

### v26 — accepted-auction breaker retest: discarded

- Full: 6 trades, 1 win, impact-adjusted NAV 90,547.81, daily geometric growth
  -1.408%, drawdown 14.23%.
- Price-only ablation: 11 trades, 1 win, NAV 77,677.07.
- Executed-flow reacceleration removed some bad trades but did not create
  positive expectancy.  This was a clean logic failure, not an implementation
  failure.

### v27 — realistic-cost market-leadership SCDAM

Three untouched weeks and an exact leadership-removal ablation were run in six
parallel jobs.

- Leadership removed materially worsened V27A, V27B and V27C.
- Full V27B: 3 trades, 1 win, NAV 101,195.94.
- Full V27C: 3 losses, NAV 91,264.93.

Dynamic market leadership has negative-selection value, but the underlying FAR
scenario remained too permissive.

### v28 — resolved-auction certificate

The candidate rejected a FAR when both the candidate and median market state
remained in a severe auction against the proposed reversal, and required the
existing minimum local impulse for follower relative-recovery approvals.

- Controlled failures were reduced.
- Holdout 2025-01-25: 5 trades, 3 wins, NAV 108,620.82, daily growth 1.188%, but
  win rate only 60%.
- The candidate was not promoted.

### v29 — independent external draw requirement

FAR was allowed only when its target came from a pre-existing independent
external liquidity hazard.  Source-range and context-only target inference was
removed by exact ablation.

- Controlled 2025-01-25: 3/3 wins, NAV 115,397.86.
- Controlled 2024-04-13: 1/1 win, NAV 105,241.12.
- New untouched 2023-11-26: one AAC loss.
- New untouched 2023-04-11: two FAR losses, NAV 94,064.64.

The independent-draw variable is useful but insufficient.  An official Binance
XRP kline containing internally impossible total/taker volume was repaired only
from its completed-bar quote volume and OHLC4; the repair was recorded in the
data manifest and regression-tested.

### v30 — source-equilibrium whole-stop transfer

After a FAR reached the midpoint of its already-completed source dealing range,
the original stop was replaced at a modeled all-cost-neutral level while the
external target remained live.

- The two controlled 2023-04-11 losses were reduced from NAV 94,064.64 to
  99,997.39.
- Untouched 2025-08-19 loss was reduced to approximately flat.
- Untouched 2023-10-20 never reached source equilibrium and remained three full
  losses.
- Some large winners were reduced because the whole position was transferred.

Source equilibrium is a real causal risk-transfer event, but whole-position
neutralization does not solve signal quality.

### v31 — sweep-efficiency selector: discarded

The raid's penetration/relative-volume efficiency was required to meet the
already-frozen displacement threshold.  It removed a new holdout winner and
kept a new holdout loser.  This exact selector failed to generalize and is
terminated; it must not be tuned further.

### v32 — funded partial source-equilibrium transfer: insufficient

At source equilibrium, the minimum partial quantity was solved such that its
modeled all-cost profit funded the complete original-stop loss of the residual
runner.  The fraction was never fixed or optimized.

Untouched results:

- 2024-09-12: baseline two losses NAV 93,977.57; funded partial NAV 96,885.72.
- 2023-01-21: one loss; source equilibrium was not reached, so result unchanged.
- 2025-07-07: no trades.

The management rule is logically valid and reduces some realized losses, but it
does not turn the scenario into positive expectancy.

## Implementation errors fixed under variable control

- Deterministic source-patch assertion count.
- Python unittest invocation.
- Docker-mounted artifact permissions.
- Missing v29 certificate import.
- Binance vendor kline with impossible total/taker flow.
- Pandas Arrow-string versus numeric repair-column dtype.

Every error was repaired with week, signal logic, costs, risk, entry, stop,
target and seed frozen, then rerun on the same inputs.

## Valid findings retained

1. Dynamic cross-market leadership consistently rejects adverse candidates.
2. A pre-existing independent external draw is materially better than a target
   inferred only from the source range or contemporaneous momentum.
3. Source dealing-range equilibrium is the first robust delivery objective: many
   eventual stop-outs first reached it, so risk ownership must change there.
4. Static FAR selectors did not generalize; more threshold filters are not a
   structural path.
5. Management can reduce drawdown but cannot repair incorrect direction.

## Structural replacement selected

The FAR lineage is no longer advanced by adding filters.  The next candidate
must make **source equilibrium the primary economic objective**, not merely a
management checkpoint, and must use post-entry aggregate trades and L1 quotes to
classify what happens after that delivery:

- equilibrium acceptance and refill failure -> separately funded external-draw
  runner;
- equilibrium rejection or renewed adverse absorption -> terminal exit;
- no delivery -> original failed-auction invalidation.

The detector, primary equilibrium trade, and optional runner will be separate
state machines.  The primary trade must have its own structurally tighter
post-retrace invalidation so that the equilibrium target has viable cost-after
payoff.  No new fixed threshold is permitted without a causal normalization or
an exact ablation.
