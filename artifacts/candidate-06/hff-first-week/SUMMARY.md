# Candidate 06 v1.5 Hierarchical Flow-Stage Factorization

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `hff_bias_only_flow`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|hff_all_flow|0|False|1.173696%|5|80.00%|3.5361014308317285|6.36%|36.76%|trade_count, positive_trade_count|
|hff_bias_response_flow|0|False|1.464847%|9|77.78%|2.6350866049008785|6.36%|25.26%|trade_count|
|hff_sweep_response_flow|0|False|0.734533%|6|66.67%|1.7951815137762517|8.02%|36.76%|geometric_daily_nav_growth, trade_count, positive_trade_count|
|hff_bias_sweep_flow|0|False|0.734568%|6|66.67%|1.8192280326533414|6.83%|37.37%|geometric_daily_nav_growth, trade_count, positive_trade_count|
|hff_bias_only_flow|0|True|1.024414%|10|70.00%|1.765695684215182|6.73%|25.58%||
|hff_response_only_flow|0|False|0.585931%|11|63.64%|1.3187726254121115|10.35%|25.26%|geometric_daily_nav_growth|
|hff_all_price_reference|0|False|0.149313%|12|58.33%|1.0656045478249423|10.35%|25.58%|geometric_daily_nav_growth|

## Frozen week 2

- gate: `False`
- geometric daily NAV growth: `-0.03974806762291272`
- trades: `15`
- win rate: `0.2`
- maximum drawdown: `0.24971495520480005`
- failures: `['geometric_daily_nav_growth', 'win_rate', 'positive_trade_count', 'profit_concentration']`

## Frozen week 3

- gate: `False`
- geometric daily NAV growth: `-0.004341949456677652`
- trades: `1`
- win rate: `0.0`
- maximum drawdown: `0.030000595747600018`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration']`
