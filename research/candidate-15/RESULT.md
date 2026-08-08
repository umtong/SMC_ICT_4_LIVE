# Candidate 15 V6 residual-laggard delivery

**CANDIDATE15_V6_DEVELOPMENT_REJECTED**

- development_only: `True`
- success_claim: `False`
- weekly_reset_nav_multiple: `1.116771367223049`
- daily_geometric_growth: `0.0026330273548270965`
- closed_trades: `23`
- wins / losses: `5 / 18`
- win_rate: `0.21739130434782608`
- payoff_ratio: `5.374907349598715`
- active_intervals: `6`
- closed_trade_path_max_drawdown: `0.3124826205116817`
- initiative_activations: `631`
- response_rejections: `557`
- accepted_market_plan_rejections: `362`
- residual_route_violations: `0`

## Interval evidence
- E01 (2021-07-12): daily_geo=0.07056212436187694, trades=3, W/L=3/0, activations=106, accepted_rejections=50
- E02 (2022-05-09): daily_geo=-0.022283706606175978, trades=5, W/L=0/5, activations=111, accepted_rejections=64
- E03 (2022-07-25): daily_geo=-0.008691346451139274, trades=4, W/L=1/3, activations=107, accepted_rejections=56
- E04 (2023-06-20): daily_geo=-0.004580716983644924, trades=1, W/L=0/1, activations=97, accepted_rejections=57
- E05 (2024-07-15): daily_geo=0.006072058117362168, trades=5, W/L=1/4, activations=101, accepted_rejections=58
- E06 (2025-08-11): daily_geo=-0.022350286345266614, trades=5, W/L=0/5, activations=109, accepted_rejections=77

## Development checks
- all_intervals_present: `True`
- minimum_closed_trades: `True`
- minimum_active_intervals: `True`
- positive_costed_growth: `True`
- minimum_win_rate: `False`
- minimum_payoff_ratio: `True`
- maximum_closed_trade_path_drawdown: `False`
- growth_not_concentrated: `False`
- safety: `False`
- only_residual_laggard_submitted: `True`

## Highest-volume diagnostic skips
- QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE: `58856`
- QHI_CONTINUATION_MSS_DISPLACEMENT_INCOMPLETE: `6263`
- QHI_COMMON_FLOW_BREADTH_BELOW_THREE: `3212`
- QHI_CONTINUATION_BAR_NOT_AFTER_ACTIVATION: `2524`
- QHI_CONTINUATION_STRICT_FVG_ABSENT: `839`
- SESSION_DECISION_WINDOW_EXPIRED: `739`
- QHI_V5_SAME_DIRECTION_EVENT_LACKED_PERSISTENT_RESPONSE: `557`
- SWEEP_ACTIVITY_OR_PENETRATION: `161`
- NO_AGGRESSOR_FLOW_AT_SWEEP: `157`
- FRAMED_TARGET_REACHED_BEFORE_CONFIRMATION: `123`
- QHI_CONTINUATION_EXTERNAL_TARGET_ABSENT: `120`
- QHI_WARMUP_OR_INCOMPLETE_WINDOW: `60`
- QHI_CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R: `45`
- LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT: `21`
- C15_V6_CORE_FAMILY_QUARANTINED: `17`
- HIGH_RECLAIM_LACKED_DISPLACEMENT: `14`
- INSUFFICIENT_COSTED_STRUCTURAL_R: `12`
- NO_CAUSAL_INTERNAL_STRUCTURE: `11`
- FVG_RETEST_NOT_EXECUTABLE: `7`
- HIGH_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- REVERSAL_TARGET_NO_LONGER_LIVE: `7`
- AMBIGUOUS_EXTERNAL_DRAW: `6`
- LOW_REJECTION_LACKED_BULLISH_MSS: `6`
- OUTSIDE_EVALUATION_WINDOW: `5`

E01-E06 are exposed controlled-development intervals. V6 may only reject or improve the residual-delivery mechanism; it cannot support a success claim.
