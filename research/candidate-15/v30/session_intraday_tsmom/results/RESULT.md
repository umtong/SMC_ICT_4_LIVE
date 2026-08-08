# Candidate 15 V30 — Session intraday time-series momentum

**V30_SESSION_INTRADAY_TSMOM_REJECTED_OR_UNDERPOWERED**

The first completed half-hour of each Asia, Europe and America session sets direction only when activity is high. Entry requires a separate first five-minute confirmation in the final half-hour.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `179 / 0.4904109589041096`
- gross / net mean: `2.239448768505529 / -17.760551231494475` bp
- win rate / payoff: `0.3128491620111732 / 0.9583908573488337`
- net t-stat: `-3.8838994906935427`
- positive months: `0 / 12`
- route stats: `{'HIGH_ACTIVITY_SESSION_TSMOM': {'trades': 37, 'mean_net_bps': -2.647622490550268, 'win_rate': 0.40540540540540543, 'payoff_ratio': 1.3398615350979275, 'net_t_stat': -0.1839441120333193}, 'SESSION_BREADTH_TSMOM': {'trades': 142, 'mean_net_bps': -21.698427030191205, 'win_rate': 0.2887323943661972, 'payoff_ratio': 0.7793637374060005, 'net_t_stat': -4.982942314197231}}`
- session counts: `{'ASIA': 71, 'AMERICA': 57, 'EUROPE': 51}`
- symbol counts: `{'XRPUSDT': 53, 'ETHUSDT': 48, 'SOLUSDT': 40, 'BTCUSDT': 38}`
- exit reasons: `{'SESSION_END': 114, 'CONFIRMATION_LEG_INVALIDATED': 65}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `147 / 0.40273972602739727`
- gross / net mean: `-0.2619911480334823 / -20.261991148033484` bp
- win rate / payoff: `0.17006802721088435 / 1.299312938583495`
- net t-stat: `-5.809534657582432`
- positive months: `0 / 12`
- route stats: `{'HIGH_ACTIVITY_SESSION_TSMOM': {'trades': 18, 'mean_net_bps': 5.38044720289931, 'win_rate': 0.3888888888888889, 'payoff_ratio': 2.1267550668315125, 'net_t_stat': 0.32725369899154444}, 'SESSION_BREADTH_TSMOM': {'trades': 129, 'mean_net_bps': -23.840005801652012, 'win_rate': 0.13953488372093023, 'payoff_ratio': 1.1565180001256707, 'net_t_stat': -7.544726299576638}}`
- session counts: `{'AMERICA': 54, 'ASIA': 48, 'EUROPE': 45}`
- symbol counts: `{'BTCUSDT': 53, 'XRPUSDT': 38, 'SOLUSDT': 29, 'ETHUSDT': 27}`
- exit reasons: `{'SESSION_END': 90, 'CONFIRMATION_LEG_INVALIDATED': 57}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `9 / 0.2903225806451613`
- gross / net mean: `-4.50694438635509 / -24.50694438635509` bp
- win rate / payoff: `0.2222222222222222 / 0.25757859793188154`
- net t-stat: `-2.7754611797352475`
- positive months: `0 / 1`
- route stats: `{'HIGH_ACTIVITY_SESSION_TSMOM': {'trades': 1, 'mean_net_bps': -33.970999147034924, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None}, 'SESSION_BREADTH_TSMOM': {'trades': 8, 'mean_net_bps': -23.323937541270112, 'win_rate': 0.25, 'payoff_ratio': 0.2575268522108555, 'net_t_stat': -2.3507629961487213}}`
- session counts: `{'ASIA': 5, 'EUROPE': 3, 'AMERICA': 1}`
- symbol counts: `{'ETHUSDT': 4, 'SOLUSDT': 2, 'XRPUSDT': 2, 'BTCUSDT': 1}`
- exit reasons: `{'SESSION_END': 5, 'CONFIRMATION_LEG_INVALIDATED': 4}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- route stats: `{}`
- session counts: `{}`
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

## Cross-split route evidence
`{'SESSION_BREADTH_TSMOM': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 142, 'mean_net_bps': -21.698427030191205, 'win_rate': 0.2887323943661972, 'payoff_ratio': 0.7793637374060005, 'net_t_stat': -4.982942314197231}, 'stability': {'trades': 129, 'mean_net_bps': -23.840005801652012, 'win_rate': 0.13953488372093023, 'payoff_ratio': 1.1565180001256707, 'net_t_stat': -7.544726299576638}, 'july_confirmation': {'trades': 8, 'mean_net_bps': -23.323937541270112, 'win_rate': 0.25, 'payoff_ratio': 0.2575268522108555, 'net_t_stat': -2.3507629961487213}, 'latest_pulse': None}}, 'HIGH_ACTIVITY_SESSION_TSMOM': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 37, 'mean_net_bps': -2.647622490550268, 'win_rate': 0.40540540540540543, 'payoff_ratio': 1.3398615350979275, 'net_t_stat': -0.1839441120333193}, 'stability': {'trades': 18, 'mean_net_bps': 5.38044720289931, 'win_rate': 0.3888888888888889, 'payoff_ratio': 2.1267550668315125, 'net_t_stat': 0.32725369899154444}, 'july_confirmation': {'trades': 1, 'mean_net_bps': -33.970999147034924, 'win_rate': 0.0, 'payoff_ratio': None, 'net_t_stat': None}, 'latest_pulse': None}}}`

## Decision
Cross-split-positive fixed routes: []. Do not tune session clocks, activity thresholds, confirmation or stop geometry; move to another independent mechanism.

This is an economic mechanism screen. A pass still requires exact NautilusTrader orders, current-NAV 3% sizing, one global slot and continuous-account validation.
