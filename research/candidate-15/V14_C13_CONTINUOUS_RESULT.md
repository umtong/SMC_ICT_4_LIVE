# Candidate 15 V14 — Candidate 13 frozen continuous-account result

**V14_CONTINUOUS_POLICY_REJECTED**

- interval: `2026-05-01 -> 2026-07-01`
- observed calendar days: `61`
- starting / final NAV: `100000 / 88275.07786528`
- NAV multiple: `0.8827507787`
- daily geometric growth: `-0.0020423765`
- completed trades: `15`
- wins / losses: `4 / 11`
- win rate: `0.266667`
- submitted plans / unique causal episodes: `21 / 21`
- completed independent-trade proxy: `15`
- required independent trades: `61`
- maximum closed-trade drawdown: `0.1550495234`

## Gate checks
- continuous_account: `True`
- weekly_nav_reset_absent: `True`
- source_lock: `True`
- all_safety_audits: `True`
- daily_geometric_growth: `False`
- independent_completed_trade_frequency: `False`
- positive_final_nav: `True`

## Interpretation
The exact frozen policy did not retain the minimum continuous-account growth rate. Its useful state, execution and risk components may be reused, but this policy must not be promoted as the final system.

This result is produced by one NautilusTrader account. Weekly NAV resets and multiplication of weekly returns are not used.
