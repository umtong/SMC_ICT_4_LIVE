# Candidate 15 V26 — Cross-sectional residual/OI state-router diagnostic

**V26_RESIDUAL_OI_ROUTER_REJECTED_OR_UNDERPOWERED**

Every thirty-minute move is decomposed with a prior-only rolling beta against the four-market median factor. OI determines liquidation versus buildup state; a separate following fifteen-minute residual/flow transition determines entry.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `37 / 0.10136986301369863`
- gross / net mean: `3.672612420696557 / -16.327387579303448` bp
- win rate / payoff: `0.5135135135135135 / 0.6402393263112071`
- net t-stat: `-0.7556135241558326`
- positive months: `4 / 12`
- route stats: `{'RESIDUAL_LIQUIDATION_EXHAUSTION_2H': {'trades': 16, 'mean_net_bps': -63.434940526434566, 'win_rate': 0.4375, 'net_t_stat': -1.7479178140375229}, 'RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H': {'trades': 21, 'mean_net_bps': 19.56408133279644, 'win_rate': 0.5714285714285714, 'net_t_stat': 0.812912015695537}}`
- symbol counts: `{'XRPUSDT': 7, 'SOLUSDT': 12, 'BTCUSDT': 11, 'ETHUSDT': 7}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `41 / 0.11232876712328767`
- gross / net mean: `20.634729857032045 / 0.634729857032042` bp
- win rate / payoff: `0.4634146341463415 / 1.176029841994455`
- net t-stat: `0.034853479655893374`
- positive months: `7 / 12`
- route stats: `{'RESIDUAL_LIQUIDATION_EXHAUSTION_2H': {'trades': 21, 'mean_net_bps': -36.313045154009664, 'win_rate': 0.38095238095238093, 'net_t_stat': -2.318810719849215}, 'RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H': {'trades': 20, 'mean_net_bps': 39.42989361862582, 'win_rate': 0.55, 'net_t_stat': 1.2433829976260256}}`
- symbol counts: `{'XRPUSDT': 9, 'ETHUSDT': 12, 'SOLUSDT': 13, 'BTCUSDT': 7}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `1 / 0.03225806451612903`
- gross / net mean: `17.275419545903503 / -2.7245804540964973` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `None`
- positive months: `0 / 1`
- route stats: `{'RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H': {'trades': 1, 'mean_net_bps': -2.7245804540964973, 'win_rate': 0.0, 'net_t_stat': None}}`
- symbol counts: `{'SOLUSDT': 1}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `True`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- symbol_concentration: `True`
- cross_split_positive_route: `False`

## Cross-split route evidence
`{'RESIDUAL_LIQUIDATION_EXHAUSTION_2H': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 16, 'mean_net_bps': -63.434940526434566, 'win_rate': 0.4375, 'net_t_stat': -1.7479178140375229}, 'stability': {'trades': 21, 'mean_net_bps': -36.313045154009664, 'win_rate': 0.38095238095238093, 'net_t_stat': -2.318810719849215}, 'july_confirmation': None}}, 'RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 21, 'mean_net_bps': 19.56408133279644, 'win_rate': 0.5714285714285714, 'net_t_stat': 0.812912015695537}, 'stability': {'trades': 20, 'mean_net_bps': 39.42989361862582, 'win_rate': 0.55, 'net_t_stat': 1.2433829976260256}, 'july_confirmation': {'trades': 1, 'mean_net_bps': -2.7245804540964973, 'win_rate': 0.0, 'net_t_stat': None}}}}`

## Decision
Do not tune the residual/OI family after these declared results; preserve only any cross-split-positive route and move to another independent mechanism.

This mechanism screen does not synthesize NAV. Any survivor still requires frozen NautilusTrader orders, event/same-leg invalidation, exact current-NAV 3% risk sizing, realistic costs, one global slot and continuous-account validation.
