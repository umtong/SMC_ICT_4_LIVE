# Candidate 15 V32 — Residual four-hour overreaction reclaim

**V32_RESIDUAL_OVERREACTION_RECLAIM_REJECTED_OR_UNDERPOWERED**

A completed four-hour cross-sectional residual overreaction is traded only after a separately completed opposite reclaim bar; the target is the event auction midpoint.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `8 / 0.021917808219178082`
- gross / net mean: `-160.16854130677646 / -180.1685413067765` bp
- win rate / payoff: `0.125 / 0.38912957887404637`
- net t-stat: `-3.1722860035344653`
- positive months: `1 / 6`
- mean holding minutes: `255.0`
- symbol counts: `{'XRPUSDT': 5, 'ETHUSDT': 3}`
- exit reasons: `{'EVENT_EXTREME_INVALIDATED': 6, 'EIGHT_HOUR_CAP': 1, 'EVENT_MIDPOINT_REACHED': 1}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `5 / 0.0136986301369863`
- gross / net mean: `45.32971436042188 / 25.329714360421878` bp
- win rate / payoff: `0.8 / 0.6295320257157268`
- net t-stat: `0.9121742648106336`
- positive months: `3 / 4`
- mean holding minutes: `89.0`
- symbol counts: `{'XRPUSDT': 4, 'BTCUSDT': 1}`
- exit reasons: `{'EVENT_MIDPOINT_REACHED': 4, 'EVENT_EXTREME_INVALIDATED': 1}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- mean holding minutes: `None`
- symbol counts: `{}`
- exit reasons: `{}`

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
- positive_stability_mean_net: `True`
- stability_net_t_stat: `False`
- stability_positive_month_share: `True`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `False`

## Decision
The fixed residual overreaction reclaim did not survive all splits. Do not tune overreaction, reclaim, stop or midpoint target thresholds.

This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.
