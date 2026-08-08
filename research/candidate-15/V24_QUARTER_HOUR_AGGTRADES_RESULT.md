# Candidate 15 V24 — Exact aggTrades quarter-hour BTC mechanism screen

**V24_EXACT_AGGTRADES_ROUTER_REJECTED_OR_UNDERPOWERED**

Boundary imbalance is reconstructed from Binance USD-M one-second bars covering seconds 0-9. Public state ends before the boundary and a separate completed five-minute interval confirms the tradable leg.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- sampled days: `5`
- selected trades / sampled day: `9 / 1.8`
- gross / net mean: `57.95224180941324 / 37.95224180941322` bp
- win rate / payoff: `0.5555555555555556 / 1.6436153759336964`
- net t-stat: `0.7009711261021019`
- positive months: `1 / 3`
- route stats: `{'QH_10S_IMMEDIATE_CONFIRMATION_4H': {'trades': 9, 'mean_net_bps': 37.95224180941322, 'win_rate': 0.5555555555555556, 'net_t_stat': 0.7009711261021019}}`
- symbol counts: `{'BTCUSDT': 9}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- sampled days: `5`
- selected trades / sampled day: `16 / 3.2`
- gross / net mean: `-30.65612437122285 / -50.65612437122285` bp
- win rate / payoff: `0.25 / 0.2544427318446031`
- net t-stat: `-3.6251902852455093`
- positive months: `0 / 5`
- route stats: `{'QH_10S_IMMEDIATE_CONFIRMATION_4H': {'trades': 16, 'mean_net_bps': -50.65612437122285, 'win_rate': 0.25, 'net_t_stat': -3.6251902852455093}}`
- symbol counts: `{'BTCUSDT': 16}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- sampled days: `4`
- selected trades / sampled day: `14 / 3.5`
- gross / net mean: `19.309002583425826 / -0.6909974165741732` bp
- win rate / payoff: `0.42857142857142855 / 1.2956701337224952`
- net t-stat: `-0.04391083180552807`
- positive months: `0 / 1`
- route stats: `{'QH_10S_IMMEDIATE_CONFIRMATION_4H': {'trades': 14, 'mean_net_bps': -0.6909974165741732, 'win_rate': 0.42857142857142855, 'net_t_stat': -0.04391083180552807}}`
- symbol counts: `{'BTCUSDT': 14}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- sampled days: `7`
- selected trades / sampled day: `12 / 1.7142857142857142`
- gross / net mean: `-5.535061052626967 / -25.535061052626972` bp
- win rate / payoff: `0.25 / 0.6696737550813843`
- net t-stat: `-2.2277745165249625`
- positive months: `0 / 1`
- route stats: `{'QH_10S_IMMEDIATE_CONFIRMATION_4H': {'trades': 12, 'mean_net_bps': -25.535061052626972, 'win_rate': 0.25, 'net_t_stat': -2.2277745165249625}}`
- symbol counts: `{'BTCUSDT': 12}`

## Advance checks
- positive_development_mean_net: `True`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- family_stability_frequency: `True`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `True`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `True`
- symbol_concentration: `True`
- cross_split_positive_route_exists: `False`

## Cross-split route evidence
`{'QH_10S_IMMEDIATE_CONFIRMATION_4H': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 9, 'mean_net_bps': 37.95224180941322, 'win_rate': 0.5555555555555556, 'net_t_stat': 0.7009711261021019}, 'stability': {'trades': 16, 'mean_net_bps': -50.65612437122285, 'win_rate': 0.25, 'net_t_stat': -3.6251902852455093}, 'july_confirmation': {'trades': 14, 'mean_net_bps': -0.6909974165741732, 'win_rate': 0.42857142857142855, 'net_t_stat': -0.04391083180552807}, 'latest_pulse': {'trades': 12, 'mean_net_bps': -25.535061052626972, 'win_rate': 0.25, 'net_t_stat': -2.2277745165249625}}}, 'QH_10S_SHORT_REVERSAL_MEDIUM_DELIVERY_8H': {'positive_across_all_declared_splits': False, 'splits': {'development': None, 'stability': None, 'july_confirmation': None, 'latest_pulse': None}}}`

## Decision
Fixed cross-split-positive routes: []. Do not tune the ten-second imbalance family; preserve only any cross-split-positive route and move to another independent mechanism.

This is a mechanism screen, not synthetic NAV. A survivor still requires frozen NautilusTrader orders, same-leg invalidation, current-NAV 3% risk sizing, actual costs, one global slot and final continuous-account validation.

## Exact-data implementation
- state source: official Binance USD-M daily aggTrades
- seconds 0-9: volume-normalized signed taker imbalance
- seconds 10-59: independent transition
- outcome origin: final trade of the event minute
- scope: BTC-only high-information screen; no final success claim
