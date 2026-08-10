# EDTMA system anatomy

The tables separate opportunity generation, re-entry policy, exit engine and stop geometry. They are not a binary promotion gate.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source/rolling exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| spring_2025_03 | `source_condition` | 28 | 18 | 7238.67 | 12107.47 | 0.598 | -4868.80 | 2 | 24 | 0 | 1 |
| spring_2025_03 | `condition_no_signal` | 28 | 18 | 7189.21 | 12760.47 | 0.563 | -5571.27 | 2 | 24 | 0 | 0 |
| spring_2025_03 | `condition_loss` | 30 | 17 | 6520.47 | 10353.17 | 0.630 | -3832.70 | 2 | 22 | 0 | 0 |
| spring_2025_03 | `condition_progress` | 31 | 17 | 6315.46 | 11283.86 | 0.560 | -4968.39 | 2 | 21 | 0 | 0 |
| spring_2025_03 | `rising_progress` | 17 | 11 | 4932.13 | 3316.54 | 1.487 | 1615.59 | 2 | 14 | 0 | 0 |
| spring_2025_03 | `profit_reentry_progress` | 20 | 11 | 4807.14 | 4526.68 | 1.062 | 280.47 | 0 | 14 | 0 | 0 |
| spring_2025_03 | `profit_reentry_condition_loss` | 20 | 11 | 4821.18 | 3916.56 | 1.231 | 904.63 | 0 | 15 | 0 | 0 |
| autumn_2025_09 | `source_condition` | 24 | 11 | 3116.31 | 11551.12 | 0.270 | -8434.81 | 1 | 9 | 0 | 12 |
| autumn_2025_09 | `condition_no_signal` | 20 | 16 | 5367.00 | 10070.59 | 0.533 | -4703.59 | 1 | 15 | 1 | 0 |
| autumn_2025_09 | `condition_loss` | 25 | 8 | 2487.48 | 10395.85 | 0.239 | -7908.38 | 1 | 8 | 0 | 0 |
| autumn_2025_09 | `condition_progress` | 26 | 9 | 3375.73 | 10350.95 | 0.326 | -6975.22 | 0 | 9 | 0 | 0 |
| autumn_2025_09 | `rising_progress` | 21 | 9 | 2993.50 | 8054.34 | 0.372 | -5060.83 | 1 | 9 | 0 | 0 |
| autumn_2025_09 | `profit_reentry_progress` | 25 | 9 | 3395.29 | 9724.10 | 0.349 | -6328.80 | 0 | 9 | 0 | 0 |
| autumn_2025_09 | `profit_reentry_condition_loss` | 24 | 8 | 2500.85 | 9769.31 | 0.256 | -7268.46 | 1 | 8 | 0 | 0 |
| winter_2026_01 | `source_condition` | 44 | 32 | 9707.33 | 8256.24 | 1.176 | 1451.10 | 1 | 24 | 1 | 18 |
| winter_2026_01 | `condition_no_signal` | 27 | 21 | 6547.84 | 6088.79 | 1.075 | 459.05 | 1 | 22 | 3 | 0 |
| winter_2026_01 | `condition_loss` | 55 | 29 | 9887.09 | 10306.36 | 0.959 | -419.27 | 2 | 31 | 0 | 0 |
| winter_2026_01 | `condition_progress` | 57 | 30 | 10099.88 | 12152.88 | 0.831 | -2053.00 | 2 | 31 | 0 | 0 |
| winter_2026_01 | `rising_progress` | 36 | 18 | 5531.05 | 7253.10 | 0.763 | -1722.05 | 1 | 18 | 0 | 0 |
| winter_2026_01 | `profit_reentry_progress` | 45 | 25 | 7512.88 | 9878.86 | 0.761 | -2365.98 | 2 | 25 | 0 | 0 |
| winter_2026_01 | `profit_reentry_condition_loss` | 41 | 21 | 5974.86 | 8288.12 | 0.721 | -2313.26 | 2 | 21 | 0 | 0 |
| summer_2026_06 | `source_condition` | 37 | 31 | 10988.21 | 5322.97 | 2.064 | 5665.23 | 6 | 32 | 1 | 3 |
| summer_2026_06 | `condition_no_signal` | 35 | 31 | 10325.09 | 3627.58 | 2.846 | 6697.51 | 6 | 32 | 2 | 0 |
| summer_2026_06 | `condition_loss` | 52 | 31 | 8893.17 | 14592.67 | 0.609 | -5699.50 | 7 | 32 | 0 | 0 |
| summer_2026_06 | `condition_progress` | 53 | 31 | 8893.17 | 14332.96 | 0.620 | -5439.79 | 7 | 33 | 0 | 0 |
| summer_2026_06 | `rising_progress` | 33 | 20 | 6553.68 | 8144.75 | 0.805 | -1591.07 | 5 | 21 | 0 | 0 |
| summer_2026_06 | `profit_reentry_progress` | 31 | 17 | 4993.54 | 9158.53 | 0.545 | -4164.99 | 4 | 18 | 0 | 0 |
| summer_2026_06 | `profit_reentry_condition_loss` | 31 | 17 | 4993.54 | 9432.90 | 0.529 | -4439.36 | 4 | 18 | 0 | 0 |

## Paired mechanism comparisons

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| autumn_2025_09 | `condition_no_signal` | `condition_loss` | 18 | 2 | 7 | 0.463 | -0.032 | -2471.83 | -3204.79 |
| autumn_2025_09 | `condition_no_signal` | `condition_progress` | 18 | 2 | 8 | 0.629 | -0.028 | -1612.88 | -2271.63 |
| autumn_2025_09 | `condition_no_signal` | `profit_reentry_progress` | 17 | 3 | 8 | 0.633 | 0.034 | -1582.53 | -1625.21 |
| autumn_2025_09 | `source_condition` | `condition_no_signal` | 19 | 5 | 1 | 1.722 | 0.128 | 281.64 | 3731.22 |
| autumn_2025_09 | `source_condition` | `condition_loss` | 21 | 3 | 4 | 0.798 | 0.100 | -1590.50 | 526.43 |
| autumn_2025_09 | `source_condition` | `condition_progress` | 21 | 3 | 5 | 1.083 | 0.104 | -727.95 | 1459.59 |
| autumn_2025_09 | `source_condition` | `rising_progress` | 14 | 10 | 7 | 0.961 | 0.303 | -1821.59 | 3373.98 |
| autumn_2025_09 | `source_condition` | `profit_reentry_progress` | 19 | 5 | 6 | 1.090 | 0.158 | -962.07 | 2106.00 |
| autumn_2025_09 | `source_condition` | `profit_reentry_condition_loss` | 19 | 5 | 5 | 0.803 | 0.154 | -1830.46 | 1166.35 |
| spring_2025_03 | `condition_no_signal` | `condition_loss` | 28 | 0 | 2 | 0.907 | 0.189 | 528.77 | 1738.57 |
| spring_2025_03 | `condition_no_signal` | `condition_progress` | 28 | 0 | 3 | 0.878 | 0.116 | 966.88 | 602.87 |
| spring_2025_03 | `condition_no_signal` | `profit_reentry_progress` | 18 | 10 | 2 | 0.669 | 0.645 | 552.34 | 5851.73 |
| spring_2025_03 | `source_condition` | `condition_no_signal` | 28 | 0 | 0 | 0.993 | -0.054 | 0.00 | -702.46 |
| spring_2025_03 | `source_condition` | `condition_loss` | 28 | 0 | 2 | 0.901 | 0.145 | 528.77 | 1036.10 |
| spring_2025_03 | `source_condition` | `condition_progress` | 28 | 0 | 3 | 0.872 | 0.068 | 966.88 | -99.59 |
| spring_2025_03 | `source_condition` | `rising_progress` | 10 | 18 | 7 | 0.681 | 0.726 | -343.96 | 6484.39 |
| spring_2025_03 | `source_condition` | `profit_reentry_progress` | 18 | 10 | 2 | 0.664 | 0.626 | 552.34 | 5149.27 |
| spring_2025_03 | `source_condition` | `profit_reentry_condition_loss` | 18 | 10 | 2 | 0.666 | 0.677 | 554.82 | 5773.43 |
| summer_2026_06 | `condition_no_signal` | `condition_loss` | 34 | 1 | 18 | 0.861 | -3.023 | 1618.51 | -12397.01 |
| summer_2026_06 | `condition_no_signal` | `condition_progress` | 34 | 1 | 19 | 0.861 | -2.951 | 2010.76 | -12137.30 |
| summer_2026_06 | `condition_no_signal` | `profit_reentry_progress` | 20 | 15 | 11 | 0.484 | -1.525 | 20.62 | -10862.50 |
| summer_2026_06 | `source_condition` | `condition_no_signal` | 35 | 2 | 0 | 0.940 | 0.319 | 0.00 | 1032.27 |
| summer_2026_06 | `source_condition` | `condition_loss` | 35 | 2 | 17 | 0.809 | -1.741 | 1992.77 | -11364.74 |
| summer_2026_06 | `source_condition` | `condition_progress` | 35 | 2 | 18 | 0.809 | -1.693 | 2386.06 | -11105.02 |
| summer_2026_06 | `source_condition` | `rising_progress` | 16 | 21 | 17 | 0.596 | -0.530 | 339.75 | -7256.30 |
| summer_2026_06 | `source_condition` | `profit_reentry_progress` | 21 | 16 | 10 | 0.454 | -0.721 | 402.15 | -9830.22 |
| summer_2026_06 | `source_condition` | `profit_reentry_condition_loss` | 21 | 16 | 10 | 0.454 | -0.772 | -8.17 | -10104.59 |
| winter_2026_01 | `condition_no_signal` | `condition_loss` | 26 | 1 | 29 | 1.510 | -0.693 | 2679.13 | -878.32 |
| winter_2026_01 | `condition_no_signal` | `condition_progress` | 26 | 1 | 31 | 1.542 | -0.996 | 2553.61 | -2512.05 |
| winter_2026_01 | `condition_no_signal` | `profit_reentry_progress` | 21 | 6 | 24 | 1.147 | -0.622 | 1407.63 | -2825.03 |
| winter_2026_01 | `source_condition` | `condition_no_signal` | 26 | 18 | 1 | 0.675 | 0.263 | 197.88 | -992.05 |
| winter_2026_01 | `source_condition` | `condition_loss` | 36 | 8 | 19 | 1.019 | -0.248 | 2128.69 | -1870.37 |
| winter_2026_01 | `source_condition` | `condition_progress` | 36 | 8 | 21 | 1.040 | -0.472 | 1974.72 | -3504.10 |
| winter_2026_01 | `source_condition` | `rising_progress` | 21 | 23 | 15 | 0.570 | 0.122 | 81.71 | -3173.15 |
| winter_2026_01 | `source_condition` | `profit_reentry_progress` | 26 | 18 | 19 | 0.774 | -0.197 | 2108.26 | -3817.08 |
| winter_2026_01 | `source_condition` | `profit_reentry_condition_loss` | 26 | 18 | 15 | 0.615 | -0.004 | 582.60 | -3764.36 |
