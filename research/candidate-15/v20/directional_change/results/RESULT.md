# Candidate 15 V20 — Directional-change intrinsic-time diagnostic

**V20_DIRECTIONAL_CHANGE_ROUTER_REJECTED_OR_UNDERPOWERED**

Actual completed closes confirm every directional change; theoretical threshold-touch prices are never used.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- selected independent episodes / day: `952 / 2.6082191780821917`
- gross / net mean: `2.8478941696649764 / -17.15210583033503` bp
- win rate / payoff: `0.31407563025210083 / 1.4149330962631435`
- net t-stat: `-4.725598926194237`
- positive months: `1 / 12`
- mean holding minutes: `109.48004201680672`
- route stats: `{'DC_ACCEPTED_OVERSHOOT': {'trades': 888, 'mean_net_bps': -15.817055121702294, 'win_rate': 0.30518018018018017, 'net_t_stat': -4.285541376867324, 'mean_holding_minutes': 111.22184684684684}, 'DC_FAILED_TRANSITION_RECLAIM': {'trades': 64, 'mean_net_bps': -35.67593441261427, 'win_rate': 0.4375, 'net_t_stat': -2.0905377958149285, 'mean_holding_minutes': 85.3125}}`
- symbol counts: `{'XRPUSDT': 245, 'BTCUSDT': 218, 'ETHUSDT': 224, 'SOLUSDT': 265}`
- exit reasons: `{'FOUR_HOUR_CAP': 130, 'NEXT_DIRECTIONAL_CHANGE': 822}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- selected independent episodes / day: `874 / 2.3945205479452056`
- gross / net mean: `2.1443417658016397 / -17.855658234198366` bp
- win rate / payoff: `0.32379862700228834 / 1.2603115417001987`
- net t-stat: `-5.677441093060842`
- positive months: `1 / 12`
- mean holding minutes: `110.76659038901602`
- route stats: `{'DC_ACCEPTED_OVERSHOOT': {'trades': 801, 'mean_net_bps': -17.90392021474829, 'win_rate': 0.30337078651685395, 'net_t_stat': -5.384444920918009, 'mean_holding_minutes': 111.95380774032459}, 'DC_FAILED_TRANSITION_RECLAIM': {'trades': 73, 'mean_net_bps': -17.32609869419169, 'win_rate': 0.547945205479452, 'net_t_stat': -1.848048570119343, 'mean_holding_minutes': 97.73972602739725}}`
- symbol counts: `{'BTCUSDT': 192, 'SOLUSDT': 242, 'XRPUSDT': 209, 'ETHUSDT': 231}`
- exit reasons: `{'NEXT_DIRECTIONAL_CHANGE': 736, 'FOUR_HOUR_CAP': 138}`

## Latest July 2026 pulse
- interval: `2026-07-01 -> 2026-08-01`
- selected independent episodes / day: `89 / 2.870967741935484`
- gross / net mean: `-10.20333023674341 / -30.203330236743415` bp
- win rate / payoff: `0.21348314606741572 / 0.8863614922507936`
- net t-stat: `-6.002082010528402`
- positive months: `0 / 1`
- mean holding minutes: `108.31460674157303`
- route stats: `{'DC_ACCEPTED_OVERSHOOT': {'trades': 81, 'mean_net_bps': -33.3099650430206, 'win_rate': 0.1728395061728395, 'net_t_stat': -6.248179049713109, 'mean_holding_minutes': 112.8395061728395}, 'DC_FAILED_TRANSITION_RECLAIM': {'trades': 8, 'mean_net_bps': 1.2513471768130398, 'win_rate': 0.625, 'net_t_stat': 0.12673357178665978, 'mean_holding_minutes': 62.5}}`
- symbol counts: `{'ETHUSDT': 24, 'SOLUSDT': 23, 'BTCUSDT': 14, 'XRPUSDT': 28}`
- exit reasons: `{'NEXT_DIRECTIONAL_CHANGE': 81, 'FOUR_HOUR_CAP': 8}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_independent_frequency: `True`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `True`
- symbol_concentration: `True`

## Cross-split route evidence
`{'DC_ACCEPTED_OVERSHOOT': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 888, 'mean_net_bps': -15.817055121702294, 'win_rate': 0.30518018018018017, 'net_t_stat': -4.285541376867324, 'mean_holding_minutes': 111.22184684684684}, 'stability': {'trades': 801, 'mean_net_bps': -17.90392021474829, 'win_rate': 0.30337078651685395, 'net_t_stat': -5.384444920918009, 'mean_holding_minutes': 111.95380774032459}, 'latest_pulse': {'trades': 81, 'mean_net_bps': -33.3099650430206, 'win_rate': 0.1728395061728395, 'net_t_stat': -6.248179049713109, 'mean_holding_minutes': 112.8395061728395}}}, 'DC_FAILED_TRANSITION_RECLAIM': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 64, 'mean_net_bps': -35.67593441261427, 'win_rate': 0.4375, 'net_t_stat': -2.0905377958149285, 'mean_holding_minutes': 85.3125}, 'stability': {'trades': 73, 'mean_net_bps': -17.32609869419169, 'win_rate': 0.547945205479452, 'net_t_stat': -1.848048570119343, 'mean_holding_minutes': 97.73972602739725}, 'latest_pulse': {'trades': 8, 'mean_net_bps': 1.2513471768130398, 'win_rate': 0.625, 'net_t_stat': 0.12673357178665978, 'mean_holding_minutes': 62.5}}}}`

## Decision
The integrated directional-change router failed at least one predeclared gate. Cross-split-positive routes: []. Do not tune thresholds; retain only those routes and move to another independent causal family.

This mechanism screen does not synthesize account NAV. A surviving route still requires frozen NautilusTrader orders, 3% current-NAV risk sizing, one global slot, actual fees/funding and continuous-account validation.
