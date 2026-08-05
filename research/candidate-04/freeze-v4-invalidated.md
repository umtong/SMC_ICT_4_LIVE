# Candidate-04 v4 invalidation

`freeze-v4-swing-displacement.json` is **not valid performance evidence** and must not be used to promote this candidate.

The invalidation was recorded before opening `reserve-2` (2024-08-07..2024-08-13).

## Causal defect

The v4 diagnostic allowed a newly confirmed swing pool to inherit a second-touch qualification before that touch was actually observable by the strategy. It also allowed a weak first break of the fixed pre-sweep structure to remain pending until a later, stronger bar. Together these rules could:

1. make a liquidity pool tradable too early; and
2. replace the first causal acceptance/rejection verdict with a later price-chasing confirmation.

Both defects concern event ordering, not parameter quality. The v4 result was therefore discarded rather than tuned.

## Correction in v5

- A pivot becomes usable only after all right-hand confirmation bars close.
- Pool touches are counted only when they are observable after pool registration.
- The first meaningful penetration consumes the pool, whether it becomes a trade or not.
- The first close through the fixed pre-sweep structure is the quality verdict. A weak first break invalidates that setup; a later stronger bar cannot resurrect it.
- Cross-day rolling notional and open-interest changes are recomputed after concatenating daily files, so UTC midnight cannot reset the state.

No data from `reserve-2` or `reserve-3` was opened while making this correction.
