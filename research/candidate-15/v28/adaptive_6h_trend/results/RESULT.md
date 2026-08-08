# Candidate 15 V28 — Adaptive six-hour trend diagnostic

**V28_ADAPTIVE_6H_TREND_REJECTED_OR_UNDERPOWERED**

The policy uses completed six-hour momentum, a wholly subsequent fifteen-minute confirmation, entry at that confirmation close, and a five-minute causal ATR trailing stop.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `40 / 0.1095890410958904`
- gross / net mean: `39.13550764153876 / 19.135507641538734` bp
- win rate / payoff: `0.35 / 2.03198803187395`
- net t-stat: `0.2028369347801407`
- positive months: `3 / 10`
- mean holding minutes: `929.5`
- route stats: `{'BREADTH_ALIGNED_6H_TREND': {'trades': 31, 'mean_net_bps': 76.61278268342086, 'win_rate': 0.3548387096774194, 'payoff_ratio': 2.6248205830360805, 'net_t_stat': 0.6866562717003081, 'mean_holding_minutes': 942.9032258064516}, 'RELATIVE_6H_LEADER': {'trades': 9, 'mean_net_bps': -178.84177305827745, 'win_rate': 0.3333333333333333, 'payoff_ratio': 0.8406662537360517, 'net_t_stat': -1.1152775488739355, 'mean_holding_minutes': 883.3333333333334}}`
- symbol counts: `{'XRPUSDT': 16, 'BTCUSDT': 9, 'SOLUSDT': 8, 'ETHUSDT': 7}`
- exit reasons: `{'TRAILING_STOP': 22, 'TWENTY_FOUR_HOUR_CAP': 18}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `19 / 0.052054794520547946`
- gross / net mean: `138.41327853734376 / 118.41327853734373` bp
- win rate / payoff: `0.5263157894736842 / 1.842732063227223`
- net t-stat: `1.1339753266834303`
- positive months: `5 / 9`
- mean holding minutes: `1021.0526315789474`
- route stats: `{'BREADTH_ALIGNED_6H_TREND': {'trades': 18, 'mean_net_bps': 116.33569496261424, 'win_rate': 0.5, 'payoff_ratio': 1.974938578135459, 'net_t_stat': 1.0540223538885793, 'mean_holding_minutes': 997.7777777777778}, 'RELATIVE_6H_LEADER': {'trades': 1, 'mean_net_bps': 155.8097828824745, 'win_rate': 1.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 1440.0}}`
- symbol counts: `{'BTCUSDT': 6, 'ETHUSDT': 5, 'XRPUSDT': 4, 'SOLUSDT': 4}`
- exit reasons: `{'TWENTY_FOUR_HOUR_CAP': 11, 'TRAILING_STOP': 8}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- mean holding minutes: `None`
- route stats: `{}`
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
- route stats: `{}`
- symbol counts: `{}`
- exit reasons: `{}`

## Advance checks
- positive_development_mean_net: `True`
- positive_stability_mean_net: `True`
- stability_net_t_stat: `True`
- stability_positive_month_share: `False`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `True`

## Cross-split route evidence
`{'BREADTH_ALIGNED_6H_TREND': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 31, 'mean_net_bps': 76.61278268342086, 'win_rate': 0.3548387096774194, 'payoff_ratio': 2.6248205830360805, 'net_t_stat': 0.6866562717003081, 'mean_holding_minutes': 942.9032258064516}, 'stability': {'trades': 18, 'mean_net_bps': 116.33569496261424, 'win_rate': 0.5, 'payoff_ratio': 1.974938578135459, 'net_t_stat': 1.0540223538885793, 'mean_holding_minutes': 997.7777777777778}, 'july_confirmation': None, 'latest_pulse': None}}, 'RELATIVE_6H_LEADER': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 9, 'mean_net_bps': -178.84177305827745, 'win_rate': 0.3333333333333333, 'payoff_ratio': 0.8406662537360517, 'net_t_stat': -1.1152775488739355, 'mean_holding_minutes': 883.3333333333334}, 'stability': {'trades': 1, 'mean_net_bps': 155.8097828824745, 'win_rate': 1.0, 'payoff_ratio': None, 'net_t_stat': None, 'mean_holding_minutes': 1440.0}, 'july_confirmation': None, 'latest_pulse': None}}}`

## Decision
Cross-split-positive fixed routes: []. Do not tune the six-hour trend family; preserve only any cross-split-positive route and move to another independent mechanism.

This is a mechanism screen, not synthetic account NAV. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, fees/slippage/impact/funding, one global slot and continuous-account validation.
