# Candidate 06 Unresolved-Objective Auction Mitigation (UOAM)

Implementation status: `FAIL`
Terminal status: `IMPLEMENTATION_OR_REFERENCE_REGRESSION_FAILURE`
Selected: none

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|uoam_bound_objective_with_causal_exit|1|True|False|-100.000000%|None|None|0.00%|None|0.00%||
|uoam_bound_objective_no_position_exit_ablation|1|True|False|-100.000000%|None|None|0.00%|None|0.00%||
|uoam_dynamic_nearest_hml_reference|1|False|True|1.024347%|10|7|70.00%|1.806344565682457|9.33%||

## Reference regression

`{'passed': True, 'baseline_path': 'artifacts/candidate-06/hml-first-week/hml_60m_5m_swing_equal_full_response/metrics.json', 'keys': ['geometric_daily_nav_growth', 'trades', 'wins', 'win_rate', 'profit_factor', 'max_drawdown_nav', 'net_pnl_after_cost'], 'differences': {}}`

## Diagnoses
- **uoam_bound_objective_with_causal_exit**: `{'classification': 'IMPLEMENTATION_OR_RUNTIME_FAILURE'}`
- **uoam_bound_objective_no_position_exit_ablation**: `{'classification': 'IMPLEMENTATION_OR_RUNTIME_FAILURE'}`
- **uoam_dynamic_nearest_hml_reference**: `{'classification': 'GATE_PASSED', 'geometric_daily_nav_growth': 0.010243468057223204, 'trades': 10, 'wins': 7, 'win_rate': 0.7, 'profit_factor': 1.806344565682457, 'max_drawdown_nav': 0.09329252748844692, 'gate_failures': [], 'objective_bindings': 0, 'objective_consumptions': 0, 'origin_invalidations': 0, 'no_objective_contexts': 0}`
