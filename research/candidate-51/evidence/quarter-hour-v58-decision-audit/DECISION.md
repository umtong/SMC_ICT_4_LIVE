# Quarter-hour fixed-bin decision audit

- decision: **discard_quarter_hour_family**
- evaluation days: 9
- no threshold or horizon was changed after observing v58

| absolute imbalance bin | horizon | one-slot trades | trades/day | mean bp | median bp | PF | mean without best bp | status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 0_025 | 240m | 52 | 5.778 | 62.29 | -16.48 | 2.41 | 25.61 | do_not_promote |
| 0_025 | 480m | 27 | 3.000 | 10.80 | -26.71 | 1.15 | -7.63 | do_not_promote |
| 0_025 | 720m | 18 | 2.000 | 17.01 | -40.38 | 1.17 | -71.15 | do_not_promote |
| 025_050 | 240m | 51 | 5.667 | -48.88 | -28.45 | 0.39 | -54.46 | do_not_promote |
| 025_050 | 480m | 27 | 3.000 | -21.16 | -5.52 | 0.79 | -44.86 | do_not_promote |
| 025_050 | 720m | 18 | 2.000 | -105.92 | 18.90 | 0.39 | -137.90 | do_not_promote |
| 050_075 | 240m | 51 | 5.667 | -41.16 | -25.04 | 0.44 | -47.78 | do_not_promote |
| 050_075 | 480m | 27 | 3.000 | -11.49 | -22.03 | 0.85 | -36.67 | do_not_promote |
| 050_075 | 720m | 18 | 2.000 | 14.33 | -29.93 | 1.18 | -28.24 | do_not_promote |
| 075_100 | 240m | 47 | 5.222 | -3.22 | -20.25 | 0.92 | -8.39 | do_not_promote |
| 075_100 | 480m | 26 | 2.889 | -33.80 | -11.42 | 0.58 | -46.53 | do_not_promote |
| 075_100 | 720m | 18 | 2.000 | -17.45 | -14.08 | 0.84 | -86.82 | do_not_promote |

A pooled subgroup is not promoted unless the same frozen subgroup remains positive after global one-slot routing, in every observed period including post-publication, and after removing its best event. Any authorized next test must use genuinely untouched dates.
