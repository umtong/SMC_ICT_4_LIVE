# Candidate 06 v1.8 Passive-Liquidity Vacuum / Replenishment Response

The passive-liquidity detector and trading scenario are separate. The initial pool breach is provisional; a later retest and a separate response are mandatory.

Implementation status: `PASS`
Data status: `PASS`
Selected: none

|variant|week|eligible|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|dlvr_passive_liquidity_bifurcation|1|True|0|False|-3.115024%|11|18.18%|0.17857085275289164|20.50%|65.93%|geometric_daily_nav_growth, win_rate, positive_trade_count, profit_concentration|
|dlvr_vacuum_continuation_only|1|True|0|False|-0.866575%|2|0.00%|0.0|8.80%|100.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|dlvr_price_only_ablation|1|False|0|False|-8.331679%|22|4.55%|0.0465693226228401|45.61%|100.00%|geometric_daily_nav_growth, win_rate, max_drawdown, positive_trade_count, profit_concentration|

## Controlled ablation

- interpretation: `PASSIVE_LIQUIDITY_CONFIRMATION_IMPROVED_COST_AFTER_EXPECTANCY`
- delta full minus ablation: `{'geometric_daily_nav_growth': 0.05216654987001956, 'win_rate': 0.13636363636363635, 'trades': -11}`

## Candidate diagnoses

- **dlvr_passive_liquidity_bifurcation**: `NEGATIVE_COST_AFTER_EXPECTANCY` — direction/timing classification produced losses after fees and one-tick slippage
  - working: NAV drawdown remained within the recoverable gate
- **dlvr_vacuum_continuation_only**: `NEGATIVE_COST_AFTER_EXPECTANCY` — direction/timing classification produced losses after fees and one-tick slippage
  - working: NAV drawdown remained within the recoverable gate
- **dlvr_price_only_ablation**: `NEGATIVE_COST_AFTER_EXPECTANCY` — direction/timing classification produced losses after fees and one-tick slippage
