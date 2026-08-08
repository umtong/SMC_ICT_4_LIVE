# Candidate 15 V21 — Prior-only co-jump state diagnostic

**V21_COJUMP_ROUTER_REJECTED_OR_UNDERPOWERED**

Jump statistics use prior-only bipower variation. The latest August pulse uses daily archives ending before 2026-08-08.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- selected independent episodes / day: `130 / 0.3561643835616438`
- gross / net mean: `-5.252223479254828 / -25.25222347925484` bp
- win rate / payoff: `0.3230769230769231 / 1.370034044972896`
- net t-stat: `-1.411233150839165`
- positive months: `2 / 12`
- route stats: `{'SYSTEMIC_COJUMP_ACCEPTANCE': {'trades': 130, 'mean_net_bps': -25.25222347925484, 'win_rate': 0.3230769230769231, 'net_t_stat': -1.411233150839165}}`
- symbol counts: `{'ETHUSDT': 40, 'BTCUSDT': 44, 'SOLUSDT': 29, 'XRPUSDT': 17}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- selected independent episodes / day: `135 / 0.3698630136986301`
- gross / net mean: `41.67348099753367 / 21.673480997533666` bp
- win rate / payoff: `0.5407407407407407 / 1.3404746175676647`
- net t-stat: `1.6920971544505294`
- positive months: `7 / 12`
- route stats: `{'SYSTEMIC_COJUMP_ACCEPTANCE': {'trades': 135, 'mean_net_bps': 21.673480997533666, 'win_rate': 0.5407407407407407, 'net_t_stat': 1.6920971544505294}}`
- symbol counts: `{'ETHUSDT': 47, 'XRPUSDT': 29, 'BTCUSDT': 41, 'SOLUSDT': 18}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- selected independent episodes / day: `14 / 0.45161290322580644`
- gross / net mean: `-10.80889817078918 / -30.80889817078919` bp
- win rate / payoff: `0.42857142857142855 / 0.5684952076492605`
- net t-stat: `-1.1498061446797665`
- positive months: `0 / 1`
- route stats: `{'SYSTEMIC_COJUMP_ACCEPTANCE': {'trades': 14, 'mean_net_bps': -30.80889817078919, 'win_rate': 0.42857142857142855, 'net_t_stat': -1.1498061446797665}}`
- symbol counts: `{'SOLUSDT': 7, 'BTCUSDT': 2, 'ETHUSDT': 4, 'XRPUSDT': 1}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- selected independent episodes / day: `1 / 0.14285714285714285`
- gross / net mean: `72.54464285714413 / 52.544642857144126` bp
- win rate / payoff: `1.0 / None`
- net t-stat: `None`
- positive months: `1 / 1`
- route stats: `{'SYSTEMIC_COJUMP_ACCEPTANCE': {'trades': 1, 'mean_net_bps': 52.544642857144126, 'win_rate': 1.0, 'net_t_stat': None}}`
- symbol counts: `{'SOLUSDT': 1}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `True`
- stability_net_t_stat: `True`
- stability_positive_month_share: `False`
- family_stability_frequency: `True`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `True`
- positive_latest_august_pulse_mean_net: `True`
- latest_august_pulse_trade_count: `False`
- symbol_concentration: `True`

## Cross-split route evidence
`{'SYSTEMIC_COJUMP_ACCEPTANCE': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 130, 'mean_net_bps': -25.25222347925484, 'win_rate': 0.3230769230769231, 'net_t_stat': -1.411233150839165}, 'stability': {'trades': 135, 'mean_net_bps': 21.673480997533666, 'win_rate': 0.5407407407407407, 'net_t_stat': 1.6920971544505294}, 'july_confirmation': {'trades': 14, 'mean_net_bps': -30.80889817078919, 'win_rate': 0.42857142857142855, 'net_t_stat': -1.1498061446797665}, 'latest_august_pulse': {'trades': 1, 'mean_net_bps': 52.544642857144126, 'win_rate': 1.0, 'net_t_stat': None}}}}`

## Decision
The co-jump router failed at least one predeclared family gate. Cross-split-positive routes: []. Do not tune jump/state thresholds; retain only those routes and move to another independent mechanism.

A family-level pass is not final-system success. Any survivor still requires frozen NautilusTrader orders, same-leg invalidation, 3% current-NAV risk sizing, actual funding/fees, one global slot and continuous-account validation with at least one independent completed trade per calendar day.
