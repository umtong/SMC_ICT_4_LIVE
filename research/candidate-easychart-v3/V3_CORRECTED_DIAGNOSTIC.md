# Corrected v3 diagnostic and semantic pivot

Development interval: `2024-02-01` through `2024-02-14`, with 30 warm-up days.
Commit `62765a469400f52f78daea773b44a8059fa4d896` repaired the Nautilus partial-parent
lifecycle bug and added an artifact validator.  The earlier apparent gain was invalid:
a remaining limit parent refilled after its protective target had already closed the
first partial position.

## Corrected evidence

- starting NAV: `100,000 USDT`
- final NAV: `58,859.44 USDT`
- total return: `-41.14%`
- daily geometric growth: `-3.715%`
- completed positions: `50`
- submitted plans: `57`
- emitted plans: `471`
- risk-budget breaches: `0`
- lifecycle validator: `PASS`
- independent completed trades/day: `3.57`

Conditional net R:

| diagnostic label | trades | wins | net R |
|---|---:|---:|---:|
| micro repeated-horizontal | 31 | 4 | -18.133 |
| macro repeated-horizontal | 9 | 4 | -0.384 |
| micro sweep/reclaim overlap | 6 | 2 | +2.435 |
| micro touch overlap | 3 | 2 | +0.628 |
| macro touch overlap | 1 | 0 | -0.968 |

These labels are diagnostic, not final strategy families.

## What failed

The corrected result rejects the current decision architecture, not every OB, FVG,
horizontal level or fakeout observation.

1. Cross-timeframe OB/FVG overlap was promoted to global context even though the source
   uses OB/FVG mainly as footprints inside meaningful liquidity and structure.
2. Horizontal fakeout, overlap-touch and overlap-sweep engines independently competed
   to trade what can be the same market episode.  Exact timestamp deduplication was not
   a sufficient causal router.
3. Repeated wick areas were treated as complete horizontal structures without one
   common resolver for direction, range, rejection, acceptance and channel state.
4. A `MICRO` trade used an objective which kept the global account occupied for
   `162.67 hours`; entry, invalidation and target scale were not reliably the same
   intraday auction leg.
5. High planned RR did not rescue poor state classification.  The micro horizontal
   family averaged about `4.70R` planned reward/risk but lost `18.13R` net.

## Decision

Do not tune the failed family thresholds.  Replace the family bundle with one
structure-first policy:

`confirmed wick structure -> pre-existing objective -> interaction -> auction state
(rejection / acceptance / rotation / bounce / unresolved) -> event-local OB/FVG where
required -> first retest -> one immutable plan`

Horizontal pivots, trend lines and exact parallel channels are context objects.
OB and FVG are event-local execution evidence, not unconditional global context.
Macro and micro scales remain observable but overlapping time-trice episodes are one
causal opportunity.  Final performance remains one four-symbol, one-slot continuous
Nautilus account.
