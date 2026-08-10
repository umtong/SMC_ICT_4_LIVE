# Quarter-hour opening order-flow post-sample audit

- source runs: 12
- assets: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- evaluation periods: 3
- evaluation days: 9
- events: 3456
- cost screen: 19 bp round trip
- signal window: complete first 10 seconds of each UTC quarter-hour
- causal entry: first observed aggregate trade at or after boundary+10 seconds
- no fitted threshold; normalized imbalance sign is the direction
- mechanism diagnostic, not NautilusTrader NAV

## Global one-slot expected-policy results

| policy | horizon | trades | trades/day | mean bp | median bp | win % | PF | post-publication bp | status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| reversal | 30m | 333 | 37.000 | -17.71 | -16.54 | 28.8 | 0.36 | -14.72 | diagnostic |
| continuation | 60m | 190 | 21.111 | -20.61 | -21.09 | 34.7 | 0.42 | -17.63 | diagnostic |
| continuation | 120m | 102 | 11.333 | -36.48 | -28.82 | 33.3 | 0.26 | -28.80 | diagnostic |
| continuation | 240m | 54 | 6.000 | -47.51 | -25.61 | 37.0 | 0.35 | -4.89 | mechanism_not_stable |
| continuation | 480m | 27 | 3.000 | -32.83 | -67.61 | 37.0 | 0.66 | -48.82 | mechanism_not_stable |
| continuation | 720m | 18 | 2.000 | -4.76 | -59.83 | 44.4 | 0.96 | -30.02 | mechanism_not_stable |

## Interpretation contract

The paper's 2021--2024 result is not accepted by citation. The family advances only if the fixed 4h--12h continuation prediction remains positive after 19 bp in global one-slot routing, in every frozen chronological period including the July 2026 post-publication period, and after removing the best event. Fixed magnitude, burst, phase, funding and cross-asset breadth groups are diagnostics of failure or portability, not an optimized filter search.
