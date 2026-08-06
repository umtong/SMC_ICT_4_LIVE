# Candidate 06 v2.0 Accepted-Auction Defense-Origin Mitigation (ADOM)

The price scenario and next-bar defense are unchanged. Only post-confirmation entry placement changes from market chase to a native passive GTD limit at the completed defense-bar origin.

Implementation status: `PASS`
Terminal status: `FIRST_WEEK_LOGIC_GATE_FAILED`
Selected: none

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|adom_defense_origin_limit|1|True|False|-2.509694%|11|1|9.09%|0.35764757171456135|18.25%|geometric_daily_nav_growth, win_rate, positive_trade_count, profit_concentration, no_runtime_errors|
|adom_market_after_defense_reference|1|False|False|0.940899%|5|2|40.00%|1.6830733587475866|10.07%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|

## Reference regression

`{'passed': True, 'baseline_path': 'artifacts/candidate-06/auction-30m-first-week/auction_30m_directional_defense/metrics.json', 'keys': ['geometric_daily_nav_growth', 'trades', 'wins', 'win_rate', 'profit_factor', 'max_drawdown_nav'], 'differences': {}}`

## Controlled ablation

`{'available': True, 'changed_variable': 'post-defense entry placement and lifetime: passive limit at completed defense-bar origin until fixed-auction expiry versus immediate market entry at defense-bar close', 'unchanged': ['completed 30-minute auction construction', 'SAC displacement and first held retest', 'next completed directional-body defense', 'accepted boundary and structural invalidation', 'structural projection target', 'fixed three-percent planned-loss risk sizing from whole-account NAV', 'Nautilus native bracket, fill, fee, position and NAV accounting', 'one global pending-entry or position slot'], 'full_minus_reference': {'geometric_daily_nav_growth': -0.03450593032046312, 'trades': 6, 'wins': -1, 'win_rate': -0.3090909090909091, 'profit_factor': -1.3254257870330253, 'max_drawdown_nav': 0.08182370305087383}}`

## Diagnoses

- **adom_defense_origin_limit**: `NEGATIVE_COST_AFTER_EXPECTANCY` — filled mitigation entries did not preserve the accepted-auction structural path after costs
  - working: recoverable drawdown gate
  - submitted/expired: `13` / `{'UNFILLED_ENTRY_EXPIRED': 2}`
- **adom_market_after_defense_reference**: `INSUFFICIENT_FILLED_INDEPENDENT_OPPORTUNITIES` — confirmed auctions did not revisit the defense origin often enough before causal expiry
  - working: positive cost-after geometric NAV growth, positive cost-after profit factor, recoverable drawdown gate
  - submitted/expired: `5` / `{}`
