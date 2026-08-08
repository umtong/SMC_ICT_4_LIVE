# Candidate 15 V3 scenario-terminal invalidation

**CANDIDATE15_V3_INSUFFICIENT_ACTIVITY**

- success_claim: `False`
- continuous_account_evidence: `False`
- weekly_reset_screen: `True`

## Predeclared V3 confirmation
- daily_geometric_growth: `-0.000876561604788197`
- weekly_reset_nav_multiple: `0.9697731408`
- closed_trades: `1`
- wins / losses: `0 / 1`
- win_rate: `0.0`
- maximum_interval_closed_trade_drawdown: `0.030226859232`

## Contaminated diagnostic replays
- V2 mechanism replay trades W/L: `1` / `1/0`
- Candidate 13 reference replay trades W/L: `2` / `2/0`
- Candidate 13 reference replay daily_geo: `0.0022834081699822993`

## Interval evidence
- M1 [contaminated-v2-mechanism-replay] (2026-04-06): daily_geo=0.006342015916146138, trades=1, W/L=1/0, router={'C15_ACCEPTANCE': 49, 'C15_FAILURE': 173, 'C15_RESOLUTION_STALE': 207}
- M2 [contaminated-v2-mechanism-replay] (2025-12-01): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 86, 'C15_FAILURE': 176, 'C15_RESOLUTION_STALE': 235, 'C15_STOP_INSIDE_SWEEP_INVALIDATION': 1}
- C1 [contaminated-candidate13-reference-replay] (2023-03-20): daily_geo=0.0062151400745527885, trades=1, W/L=1/0, router={'C15_ACCEPTANCE': 44, 'C15_FAILURE': 146, 'C15_RESOLUTION_STALE': 175}
- C2 [contaminated-candidate13-reference-replay] (2023-06-20): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 54, 'C15_FAILURE': 133, 'C15_RESOLUTION_STALE': 176, 'C15_UNROUTED_SCENARIO_FAMILY': 3}
- C3 [contaminated-candidate13-reference-replay] (2024-09-17): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 84, 'C15_FAILURE': 174, 'C15_RESOLUTION_STALE': 236, 'C15_STOP_INSIDE_SWEEP_INVALIDATION': 1}
- C4 [contaminated-candidate13-reference-replay] (2024-12-31): daily_geo=0.005221705862516899, trades=1, W/L=1/0, router={'C15_ACCEPTANCE': 66, 'C15_FAILURE': 193, 'C15_RESOLUTION_STALE': 235}
- C5 [contaminated-candidate13-reference-replay] (2025-04-14): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 76, 'C15_FAILURE': 188, 'C15_RESOLUTION_STALE': 246, 'C15_STOP_INSIDE_SWEEP_INVALIDATION': 1}
- V1 [predeclared-v3-confirmation] (2026-02-09): daily_geo=-0.004375131153670638, trades=1, W/L=0/1, router={'C15_ACCEPTANCE': 68, 'C15_FAILURE': 185, 'C15_RESOLUTION_STALE': 240, 'C15_UNROUTED_SCENARIO_FAMILY': 2}
- V2 [predeclared-v3-confirmation] (2025-11-03): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 106, 'C15_FAILURE': 189, 'C15_RESOLUTION_STALE': 267, 'C15_STOP_INSIDE_SWEEP_INVALIDATION': 3}
- V3 [predeclared-v3-confirmation] (2025-09-15): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 74, 'C15_FAILURE': 162, 'C15_RESOLUTION_STALE': 211, 'C15_STOP_INSIDE_SWEEP_INVALIDATION': 2}
- V4 [predeclared-v3-confirmation] (2025-06-16): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 101, 'C15_FAILURE': 148, 'C15_RESOLUTION_STALE': 217}
- V5 [predeclared-v3-confirmation] (2025-03-10): daily_geo=0.0, trades=0, W/L=0/0, router={'C15_ACCEPTANCE': 89, 'C15_FAILURE': 198, 'C15_RESOLUTION_STALE': 263}

## Checks
- all_intervals_present: `True`
- five_predeclared_confirmation_intervals: `True`
- confirmation_activity: `False`
- positive_costed_growth: `False`
- project_growth_threshold: `False`
- win_rate_at_least_0_65: `False`
- maximum_interval_drawdown_at_most_0_20: `True`
- safety: `True`

Classification uses only V1-V5. M1/M2 and C1-C5 are contaminated diagnostics. The confirmation weeks do not form one continuous account path.
