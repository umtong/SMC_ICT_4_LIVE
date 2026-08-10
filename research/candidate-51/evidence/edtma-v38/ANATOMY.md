# EDTMA system anatomy

The tables separate opportunity generation, re-entry policy, exit engine and stop geometry. They are not a binary promotion gate.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source/rolling exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| spring_2025_03 | `source_condition` | 28 | 18 | 7238.67 | 12107.47 | 0.598 | -4868.80 | 2 | 24 | 0 | 1 |
| spring_2025_03 | `source_rising_edge` | 15 | 10 | 4770.76 | 5836.82 | 0.817 | -1066.06 | 2 | 13 | 0 | 1 |
| spring_2025_03 | `condition_no_signal` | 28 | 18 | 7189.21 | 12760.47 | 0.563 | -5571.27 | 2 | 24 | 0 | 0 |
| spring_2025_03 | `condition_rolling_chandelier` | 29 | 19 | 7533.61 | 10336.03 | 0.729 | -2802.42 | 2 | 25 | 0 | 1 |
| spring_2025_03 | `condition_structural_source_exit` | 30 | 18 | 9631.92 | 13645.98 | 0.706 | -4014.06 | 2 | 24 | 0 | 2 |
| spring_2025_03 | `condition_structural_no_signal` | 30 | 20 | 10070.71 | 11998.31 | 0.839 | -1927.61 | 2 | 26 | 0 | 0 |
| autumn_2025_09 | `source_condition` | 24 | 11 | 3116.31 | 11551.12 | 0.270 | -8434.81 | 1 | 9 | 0 | 12 |
| autumn_2025_09 | `source_rising_edge` | 19 | 12 | 4407.39 | 6938.11 | 0.635 | -2530.72 | 1 | 11 | 0 | 6 |
| autumn_2025_09 | `condition_no_signal` | 20 | 16 | 5367.00 | 10070.59 | 0.533 | -4703.59 | 1 | 15 | 1 | 0 |
| autumn_2025_09 | `condition_rolling_chandelier` | 28 | 11 | 2287.18 | 12605.77 | 0.181 | -10318.59 | 1 | 6 | 0 | 19 |
| autumn_2025_09 | `condition_structural_source_exit` | 28 | 11 | 5948.40 | 23524.74 | 0.253 | -17576.34 | 1 | 9 | 0 | 12 |
| autumn_2025_09 | `condition_structural_no_signal` | 25 | 14 | 8384.02 | 27618.23 | 0.304 | -19234.21 | 1 | 14 | 0 | 0 |
| winter_2026_01 | `source_condition` | 44 | 32 | 9707.33 | 8256.24 | 1.176 | 1451.10 | 1 | 24 | 1 | 18 |
| winter_2026_01 | `source_rising_edge` | 25 | 16 | 4156.73 | 4977.05 | 0.835 | -820.31 | 1 | 12 | 1 | 11 |
| winter_2026_01 | `condition_no_signal` | 27 | 21 | 6547.84 | 6088.79 | 1.075 | 459.05 | 1 | 22 | 3 | 0 |
| winter_2026_01 | `condition_rolling_chandelier` | 57 | 32 | 8978.90 | 6641.31 | 1.352 | 2337.59 | 2 | 25 | 1 | 31 |
| winter_2026_01 | `condition_structural_source_exit` | 56 | 34 | 23187.46 | 34712.38 | 0.668 | -11524.92 | 1 | 27 | 1 | 20 |
| winter_2026_01 | `condition_structural_no_signal` | 41 | 28 | 21927.55 | 31476.34 | 0.697 | -9548.79 | 1 | 28 | 2 | 0 |
| summer_2026_06 | `source_condition` | 37 | 31 | 10988.21 | 5322.97 | 2.064 | 5665.23 | 6 | 32 | 1 | 3 |
| summer_2026_06 | `source_rising_edge` | 26 | 21 | 8432.00 | 5204.97 | 1.620 | 3227.03 | 4 | 22 | 0 | 3 |
| summer_2026_06 | `condition_no_signal` | 35 | 31 | 10325.09 | 3627.58 | 2.846 | 6697.51 | 6 | 32 | 2 | 0 |
| summer_2026_06 | `condition_rolling_chandelier` | 37 | 31 | 9929.70 | 5219.07 | 1.903 | 4710.63 | 6 | 31 | 1 | 4 |
| summer_2026_06 | `condition_structural_source_exit` | 43 | 32 | 26165.53 | 26690.82 | 0.980 | -525.30 | 9 | 33 | 0 | 2 |
| summer_2026_06 | `condition_structural_no_signal` | 41 | 32 | 21040.27 | 23884.77 | 0.881 | -2844.50 | 9 | 33 | 0 | 0 |

## Paired mechanism comparisons

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| autumn_2025_09 | `source_condition` | `source_rising_edge` | 14 | 10 | 5 | 1.414 | 0.399 | 1750.01 | 5904.09 |
| autumn_2025_09 | `source_condition` | `condition_no_signal` | 19 | 5 | 1 | 1.722 | 0.128 | 281.64 | 3731.22 |
| autumn_2025_09 | `source_condition` | `condition_rolling_chandelier` | 23 | 1 | 5 | 0.734 | -0.091 | -960.22 | -1883.78 |
| autumn_2025_09 | `source_condition` | `condition_structural_source_exit` | 24 | 0 | 4 | 1.909 | -1.037 | -9981.74 | -9141.53 |
| autumn_2025_09 | `source_condition` | `condition_structural_no_signal` | 20 | 4 | 5 | 2.690 | -1.391 | -9556.69 | -10799.40 |
| spring_2025_03 | `source_condition` | `source_rising_edge` | 10 | 18 | 5 | 0.659 | 0.518 | -2427.95 | 3802.74 |
| spring_2025_03 | `source_condition` | `condition_no_signal` | 28 | 0 | 0 | 0.993 | -0.054 | 0.00 | -702.46 |
| spring_2025_03 | `source_condition` | `condition_rolling_chandelier` | 28 | 0 | 1 | 1.041 | 0.146 | 150.32 | 2066.38 |
| spring_2025_03 | `source_condition` | `condition_structural_source_exit` | 27 | 1 | 3 | 1.331 | -0.127 | -1687.88 | 854.75 |
| spring_2025_03 | `source_condition` | `condition_structural_no_signal` | 26 | 2 | 4 | 1.391 | 0.009 | -1650.35 | 2941.20 |
| summer_2026_06 | `source_condition` | `source_rising_edge` | 17 | 20 | 9 | 0.767 | 0.022 | 4401.24 | -2438.21 |
| summer_2026_06 | `source_condition` | `condition_no_signal` | 35 | 2 | 0 | 0.940 | 0.319 | 0.00 | 1032.27 |
| summer_2026_06 | `source_condition` | `condition_rolling_chandelier` | 37 | 0 | 0 | 0.904 | 0.020 | 0.00 | -954.60 |
| summer_2026_06 | `source_condition` | `condition_structural_source_exit` | 37 | 0 | 6 | 2.381 | -4.014 | 4967.56 | -6190.53 |
| summer_2026_06 | `source_condition` | `condition_structural_no_signal` | 35 | 2 | 6 | 1.915 | -3.487 | 4967.56 | -8509.73 |
| winter_2026_01 | `source_condition` | `source_rising_edge` | 19 | 25 | 6 | 0.428 | 0.397 | -829.86 | -2271.41 |
| winter_2026_01 | `source_condition` | `condition_no_signal` | 26 | 18 | 1 | 0.675 | 0.263 | 197.88 | -992.05 |
| winter_2026_01 | `source_condition` | `condition_rolling_chandelier` | 41 | 3 | 16 | 0.925 | 0.196 | 659.45 | 886.50 |
| winter_2026_01 | `source_condition` | `condition_structural_source_exit` | 44 | 0 | 12 | 2.389 | -3.204 | -3842.89 | -12976.02 |
| winter_2026_01 | `source_condition` | `condition_structural_no_signal` | 32 | 12 | 9 | 2.259 | -2.812 | 3006.58 | -10999.89 |
