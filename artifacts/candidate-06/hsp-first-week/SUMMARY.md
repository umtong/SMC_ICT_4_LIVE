# Candidate 06 v1.4 Hierarchical Confirmed Swing-Pool Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `hsp_60m_5m_swing_price_ablation`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|hsp_60m_5m_swing_break|0|False|0.857565%|4|75.00%|2.948211659160862|6.83%|46.80%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|hsp_60m_5m_swing_last|0|False|1.173696%|5|80.00%|3.5361014308317285|6.36%|36.76%|trade_count, positive_trade_count|
|hsp_60m_10m_swing_break|0|False|0.403286%|1|100.00%|None|2.04%|100.00%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|hsp_30m_5m_swing_break|0|False|-2.438623%|16|31.25%|0.45517318465147405|22.10%|32.11%|geometric_daily_nav_growth, win_rate|
|hsp_60m_5m_previous_bucket_reference|0|False|0.857486%|4|75.00%|2.9476636487643626|6.83%|46.80%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|hsp_60m_5m_swing_price_ablation|0|True|1.024430%|10|70.00%|1.7657174486705915|6.73%|25.58%||

## Frozen week 2

- gate: `False`
- geometric daily NAV growth: `-0.023117241162476265`
- trades: `10`
- win rate: `0.2`
- maximum drawdown: `0.20023842188790003`
- failures: `['geometric_daily_nav_growth', 'win_rate', 'positive_trade_count', 'profit_concentration']`

## Frozen week 3

- gate: `False`
- geometric daily NAV growth: `-0.01725676506220364`
- trades: `4`
- win rate: `0.0`
- maximum drawdown: `0.13764701587892925`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration']`
