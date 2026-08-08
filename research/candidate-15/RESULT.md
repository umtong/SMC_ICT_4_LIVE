# Candidate 15 sequential response router

**CANDIDATE15_SCREEN_REJECTED**

- success_claim: `False`
- continuous_account_evidence: `False`
- weekly_reset_screen: `True`
- daily_geometric_growth: `-0.0038191182630036556`
- weekly_reset_nav_multiple: `0.9227886761`
- closed_trades: `5`
- wins / losses: `1 / 4`
- win_rate: `0.2`
- maximum_interval_closed_trade_drawdown: `0.061642905672`

## Interval evidence
- D1 (2026-06-08): daily_geo=-0.00904806124159776, trades=2, W/L=0/2, router={'C15_ACCEPTANCE': 89, 'C15_FAILURE': 167}
- H1 (2026-04-06): daily_geo=0.0019644646145331296, trades=2, W/L=1/1, router={'C15_ACCEPTANCE': 48, 'C15_FAILURE': 171}
- S1 (2025-10-10): daily_geo=-0.004343124416010246, trades=1, W/L=0/1, router={'C15_ACCEPTANCE': 97, 'C15_FAILURE': 170}

## Checks
- all_intervals_present: `True`
- screening_activity: `True`
- positive_costed_growth: `False`
- project_growth_threshold: `False`
- win_rate_at_least_0_65: `False`
- maximum_interval_drawdown_at_most_0_20: `True`
- safety: `True`

The three intervals are a parallel information-value screen. They do not form one continuous account path.
