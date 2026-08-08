# Candidate 15 V19 — Funding-settlement state diagnostic

**V19_FUNDING_SETTLEMENT_ROUTER_REJECTED_OR_UNDERPOWERED**

The realized funding-rate value is not used by the signal. Funding archives provide settlement timestamps only.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- selected independent episodes / day: `6 / 0.01643835616438356`
- gross / net mean: `4.074173914626241 / -11.925826085373759` bp
- win rate / payoff: `0.6666666666666666 / 0.34609367656980383`
- net t-stat: `-0.27243845757396473`
- positive months: `4 / 6`
- route stats: `{'SPOT_CONFIRMED_FUNDING_CONTINUATION': {'trades': 6, 'mean_net_bps': -11.925826085373759, 'win_rate': 0.6666666666666666, 'net_t_stat': -0.27243845757396473}}`
- symbol counts: `{'ETHUSDT': 3, 'SOLUSDT': 1, 'XRPUSDT': 2}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- selected independent episodes / day: `2 / 0.005479452054794521`
- gross / net mean: `-21.92220305522796 / -37.92220305522796` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `-1.4936394105560258`
- positive months: `0 / 2`
- route stats: `{'FUNDING_CROWD_UNWIND_REVERSAL': {'trades': 1, 'mean_net_bps': -12.533074469560187, 'win_rate': 0.0, 'net_t_stat': None}, 'SPOT_CONFIRMED_FUNDING_CONTINUATION': {'trades': 1, 'mean_net_bps': -63.31133164089574, 'win_rate': 0.0, 'net_t_stat': None}}`
- symbol counts: `{'XRPUSDT': 2}`

## Latest July 2026 pulse
- interval: `2026-07-01 -> 2026-08-01`
- selected independent episodes / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- route stats: `{}`
- symbol counts: `{}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_independent_frequency: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `False`

## Cross-split route evidence
`{'FUNDING_CROWD_UNWIND_REVERSAL': {'positive_across_all_declared_splits': False, 'splits': {'development': None, 'stability': {'trades': 1, 'mean_net_bps': -12.533074469560187, 'win_rate': 0.0, 'net_t_stat': None}, 'latest_pulse': None}}, 'SPOT_CONFIRMED_FUNDING_CONTINUATION': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 6, 'mean_net_bps': -11.925826085373759, 'win_rate': 0.6666666666666666, 'net_t_stat': -0.27243845757396473}, 'stability': {'trades': 1, 'mean_net_bps': -63.31133164089574, 'win_rate': 0.0, 'net_t_stat': None}, 'latest_pulse': None}}}`

## Decision
The integrated funding-settlement router failed at least one predeclared gate. Cross-split-positive routes: []. Do not tune numeric thresholds; retain only those routes and move to another independent causal family.

This mechanism screen does not synthesize account NAV. A surviving route still requires frozen NautilusTrader orders, event-extreme invalidation, current-NAV 3% risk sizing, one global slot and continuous-account validation.
