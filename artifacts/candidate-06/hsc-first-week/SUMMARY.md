# Candidate 06 v1.3 Hierarchical Liquidity Sweep Continuation

Selection uses fixed causal priority rather than maximum backtest return.

Selected: none

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|hsc_30m_5m_sweep_break|0|False|-0.432490%|5|40.00%|0.6713430887928845|6.47%|69.75%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|hsc_30m_5m_last_break|0|False|-0.689246%|4|25.00%|0.4739262803682892|7.01%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|hsc_60m_5m_sweep_break|0|False|0.276553%|4|50.00%|1.3164449625451775|8.35%|53.72%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|hsc_30m_15m_sweep_break|0|False|0.000000%|0|0.00%|None|0.00%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|hsc_30m_5m_price_ablation|0|False|-1.951785%|12|33.33%|0.43631814994934515|12.89%|44.55%|geometric_daily_nav_growth, win_rate, positive_trade_count, profit_concentration|
