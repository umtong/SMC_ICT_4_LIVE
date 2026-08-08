# Candidate 15 V31 — Squeeze expansion diagnostic

**V31_SQUEEZE_EXPANSION_REJECTED_OR_UNDERPOWERED**

A completed one-hour BB/Keltner squeeze creates only context; a later completed fifteen-minute breakout chooses direction and a separate five-minute bar confirms entry.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `203 / 0.5561643835616439`
- gross / net mean: `6.119141934488938 / -13.88085806551106` bp
- win rate / payoff: `0.17733990147783252 / 1.1700211013458373`
- net t-stat: `-7.867962543877577`
- positive months: `2 / 12`
- route stats: `{'BREADTH_SQUEEZE_RELEASE': {'trades': 155, 'mean_net_bps': -15.313644722822421, 'win_rate': 0.1870967741935484, 'payoff_ratio': 0.9827683612948074, 'net_t_stat': -7.485363533122384, 'mean_holding_minutes': 444.2903225806452}, 'RELATIVE_SQUEEZE_RELEASE': {'trades': 48, 'mean_net_bps': -9.254983442659791, 'win_rate': 0.14583333333333334, 'payoff_ratio': 1.8007803180560515, 'net_t_stat': -2.774071629568776, 'mean_holding_minutes': 500.8333333333333}}`
- symbol counts: `{'ETHUSDT': 66, 'XRPUSDT': 54, 'BTCUSDT': 51, 'SOLUSDT': 32}`
- exit reasons: `{'TRAILING_STOP': 201, 'TWELVE_HOUR_CAP': 2}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `178 / 0.4876712328767123`
- gross / net mean: `9.144470055359913 / -10.855529944640086` bp
- win rate / payoff: `0.20224719101123595 / 1.214743338229203`
- net t-stat: `-4.901317684745663`
- positive months: `2 / 12`
- route stats: `{'BREADTH_SQUEEZE_RELEASE': {'trades': 123, 'mean_net_bps': -7.064083091640962, 'win_rate': 0.2032520325203252, 'payoff_ratio': 1.535956957525349, 'net_t_stat': -2.4036011473630416, 'mean_holding_minutes': 475.4471544715447}, 'RELATIVE_SQUEEZE_RELEASE': {'trades': 55, 'mean_net_bps': -19.334404090622964, 'win_rate': 0.2, 'payoff_ratio': 0.6855808747293415, 'net_t_stat': -5.554943549212026, 'mean_holding_minutes': 448.6363636363636}}`
- symbol counts: `{'SOLUSDT': 67, 'XRPUSDT': 48, 'BTCUSDT': 36, 'ETHUSDT': 27}`
- exit reasons: `{'TRAILING_STOP': 176, 'TWELVE_HOUR_CAP': 2}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `15 / 0.4838709677419355`
- gross / net mean: `-12.73667318930814 / -32.736673189308146` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `-3.7026829930336407`
- positive months: `0 / 1`
- route stats: `{'BREADTH_SQUEEZE_RELEASE': {'trades': 13, 'mean_net_bps': -36.59601166683099, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': -4.385236604537279, 'mean_holding_minutes': 636.1538461538462}, 'RELATIVE_SQUEEZE_RELEASE': {'trades': 2, 'mean_net_bps': -7.650973084409677, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': -2.369298784773438, 'mean_holding_minutes': 540.0}}`
- symbol counts: `{'SOLUSDT': 9, 'ETHUSDT': 3, 'XRPUSDT': 3}`
- exit reasons: `{'TRAILING_STOP': 13, 'TWELVE_HOUR_CAP': 2}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `3 / 0.42857142857142855`
- gross / net mean: `5.575197352793919 / -14.424802647206079` bp
- win rate / payoff: `0.3333333333333333 / 0.35462031791109255`
- net t-stat: `-0.6223805457736976`
- positive months: `0 / 1`
- route stats: `{'BREADTH_SQUEEZE_RELEASE': {'trades': 3, 'mean_net_bps': -14.424802647206079, 'win_rate': 0.3333333333333333, 'payoff_ratio': 0.35462031791109255, 'net_t_stat': -0.6223805457736976, 'mean_holding_minutes': 476.6666666666667}}`
- symbol counts: `{'SOLUSDT': 1, 'XRPUSDT': 1, 'ETHUSDT': 1}`
- exit reasons: `{'TRAILING_STOP': 3}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `True`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `True`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `True`
- symbol_concentration: `True`

## Decision
No fixed squeeze-release route survived all splits. Do not tune channel, breakout, confirmation or stop thresholds.

This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.
