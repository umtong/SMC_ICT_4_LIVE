# Candidate 15 V35 — Beta-residual price discovery

**V35_BETA_RESIDUAL_DISCOVERY_REJECTED_OR_UNDERPOWERED**

The common crypto factor is removed with prior-only rolling beta. Only the unique 24-hour residual leader with high notional and aligned flow may enter after a separate fifteen-minute confirmation.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `109 / 0.29863013698630136`
- gross / net mean: `-9.04749670935552 / -29.04749670935554` bp
- win rate / payoff: `0.29357798165137616 / 1.6311658314163973`
- net t-stat: `-1.4195482387686815`
- positive months: `3 / 12`
- mean holding minutes: `164.40366972477065`
- symbol counts: `{'XRPUSDT': 35, 'BTCUSDT': 31, 'SOLUSDT': 23, 'ETHUSDT': 20}`
- exit reasons: `{'TRAILING_STOP': 93, 'SIX_HOUR_CAP': 16}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `98 / 0.2684931506849315`
- gross / net mean: `3.260532634844674 / -16.73946736515534` bp
- win rate / payoff: `0.30612244897959184 / 1.6769037060774405`
- net t-stat: `-0.909708375132812`
- positive months: `3 / 12`
- mean holding minutes: `178.57142857142858`
- symbol counts: `{'BTCUSDT': 33, 'SOLUSDT': 23, 'ETHUSDT': 22, 'XRPUSDT': 20}`
- exit reasons: `{'TRAILING_STOP': 79, 'SIX_HOUR_CAP': 19}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `7 / 0.22580645161290322`
- gross / net mean: `-15.920084638772083 / -35.92008463877209` bp
- win rate / payoff: `0.2857142857142857 / 0.9003914299153964`
- net t-stat: `-1.1423102399598297`
- positive months: `0 / 1`
- mean holding minutes: `152.14285714285714`
- symbol counts: `{'XRPUSDT': 4, 'BTCUSDT': 2, 'ETHUSDT': 1}`
- exit reasons: `{'TRAILING_STOP': 6, 'SIX_HOUR_CAP': 1}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- mean holding minutes: `None`
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
The fixed beta-residual discovery continuation did not survive every split. Do not tune beta, horizon, z-score, confirmation or stops.

This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.
