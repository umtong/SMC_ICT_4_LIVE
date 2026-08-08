# Candidate 15 V8 managed residual information transfer

**CANDIDATE15_V8_INSUFFICIENT_ACTIVITY**

- development_only: `True`
- success_claim: `False`
- weekly_reset_nav_multiple: `0.992702955253653`
- daily_geometric_growth: `-0.00017436094986819495`
- closed_trades: `11`
- wins / losses: `1 / 10`
- win_rate: `0.09090909090909091`
- payoff_ratio: `11.108692485639239`
- active_intervals: `5`
- closed_trade_path_max_drawdown: `0.11436890687498723`
- submitted_managed_transfer_plans: `22`
- transfer_stage_counts: `{'PARITY_HANDOFF_RETEST': 14, 'PARTIAL_CATCH_UP': 8}`
- transfer_completions: `5`
- protection_actions: `5`
- management_fail_closed_count: `6`
- route_violations: `0`

## Interval evidence
- E01 (2021-07-12): daily_geo=-8.3208667137977e-05, trades=1, W/L=0/1, transfer_states=55, stage_rejections=3, protect=1
- E02 (2022-05-09): daily_geo=-6.492520976600198e-05, trades=2, W/L=0/2, transfer_states=47, stage_rejections=0, protect=2
- E03 (2022-07-25): daily_geo=-0.008703633558645599, trades=3, W/L=0/3, transfer_states=49, stage_rejections=2, protect=1
- E04 (2023-06-20): daily_geo=0.0, trades=0, W/L=0/0, transfer_states=46, stage_rejections=4, protect=0
- E05 (2024-07-15): daily_geo=0.026379195798359845, trades=1, W/L=1/0, transfer_states=73, stage_rejections=5, protect=1
- E06 (2025-08-11): daily_geo=-0.018029145778253826, trades=4, W/L=0/4, transfer_states=55, stage_rejections=3, protect=0

## Development checks
- all_intervals_present: `True`
- minimum_closed_trades: `False`
- minimum_active_intervals: `True`
- positive_costed_growth: `False`
- minimum_win_rate: `False`
- minimum_payoff_ratio: `True`
- maximum_closed_trade_path_drawdown: `True`
- growth_not_concentrated: `False`
- safety: `False`
- only_managed_transfer_submitted: `True`
- management_integrity: `False`

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
- QHI_V8_PLAN_NOT_STATE_RESIDUAL: `145`
- FRAMED_TARGET_REACHED_BEFORE_CONFIRMATION: `123`
- QHI_WARMUP_OR_INCOMPLETE_WINDOW: `60`
- QHI_CONTINUATION_EXTERNAL_TARGET_ABSENT: `44`
- LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT: `21`
- QHI_CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R: `21`
- C15_V8_CORE_FAMILY_QUARANTINED: `17`
- QHI_V8_TRANSFER_STAGE_UNRESOLVED: `17`
- HIGH_RECLAIM_LACKED_DISPLACEMENT: `14`
- INSUFFICIENT_COSTED_STRUCTURAL_R: `12`
- NO_CAUSAL_INTERNAL_STRUCTURE: `11`
- FVG_RETEST_NOT_EXECUTABLE: `7`
- HIGH_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- REVERSAL_TARGET_NO_LONGER_LIVE: `7`
- AMBIGUOUS_EXTERNAL_DRAW: `6`

E01-E06 are exposed controlled-development intervals. V8 can only reject or improve the managed-transfer mechanism; it cannot support a success claim.
