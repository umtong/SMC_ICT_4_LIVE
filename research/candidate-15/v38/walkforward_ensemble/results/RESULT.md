# Candidate 15 V38 — Prior-only walk-forward ensemble

**V38_WALKFORWARD_ENSEMBLE_REJECTED_OR_UNDERPOWERED**

Two fixed ridge models with 180-day and 365-day training windows are activated only by their own purged prior validation and must agree before a later fifteen-minute execution confirmation.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `109 / 0.29863013698630136`
- gross / net mean: `-28.822655178577804 / -48.82265517857782` bp
- win rate / payoff: `0.29357798165137616 / 1.1259407658231981`
- net t-stat: `-3.2107486588366174`
- positive months: `0 / 2`
- symbol counts: `{'XRPUSDT': 43, 'SOLUSDT': 29, 'ETHUSDT': 20, 'BTCUSDT': 17}`
- exit reasons: `{'TRAILING_STOP': 74, 'FOUR_HOUR_CAP': 35}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `True`

## Decision
The frozen walk-forward ensemble did not survive every split. Do not tune windows, features, validation activation, confirmation or stops.

This is an economic mechanism screen. A pass still requires the same online model updates inside frozen NautilusTrader execution, current-NAV 3% sizing and one continuous account.
