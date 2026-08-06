# Candidate 06 v2.1 Sequential Impact Persistence Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: none

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|sipr_full|0|False|0.000000%|0|0.00%|None|0.00%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|sipr_sequence_only_ablation|0|False|0.000000%|0|0.00%|None|0.00%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|sipr_impact_only_ablation|0|False|-1.297107%|3|0.00%|0.0|8.73%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|sipr_raw_15m_reference|0|False|-1.725634%|4|0.00%|0.0|11.47%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
