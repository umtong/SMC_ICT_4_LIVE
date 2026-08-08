# Candidate 11 core FAR continuous development result

**DEVELOPMENT_GATE_FAILED**

This is development evidence. It cannot establish strategy success.

## Aggregate

- calendar days: `84`
- closed trades: `9`
- economic clusters: `9`
- pooled NAV multiple: `0.8317388994`
- pooled daily geometric growth: `-0.2190890657%`
- minimum leave-one-cluster-out log growth: `-0.2459756807`
- maximum positive cluster share: `100.0000000000%`

## Blocks

- D1 2023-09-22 to 2023-10-20: daily_geo=0.111388%, trades=2, W/L=1/1, safety=True
- D2 2024-10-01 to 2024-10-29: daily_geo=-0.437130%, trades=4, W/L=0/4, safety=True
- D3 2025-09-16 to 2025-10-14: daily_geo=-0.330678%, trades=3, W/L=0/3, safety=True

## Direction evidence

- LONG: clusters=4, trades=4, log_growth=-0.0302150714
- SHORT: clusters=5, trades=5, log_growth=-0.1540216388

## Gate checks

- all_blocks_complete: `True`
- all_blocks_positive: `False`
- minimum_economic_clusters: `False`
- pooled_daily_geometric_growth: `False`
- positive_leave_one_cluster_out_growth: `False`
- growth_concentration: `False`
- claimed_direction_cluster_coverage: `False`
- claimed_direction_positive_growth: `False`
- all_safety_audits: `True`
- no_resolution_tail_forced_exit: `True`
- trade_mapping_and_nav_reconciliation: `True`

## Decision

Reject this scenario contract or replace one causally identified market-state assumption. Do not perform a return-targeted threshold sweep.
