# Candidate 15 V36 — CVD boundary divergence

**V36_CVD_BOUNDARY_DIVERGENCE_REJECTED_OR_UNDERPOWERED**

A new completed 24-hour price boundary is treated as failed only when rolling signed taker flow does not confirm it and a separate fifteen-minute bar reclaims the old boundary.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `426 / 1.167123287671233`
- gross / net mean: `-9.363992902497914 / -29.363992902497923` bp
- win rate / payoff: `0.4061032863849765 / 0.8128474983558835`
- net t-stat: `-4.759319822493137`
- positive months: `2 / 12`
- route stats: `{'FAILED_HIGH_CVD_DIVERGENCE': {'trades': 232, 'mean_net_bps': -25.11535837888923, 'win_rate': 0.4396551724137931, 'payoff_ratio': 0.7665139274879365, 'net_t_stat': -2.9479171861868574}, 'FAILED_LOW_CVD_DIVERGENCE': {'trades': 194, 'mean_net_bps': -34.44483418846297, 'win_rate': 0.36597938144329895, 'payoff_ratio': 0.8776348322655186, 'net_t_stat': -3.852749682081774}}`
- symbol counts: `{'SOLUSDT': 131, 'BTCUSDT': 103, 'XRPUSDT': 98, 'ETHUSDT': 94}`
- exit reasons: `{'BOUNDARY_EXTREME_INVALIDATED': 205, 'PRIOR_VALUE_REACHED': 111, 'SIX_HOUR_CAP': 110}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `387 / 1.0602739726027397`
- gross / net mean: `-3.0971812837680073 / -23.097181283768016` bp
- win rate / payoff: `0.37467700258397935 / 0.9852050423702512`
- net t-stat: `-3.966797117759244`
- positive months: `2 / 12`
- route stats: `{'FAILED_HIGH_CVD_DIVERGENCE': {'trades': 201, 'mean_net_bps': -20.863767952331358, 'win_rate': 0.3880597014925373, 'payoff_ratio': 0.9710014387183745, 'net_t_stat': -2.697691271175353}, 'FAILED_LOW_CVD_DIVERGENCE': {'trades': 186, 'mean_net_bps': -25.510708593546337, 'win_rate': 0.3602150537634409, 'payoff_ratio': 1.0032183171604478, 'net_t_stat': -2.902657075056775}}`
- symbol counts: `{'XRPUSDT': 104, 'BTCUSDT': 99, 'SOLUSDT': 96, 'ETHUSDT': 88}`
- exit reasons: `{'BOUNDARY_EXTREME_INVALIDATED': 195, 'PRIOR_VALUE_REACHED': 99, 'SIX_HOUR_CAP': 93}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `31 / 1.0`
- gross / net mean: `-13.846614773919022 / -33.84661477391902` bp
- win rate / payoff: `0.3870967741935484 / 0.5780587386654927`
- net t-stat: `-2.4072344186625036`
- positive months: `0 / 1`
- route stats: `{'FAILED_HIGH_CVD_DIVERGENCE': {'trades': 20, 'mean_net_bps': -23.884814583387353, 'win_rate': 0.4, 'payoff_ratio': 0.7507055504114459, 'net_t_stat': -1.3037441699025163}, 'FAILED_LOW_CVD_DIVERGENCE': {'trades': 11, 'mean_net_bps': -51.958978756703885, 'win_rate': 0.36363636363636365, 'payoff_ratio': 0.3135265618256836, 'net_t_stat': -2.4196547495031395}}`
- symbol counts: `{'SOLUSDT': 10, 'BTCUSDT': 8, 'XRPUSDT': 7, 'ETHUSDT': 6}`
- exit reasons: `{'BOUNDARY_EXTREME_INVALIDATED': 15, 'PRIOR_VALUE_REACHED': 11, 'SIX_HOUR_CAP': 5}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `8 / 1.1428571428571428`
- gross / net mean: `35.87789035963115 / 15.877890359631154` bp
- win rate / payoff: `0.5 / 1.9210485780291642`
- net t-stat: `0.7014539585589398`
- positive months: `1 / 1`
- route stats: `{'FAILED_HIGH_CVD_DIVERGENCE': {'trades': 5, 'mean_net_bps': -6.082360675839764, 'win_rate': 0.4, 'payoff_ratio': 0.9276627259363751, 'net_t_stat': -0.43684417208644977}, 'FAILED_LOW_CVD_DIVERGENCE': {'trades': 3, 'mean_net_bps': 52.47830875208269, 'win_rate': 0.6666666666666666, 'payoff_ratio': 1.8523666544910895, 'net_t_stat': 0.9480155262782786}}`
- symbol counts: `{'XRPUSDT': 4, 'BTCUSDT': 3, 'SOLUSDT': 1}`
- exit reasons: `{'BOUNDARY_EXTREME_INVALIDATED': 3, 'PRIOR_VALUE_REACHED': 3, 'SIX_HOUR_CAP': 2}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `True`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `True`
- positive_latest_pulse_mean_net: `True`
- latest_pulse_trade_count: `True`
- symbol_concentration: `True`

## Cross-split route evidence
`{'FAILED_HIGH_CVD_DIVERGENCE': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 232, 'mean_net_bps': -25.11535837888923, 'win_rate': 0.4396551724137931, 'payoff_ratio': 0.7665139274879365, 'net_t_stat': -2.9479171861868574}, 'stability': {'trades': 201, 'mean_net_bps': -20.863767952331358, 'win_rate': 0.3880597014925373, 'payoff_ratio': 0.9710014387183745, 'net_t_stat': -2.697691271175353}, 'july_confirmation': {'trades': 20, 'mean_net_bps': -23.884814583387353, 'win_rate': 0.4, 'payoff_ratio': 0.7507055504114459, 'net_t_stat': -1.3037441699025163}, 'latest_pulse': {'trades': 5, 'mean_net_bps': -6.082360675839764, 'win_rate': 0.4, 'payoff_ratio': 0.9276627259363751, 'net_t_stat': -0.43684417208644977}}}, 'FAILED_LOW_CVD_DIVERGENCE': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 194, 'mean_net_bps': -34.44483418846297, 'win_rate': 0.36597938144329895, 'payoff_ratio': 0.8776348322655186, 'net_t_stat': -3.852749682081774}, 'stability': {'trades': 186, 'mean_net_bps': -25.510708593546337, 'win_rate': 0.3602150537634409, 'payoff_ratio': 1.0032183171604478, 'net_t_stat': -2.902657075056775}, 'july_confirmation': {'trades': 11, 'mean_net_bps': -51.958978756703885, 'win_rate': 0.36363636363636365, 'payoff_ratio': 0.3135265618256836, 'net_t_stat': -2.4196547495031395}, 'latest_pulse': {'trades': 3, 'mean_net_bps': 52.47830875208269, 'win_rate': 0.6666666666666666, 'payoff_ratio': 1.8523666544910895, 'net_t_stat': 0.9480155262782786}}}}`

## Decision
Cross-split-positive fixed routes: []. Do not tune boundary, flow divergence, confirmation, stop or value-target rules.

This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.
