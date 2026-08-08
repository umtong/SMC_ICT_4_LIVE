# Candidate 15 V15 — Quarter-hour public-state delivery diagnostic

**V15_QUARTER_HOUR_PUBLIC_DELIVERY_REJECTED_OR_UNDERPOWERED**

## Development
- interval: `2024-07-01 -> 2025-01-01`
- selected independent episodes: `71` (`0.3859` per day)
- gross / net mean: `-31.76641488025288 / -51.766414880252896` bp
- after-cost win rate: `0.39436619718309857`
- net t-stat: `-1.640843247484481`

## Untouched evaluation
- interval: `2025-01-01 -> 2026-05-01`
- selected independent episodes: `237` (`0.4887` per day)
- gross / net mean: `36.35952434284628 / 16.359524342846257` bp
- after-cost win rate: `0.510548523206751`
- payoff ratio: `1.1202646685254407`
- net t-stat: `0.8461344620656478`
- positive months: `3 / 4`
- route counts: `{'QH_PUBLIC_DELIVERY_8H': 237}`
- symbol counts: `{'ETHUSDT': 65, 'XRPUSDT': 66, 'SOLUSDT': 77, 'BTCUSDT': 29}`

## Advance checks
- positive_development_mean_net: `False`
- positive_evaluation_mean_net: `True`
- evaluation_net_t_stat: `False`
- positive_evaluation_month_share: `True`
- independent_frequency: `False`
- symbol_concentration: `True`

## Decision
The route did not jointly survive costs, time stability and independent-frequency gates. Do not threshold-tune this family; retain only any route with independently positive evaluation evidence and move to a different causal scenario.

This diagnostic does not synthesize account NAV. It only determines whether the causal route is economically strong enough to justify a frozen NautilusTrader implementation.
