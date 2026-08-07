# Local auction-state router — frozen BTC Week-1 failure

## Verdict

The acceptance-continuation branch is rejected as a trading scenario. It did not
repair the density bottleneck of the useful rejection branch; it added a large
population of negative-expectancy trades.

This is a **logic failure**, not an implementation failure. The run completed in
NautilusTrader 1.230.0 with the frozen full-NAV 3% loss budget, taker fees,
adverse ticks, funding reserve, cost-viable MIT targets, one pending/open slot,
and a 30-minute maximum hold.

## Immutable run

- source commit: `0df71cf66933fec47c23d3ee939656229cc60c42`
- workflow run: `31194458434`
- period: `2025-12-22` to `2025-12-29` exclusive
- instrument: `BTCUSDT`

## Results

| Variant | Structural signals | Trades | Wins | Losses | Win rate | Net return | Daily geometric growth | Profit factor | Max drawdown | Active days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rejection + acceptance router | 25 | 22 | 8 | 14 | 36.36% | -8.0762% | -1.1958% | 0.7626 | 15.8930% | 5 |
| rejection-only ablation | 4 | 4 | 3 | 1 | 75.00% | +6.2513% | +0.8700% | 3.3450 | 3.8476% | 2 |

The combined router generated 21 acceptance signals and four rejection signals.
After execution-cost viability and slot handling, the acceptance branch produced
18 trades with approximately five wins and thirteen losses. The branch therefore
increased activity while destroying the positive expectancy of the rejection
state.

## Why the acceptance state was not actually established

The implementation classified a first-touch bar as accepted when one completed
15-second bar closed outside the source liquidity with directional body, flow,
and high path efficiency. It then allowed the very next 15-second bar to count as
an accepted retest when that bar touched the source and closed outside again.

That sequence does not prove a new auction was established:

1. There is no mandatory price separation from the source after the initial
   outside close.
2. There is no independently formed protected swing or other completed structure
   showing that the broken level became defended inventory.
3. A single next-bar touch can therefore be ordinary traversal around the
   liquidity pool rather than a mitigation of an accepted displacement leg.
4. The stop is placed beyond that one retest bar. Normal continuation mitigation
   can then hit the stop even when the broader direction is not yet disproved.

The branch consequently confused **outside printing** with **persistent auction
acceptance**.

## Relation to earlier failures

This reproduces the earlier continuation findings rather than opening a new
family:

- Removing an outside-hold requirement increased density but converted most
  first-touch continuation guesses into stops.
- Requiring a separate hold protected path quality but reduced the population to
  a few events.
- Flow and open-interest labels alone did not distinguish forced overshoot,
  temporary marking, broad-market price discovery, and durable local acceptance.
- Continuation stops anchored too close to the broken pool were incompatible
  with normal mitigation.

Therefore the correct response is not to tune efficiency, body, imbalance,
retest-window, or stop-buffer thresholds on this week.

## Research decision

- Permanently discard the current acceptance-continuation branch.
- Retain the rejection-only route as the quality benchmark.
- Do not revive local acceptance unless a future hypothesis supplies a distinct,
  causally completed state transition before entry, such as separation followed
  by independently defended structure, and demonstrates adequate density without
  parameter fitting.
- Before inventing another filter, test whether the already useful multiclock
  rejection route transfers unchanged to untouched project symbols. That test
  separates a 15-second execution-clock mismatch from true structural
  non-portability and has higher information value than another BTC threshold
  experiment.
