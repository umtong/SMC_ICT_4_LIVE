# Candidate 15 V10 execution-valid beta-coherent diffusion lag

**CANDIDATE15_V10_INSUFFICIENT_ACTIVITY**

- development_only: `True`
- success_claim: `False`
- weekly_reset_nav_multiple: `1.2002300321592052`
- daily_geometric_growth: `0.0043550087479180925`
- closed_trades: `2`
- wins / losses: `2 / 0`
- win_rate: `1.0`
- payoff_ratio: `inf`
- active_intervals: `2`
- submitted_beta_coherent_plans: `4`
- route_violations: `0`
- transfer_completions: `2`
- protection_actions: `2`
- management_fail_closed_count: `0`
- cost_cover_contract_violations: `0`

## Interval evidence
- E01 (2021-07-12): daily_geo=3.6054474847324266e-05, trades=1, W/L=1/0, beta_states=41
- E02 (2022-05-09): daily_geo=0.0, trades=0, W/L=0/0, beta_states=36
- E03 (2022-07-25): daily_geo=0.0, trades=0, W/L=0/0, beta_states=31
- E04 (2023-06-20): daily_geo=0.0, trades=0, W/L=0/0, beta_states=38
- E05 (2024-07-15): daily_geo=0.026379195798359845, trades=1, W/L=1/0, beta_states=59
- E06 (2025-08-11): daily_geo=0.0, trades=0, W/L=0/0, beta_states=33

## Development checks
- all_intervals_present: `True`
- minimum_closed_trades: `False`
- minimum_active_intervals: `False`
- positive_costed_growth: `True`
- minimum_win_rate: `True`
- minimum_payoff_ratio: `True`
- maximum_closed_trade_path_drawdown: `True`
- growth_not_concentrated: `False`
- safety: `True`
- only_beta_coherent_diffusion_submitted: `True`
- management_integrity: `True`

E01-E06 are exposed mechanism-development intervals; V10 verifies execution validity only and cannot support a success claim.
