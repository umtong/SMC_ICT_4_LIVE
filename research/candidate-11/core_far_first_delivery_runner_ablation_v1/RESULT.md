# Candidate 11 core FAR first-delivery / external-runner ablation

**FIRST_DELIVERY_IMPROVED_BUT_INSUFFICIENT**

Opened-data TEMPORARY_TEST only; it cannot advance a candidate or claim alpha.

## Account comparison

- baseline NAV multiple: `0.8317388994`
- ablation NAV multiple: `0.8587199237`
- baseline daily geometric growth: `-0.2190890657%`
- ablation daily geometric growth: `-0.1811600631%`
- pooled log-growth delta: `0.0319242508`
- paired scenarios improved: `3`

## Realization events

- split_activated: `7`
- baseline_fallback: `2`
- targets_submitted: `7`
- first_delivery_fills: `7`
- runner_fills: `1`
- stop_fills: `7`
- stop_resize_requests: `10`
- fail_closed: `0`

## Block comparison

- D1: baseline_log=0.0311712200, ablation_log=0.0021159919503657646, delta=-0.029055228021888285
- D2: baseline_log=-0.1226647338, ablation_log=-0.09214896658805032, delta=0.030515767182841574
- D3: baseline_log=-0.0927431964, ablation_log=-0.0622794847525141, delta=0.030463711683056796

## Checks

### implementation_checks
- all_blocks_complete: `True`
- all_safety_audits: `True`
- no_resolution_tail_forced_exit: `True`
- trade_mapping_and_nav_reconciliation: `True`
- no_first_delivery_fail_close: `True`
- allocation_contract: `True`
- all_activated_targets_submitted: `True`

### controlled_checks
- exact_scenario_id_set: `True`
- same_trade_count: `True`
- same_direction_per_scenario: `True`

### economic_checks
- pooled_log_growth_improved: `True`
- pooled_log_growth_positive: `False`
- at_least_two_positive_blocks: `False`
- at_least_three_first_delivery_fills: `True`
- at_least_one_external_runner_fill: `True`
- at_least_three_paired_scenarios_improved: `True`
- positive_leave_one_scenario_out_growth: `False`
- positive_growth_not_concentrated: `False`

## Decision

Record which realization component improved, but do not promote or validate it. Replace the next causally identified market-state or realization assumption rather than fitting numbers.
