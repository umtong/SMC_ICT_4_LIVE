# Candidate 06 v1.8 Adaptive-Fresh Hierarchical Liquidity Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: `afhr_full`

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|afhr_full|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||
|afhr_quality_only_ablation|0|False|-0.293942%|15|53.33%|0.9015125586273351|16.10%|23.37%|geometric_daily_nav_growth|
|afhr_freshness_only_ablation|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||
|afhr_parent_hml_reference|0|True|1.024347%|10|70.00%|1.806344565682457|9.33%|26.33%||

## Frozen week 2

- gate: `False`
- geometric daily NAV growth: `-0.0016507804224638045`
- trades: `5`
- win rate: `0.4`
- maximum drawdown: `0.06869840320239994`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration']`

## Frozen week 3

- gate: `False`
- geometric daily NAV growth: `-0.004132266888758118`
- trades: `2`
- win rate: `0.5`
- maximum drawdown: `0.04321290136809999`
- failures: `['geometric_daily_nav_growth', 'trade_count', 'positive_trade_count', 'profit_concentration']`

## Structural independence diagnostic

Bias-context concentration is reported separately. It informs causal interpretation and later validation but is not an arbitrary first-week hard gate.

- `afhr_full`: contexts=3, largest context share=40.00%, selection gate=True
- `afhr_quality_only_ablation`: contexts=3, largest context share=53.33%, selection gate=False
- `afhr_freshness_only_ablation`: contexts=3, largest context share=40.00%, selection gate=True
- `afhr_parent_hml_reference`: contexts=3, largest context share=40.00%, selection gate=True
