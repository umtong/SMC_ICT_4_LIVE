# Candidate 16 v9 — residual-state role separation

## Objective

The project needs a high-opportunity, cost-positive day-trading family that can
coexist inside one four-symbol shared account. Candidate 05 v52 found frequent
robust cross-sectional residual extremes, but the inherited state bar required
residual inflection, non-expanding OI, tail-flow acceleration, displayed-depth
alignment, path efficiency and notional burst at once. Candidate 16 v8 then
required a separate later residual/price/flow/depth transition. In the first
untouched week, three residual inflections passed OI but none passed the state
microstructure gate, so the later transition was never tested.

## One changed causal claim

**Control v8**

`residual extreme → inflection → OI non-expansion → same-bar microstructure gate → freeze → later transition`

**Variant v9**

`residual extreme → inflection → OI non-expansion → freeze → later microstructure transition`

Flow, depth, efficiency and burst on the state bar are retained as diagnostics.
They cannot admit or reject a v9 state. Everything after state freezing is
inherited unchanged from v8.

## Predictions before execution

1. v9 should freeze OI-qualified residual episodes that v8 discards before its
   transition.
2. No order may be created on the state bar.
3. Some added states may expire or neutralize without entry; that is evidence
   about the later transition, not an implementation failure.
4. If states appear but no later confirmation occurs, the next uncertainty is
   which independent transition condition fails, not whether to lower the
   residual threshold.
5. If later confirmations occur but no order opens, natural-target geometry or
   FOK execution is the next bottleneck.
6. If trades occur, every winner, loser and missed episode must be reviewed.
   Aggregate profit cannot validate the role claim by itself.
7. Any non-residual entry, same-timestamp peer use, global-slot violation,
   liquidation or account-integrity failure invalidates the experiment.

## Development periods

The paired run reuses four already-inspected diagnostic weeks and therefore is
not holdout evidence:

- 2023-07-09 through 2023-07-15
- 2023-09-08 through 2023-09-14
- 2024-01-15 through 2024-01-21
- 2024-03-18 through 2024-03-24

A new untouched period is allowed only after the state/transition structure is
fixed from this diagnosis.
