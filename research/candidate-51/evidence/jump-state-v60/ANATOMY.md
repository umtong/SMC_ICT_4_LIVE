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
| blind_direct_reversal | 60m | 607 | 4.336 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| blind_direct_reversal | 120m | 451 | 3.221 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| blind_direct_reversal | 240m | 313 | 2.236 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| blind_direct_reversal | 480m | 217 | 1.550 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_direct_continuation | 60m | 196 | 1.400 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_direct_continuation | 120m | 169 | 1.207 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_direct_continuation | 240m | 143 | 1.021 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_direct_continuation | 480m | 120 | 0.857 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_delayed_continuation | 60m | 196 | 1.400 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_delayed_continuation | 120m | 169 | 1.207 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_delayed_continuation | 240m | 143 | 1.021 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| accepted_unwind_delayed_continuation | 480m | 120 | 0.857 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected_unwind_delayed_reversal | 60m | 80 | 0.571 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected_unwind_delayed_reversal | 120m | 78 | 0.557 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected_unwind_delayed_reversal | 240m | 67 | 0.479 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected_unwind_delayed_reversal | 480m | 61 | 0.436 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| persistent15_delayed_continuation | 60m | 269 | 1.921 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| persistent15_delayed_continuation | 120m | 221 | 1.579 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| persistent15_delayed_continuation | 240m | 182 | 1.300 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| persistent15_delayed_continuation | 480m | 142 | 1.014 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected15_delayed_reversal | 60m | 159 | 1.136 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected15_delayed_reversal | 120m | 143 | 1.021 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected15_delayed_reversal | 240m | 124 | 0.886 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |
| rejected15_delayed_reversal | 480m | 103 | 0.736 | 0.00 | 0.00 | 0.0 | na | 0.00 | route_rejected |

## Composite state router

| horizon | trades | trades/day | mean bp | median bp | PF | status |
|---:|---:|---:|---:|---:|---:|---|
| 60m | 472 | 3.371 | 0.00 | 0.00 | na | composite_rejected |
| 120m | 362 | 2.586 | 0.00 | 0.00 | na | composite_rejected |
| 240m | 266 | 1.900 | 0.00 | 0.00 | na | composite_rejected |
| 480m | 192 | 1.371 | 0.00 | 0.00 | na | composite_rejected |

Blind reversal is the reused baseline, not a straw man. The router advances only if pre-existing derivatives states reverse the baseline loss mechanism, survive chronological and post-publication partitions, remain after the best event is removed, and preserve enough global one-slot opportunity. A surviving route still requires complete structural geometry and NautilusTrader validation.
