# Slope-is-Dope system anatomy

This is not a gate. Opportunity, direction, winner engine, exit engine, risk geometry and implementation are shown separately.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| winter_2025_02 | `source_exact_condition` | 69 | 36 | 5352.68 | 6067.27 | 0.882 | -714.59 | 1 | 21 | 0 | 48 |
| winter_2025_02 | `source_exact_rising` | 27 | 12 | 1399.14 | 2752.30 | 0.508 | -1353.16 | 1 | 7 | 0 | 20 |
| winter_2025_02 | `corrected_condition` | 31 | 25 | 4813.12 | 4710.67 | 1.022 | 102.45 | 0 | 25 | 0 | 6 |
| winter_2025_02 | `corrected_rising` | 18 | 13 | 1781.50 | 3638.56 | 0.490 | -1857.07 | 1 | 13 | 0 | 5 |
| winter_2025_02 | `ma_only_condition` | 31 | 26 | 4910.57 | 5116.76 | 0.960 | -206.19 | 0 | 26 | 1 | 3 |
| winter_2025_02 | `no_signal_condition` | 25 | 23 | 4307.08 | 3096.29 | 1.391 | 1210.79 | 0 | 21 | 3 | 0 |
| winter_2025_02 | `corrected_long_only` | 3 | 3 | 614.79 | 0.00 | 0.000 | 614.79 | 0 | 3 | 0 | 0 |
| winter_2025_02 | `corrected_short_only` | 28 | 22 | 4201.47 | 4710.67 | 0.892 | -509.20 | 0 | 22 | 0 | 6 |
| winter_2025_02 | `structural_corrected` | 31 | 25 | 12196.12 | 13203.20 | 0.924 | -1007.08 | 0 | 25 | 0 | 5 |
| winter_2025_02 | `structural_ma_only` | 31 | 25 | 12033.37 | 16181.46 | 0.744 | -4148.09 | 0 | 25 | 0 | 1 |
| summer_2025_08 | `source_exact_condition` | 66 | 33 | 5286.58 | 6227.20 | 0.849 | -940.62 | 0 | 26 | 0 | 41 |
| summer_2025_08 | `source_exact_rising` | 31 | 12 | 1638.00 | 2679.08 | 0.611 | -1041.08 | 0 | 10 | 0 | 21 |
| summer_2025_08 | `corrected_condition` | 35 | 26 | 5164.97 | 4456.15 | 1.159 | 708.82 | 0 | 26 | 1 | 8 |
| summer_2025_08 | `corrected_rising` | 20 | 14 | 1992.77 | 1996.55 | 0.998 | -3.78 | 0 | 14 | 0 | 6 |
| summer_2025_08 | `ma_only_condition` | 32 | 25 | 4831.19 | 6536.51 | 0.739 | -1705.32 | 0 | 24 | 2 | 6 |
| summer_2025_08 | `no_signal_condition` | 28 | 24 | 4499.71 | 2147.21 | 2.096 | 2352.50 | 0 | 23 | 2 | 0 |
| summer_2025_08 | `corrected_long_only` | 19 | 15 | 2981.39 | 2758.10 | 1.081 | 223.29 | 0 | 15 | 0 | 4 |
| summer_2025_08 | `corrected_short_only` | 16 | 11 | 2162.72 | 1680.09 | 1.287 | 482.62 | 0 | 11 | 1 | 4 |
| summer_2025_08 | `structural_corrected` | 35 | 26 | 12400.47 | 12883.31 | 0.963 | -482.85 | 0 | 26 | 1 | 7 |
| summer_2025_08 | `structural_ma_only` | 33 | 26 | 11903.27 | 14731.71 | 0.808 | -2828.44 | 0 | 25 | 2 | 3 |
| winter_2026_01 | `source_exact_condition` | 51 | 27 | 3894.30 | 3323.22 | 1.172 | 571.09 | 0 | 20 | 1 | 30 |
| winter_2026_01 | `source_exact_rising` | 31 | 17 | 2355.13 | 1343.40 | 1.753 | 1011.72 | 1 | 12 | 1 | 18 |
| winter_2026_01 | `corrected_condition` | 34 | 27 | 4453.54 | 3115.94 | 1.429 | 1337.60 | 0 | 26 | 2 | 6 |
| winter_2026_01 | `corrected_rising` | 22 | 18 | 2804.98 | 1052.22 | 2.666 | 1752.76 | 2 | 17 | 1 | 4 |
| winter_2026_01 | `ma_only_condition` | 28 | 23 | 3853.01 | 2330.32 | 1.653 | 1522.69 | 0 | 21 | 5 | 2 |
| winter_2026_01 | `no_signal_condition` | 25 | 20 | 3211.44 | 3114.78 | 1.031 | 96.66 | 0 | 18 | 5 | 0 |
| winter_2026_01 | `corrected_long_only` | 22 | 19 | 3480.25 | 1449.82 | 2.400 | 2030.43 | 0 | 18 | 1 | 3 |
| winter_2026_01 | `corrected_short_only` | 15 | 10 | 1485.34 | 2118.43 | 0.701 | -633.09 | 0 | 10 | 1 | 4 |
| winter_2026_01 | `structural_corrected` | 37 | 29 | 20229.08 | 14420.99 | 1.403 | 5808.09 | 0 | 28 | 1 | 5 |
| winter_2026_01 | `structural_ma_only` | 32 | 26 | 17382.13 | 11559.63 | 1.504 | 5822.50 | 0 | 24 | 4 | 1 |
| summer_2026_06 | `source_exact_condition` | 84 | 50 | 5816.73 | 5597.16 | 1.039 | 219.57 | 11 | 28 | 0 | 56 |
| summer_2026_06 | `source_exact_rising` | 32 | 15 | 1636.52 | 2848.20 | 0.575 | -1211.68 | 0 | 10 | 0 | 22 |
| summer_2026_06 | `corrected_condition` | 38 | 32 | 6256.55 | 2954.24 | 2.118 | 3302.31 | 2 | 31 | 1 | 6 |
| summer_2026_06 | `corrected_rising` | 23 | 15 | 2580.23 | 2810.04 | 0.918 | -229.81 | 0 | 15 | 1 | 7 |
| summer_2026_06 | `ma_only_condition` | 35 | 33 | 6251.61 | 1793.07 | 3.487 | 4458.54 | 2 | 31 | 2 | 2 |
| summer_2026_06 | `no_signal_condition` | 35 | 33 | 6248.82 | 2464.35 | 2.536 | 3784.47 | 2 | 31 | 2 | 0 |
| summer_2026_06 | `corrected_long_only` | 1 | 0 | 0.00 | 369.64 | 0.000 | -369.64 | 0 | 0 | 0 | 1 |
| summer_2026_06 | `corrected_short_only` | 37 | 32 | 6256.55 | 2571.13 | 2.433 | 3685.42 | 2 | 31 | 1 | 5 |
| summer_2026_06 | `structural_corrected` | 38 | 32 | 16034.88 | 12308.57 | 1.303 | 3726.31 | 2 | 31 | 1 | 5 |
| summer_2026_06 | `structural_ma_only` | 36 | 32 | 15803.81 | 11446.40 | 1.381 | 4357.41 | 2 | 31 | 1 | 1 |

## Paired mechanism trade-offs

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| summer_2025_08 | `corrected_condition` | `corrected_rising` | 8 | 27 | 12 | 0.386 | 0.552 | 297.24 | -712.60 |
| summer_2025_08 | `corrected_condition` | `corrected_long_only` | 19 | 16 | 0 | 0.577 | 0.381 | 0.00 | -485.53 |
| summer_2025_08 | `corrected_condition` | `corrected_short_only` | 16 | 19 | 0 | 0.419 | 0.623 | 0.00 | -226.20 |
| summer_2025_08 | `corrected_condition` | `structural_corrected` | 35 | 0 | 0 | 2.401 | -1.891 | 0.00 | -1191.67 |
| summer_2025_08 | `source_exact_condition` | `source_exact_rising` | 17 | 49 | 14 | 0.310 | 0.570 | -480.30 | -100.47 |
| summer_2025_08 | `source_exact_condition` | `corrected_condition` | 32 | 34 | 3 | 0.977 | 0.284 | 955.01 | 1649.44 |
| summer_2025_08 | `source_exact_condition` | `ma_only_condition` | 29 | 37 | 3 | 0.914 | -0.050 | 955.01 | -764.70 |
| summer_2025_08 | `source_exact_condition` | `no_signal_condition` | 22 | 44 | 6 | 0.851 | 0.655 | 1345.30 | 3293.12 |
| summer_2025_08 | `source_exact_condition` | `structural_corrected` | 32 | 34 | 3 | 2.346 | -1.069 | 2514.01 | 457.77 |
| summer_2025_08 | `source_exact_condition` | `structural_ma_only` | 30 | 36 | 3 | 2.252 | -1.366 | 2514.01 | -1887.82 |
| summer_2026_06 | `corrected_condition` | `corrected_rising` | 8 | 30 | 15 | 0.412 | 0.049 | 546.89 | -3532.12 |
| summer_2026_06 | `corrected_condition` | `corrected_long_only` | 1 | 37 | 0 | 0.000 | 0.875 | 0.00 | -3671.95 |
| summer_2026_06 | `corrected_condition` | `corrected_short_only` | 37 | 1 | 0 | 1.000 | 0.130 | 0.00 | 383.11 |
| summer_2026_06 | `corrected_condition` | `structural_corrected` | 38 | 0 | 0 | 2.563 | -3.166 | 0.00 | 424.00 |
| summer_2026_06 | `source_exact_condition` | `source_exact_rising` | 17 | 67 | 15 | 0.281 | 0.491 | -572.97 | -1431.25 |
| summer_2026_06 | `source_exact_condition` | `corrected_condition` | 31 | 53 | 7 | 1.076 | 0.472 | 457.79 | 3082.74 |
| summer_2026_06 | `source_exact_condition` | `ma_only_condition` | 29 | 55 | 6 | 1.075 | 0.680 | 1235.37 | 4238.96 |
| summer_2026_06 | `source_exact_condition` | `no_signal_condition` | 29 | 55 | 6 | 1.074 | 0.560 | 1234.53 | 3564.90 |
| summer_2026_06 | `source_exact_condition` | `structural_corrected` | 31 | 53 | 7 | 2.757 | -1.199 | -825.73 | 3506.74 |
| summer_2026_06 | `source_exact_condition` | `structural_ma_only` | 30 | 54 | 6 | 2.717 | -1.045 | 3450.57 | 4137.84 |
| winter_2025_02 | `corrected_condition` | `corrected_rising` | 8 | 23 | 10 | 0.370 | 0.228 | -1056.54 | -1959.52 |
| winter_2025_02 | `corrected_condition` | `corrected_long_only` | 3 | 28 | 0 | 0.128 | 1.000 | 0.00 | 512.34 |
| winter_2025_02 | `corrected_condition` | `corrected_short_only` | 28 | 3 | 0 | 0.873 | 0.000 | 0.00 | -611.65 |
| winter_2025_02 | `corrected_condition` | `structural_corrected` | 31 | 0 | 0 | 2.534 | -1.803 | 0.00 | -1109.53 |
| winter_2025_02 | `source_exact_condition` | `source_exact_rising` | 16 | 53 | 11 | 0.261 | 0.546 | -1548.13 | -638.57 |
| winter_2025_02 | `source_exact_condition` | `corrected_condition` | 28 | 41 | 3 | 0.899 | 0.224 | 466.30 | 817.04 |
| winter_2025_02 | `source_exact_condition` | `ma_only_condition` | 28 | 41 | 3 | 0.917 | 0.157 | 466.10 | 508.41 |
| winter_2025_02 | `source_exact_condition` | `no_signal_condition` | 21 | 48 | 4 | 0.805 | 0.490 | 659.98 | 1925.38 |
| winter_2025_02 | `source_exact_condition` | `structural_corrected` | 28 | 41 | 3 | 2.279 | -1.176 | 1872.18 | -292.49 |
| winter_2025_02 | `source_exact_condition` | `structural_ma_only` | 28 | 41 | 3 | 2.248 | -1.667 | 1856.36 | -3433.50 |
| winter_2026_01 | `corrected_condition` | `corrected_rising` | 12 | 22 | 10 | 0.630 | 0.662 | 779.72 | 415.16 |
| winter_2026_01 | `corrected_condition` | `corrected_long_only` | 21 | 13 | 1 | 0.781 | 0.535 | 179.95 | 692.83 |
| winter_2026_01 | `corrected_condition` | `corrected_short_only` | 13 | 21 | 2 | 0.334 | 0.320 | -131.89 | -1970.70 |
| winter_2026_01 | `corrected_condition` | `structural_corrected` | 34 | 0 | 3 | 4.542 | -3.628 | 476.04 | 4470.49 |
| winter_2026_01 | `source_exact_condition` | `source_exact_rising` | 18 | 33 | 13 | 0.605 | 0.596 | 452.24 | 440.64 |
| winter_2026_01 | `source_exact_condition` | `corrected_condition` | 32 | 19 | 2 | 1.144 | 0.062 | -380.70 | 766.52 |
| winter_2026_01 | `source_exact_condition` | `ma_only_condition` | 26 | 25 | 2 | 0.989 | 0.299 | 236.01 | 951.61 |
| winter_2026_01 | `source_exact_condition` | `no_signal_condition` | 22 | 29 | 3 | 0.825 | 0.063 | 394.41 | -474.42 |
| winter_2026_01 | `source_exact_condition` | `structural_corrected` | 34 | 17 | 3 | 5.195 | -3.339 | -167.85 | 5237.01 |
| winter_2026_01 | `source_exact_condition` | `structural_ma_only` | 29 | 22 | 3 | 4.463 | -2.478 | 1156.45 | 5251.41 |
