# Candidate 15 V17 — Open-interest state-transition diagnostic

**V17_OPEN_INTEREST_ROUTER_REJECTED_OR_UNDERPOWERED**

## Development
- selected trades / day: `100 / 0.5434782608695652`
- gross / net mean: `22.9822860087343 / 6.982286008734295` bp
- win rate / payoff: `0.44 / 1.4351197468076198`
- net t-stat: `0.3559540215741479`

## Untouched evaluation
- selected trades / day: `529 / 1.090721649484536`
- gross / net mean: `18.079333823927474 / 2.0793338239274703` bp
- win rate / payoff: `0.4763705103969754 / 1.1533289227011039`
- net t-stat: `0.3368976673375452`
- positive months: `8 / 16`
- route stats: `{'DELEVERAGING_EXHAUSTION_REVERSAL': {'trades': 278, 'mean_net_bps': -8.6045761214404, 'win_rate': 0.43884892086330934, 'net_t_stat': -1.2947486664156014}, 'POSITION_BUILDUP_ACCEPTANCE_CONTINUATION': {'trades': 251, 'mean_net_bps': 13.912508982542079, 'win_rate': 0.5179282868525896, 'net_t_stat': 1.3013365405309276}}`
- symbol counts: `{'BTCUSDT': 158, 'SOLUSDT': 106, 'XRPUSDT': 99, 'ETHUSDT': 166}`

## Advance checks
- positive_development_mean_net: `True`
- positive_evaluation_mean_net: `True`
- evaluation_net_t_stat: `False`
- positive_month_share: `False`
- independent_frequency: `True`
- symbol_concentration: `True`

## Decision
The OI state family did not jointly survive costs, stability and independent frequency. Do not tune its numeric thresholds after evaluation; preserve only an independently positive route and move to a different causal family.

This is a mechanism screen rather than synthetic NAV. Any surviving route still requires frozen NautilusTrader orders, 3% current-NAV risk sizing, one global slot and continuous-account validation.
