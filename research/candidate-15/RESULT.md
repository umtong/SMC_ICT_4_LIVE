# Candidate 15 V5 timeframe-consistent response initiative

**CANDIDATE15_V5_DEVELOPMENT_REJECTED**

- development_only: `True`
- success_claim: `False`
- weekly_reset_nav_multiple: `0.3961044676`
- daily_geometric_growth: `-0.021808146941102013`
- closed_trades: `61`
- wins / losses: `8 / 53`
- win_rate: `0.13114754098360656`
- payoff_ratio: `4.184466186205397`
- active_intervals: `6`
- closed_trade_path_max_drawdown: `0.7285151154598299`
- initiative_activations: `631`
- response_rejections: `557`

## Interval evidence
- E01 (2021-07-12): daily_geo=0.06028008714783362, trades=8, W/L=4/4, activations=106, response_rejections=99
- E02 (2022-05-09): daily_geo=-0.028479496874950962, trades=12, W/L=2/10, activations=111, response_rejections=89
- E03 (2022-07-25): daily_geo=-0.011672512780311558, trades=10, W/L=2/8, activations=107, response_rejections=86
- E04 (2023-06-20): daily_geo=-0.043739042204996886, trades=10, W/L=0/10, activations=97, response_rejections=106
- E05 (2024-07-15): daily_geo=-0.05100633720296207, trades=11, W/L=0/11, activations=101, response_rejections=93
- E06 (2025-08-11): daily_geo=-0.05173207256764364, trades=10, W/L=0/10, activations=109, response_rejections=84

## Development checks
- all_intervals_present: `True`
- minimum_closed_trades: `True`
- minimum_active_intervals: `True`
- positive_costed_growth: `False`
- minimum_win_rate: `False`
- minimum_payoff_ratio: `True`
- maximum_closed_trade_path_drawdown: `False`
- growth_not_concentrated: `False`
- safety: `False`
- only_response_continuation_submitted: `True`

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
- C15_V5_CORE_FAMILY_QUARANTINED: `17`
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

E01-E06 are exposed controlled-development intervals and cannot support a success claim.
