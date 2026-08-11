# TrendRider + external RAHTF clean-state v3 diagnostic

- eligibility: `EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING`
- mechanically valid: True
- decision: `RAHTF_CLEAN_STATE_HYPOTHESIS_REJECTED_NO_RETUNING`
- thresholds searched: False
- policy-fresh authorized: False
- integration authorized: False
- long evaluation authorized: False

| stage | case | trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| november_winner_development | exact_control | 19 | 8/11 | 3.229479621725664 | 454.5879470589474 | 0.005934930705454811 | 0.08637170994120003 | 0.03345745590708549 |
| november_winner_development | rahtf_clean | 6 | 3/3 | 7.824730844086973 | 618.4144347316667 | 0.002605749694821524 | 0.03710486608389996 | 0.01282493326295997 |
| june_failure_development | exact_control | 16 | 6/10 | 0.6689834026737363 | -63.30779755187499 | -0.0003635373869791403 | -0.01012924760830003 | 0.020118965498078678 |
| june_failure_development | rahtf_clean | 1 | 0/1 | 0.0 | -303.13743548 | -0.00010842191750948249 | -0.003031374354800054 | 0.006655138492346779 |

## Predeclared causal predictions

`{"improvement_not_slot_outlier_dominated": true, "june_best_control_trade_preserved": false, "june_candidate_positive": false, "june_context_not_ready": 0, "june_expectancy_and_pf_improved": false, "june_label_rejections": 39, "june_slow_drift_rejections": 2, "november_candidate_positive": true, "november_winner_engine_preserved": false, "selective_june_early_loss_rejection": false, "state_gate_changed_entries": true}`

The complete external RAHTF fade strategy is not used.  This result measures only whether its frozen clean-trend label and slow-drift confirmation solve the observed TrendRider state error.
