# Candidate 11 core FAR structural risk-transfer ablation

**STRUCTURAL_RISK_TRANSFER_IMPROVED_BUT_INSUFFICIENT**

This is an opened-data mechanism ablation. It cannot advance a candidate or claim alpha.

## Account comparison

- baseline NAV multiple: `0.8317388994`
- ablation NAV multiple: `0.9873573386`
- baseline daily geometric growth: `-0.2190890657%`
- ablation daily geometric growth: `-0.0151455909%`
- pooled log-growth delta: `0.1715134503`
- paired scenarios improved: `6`
- transfer requested / confirmed: `7 / 7`

## Block comparison

- D1: baseline_log=0.0311712200, ablation_log=0.06084183587683508, delta=0.029670615904581032
- D2: baseline_log=-0.1226647338, ablation_log=-0.029771236600029036, delta=0.09289349717086286
- D3: baseline_log=-0.0927431964, ablation_log=-0.04379385917703111, delta=0.04894933725853979

## Checks

### implementation_checks
- all_blocks_complete: `True`
- all_safety_audits: `True`
- no_resolution_tail_forced_exit: `True`
- trade_mapping_and_nav_reconciliation: `True`
- no_modify_rejection: `True`
- unique_stop_lookup: `True`

### controlled_checks
- exact_scenario_id_set: `True`
- same_trade_count: `True`
- same_direction_per_scenario: `True`

### improvement_checks
- pooled_log_growth_improved: `True`
- pooled_log_growth_positive: `False`
- at_least_two_positive_blocks: `False`
- at_least_three_confirmed_transfers: `True`
- at_least_three_paired_scenarios_improved: `True`
- positive_leave_one_scenario_out_growth: `False`
- positive_growth_not_concentrated: `False`

## Decision

Record the mechanism effect but do not retain it as a candidate component.
