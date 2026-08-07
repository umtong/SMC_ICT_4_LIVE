# CVPD Terminal Report

## Decision

`Cross-Venue Price-Discovery Bifurcation` is discarded after a valid
NautilusTrader first-week evaluation.

The first run failed before market metrics because the reusable strategy
constructor tried to create the CVPD engine through a generic selector which
had no channel for the synchronized spot context mapping.  The repair changed
only construction order: initialize with a supported placeholder and inject the
CVPD engine before any bar is processed.  The same week, data, state rules,
costs, fills, risk and gates were then rerun.

## Valid first-week result

| variant | trades | wins | cost-after geometric growth/day | conclusion |
|---|---:|---:|---:|---|
| full bifurcation | 1 | 0 | -0.4342% | negative expectancy |
| perpetual false-break only | 0 | 0 | 0.0000% | no completed response |
| spot-led relay only | 1 | 0 | -0.4342% | negative expectancy |
| no-basis ablation | 2 | 0 | -0.8665% | negative expectancy |

The robust prior-only basis gate removed one losing event relative to the
no-basis ablation, so it had real false-positive filtering value.  It did not
create an independent positive edge.  Most nominal cross-venue liquidity events
were simultaneous spot/perpetual confirmation and therefore correctly treated
as price discovery rather than divergence.  The remaining one-venue events were
rare; the completed relay trade immediately failed.

## Failure cause

The dominant error was not cost modelling or a missing direction filter.  The
same BTC liquidity event was usually confirmed by both Binance venues.  When it
was not, non-confirmation plus basis state was insufficient to imply either
perpetual mean reversion or spot-led perpetual catch-up after a separate
response.  Cross-venue disagreement is therefore preserved as diagnostic
context but CVPD is not retained as a trading scenario.

## Working parts retained

- exact completed-time synchronization of spot and perpetual bars;
- prior-only robust basis median/MAD classification;
- same-event ambiguity when both venues confirm the break;
- separate response after the initiating divergence;
- NautilusTrader-only execution and whole-NAV three-percent planned-loss sizing.

No sealed week or long evaluation was opened.
