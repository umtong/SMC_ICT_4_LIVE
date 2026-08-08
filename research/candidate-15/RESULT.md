# Candidate 15 V7 bounded residual information transfer

**CANDIDATE15_V7_INSUFFICIENT_ACTIVITY**

- development_only: `True`
- success_claim: `False`
- weekly_reset_nav_multiple: `1.0386173287064`
- daily_geometric_growth: `0.0009025579406663048`
- closed_trades: `1`
- wins / losses: `1 / 0`
- win_rate: `1.0`
- payoff_ratio: `inf`
- active_intervals: `1`
- closed_trade_path_max_drawdown: `0.0`
- initiative_activations: `670`
- response_rejections: `557`
- submitted_bounded_transfer_plans: `2`
- bounded_transfer_route_violations: `0`

## Interval evidence
- E01 (2021-07-12): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=55, geometry_rejections=5
- E02 (2022-05-09): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=47, geometry_rejections=5
- E03 (2022-07-25): daily_geo=0.005427581521168759, trades=1, W/L=1/0, transfer_states=49, geometry_rejections=7
- E04 (2023-06-20): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=46, geometry_rejections=7
- E05 (2024-07-15): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=73, geometry_rejections=9
- E06 (2025-08-11): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=55, geometry_rejections=11

## Development checks
- all_intervals_present: `True`
- minimum_closed_trades: `False`
- minimum_active_intervals: `False`
- positive_costed_growth: `True`
- minimum_win_rate: `True`
- minimum_payoff_ratio: `True`
- maximum_closed_trade_path_drawdown: `True`
- growth_not_concentrated: `False`
- safety: `True`
- only_bounded_transfer_submitted: `True`

## Highest-volume diagnostic skips
- QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE: `64324`
- QHI_COMMON_FLOW_BREADTH_BELOW_THREE: `3212`
- QHI_CONTINUATION_MSS_DISPLACEMENT_INCOMPLETE: `2864`
- QHI_CONTINUATION_BAR_NOT_AFTER_ACTIVATION: `1300`
- SESSION_DECISION_WINDOW_EXPIRED: `739`
- QHI_V5_SAME_DIRECTION_EVENT_LACKED_PERSISTENT_RESPONSE: `557`
- QHI_CONTINUATION_STRICT_FVG_ABSENT: `352`
- SWEEP_ACTIVITY_OR_PENETRATION: `161`
- NO_AGGRESSOR_FLOW_AT_SWEEP: `157`
- QHI_V7_PLAN_NOT_STATE_RESIDUAL: `145`
- FRAMED_TARGET_REACHED_BEFORE_CONFIRMATION: `123`
- QHI_WARMUP_OR_INCOMPLETE_WINDOW: `60`
- QHI_CONTINUATION_EXTERNAL_TARGET_ABSENT: `44`
- QHI_V7_BOUNDED_TRANSFER_GEOMETRY_UNRESOLVED: `44`
- LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT: `21`
- QHI_CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R: `21`
- C15_V7_CORE_FAMILY_QUARANTINED: `17`
- HIGH_RECLAIM_LACKED_DISPLACEMENT: `14`
- INSUFFICIENT_COSTED_STRUCTURAL_R: `12`
- NO_CAUSAL_INTERNAL_STRUCTURE: `11`
- FVG_RETEST_NOT_EXECUTABLE: `7`
- HIGH_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- REVERSAL_TARGET_NO_LONGER_LIVE: `7`
- AMBIGUOUS_EXTERNAL_DRAW: `6`

E01-E06 are exposed controlled-development intervals. V7 can only reject or improve the bounded-transfer mechanism; it cannot support a success claim.
