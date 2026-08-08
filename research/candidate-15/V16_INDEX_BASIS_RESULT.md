# Candidate 15 V16 — Futures-index basis state-transition diagnostic

**V16_INDEX_BASIS_ROUTER_REJECTED_OR_UNDERPOWERED**

## Development
- trades / day: `384 / 2.0869565217391304`
- gross / net mean: `7.099155955610307 / -8.900844044389697` bp
- net t-stat: `-1.3192420000259752`

## Untouched evaluation
- trades / day: `1013 / 2.088659793814433`
- gross / net mean: `2.5462241395374323 / -13.45377586046257` bp
- win rate: `0.42250740375123397`
- net t-stat: `-3.5531564276025707`
- positive months: `2 / 16`
- route stats: `{'INDEX_CONFIRMED_DISCOVERY': {'trades': 130, 'mean_net_bps': -28.195376124575017, 'win_rate': 0.35384615384615387, 'net_t_stat': -1.6405542519795062}, 'REFLEXIVE_BASIS_REPAIR': {'trades': 883, 'mean_net_bps': -11.2834383357348, 'win_rate': 0.43261608154020387, 'net_t_stat': -3.1952518025810024}}`
- symbol counts: `{'SOLUSDT': 208, 'XRPUSDT': 226, 'ETHUSDT': 285, 'BTCUSDT': 294}`

## Advance checks
- positive_development_mean_net: `False`
- positive_evaluation_mean_net: `False`
- evaluation_net_t_stat: `False`
- positive_month_share: `False`
- independent_frequency: `True`
- symbol_concentration: `True`

## Decision
The basis-transition family did not jointly survive costs, stability and frequency. Do not tune numeric thresholds after this evaluation; preserve only a route with independently positive evidence and move to a different mechanism.

This is a causal mechanism screen, not synthesized account NAV. A surviving route still requires frozen NautilusTrader orders, risk sizing and continuous NAV validation.
