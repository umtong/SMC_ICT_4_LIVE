# Candidate 06 v1.8 Adaptive-Fresh Hierarchical Liquidity Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `afhr_freshness_only_ablation`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|afhr_full|1|None|-100.000000%|None|0.00%|None|0.00%|100.00%||
|afhr_quality_only_ablation|1|None|-100.000000%|None|0.00%|None|0.00%|100.00%||
|afhr_freshness_only_ablation|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||
|afhr_parent_hml_reference|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||

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

## Structural independence diagnostic

Bias-context concentration is reported separately. It informs causal interpretation and later validation but is not an arbitrary first-week hard gate.

- `afhr_full`: contexts=0, largest context share=100.00%, selection gate=False
- `afhr_quality_only_ablation`: contexts=0, largest context share=100.00%, selection gate=False
- `afhr_freshness_only_ablation`: contexts=3, largest context share=40.00%, selection gate=True
- `afhr_parent_hml_reference`: contexts=3, largest context share=40.00%, selection gate=True
