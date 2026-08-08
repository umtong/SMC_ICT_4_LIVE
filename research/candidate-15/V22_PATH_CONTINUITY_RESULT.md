# Candidate 15 V22 — Price-path continuity diagnostic

**V22_PATH_CONTINUITY_ROUTER_REJECTED_OR_UNDERPOWERED**

The state router distinguishes a distributed one-hour path from a single-bar-dominated shock before the independent confirmation bar.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- selected independent episodes / day: `101 / 0.27671232876712326`
- gross / net mean: `2.892432384068719 / -17.107567615931295` bp
- win rate / payoff: `0.39603960396039606 / 1.1334991703182054`
- net t-stat: `-1.0230919551933062`
- positive months: `4 / 12`
- route stats: `{'GRADUAL_INFORMATION_DIFFUSION': {'trades': 101, 'mean_net_bps': -17.107567615931295, 'win_rate': 0.39603960396039606, 'net_t_stat': -1.0230919551933062}}`
- symbol counts: `{'ETHUSDT': 31, 'SOLUSDT': 33, 'XRPUSDT': 11, 'BTCUSDT': 26}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- selected independent episodes / day: `100 / 0.273972602739726`
- gross / net mean: `2.553659749572801 / -17.44634025042721` bp
- win rate / payoff: `0.37 / 1.172957721730705`
- net t-stat: `-1.2190945852330328`
- positive months: `5 / 12`
- route stats: `{'GRADUAL_INFORMATION_DIFFUSION': {'trades': 100, 'mean_net_bps': -17.44634025042721, 'win_rate': 0.37, 'net_t_stat': -1.2190945852330328}}`
- symbol counts: `{'ETHUSDT': 25, 'BTCUSDT': 29, 'XRPUSDT': 22, 'SOLUSDT': 24}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- selected independent episodes / day: `8 / 0.25806451612903225`
- gross / net mean: `21.910668178581894 / 1.910668178581893` bp
- win rate / payoff: `0.625 / 0.6403978036902839`
- net t-stat: `0.0670459215268771`
- positive months: `1 / 1`
- route stats: `{'GRADUAL_INFORMATION_DIFFUSION': {'trades': 8, 'mean_net_bps': 1.910668178581893, 'win_rate': 0.625, 'net_t_stat': 0.0670459215268771}}`
- symbol counts: `{'BTCUSDT': 1, 'SOLUSDT': 2, 'ETHUSDT': 3, 'XRPUSDT': 2}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- selected independent episodes / day: `3 / 0.42857142857142855`
- gross / net mean: `11.057397303578426 / -8.942602696421572` bp
- win rate / payoff: `0.3333333333333333 / 1.3240020241436548`
- net t-stat: `-0.2870276036602644`
- positive months: `0 / 1`
- route stats: `{'GRADUAL_INFORMATION_DIFFUSION': {'trades': 3, 'mean_net_bps': -8.942602696421572, 'win_rate': 0.3333333333333333, 'net_t_stat': -0.2870276036602644}}`
- symbol counts: `{'SOLUSDT': 3}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- family_stability_frequency: `True`
- positive_july_confirmation_mean_net: `True`
- july_confirmation_trade_count: `False`
- positive_latest_august_pulse_mean_net: `False`
- latest_august_pulse_trade_count: `True`
- symbol_concentration: `True`

## Cross-split route evidence
`{'GRADUAL_INFORMATION_DIFFUSION': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 101, 'mean_net_bps': -17.107567615931295, 'win_rate': 0.39603960396039606, 'net_t_stat': -1.0230919551933062}, 'stability': {'trades': 100, 'mean_net_bps': -17.44634025042721, 'win_rate': 0.37, 'net_t_stat': -1.2190945852330328}, 'july_confirmation': {'trades': 8, 'mean_net_bps': 1.910668178581893, 'win_rate': 0.625, 'net_t_stat': 0.0670459215268771}, 'latest_august_pulse': {'trades': 3, 'mean_net_bps': -8.942602696421572, 'win_rate': 0.3333333333333333, 'net_t_stat': -0.2870276036602644}}}}`

## Decision
The path-continuity router failed at least one predeclared family gate. Cross-split-positive routes: []. Do not tune path/state thresholds; retain only those routes and move to another independent mechanism.

A family-level pass is not final-system success. Any survivor still requires frozen NautilusTrader orders, same-leg invalidation, current-NAV 3% risk sizing, actual costs, one global slot and final continuous-account frequency of at least one independent completed trade per calendar day.
