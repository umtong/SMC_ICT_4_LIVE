# Candidate 06 Unresolved-Objective Auction Mitigation (UOAM)

Implementation status: `PASS`
Terminal status: `FIRST_WEEK_LOGIC_GATE_FAILED`
Selected: none

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|uoam_bound_objective_with_causal_exit|1|True|False|0.000000%|0|0|0.00%|None|0.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|uoam_bound_objective_no_position_exit_ablation|1|True|False|0.000000%|0|0|0.00%|None|0.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|uoam_dynamic_nearest_hml_reference|1|False|True|1.024347%|10|7|70.00%|1.806344565682457|9.33%||

## Reference regression

`{'passed': True, 'baseline_path': 'artifacts/candidate-06/hml-first-week/hml_60m_5m_swing_equal_full_response/metrics.json', 'keys': ['geometric_daily_nav_growth', 'trades', 'wins', 'win_rate', 'profit_factor', 'max_drawdown_nav', 'net_pnl_after_cost'], 'differences': {}}`

## Diagnoses
- **uoam_bound_objective_with_causal_exit**: `{'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': 0.0, 'trades': 0, 'wins': 0, 'win_rate': 0.0, 'profit_factor': None, 'max_drawdown_nav': 0.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'objective_bindings': 2, 'objective_consumptions': 0, 'origin_invalidations': 0, 'no_objective_contexts': 16}`
- **uoam_bound_objective_no_position_exit_ablation**: `{'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': 0.0, 'trades': 0, 'wins': 0, 'win_rate': 0.0, 'profit_factor': None, 'max_drawdown_nav': 0.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'objective_bindings': 2, 'objective_consumptions': 0, 'origin_invalidations': 0, 'no_objective_contexts': 16}`
- **uoam_dynamic_nearest_hml_reference**: `{'classification': 'GATE_PASSED', 'geometric_daily_nav_growth': 0.010243468057223204, 'trades': 10, 'wins': 7, 'win_rate': 0.7, 'profit_factor': 1.806344565682457, 'max_drawdown_nav': 0.09329252748844692, 'gate_failures': [], 'objective_bindings': 0, 'objective_consumptions': 0, 'origin_invalidations': 0, 'no_objective_contexts': 0}`
