# UOAM Temporal-Preexistence Ablation

Terminal status: `TEMPORAL_ABLATION_FIRST_WEEK_LOGIC_FAILED`
Selected: none

|variant|week|gate|geom/day|trades|wins|win rate|PF|max DD|bindings|no-objective|
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
|uoam_strict_confirmed_before_acceptance_reference|1|False|0.000000%|0|0|0.00%|None|0.00%|2|16|
|uoam_source_preexists_confirm_by_acceptance_end|1|False|0.000000%|0|0|0.00%|None|0.00%|2|16|

## Controlled variable

Only objective confirmation timing changes. Source-time precedence, untouched objective, target ladder, sweep, response, entry, stop, target, timeout, cost, fill and three-percent NAV risk remain fixed.

Strict regression: `{'passed': True, 'keys': ['geometric_daily_nav_growth', 'trades', 'wins', 'win_rate', 'profit_factor', 'max_drawdown_nav', 'net_pnl_after_cost'], 'differences': {}}`
