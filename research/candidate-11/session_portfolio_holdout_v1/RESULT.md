# Candidate 11 untouched holdout result

**CANDIDATE11_UNTOUCHED_HOLDOUT_GATE_FAILED**

- gate_passed: `False`
- success_claim: `False`
- daily_geometric_growth: `-0.0007767014`
- pooled_nav_multiple: `0.9838153363`
- closed_trades: `3`
- wins / losses: `1 / 2`
- win_rate: `0.333333`
- payoff_ratio: `1.4908765064009988`
- active_weeks: `2 / 3`
- maximum_weekly_closed_trade_drawdown: `0.0302651260`
- maximum_positive_log_growth_share_from_one_week: `1.0000000000`
- source_commit_before_market_data: `e25305047e4d1db3f5cb4e461795ea634d463ae6`

## Precommitted gate checks
- active_weeks: `True`
- all_holdouts_complete: `True`
- all_safety_audits: `True`
- closed_trades: `False`
- daily_geometric_growth: `False`
- growth_concentration: `False`
- max_drawdown: `True`
- payoff_ratio: `True`
- win_rate: `False`

## Untouched weekly evidence
- H1 (2023-03-06): daily_geo=-0.004372, trades=1, W/L=0/1, plans=1, safety=True
- H2 (2023-09-08): daily_geo=0.000000, trades=0, W/L=0/0, plans=0, safety=True
- H3 (2024-04-14): daily_geo=0.002052, trades=2, W/L=1/1, plans=2, safety=True

## Diagnostic + holdout continuity context (not used for the holdout gate)
- calendar_days: `56`
- nav_multiple: `1.4848824399`
- daily_geometric_growth: `0.0070845418`
- trades / wins / losses: `14 / 10 / 4`
