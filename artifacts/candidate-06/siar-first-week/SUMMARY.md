# Candidate 06 v1.9 Surprise-Impact Acceptance Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `siar_surprise_only_ablation`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|siar_full|0|False|0.630639%|1|100.00%|None|0.43%|100.00%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|siar_surprise_only_ablation|0|True|1.661431%|11|72.73%|2.333189704729081|9.33%|22.58%||
|siar_impact_only_ablation|0|False|0.134451%|4|50.00%|1.1543881571257493|5.93%|63.41%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|siar_freshness_reference|0|True|1.220153%|12|66.67%|1.7067560092238874|9.33%|22.58%||

## Frozen week 2

- gate: `False`
- geometric daily NAV growth: `-0.03966197194649623`
- trades: `17`
- win rate: `0.23529411764705882`
- maximum drawdown: `0.2902925449814`
- failures: `['geometric_daily_nav_growth', 'win_rate', 'max_drawdown', 'positive_trade_count', 'profit_concentration']`

## Frozen week 3

- gate: `False`
- geometric daily NAV growth: `-0.004132266888758118`
- trades: `2`
- win rate: `0.5`
- maximum drawdown: `0.04321290136809999`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'positive_trade_count', 'profit_concentration']`
