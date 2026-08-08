# Candidate 15 V4 persistent cross-market initiative

**CANDIDATE15_V4_DEVELOPMENT_REJECTED**

- development_only: `True`
- success_claim: `False`
- continuous_account_evidence: `False`
- weekly_reset_nav_multiple: `0.4445051557`
- daily_geometric_growth: `-0.019119469406366066`
- closed_trades: `104`
- wins / losses: `17 / 87`
- win_rate: `0.16346153846153846`
- payoff_ratio: `4.214434084721173`
- active_intervals: `6`
- closed_trade_path_max_drawdown: `0.7210205412711269`
- maximum_positive_log_growth_share_from_one_interval: `0.8784891865996702`
- module_counts: `{'PERSISTENT_QH_MSS_FVG_CONTINUATION': 177}`

## Interval evidence
- E01 (2021-07-12): daily_geo=0.05355510544044858, trades=19, W/L=5/14, initiative_activations=177, continuation_plans=111
- E02 (2022-05-09): daily_geo=-0.00013681884873088123, trades=9, W/L=2/7, initiative_activations=170, continuation_plans=118
- E03 (2022-07-25): daily_geo=-0.0708026149584505, trades=25, W/L=3/22, initiative_activations=180, continuation_plans=139
- E04 (2023-06-20): daily_geo=-0.021231476076162534, trades=14, W/L=1/13, initiative_activations=171, continuation_plans=138
- E05 (2024-07-15): daily_geo=0.007242181472998862, trades=18, W/L=5/13, initiative_activations=168, continuation_plans=132
- E06 (2025-08-11): daily_geo=-0.07705254739870067, trades=19, W/L=1/18, initiative_activations=149, continuation_plans=175

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
- only_v4_module_submitted: `True`

## Highest-volume diagnostic skips
- QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE: `43040`
- QHI_CONTINUATION_MSS_DISPLACEMENT_INCOMPLETE: `18307`
- QHI_CONTINUATION_BAR_NOT_AFTER_ACTIVATION: `4036`
- QHI_COMMON_FLOW_BREADTH_BELOW_THREE: `2804`
- QHI_CONTINUATION_STRICT_FVG_ABSENT: `2318`
- SESSION_DECISION_WINDOW_EXPIRED: `739`
- QHI_CONTINUATION_WARMUP: `256`
- QHI_CONTINUATION_EXTERNAL_TARGET_ABSENT: `250`
- SWEEP_ACTIVITY_OR_PENETRATION: `161`
- NO_AGGRESSOR_FLOW_AT_SWEEP: `157`
- FRAMED_TARGET_REACHED_BEFORE_CONFIRMATION: `123`
- QHI_CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R: `76`
- LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT: `21`
- C15_V4_CORE_FAMILY_QUARANTINED: `17`
- HIGH_RECLAIM_LACKED_DISPLACEMENT: `14`
- QHI_WARMUP_OR_INCOMPLETE_WINDOW: `12`
- INSUFFICIENT_COSTED_STRUCTURAL_R: `12`
- NO_CAUSAL_INTERNAL_STRUCTURE: `11`
- FVG_RETEST_NOT_EXECUTABLE: `7`
- HIGH_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED: `7`
- REVERSAL_TARGET_NO_LONGER_LIVE: `7`
- AMBIGUOUS_EXTERNAL_DRAW: `6`
- LOW_REJECTION_LACKED_BULLISH_MSS: `6`
- OUTSIDE_EVALUATION_WINDOW: `5`

E01-E06 are exposed mechanism-development intervals. This result cannot support a success claim.
