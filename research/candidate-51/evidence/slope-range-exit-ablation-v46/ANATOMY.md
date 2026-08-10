# Slope-is-Dope system anatomy

This is not a gate. Opportunity, direction, winner engine, exit engine, risk geometry and implementation are shown separately.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev_2024_03 | `range_control` | 16 | 11 | 4382.90 | 5561.34 | 0.788 | -1178.44 | 1 | 12 | 0 | 4 |
| dev_2024_03 | `ma_cross_only` | 14 | 11 | 4081.72 | 3682.63 | 1.108 | 399.08 | 1 | 11 | 1 | 2 |
| dev_2024_03 | `no_source_exit` | 13 | 10 | 3988.37 | 4469.91 | 0.892 | -481.54 | 1 | 11 | 0 | 0 |
| dev_2024_06 | `range_control` | 14 | 8 | 4291.09 | 4668.97 | 0.919 | -377.88 | 0 | 8 | 1 | 5 |
| dev_2024_06 | `ma_cross_only` | 11 | 8 | 4625.75 | 3193.27 | 1.449 | 1432.48 | 0 | 8 | 1 | 2 |
| dev_2024_06 | `no_source_exit` | 11 | 8 | 4723.04 | 179.74 | 26.277 | 4543.30 | 0 | 8 | 3 | 0 |
| dev_2024_10 | `range_control` | 16 | 15 | 4620.36 | 870.21 | 5.309 | 3750.15 | 0 | 14 | 1 | 1 |
| dev_2024_10 | `ma_cross_only` | 16 | 15 | 4621.66 | 696.23 | 6.638 | 3925.43 | 0 | 14 | 1 | 1 |
| dev_2024_10 | `no_source_exit` | 16 | 15 | 4621.98 | 652.31 | 7.086 | 3969.68 | 0 | 14 | 1 | 0 |
| dev_2025_02 | `range_control` | 31 | 27 | 12204.65 | 7003.01 | 1.743 | 5201.64 | 0 | 27 | 0 | 4 |
| dev_2025_02 | `ma_cross_only` | 29 | 26 | 11170.76 | 7621.08 | 1.466 | 3549.68 | 0 | 26 | 0 | 1 |
| dev_2025_02 | `no_source_exit` | 29 | 26 | 11220.42 | 6340.17 | 1.770 | 4880.25 | 0 | 26 | 1 | 0 |
| dev_2025_05 | `range_control` | 26 | 23 | 6255.00 | 3378.95 | 1.851 | 2876.06 | 0 | 23 | 1 | 2 |
| dev_2025_05 | `ma_cross_only` | 26 | 23 | 6218.99 | 3007.91 | 2.068 | 3211.08 | 0 | 23 | 2 | 0 |
| dev_2025_05 | `no_source_exit` | 26 | 23 | 6218.99 | 3007.91 | 2.068 | 3211.08 | 0 | 23 | 2 | 0 |
| dev_2025_08 | `range_control` | 32 | 26 | 10652.87 | 7269.36 | 1.465 | 3383.52 | 0 | 26 | 1 | 5 |
| dev_2025_08 | `ma_cross_only` | 32 | 27 | 10554.64 | 9911.23 | 1.065 | 643.41 | 0 | 26 | 2 | 2 |
| dev_2025_08 | `no_source_exit` | 32 | 27 | 10548.31 | 9943.29 | 1.061 | 605.02 | 0 | 26 | 2 | 0 |
| dev_2025_11 | `range_control` | 22 | 16 | 7007.63 | 7481.75 | 0.937 | -474.11 | 2 | 16 | 0 | 6 |
| dev_2025_11 | `ma_cross_only` | 18 | 16 | 6193.79 | 4535.95 | 1.365 | 1657.84 | 2 | 15 | 1 | 1 |
| dev_2025_11 | `no_source_exit` | 18 | 17 | 6302.28 | 3117.56 | 2.022 | 3184.71 | 2 | 15 | 2 | 0 |
| dev_2026_01 | `range_control` | 26 | 22 | 10187.09 | 3967.81 | 2.567 | 6219.28 | 0 | 22 | 0 | 4 |
| dev_2026_01 | `ma_cross_only` | 21 | 19 | 7209.35 | 1782.38 | 4.045 | 5426.96 | 0 | 18 | 2 | 1 |
| dev_2026_01 | `no_source_exit` | 21 | 19 | 7183.87 | 2702.28 | 2.658 | 4481.58 | 0 | 18 | 2 | 0 |
| dev_2026_03 | `range_control` | 7 | 4 | 1667.88 | 4711.59 | 0.354 | -3043.71 | 1 | 3 | 1 | 3 |
| dev_2026_03 | `ma_cross_only` | 7 | 5 | 1973.58 | 2251.29 | 0.877 | -277.71 | 1 | 4 | 2 | 1 |
| dev_2026_03 | `no_source_exit` | 7 | 5 | 1957.32 | 3064.29 | 0.639 | -1106.96 | 1 | 4 | 2 | 0 |
| dev_2026_06 | `range_control` | 33 | 29 | 13634.48 | 8299.51 | 1.643 | 5334.97 | 2 | 28 | 1 | 3 |
| dev_2026_06 | `ma_cross_only` | 30 | 28 | 12953.56 | 5020.52 | 2.580 | 7933.04 | 2 | 27 | 1 | 1 |
| dev_2026_06 | `no_source_exit` | 30 | 28 | 12946.57 | 6600.48 | 1.961 | 6346.09 | 2 | 27 | 1 | 0 |
| dev_2026_07 | `range_control` | 8 | 6 | 2537.80 | 2433.86 | 1.043 | 103.93 | 0 | 5 | 1 | 2 |
| dev_2026_07 | `ma_cross_only` | 8 | 6 | 2537.80 | 3335.93 | 0.761 | -798.13 | 0 | 5 | 1 | 2 |
| dev_2026_07 | `no_source_exit` | 8 | 6 | 2537.80 | 4544.55 | 0.558 | -2006.75 | 0 | 5 | 1 | 0 |

## Paired mechanism trade-offs

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev_2024_03 | `ma_cross_only` | `no_source_exit` | 13 | 1 | 0 | 0.977 | -0.214 | 0.00 | -880.62 |
| dev_2024_03 | `range_control` | `ma_cross_only` | 14 | 2 | 0 | 0.931 | 0.338 | 0.00 | 1577.52 |
| dev_2024_03 | `range_control` | `no_source_exit` | 13 | 3 | 0 | 0.910 | 0.196 | 0.00 | 696.90 |
| dev_2024_06 | `ma_cross_only` | `no_source_exit` | 11 | 0 | 0 | 1.021 | 0.944 | 0.00 | 3110.81 |
| dev_2024_06 | `range_control` | `ma_cross_only` | 11 | 3 | 0 | 1.078 | 0.316 | 0.00 | 1810.37 |
| dev_2024_06 | `range_control` | `no_source_exit` | 11 | 3 | 0 | 1.101 | 0.962 | 0.00 | 4921.18 |
| dev_2024_10 | `ma_cross_only` | `no_source_exit` | 16 | 0 | 0 | 1.000 | 0.063 | 0.00 | 44.24 |
| dev_2024_10 | `range_control` | `ma_cross_only` | 16 | 0 | 0 | 1.000 | 0.200 | 0.00 | 175.28 |
| dev_2024_10 | `range_control` | `no_source_exit` | 16 | 0 | 0 | 1.000 | 0.250 | 0.00 | 219.53 |
| dev_2025_02 | `ma_cross_only` | `no_source_exit` | 29 | 0 | 0 | 1.004 | 0.168 | 0.00 | 1330.57 |
| dev_2025_02 | `range_control` | `ma_cross_only` | 29 | 2 | 0 | 0.915 | -0.088 | 0.00 | -1651.96 |
| dev_2025_02 | `range_control` | `no_source_exit` | 29 | 2 | 0 | 0.919 | 0.095 | 0.00 | -321.39 |
| dev_2025_05 | `ma_cross_only` | `no_source_exit` | 26 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2025_05 | `range_control` | `ma_cross_only` | 26 | 0 | 0 | 0.994 | 0.110 | 0.00 | 335.03 |
| dev_2025_05 | `range_control` | `no_source_exit` | 26 | 0 | 0 | 0.994 | 0.110 | 0.00 | 335.03 |
| dev_2025_08 | `ma_cross_only` | `no_source_exit` | 32 | 0 | 0 | 0.999 | -0.003 | 0.00 | -38.39 |
| dev_2025_08 | `range_control` | `ma_cross_only` | 32 | 0 | 0 | 0.991 | -0.363 | 0.00 | -2740.11 |
| dev_2025_08 | `range_control` | `no_source_exit` | 32 | 0 | 0 | 0.990 | -0.368 | 0.00 | -2778.50 |
| dev_2025_11 | `ma_cross_only` | `no_source_exit` | 18 | 0 | 0 | 1.018 | 0.313 | 0.00 | 1526.88 |
| dev_2025_11 | `range_control` | `ma_cross_only` | 18 | 4 | 0 | 0.884 | 0.394 | 0.00 | 2131.95 |
| dev_2025_11 | `range_control` | `no_source_exit` | 18 | 4 | 0 | 0.899 | 0.583 | 0.00 | 3658.83 |
| dev_2026_01 | `ma_cross_only` | `no_source_exit` | 21 | 0 | 0 | 0.996 | -0.516 | 0.00 | -945.38 |
| dev_2026_01 | `range_control` | `ma_cross_only` | 21 | 5 | 0 | 0.708 | 0.551 | 0.00 | -792.32 |
| dev_2026_01 | `range_control` | `no_source_exit` | 21 | 5 | 0 | 0.705 | 0.319 | 0.00 | -1737.70 |
| dev_2026_03 | `ma_cross_only` | `no_source_exit` | 7 | 0 | 0 | 0.992 | -0.361 | 0.00 | -829.25 |
| dev_2026_03 | `range_control` | `ma_cross_only` | 7 | 0 | 0 | 1.183 | 0.522 | 0.00 | 2766.00 |
| dev_2026_03 | `range_control` | `no_source_exit` | 7 | 0 | 0 | 1.174 | 0.350 | 0.00 | 1936.75 |
| dev_2026_06 | `ma_cross_only` | `no_source_exit` | 30 | 0 | 0 | 0.999 | -0.315 | 0.00 | -1586.96 |
| dev_2026_06 | `range_control` | `ma_cross_only` | 29 | 4 | 1 | 0.950 | 0.395 | -3299.90 | 2598.07 |
| dev_2026_06 | `range_control` | `no_source_exit` | 29 | 4 | 1 | 0.950 | 0.205 | -3251.34 | 1011.12 |
| dev_2026_07 | `ma_cross_only` | `no_source_exit` | 8 | 0 | 0 | 1.000 | -0.362 | 0.00 | -1208.62 |
| dev_2026_07 | `range_control` | `ma_cross_only` | 8 | 0 | 0 | 1.000 | -0.371 | 0.00 | -902.07 |
| dev_2026_07 | `range_control` | `no_source_exit` | 8 | 0 | 0 | 1.000 | -0.867 | 0.00 | -2110.68 |
