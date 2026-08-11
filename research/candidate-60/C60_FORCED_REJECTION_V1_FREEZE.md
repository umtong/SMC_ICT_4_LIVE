# Candidate 60 — Forced Rejection V1 freeze

## Why this family exists

Candidate 16's frozen liquidation study separated visibly similar liquidation bursts into forced deleveraging, spot-confirmed information repricing, and unresolved states. Its path diagnostic showed that event-time classification alone was not an executable entry. Candidate 60 therefore freezes a strictly later transition owned by the reversal side rather than fading the event bar itself.

This is not a claim that a positive average proves the strategy. The rule was selected because it makes a falsifiable causal prediction: a forced derivatives episode should reverse only after price and spot reacceptance, basis normalization, and continuing contract extinction agree. The same surface rejection without the forced event state should be materially weaker.

## Development evidence used before this freeze

Development source: the immutable Candidate 16 v10 event-time panel from commit `d35fe7c3556a387933103e18d491ab56d2f37c18`, covering the first calendar day of each month from 2021-09 through 2023-12.

The frozen rule produced 14 forced transitions across 13 sample days and all four symbols. Entry was the next minute open and the primary labelled exit was 15 bars later. With a 20 bp round-trip diagnostic cost:

- mean cost-after log return: `+0.00153585`;
- median cost-after log return: `+0.00129241`;
- positive cost-after fraction: `9/14`;
- mean after removing the single best observation: `+0.00090226`;
- exact-opposite mean cost-after log return: `-0.00553585`.

This was not yet a production strategy. ETH was negative in development, a two-event-range emergency boundary gave only modest risk-normalized expectancy, and the family was too sparse to meet the integrated-system frequency target by itself. These weaknesses are preserved rather than hidden.

## Frozen untouched rule

The source event contract is reused unchanged. A primary episode must be classified at event time as one of:

- `FORCED_BASIS_DISLOCATION`;
- `FORCED_OI_DERIVATIVES_LEAD`.

During completed minutes `t=1..3` after the event, select the first minute for which all four strict conditions are true:

1. `reversal_close_vs_event_mid > 0`;
2. `reversal_spot_return_from_event > 0`;
3. `perp_basis_contraction_for_reversal > 0`;
4. `oi_change_from_event < 0`.

The labelled entry observation is the next minute open. The primary outcome is the reversal-side log return to the close at `confirmation_t + 15`, less `0.0020` round-trip diagnostic cost.

No threshold, direction, symbol, regime, confirmation window, exit horizon, or cost may be changed after seeing the 2024-01 through 2025-12 untouched result. The script must also retain unresolved and spot-confirmed events as causal controls and calculate the exact opposite direction.

## Pre-registered interpretation

A positive total alone is insufficient. The mechanism is structurally weakened if mean and median fail together, the mean becomes non-positive after removing the best observation, the exact opposite is not materially worse, results depend on one symbol or one observation, or unresolved controls behave similarly.

Survival of this test permits only the next step: freeze executable invalidation and management and evaluate through NautilusTrader under the four-symbol global single-position constraint, continuous NAV, current-NAV 3% planned loss, and realistic costs. Failure does not imply that every component is useless; event classification, transition timing, and each losing state must be diagnosed separately before deciding what to preserve.
