# Candidate 06 v6.0 Adaptive Flow-Impact Bifurcation

Terminal status: `FIRST_WEEK_LOGIC_GATE_FAILED`
Selected: none

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|mean R|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|afib_full_bifurcation|1|True|False|-0.375202%|1|0|0.00%|0.0|-0.866|2.60%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|afib_absorbed_reversal_only|1|True|False|0.000000%|0|0|0.00%|None|0.000|0.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|afib_efficient_continuation_only|1|True|False|-0.375202%|1|0|0.00%|0.0|-0.866|2.60%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|afib_raw_flow_reference|1|False|False|-16.818246%|104|31|29.81%|0.4136639147080526|-0.399|73.12%|geometric_daily_nav_growth, win_rate, max_drawdown|

## Diagnoses

- **afib_full_bifurcation**: `NEGATIVE_COST_AFTER_EXPECTANCY` — `{'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.003752024634036455, 'trades': 1, 'wins': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'max_drawdown_nav': 0.025970382745800016, 'mean_r_after_cost': -0.8656794248599999, 'scenario_breakdown': {'AFIB_CONTINUATION': {'mean_r': -0.8656794248599999, 'pnl_after_cost': -2597.03827458, 'trades': 1, 'win_rate': 0.0, 'wins': 0}}, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'NET_REWARD_RISK_ERODED_AFTER_DELAY': 6}}`
- **afib_absorbed_reversal_only**: `NO_COMPLETED_FLOW_IMPACT_RESPONSE` — `{'classification': 'NO_COMPLETED_FLOW_IMPACT_RESPONSE', 'geometric_daily_nav_growth': 0.0, 'trades': 0, 'wins': 0, 'win_rate': 0.0, 'profit_factor': None, 'max_drawdown_nav': 0.0, 'mean_r_after_cost': 0.0, 'scenario_breakdown': {}, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {}}`
- **afib_efficient_continuation_only**: `NEGATIVE_COST_AFTER_EXPECTANCY` — `{'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.003752024634036455, 'trades': 1, 'wins': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'max_drawdown_nav': 0.025970382745800016, 'mean_r_after_cost': -0.8656794248599999, 'scenario_breakdown': {'AFIB_CONTINUATION': {'mean_r': -0.8656794248599999, 'pnl_after_cost': -2597.03827458, 'trades': 1, 'win_rate': 0.0, 'wins': 0}}, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'NET_REWARD_RISK_ERODED_AFTER_DELAY': 6}}`
- **afib_raw_flow_reference**: `NEGATIVE_COST_AFTER_EXPECTANCY` — `{'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.1681824634229988, 'trades': 104, 'wins': 31, 'win_rate': 0.2980769230769231, 'profit_factor': 0.4136639147080526, 'max_drawdown_nav': 0.7312214027483, 'mean_r_after_cost': -0.39896427950433916, 'scenario_breakdown': {'AFIB_CONTINUATION': {'mean_r': -0.39755063527857165, 'pnl_after_cost': -53104.16959729, 'trades': 74, 'win_rate': 0.2972972972972973, 'wins': 22}, 'AFIB_REVERSAL': {'mean_r': -0.40245126859456576, 'pnl_after_cost': -19341.07855708, 'trades': 30, 'win_rate': 0.3, 'wins': 9}}, 'gate_failures': ['geometric_daily_nav_growth', 'win_rate', 'max_drawdown'], 'entry_abstentions': {'NET_REWARD_RISK_ERODED_AFTER_DELAY': 294}}`

## Fixed causal contract

- Signed aggressive flow is normalized only with completed prior minutes.
- The initiating flow shock cannot emit an order.
- Efficient impact and absorbed impact are mutually classified before confirmation.
- A separate completed response is mandatory for continuation or reversal.
- All orders, fills, fees, margin, positions and NAV are native NautilusTrader outputs.
- Approved trades risk three percent of whole-account NAV after explicit costs.
