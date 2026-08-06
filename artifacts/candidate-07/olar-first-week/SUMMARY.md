# Candidate 07 Objective-Lifecycle Acceptance Relay

The full causal hypothesis is the only selection-eligible variant. Ablations are attribution controls, not fallback strategies.

Full first-week gate: `False`
Selected: `None`
All three weeks passed: `False`
Long evaluation authorized: `False`

|variant|eligible|rc|classification|gate|geom/day|trades|win rate|PF|max DD|failures|
|---|---|---:|---|---|---:|---:|---:|---:|---:|---|
|olar_full|True|1|IMPLEMENTATION_OR_RUNTIME_FAILURE|False|-100.000000%|0|0.00%|None|0.00%||
|olar_objective_reuse_ablation|False|1|IMPLEMENTATION_OR_RUNTIME_FAILURE|False|-100.000000%|0|0.00%|None|0.00%||
|hml_parent_reference|False|0|LOGIC_GATE_FAILED|False|-2.188338%|9|22.22%|0.25543374908571587|18.43%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
