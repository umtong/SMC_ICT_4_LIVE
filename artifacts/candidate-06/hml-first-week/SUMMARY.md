# Candidate 06 v1.6 Hierarchical Swing and Equal-Liquidity Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `hml_60m_5m_swing_equal_full_response`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|hml_60m_5m_swing_equal_all_flow|0|False|1.173696%|5|80.00%|3.5361014308317285|6.36%|36.76%|trade_count, positive_trade_count|
|hml_60m_5m_swing_equal_bias_response|0|False|0.585716%|11|63.64%|1.3367219679061373|9.33%|26.33%|geometric_daily_nav_growth|
|hml_45m_5m_swing_equal_bias_response|0|False|-1.079080%|13|46.15%|0.6477574696515191|12.79%|31.46%|geometric_daily_nav_growth|
|hml_60m_5m_equal_only_bias_response|0|False|-1.019843%|4|25.00%|0.22237958790124834|10.09%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|hml_60m_5m_swing_equal_full_response|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||
|hml_60m_5m_swing_only_reference|0|False|1.464847%|9|77.78%|2.6350866049008785|6.36%|25.26%|trade_count|

## Frozen week 2

- gate: `False`
- geometric daily NAV growth: `-0.04798367281420002`
- trades: `19`
- win rate: `0.21052631578947367`
- maximum drawdown: `0.3115782623623`
- failures: `['geometric_daily_nav_growth', 'win_rate', 'max_drawdown', 'positive_trade_count', 'profit_concentration']`

## Frozen week 3

- gate: `False`
- geometric daily NAV growth: `-0.004132266888758118`
- trades: `2`
- win rate: `0.5`
- maximum drawdown: `0.04321290136809999`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'positive_trade_count', 'profit_concentration']`
