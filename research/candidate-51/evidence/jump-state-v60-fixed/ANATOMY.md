# Jump derivatives-state router anatomy

- source periods: 10
- unique jump episodes: 1306
- timeframes: 60m, 120m, 240m
- fixed jump threshold: 2.0 prior sigma
- cost screen: inherited v57 19 bp round trip
- no fitted state or jump thresholds

## Route results

| route | horizon | trades | trades/day | mean bp | median bp | win % | PF | ex-best bp | status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| rejected_unwind_delayed_reversal | 240m | 67 | 0.479 | 1.36 | 3.19 | 51.5 | 1.02 | -11.59 | route_rejected |
| rejected_unwind_delayed_reversal | 480m | 61 | 0.436 | 0.90 | -39.30 | 32.8 | 1.01 | -20.44 | route_rejected |
| rejected_unwind_delayed_reversal | 60m | 80 | 0.571 | -3.47 | -7.86 | 46.2 | 0.92 | -10.12 | route_rejected |
| persistent15_delayed_continuation | 120m | 221 | 1.579 | -3.66 | -24.61 | 38.6 | 0.95 | -10.72 | route_rejected |
| rejected15_delayed_reversal | 240m | 124 | 0.886 | -10.52 | -27.45 | 37.4 | 0.88 | -17.64 | route_rejected |
| accepted_unwind_direct_continuation | 60m | 196 | 1.400 | -10.58 | -30.64 | 33.2 | 0.80 | -14.77 | route_rejected |
| persistent15_delayed_continuation | 60m | 269 | 1.921 | -15.03 | -26.14 | 36.8 | 0.75 | -19.77 | route_rejected |
| blind_direct_reversal | 480m | 217 | 1.550 | -16.43 | -21.47 | 39.0 | 0.86 | -32.04 | route_rejected |
| rejected15_delayed_reversal | 60m | 159 | 1.136 | -17.70 | -21.49 | 30.2 | 0.61 | -21.11 | route_rejected |
| accepted_unwind_direct_continuation | 120m | 169 | 1.207 | -18.31 | -33.96 | 37.3 | 0.75 | -24.79 | route_rejected |
| rejected15_delayed_reversal | 120m | 143 | 1.021 | -19.34 | -22.49 | 38.5 | 0.69 | -26.32 | route_rejected |
| rejected_unwind_delayed_reversal | 120m | 78 | 0.557 | -21.39 | -7.07 | 43.6 | 0.68 | -31.91 | route_rejected |
| accepted_unwind_delayed_continuation | 60m | 196 | 1.400 | -21.83 | -22.34 | 35.2 | 0.59 | -24.21 | route_rejected |
| accepted_unwind_direct_continuation | 240m | 143 | 1.021 | -22.65 | -28.35 | 44.0 | 0.77 | -29.16 | route_rejected |
| accepted_unwind_direct_continuation | 480m | 120 | 0.857 | -24.56 | -19.00 | 46.1 | 0.80 | -36.51 | route_rejected |
| blind_direct_reversal | 120m | 451 | 3.221 | -24.68 | -8.15 | 45.8 | 0.67 | -28.20 | route_rejected |
| blind_direct_reversal | 240m | 313 | 2.236 | -25.51 | -14.98 | 44.7 | 0.72 | -33.89 | route_rejected |
| accepted_unwind_delayed_continuation | 120m | 169 | 1.207 | -26.42 | -31.23 | 36.1 | 0.65 | -35.74 | route_rejected |
| accepted_unwind_delayed_continuation | 240m | 143 | 1.021 | -27.51 | -33.65 | 46.1 | 0.71 | -42.06 | route_rejected |
| blind_direct_reversal | 60m | 607 | 4.336 | -28.37 | -9.98 | 42.2 | 0.51 | -29.23 | route_rejected |
| persistent15_delayed_continuation | 240m | 182 | 1.300 | -31.02 | -29.25 | 43.1 | 0.71 | -44.41 | route_rejected |
| persistent15_delayed_continuation | 480m | 142 | 1.014 | -40.78 | -20.46 | 43.4 | 0.73 | -52.38 | route_rejected |
| rejected15_delayed_reversal | 480m | 103 | 0.736 | -48.37 | -53.54 | 33.7 | 0.62 | -57.23 | route_rejected |
| accepted_unwind_delayed_continuation | 480m | 120 | 0.857 | -49.47 | -26.60 | 43.5 | 0.61 | -58.67 | route_rejected |

## Composite state router

| horizon | trades | trades/day | mean bp | median bp | PF | status |
|---:|---:|---:|---:|---:|---:|---|
| 60m | 472 | 3.371 | -13.64 | -23.56 | 0.75 | composite_rejected |
| 120m | 362 | 2.586 | 0.55 | -21.98 | 1.01 | composite_rejected |
| 240m | 266 | 1.900 | -30.22 | -28.35 | 0.70 | composite_rejected |
| 480m | 192 | 1.371 | -58.76 | -35.47 | 0.61 | composite_rejected |

Blind reversal is the reused baseline, not a straw man. The router advances only if pre-existing derivatives states reverse the baseline loss mechanism, survive chronological and post-publication partitions, remain after the best event is removed, and preserve enough global one-slot opportunity. A surviving route still requires complete structural geometry and NautilusTrader validation.
