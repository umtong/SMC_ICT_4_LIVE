# Candidate 01 v33 — Two-Sided Pullback Auction Result

## Frozen question

V30 forced continuation after an accepted counterflow pullback. V31 and v32
forced reversal. V33 removed that directional prior and maintained both branches
until the market completed one structural resolution.

After the same cost-resolved outside initiative and first completed opposite-
flow pullback whose close preserved outside value:

1. continuation branch resolved on a later completed close beyond the frozen
   pullback extreme in the initiative direction;
2. reversal branch resolved on a later completed close through both the accepted
   boundary and the opposite frozen pullback extreme;
3. each branch was independently invalidated at its opposite pullback extreme
   plus one 7-bp buffer;
4. the first completed structural resolution ended the setup and was traded
   only if its branch remained valid and its stop was not touched in the same
   event;
5. primary additionally required aggressive flow on the winning event to agree
   with the resolved direction;
6. control removed only winning-event flow agreement;
7. target was the nearest active unconsumed completed-day/week level beyond the
   pre-initiative structure edge in the resolved direction.

## Implementation error and controlled repair

The first performance execution completed successfully, but the initial gate
compared a full-context count of flow-aligned resolutions with an evaluation-
week primary-plan count. That assertion mixed time scopes and failed after the
performance step.

No signal, state, order, cost, risk or performance code changed. The gate was
repaired to validate `primary_plans.csv` and `control_plans.csv` against their
own evaluation-period counts and scenario-ID contracts. The identical frozen
week was then rerun. The rerun reproduced the original performance exactly and
correctly opened the single control.

## Authoritative first BTC week

- Evaluation: `2020-11-23T00:00:00Z` to `2020-11-30T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution data: official Binance Vision BTCUSDT USD-M aggregate trades
  represented one-for-one as NautilusTrader `TradeTick`
- Costs: 7 bps per side
- Planned risk: current Nautilus account NAV × 3%
- Maximum hold: four hours
- Custom fill, PnL or NAV simulator: none

### Primary — first live branch resolution plus aligned aggressive flow

| Diagnostic | Result |
|---|---:|
| full-context outside initiatives | 88 |
| accepted counterflow pullbacks | 59 |
| continuation branches invalidated | 24 |
| reversal branches invalidated | 2 |
| structural resolutions | 46 |
| continuation resolutions | 26 |
| reversal resolutions | 20 |
| first resolution on dead branch | 5 |
| tradeable resolutions with calendar target | 26 |
| evaluation primary plans | 15 |
| selected continuation plans | 7 |
| selected reversal plans | 8 |
| Nautilus submissions / trades | 12 |
| wins | 3 |
| win rate | **25.00%** |
| total return | **-3.6433%** |
| geometric daily return | **-0.5288%** |
| profit factor | **0.8601** |
| maximum drawdown | **-14.5128%** |
| maximum-hold exits | 3 |
| minimum cost-after RR at submission | 3.7413 |

The evaluation produced five filled continuation trades and seven filled
reversal trades. Every continuation trade stopped. The reversal stream was
mixed: six long reversals contained three positive four-hour exits and three
stops; the single short reversal stopped. No selected trade reached its causal
calendar target.

### Single ablation — remove winning-event flow agreement

The control added one evaluation-period continuation plan whose completed
resolution had no aligned aggressive flow. Nautilus rejected that extra plan for
insufficient cost-after reward/risk. Consequently all submitted orders, fills,
positions, daily NAV and performance metrics were exactly identical to the
primary:

| Metric | Primary | Control |
|---|---:|---:|
| selected plans | 15 | 16 |
| submissions | 12 | 12 |
| trades | 12 | 12 |
| wins | 3 | 3 |
| total return | -3.6433% | -3.6433% |
| geometric daily return | -0.5288% | -0.5288% |
| profit factor | 0.8601 | 0.8601 |
| maximum drawdown | -14.5128% | -14.5128% |

Both runs ended flat with zero global-entry-gate violations, zero protective-
order failures and zero liquidation markers.

## Diagnosis

This is a logical failure after one controlled implementation repair.

The two-sided state machine successfully avoided selecting a direction before a
completed resolution. Branch-specific invalidation, first-resolution
precedence, causal calendar targets, executable reward/risk and NautilusTrader
execution all worked as designed. The remaining failure was that a completed
close outside the frozen pullback range was still not equivalent to durable
auction control.

Useful components:

- the initiative/pullback detector continued to generate many independent
  opportunities;
- independent branch invalidation prevented some stale resolutions;
- continuation and reversal were represented symmetrically rather than forced;
- target distance was sufficient, so cost-after RR was not the dominant issue;
- operational execution remained clean.

Dominant failure drivers:

- all five filled continuation resolutions reverted to their structural stop;
- profitable reversal longs were four-hour exits rather than target reaches;
- the week again showed large directional/regime asymmetry;
- aggressive-flow sign on the resolution event did not discriminate because 25
  of 26 full-context tradeable resolutions were already aligned.

The result means neither the first completed structural close nor its aggressive
flow identifies whether passive liquidity has actually withdrawn or replenished.
Adding more close-distance, wick, session or flow-score thresholds would fit the
same missing variable rather than observe it.

## Decision

`STOP` — do not run weeks 2 and 3 and do not tune branch offsets, resolution
thresholds, calendar targets or hold time.

The next structural path is to observe, or causally proxy, passive-liquidity
resilience. Official Binance Vision archive availability for `bookTicker`,
`bookDepth` and futures `metrics` is being independently verified. If usable
historical quote data are incomplete, the next candidate will use an
impact-saturation failure state: declining marginal price response during the
initiative followed by a stronger opposite-flow response, with the v31
immediate-reversal stream as the single control.
