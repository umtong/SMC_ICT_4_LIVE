# Accepted forced-unwind structural geometry

- source periods: 10
- unique episodes: 84
- decision: **promote_best_fixed_geometry_to_nautilus**
- cost screen: 19 bp round trip
- no threshold search; all entry, stop, target and hold choices were frozen before this audit

| configuration | trades | trades/day | mean R | median R | PF | net bp | mean R ex-best | status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| direct:acceptance_extreme:time_only:480m | 39 | 0.279 | 0.479 | -1.000 | 1.93 | 63.66 | 0.257 | geometry_rejected |
| delayed15:acceptance_extreme:time_only:480m | 38 | 0.271 | 0.466 | -0.794 | 1.90 | 31.23 | 0.055 | geometry_rejected |
| delayed15:impulse_origin:time_only:480m | 39 | 0.279 | 0.282 | 0.043 | 2.39 | 49.78 | 0.179 | geometry_rejected |
| direct:acceptance_extreme:two_r:240m | 40 | 0.286 | 0.253 | 0.491 | 1.62 | 86.54 | 0.210 | geometry_rejected |
| delayed15:impulse_origin:two_r:480m | 39 | 0.279 | 0.248 | 0.079 | 2.23 | 45.91 | 0.208 | geometry_rejected |
| direct:acceptance_extreme:time_only:240m | 40 | 0.286 | 0.240 | -0.184 | 1.51 | 48.45 | 0.123 | geometry_rejected |
| direct:acceptance_extreme:impulse_extension:480m | 39 | 0.279 | 0.222 | 0.045 | 1.46 | 44.76 | 0.144 | geometry_rejected |
| direct:acceptance_extreme:impulse_extension:240m | 40 | 0.286 | 0.205 | 0.069 | 1.44 | 56.17 | 0.128 | geometry_rejected |
| delayed15:acceptance_extreme:two_r:240m | 39 | 0.279 | 0.176 | 0.028 | 1.44 | 119.61 | 0.129 | geometry_rejected |
| delayed15:acceptance_extreme:impulse_extension:480m | 38 | 0.271 | 0.149 | -0.349 | 1.30 | 37.98 | 0.055 | geometry_rejected |
| delayed15:impulse_origin:two_r:240m | 40 | 0.286 | 0.132 | 0.041 | 1.60 | 90.59 | 0.085 | geometry_rejected |
| delayed15:acceptance_extreme:two_r:480m | 38 | 0.271 | 0.115 | 0.028 | 1.28 | 20.97 | 0.068 | geometry_rejected |
| delayed15:acceptance_extreme:impulse_extension:240m | 39 | 0.279 | 0.106 | -0.252 | 1.22 | 33.30 | 0.013 | geometry_rejected |
| delayed15:acceptance_extreme:time_only:240m | 39 | 0.279 | 0.088 | -0.252 | 1.18 | -0.68 | -0.018 | geometry_rejected |
| delayed15:impulse_origin:impulse_extension:240m | 40 | 0.286 | 0.083 | 0.041 | 1.38 | 32.39 | 0.049 | geometry_rejected |
| delayed15:impulse_origin:time_only:240m | 40 | 0.286 | 0.055 | 0.006 | 1.25 | -3.41 | 0.004 | geometry_rejected |
| direct:impulse_origin:two_r:480m | 39 | 0.279 | 0.382 | 0.119 | 3.33 | 105.38 | 0.342 | geometry_survives_initial_falsification |
| direct:impulse_origin:time_only:480m | 39 | 0.279 | 0.379 | 0.115 | 3.31 | 85.34 | 0.258 | geometry_survives_initial_falsification |
| direct:impulse_origin:two_r:240m | 40 | 0.286 | 0.280 | 0.105 | 2.34 | 104.79 | 0.238 | geometry_survives_initial_falsification |
| direct:impulse_origin:impulse_extension:480m | 39 | 0.279 | 0.271 | 0.401 | 2.81 | 70.67 | 0.254 | geometry_survives_initial_falsification |
| direct:acceptance_extreme:two_r:480m | 39 | 0.279 | 0.226 | 0.507 | 1.55 | 48.56 | 0.182 | geometry_survives_initial_falsification |
| delayed15:impulse_origin:impulse_extension:480m | 39 | 0.279 | 0.192 | 0.244 | 2.04 | 62.03 | 0.160 | geometry_survives_initial_falsification |
| direct:impulse_origin:impulse_extension:240m | 40 | 0.286 | 0.159 | 0.174 | 1.78 | 50.56 | 0.139 | geometry_survives_initial_falsification |
| direct:impulse_origin:time_only:240m | 40 | 0.286 | 0.156 | 0.105 | 1.74 | 41.05 | 0.093 | geometry_survives_initial_falsification |

A time-return clue is promoted only when a logical invalidation and same-leg objective preserve positive after-cost R across chronology, after one-slot arbitration and after removing the best episode. A surviving geometry still requires NautilusTrader account validation; a failed geometry means the v57 state remains descriptive rather than tradable.
