# Candidate 06 v5.3 Pressure-Gated HML

Terminal status: `IMPLEMENTATION_OR_REFERENCE_REGRESSION_FAILURE`
Selected: none
HML reference regression: `{'passed': False, 'differences': {'geometric_daily_nav_growth': {'expected': 0.010243468057223204, 'actual': 0.008575653298273922}, 'trades': {'expected': 10, 'actual': 4}, 'wins': {'expected': 7, 'actual': 3}, 'win_rate': {'expected': 0.7, 'actual': 0.75}, 'profit_factor': {'expected': 1.806344565682457, 'actual': 2.948211659160862}, 'max_drawdown_nav': {'expected': 0.09329252748844692, 'actual': 0.06829194955989941}}}`

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|phml_gate_and_exit|1|True|False|-0.028691%|1|0|0.00%|0.0|1.01%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|phml_gate_only_ablation|1|False|False|0.142511%|1|1|100.00%|None|2.54%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|phml_exit_only_ablation|1|False|False|-0.411446%|5|2|40.00%|0.5422716386636941|10.67%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|phml_hml_reference|1|False|False|0.857565%|4|3|75.00%|2.948211659160862|6.83%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
