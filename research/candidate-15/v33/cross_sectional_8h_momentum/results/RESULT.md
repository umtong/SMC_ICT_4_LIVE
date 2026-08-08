# Candidate 15 V33 — Cross-sectional eight-hour momentum

**V33_CROSS_SECTIONAL_8H_MOMENTUM_REJECTED_OR_UNDERPOWERED**

Completed 24-hour and seven-day trends are ranked only at fixed eight-hour boundaries. Entry follows a wholly subsequent completed one-hour confirmation and one global slot arbitrates all symbols.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `126 / 0.3452054794520548`
- gross / net mean: `-20.962126383834274 / -40.962126383834295` bp
- win rate / payoff: `0.25396825396825395 / 1.6358112999883885`
- net t-stat: `-2.588047543715741`
- positive months: `3 / 12`
- mean holding minutes: `250.0`
- route stats: `{'BREADTH_TREND_LEADER': {'trades': 115, 'mean_net_bps': -31.376259248626095, 'win_rate': 0.2608695652173913, 'payoff_ratio': 1.7970599776800025, 'net_t_stat': -1.9056285939365862, 'mean_holding_minutes': 247.82608695652175}, 'CROSS_SECTIONAL_TREND_LEADER': {'trades': 11, 'mean_net_bps': -141.1780100701018, 'win_rate': 0.18181818181818182, 'payoff_ratio': 0.5776986944384963, 'net_t_stat': -2.847699344564296, 'mean_holding_minutes': 272.72727272727275}}`
- symbol counts: `{'BTCUSDT': 34, 'SOLUSDT': 34, 'ETHUSDT': 29, 'XRPUSDT': 29}`
- exit reasons: `{'TRAILING_STOP': 94, 'NEXT_DECISION_CAP': 32}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `130 / 0.3561643835616438`
- gross / net mean: `16.648433249083862 / -3.35156675091615` bp
- win rate / payoff: `0.3384615384615385 / 1.850398798861827`
- net t-stat: `-0.2354399335655812`
- positive months: `5 / 12`
- mean holding minutes: `270.9230769230769`
- route stats: `{'BREADTH_TREND_LEADER': {'trades': 125, 'mean_net_bps': -2.1771710385940035, 'win_rate': 0.336, 'payoff_ratio': 1.9073893631166838, 'net_t_stat': -0.14888903920015886, 'mean_holding_minutes': 270.72}, 'CROSS_SECTIONAL_TREND_LEADER': {'trades': 5, 'mean_net_bps': -32.711459558969906, 'win_rate': 0.4, 'payoff_ratio': 0.8185718941047448, 'net_t_stat': -0.5185937637156515, 'mean_holding_minutes': 276.0}}`
- symbol counts: `{'XRPUSDT': 36, 'BTCUSDT': 36, 'SOLUSDT': 34, 'ETHUSDT': 24}`
- exit reasons: `{'TRAILING_STOP': 84, 'NEXT_DECISION_CAP': 46}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `8 / 0.25806451612903225`
- gross / net mean: `42.47951831713101 / 22.479518317131006` bp
- win rate / payoff: `0.5 / 1.7908500322671865`
- net t-stat: `0.5649439496467183`
- positive months: `1 / 1`
- mean holding minutes: `367.5`
- route stats: `{'BREADTH_TREND_LEADER': {'trades': 8, 'mean_net_bps': 22.479518317131006, 'win_rate': 0.5, 'payoff_ratio': 1.7908500322671865, 'net_t_stat': 0.5649439496467183, 'mean_holding_minutes': 367.5}}`
- symbol counts: `{'BTCUSDT': 4, 'XRPUSDT': 2, 'ETHUSDT': 2}`
- exit reasons: `{'NEXT_DECISION_CAP': 5, 'TRAILING_STOP': 3}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `2 / 0.2857142857142857`
- gross / net mean: `-87.07907934500048 / -107.07907934500048` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `-8.13224279324338`
- positive months: `0 / 1`
- mean holding minutes: `150.0`
- route stats: `{'BREADTH_TREND_LEADER': {'trades': 1, 'mean_net_bps': -93.91185327128235, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 60.0}, 'CROSS_SECTIONAL_TREND_LEADER': {'trades': 1, 'mean_net_bps': -120.24630541871859, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 240.0}}`
- symbol counts: `{'SOLUSDT': 1, 'XRPUSDT': 1}`
- exit reasons: `{'TRAILING_STOP': 2}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `True`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `True`

## Cross-split route evidence
`{'BREADTH_TREND_LEADER': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 115, 'mean_net_bps': -31.376259248626095, 'win_rate': 0.2608695652173913, 'payoff_ratio': 1.7970599776800025, 'net_t_stat': -1.9056285939365862, 'mean_holding_minutes': 247.82608695652175}, 'stability': {'trades': 125, 'mean_net_bps': -2.1771710385940035, 'win_rate': 0.336, 'payoff_ratio': 1.9073893631166838, 'net_t_stat': -0.14888903920015886, 'mean_holding_minutes': 270.72}, 'july_confirmation': {'trades': 8, 'mean_net_bps': 22.479518317131006, 'win_rate': 0.5, 'payoff_ratio': 1.7908500322671865, 'net_t_stat': 0.5649439496467183, 'mean_holding_minutes': 367.5}, 'latest_pulse': {'trades': 1, 'mean_net_bps': -93.91185327128235, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 60.0}}}, 'CROSS_SECTIONAL_TREND_LEADER': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 11, 'mean_net_bps': -141.1780100701018, 'win_rate': 0.18181818181818182, 'payoff_ratio': 0.5776986944384963, 'net_t_stat': -2.847699344564296, 'mean_holding_minutes': 272.72727272727275}, 'stability': {'trades': 5, 'mean_net_bps': -32.711459558969906, 'win_rate': 0.4, 'payoff_ratio': 0.8185718941047448, 'net_t_stat': -0.5185937637156515, 'mean_holding_minutes': 276.0}, 'july_confirmation': None, 'latest_pulse': {'trades': 1, 'mean_net_bps': -120.24630541871859, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 240.0}}}}`

## Decision
Cross-split-positive fixed routes: []. Do not change horizons, decision clocks, ranking, confirmation or stop rules; move to another independent mechanism.

This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.
