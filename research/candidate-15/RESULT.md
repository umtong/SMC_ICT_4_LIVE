# Candidate 15 V2 causal decision lease

**CANDIDATE15_V2_INSUFFICIENT_ACTIVITY**

- success_claim: `False`
- continuous_account_evidence: `False`
- weekly_reset_screen: `True`

## Predeclared V2 confirmation
- daily_geometric_growth: `-0.0009128842431938753`
- weekly_reset_nav_multiple: `0.9685399563`
- closed_trades: `1`
- wins / losses: `0 / 1`
- win_rate: `0.0`
- maximum_interval_closed_trade_drawdown: `0.0314600437248`

## Contaminated mechanism replay
- closed_trades: `1`
- wins / losses: `1 / 0`
- daily_geometric_growth: `0.0021095519665663347`

## Interval evidence
- D1 [contaminated-v1-mechanism-replay] (2026-06-08): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 90, 'C15_FAILURE': 167}
- H1 [contaminated-v1-mechanism-replay] (2026-04-06): daily_geo=0.006342015916146138, trades=1, W/L=1/0, router={'C15_ACCEPTANCE': 49, 'C15_FAILURE': 173}
- S1 [contaminated-v1-mechanism-replay] (2025-10-10): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 99, 'C15_FAILURE': 168}
- U1 [predeclared-v2-confirmation] (2026-03-02): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 72, 'C15_FAILURE': 162}
- U2 [predeclared-v2-confirmation] (2025-12-01): daily_geo=-0.004556095243673468, trades=1, W/L=0/1, router={'C15_ACCEPTANCE': 86, 'C15_FAILURE': 176}
- U3 [predeclared-v2-confirmation] (2025-08-18): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 97, 'C15_FAILURE': 181}
- U4 [predeclared-v2-confirmation] (2025-05-05): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 93, 'C15_FAILURE': 152}
- U5 [predeclared-v2-confirmation] (2025-01-13): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 79, 'C15_FAILURE': 187}

## Checks
- all_intervals_present: `True`
- five_predeclared_confirmation_intervals: `True`
- confirmation_activity: `False`
- positive_costed_growth: `False`
- project_growth_threshold: `False`
- win_rate_at_least_0_65: `False`
- maximum_interval_drawdown_at_most_0_20: `True`
- safety: `True`

Classification uses only U1-U5. D1/H1/S1 are contaminated mechanism replays. The confirmation weeks do not form one continuous account path.
