# EDTMA system anatomy

The tables separate opportunity generation, re-entry policy, exit engine and stop geometry. They are not a binary promotion gate.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source/rolling exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| spring_2025_03 | `source_score` | 28 | 18 | 7189.21 | 12760.47 | 0.563 | -5571.27 | 2 | 24 | 0 | 0 |
| spring_2025_03 | `freshest` | 29 | 18 | 6757.72 | 22053.01 | 0.306 | -15295.29 | 5 | 21 | 0 | 0 |
| spring_2025_03 | `moderate_volume` | 29 | 19 | 8124.68 | 19594.76 | 0.415 | -11470.08 | 3 | 22 | 0 | 0 |
| spring_2025_03 | `breadth2_score` | 20 | 14 | 5175.36 | 6975.05 | 0.742 | -1799.69 | 2 | 18 | 0 | 0 |
| spring_2025_03 | `breadth2_fresh` | 21 | 14 | 4610.81 | 16650.97 | 0.277 | -12040.17 | 5 | 15 | 0 | 0 |
| spring_2025_03 | `breadth2_moderate` | 21 | 15 | 6029.49 | 14103.21 | 0.428 | -8073.72 | 3 | 16 | 0 | 0 |
| spring_2025_03 | `btc_anchor_score` | 19 | 12 | 4372.02 | 9696.94 | 0.451 | -5324.92 | 2 | 16 | 0 | 0 |
| spring_2025_03 | `btc_anchor_fresh` | 21 | 14 | 4599.92 | 16636.98 | 0.276 | -12037.06 | 6 | 15 | 0 | 0 |
| autumn_2025_09 | `source_score` | 20 | 16 | 5367.00 | 10070.59 | 0.533 | -4703.59 | 1 | 15 | 1 | 0 |
| autumn_2025_09 | `freshest` | 22 | 18 | 6111.28 | 10092.63 | 0.606 | -3981.36 | 1 | 17 | 1 | 0 |
| autumn_2025_09 | `moderate_volume` | 22 | 18 | 6111.28 | 10092.63 | 0.606 | -3981.36 | 1 | 17 | 1 | 0 |
| autumn_2025_09 | `breadth2_score` | 4 | 3 | 1606.06 | 39.97 | 40.181 | 1566.09 | 0 | 3 | 1 | 0 |
| autumn_2025_09 | `breadth2_fresh` | 6 | 6 | 2543.02 | 0.00 | 0.000 | 2543.02 | 0 | 5 | 1 | 0 |
| autumn_2025_09 | `breadth2_moderate` | 6 | 6 | 2543.02 | 0.00 | 0.000 | 2543.02 | 0 | 5 | 1 | 0 |
| autumn_2025_09 | `btc_anchor_score` | 3 | 2 | 1335.41 | 1403.17 | 0.952 | -67.77 | 1 | 2 | 0 | 0 |
| autumn_2025_09 | `btc_anchor_fresh` | 3 | 2 | 1335.41 | 1403.17 | 0.952 | -67.77 | 1 | 2 | 0 | 0 |
| winter_2026_01 | `source_score` | 27 | 21 | 6547.84 | 6088.79 | 1.075 | 459.05 | 1 | 22 | 3 | 0 |
| winter_2026_01 | `freshest` | 22 | 19 | 8431.61 | 3090.86 | 2.728 | 5340.75 | 1 | 17 | 4 | 0 |
| winter_2026_01 | `moderate_volume` | 19 | 15 | 6749.20 | 3197.27 | 2.111 | 3551.94 | 1 | 14 | 4 | 0 |
| winter_2026_01 | `breadth2_score` | 18 | 15 | 4151.32 | 79.65 | 52.117 | 4071.66 | 1 | 16 | 2 | 0 |
| winter_2026_01 | `breadth2_fresh` | 16 | 15 | 5905.73 | 44.78 | 131.887 | 5860.95 | 1 | 13 | 3 | 0 |
| winter_2026_01 | `breadth2_moderate` | 13 | 10 | 4642.95 | 261.19 | 17.776 | 4381.76 | 1 | 9 | 4 | 0 |
| winter_2026_01 | `btc_anchor_score` | 19 | 16 | 4512.31 | 79.77 | 56.564 | 4432.54 | 1 | 17 | 2 | 0 |
| winter_2026_01 | `btc_anchor_fresh` | 15 | 13 | 5822.21 | 100.50 | 57.930 | 5721.71 | 1 | 11 | 4 | 0 |
| summer_2026_06 | `source_score` | 35 | 31 | 10325.09 | 3627.58 | 2.846 | 6697.51 | 6 | 32 | 2 | 0 |
| summer_2026_06 | `freshest` | 39 | 33 | 12166.40 | 6989.84 | 1.741 | 5176.56 | 4 | 35 | 2 | 0 |
| summer_2026_06 | `moderate_volume` | 43 | 37 | 13680.80 | 7065.46 | 1.936 | 6615.34 | 2 | 38 | 3 | 0 |
| summer_2026_06 | `breadth2_score` | 27 | 26 | 9045.80 | 48.06 | 188.222 | 8997.74 | 6 | 26 | 1 | 0 |
| summer_2026_06 | `breadth2_fresh` | 27 | 24 | 9655.10 | 3434.33 | 2.811 | 6220.76 | 4 | 25 | 1 | 0 |
| summer_2026_06 | `breadth2_moderate` | 32 | 29 | 11778.65 | 3454.76 | 3.409 | 8323.89 | 2 | 30 | 1 | 0 |
| summer_2026_06 | `btc_anchor_score` | 21 | 20 | 7672.96 | 48.06 | 159.656 | 7624.90 | 6 | 20 | 1 | 0 |
| summer_2026_06 | `btc_anchor_fresh` | 20 | 17 | 7376.29 | 6226.17 | 1.185 | 1150.12 | 5 | 17 | 1 | 0 |

## Paired mechanism comparisons

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| autumn_2025_09 | `source_score` | `freshest` | 18 | 2 | 4 | 1.139 | -0.002 | 1224.49 | 722.23 |
| autumn_2025_09 | `source_score` | `moderate_volume` | 18 | 2 | 4 | 1.139 | -0.002 | 1224.49 | 722.23 |
| autumn_2025_09 | `source_score` | `breadth2_score` | 2 | 18 | 2 | 0.299 | 0.996 | 785.11 | 6269.68 |
| autumn_2025_09 | `source_score` | `breadth2_fresh` | 1 | 19 | 5 | 0.474 | 1.000 | 2004.27 | 7246.61 |
| autumn_2025_09 | `source_score` | `breadth2_moderate` | 1 | 19 | 5 | 0.474 | 1.000 | 2004.27 | 7246.61 |
| autumn_2025_09 | `source_score` | `btc_anchor_score` | 2 | 18 | 1 | 0.249 | 0.861 | 810.93 | 4635.83 |
| autumn_2025_09 | `source_score` | `btc_anchor_fresh` | 2 | 18 | 1 | 0.249 | 0.861 | 810.93 | 4635.83 |
| spring_2025_03 | `source_score` | `freshest` | 8 | 20 | 21 | 0.940 | -0.728 | -12117.83 | -9724.02 |
| spring_2025_03 | `source_score` | `moderate_volume` | 9 | 19 | 20 | 1.130 | -0.536 | -7857.20 | -5898.81 |
| spring_2025_03 | `source_score` | `breadth2_score` | 19 | 9 | 1 | 0.720 | 0.453 | 526.02 | 3771.58 |
| spring_2025_03 | `source_score` | `breadth2_fresh` | 1 | 27 | 20 | 0.641 | -0.305 | -12994.33 | -6468.90 |
| spring_2025_03 | `source_score` | `breadth2_moderate` | 2 | 26 | 19 | 0.839 | -0.105 | -8576.70 | -2502.45 |
| spring_2025_03 | `source_score` | `btc_anchor_score` | 18 | 10 | 1 | 0.608 | 0.240 | -2850.56 | 246.35 |
| spring_2025_03 | `source_score` | `btc_anchor_fresh` | 2 | 26 | 19 | 0.640 | -0.304 | -10039.44 | -6465.79 |
| summer_2026_06 | `source_score` | `freshest` | 13 | 22 | 26 | 1.178 | -0.927 | 6198.80 | -1520.95 |
| summer_2026_06 | `source_score` | `moderate_volume` | 13 | 22 | 30 | 1.325 | -0.948 | 7194.04 | -82.17 |
| summer_2026_06 | `source_score` | `breadth2_score` | 27 | 8 | 0 | 0.876 | 0.987 | 0.00 | 2300.23 |
| summer_2026_06 | `source_score` | `breadth2_fresh` | 5 | 30 | 22 | 0.935 | 0.053 | 4962.55 | -476.74 |
| summer_2026_06 | `source_score` | `breadth2_moderate` | 5 | 30 | 27 | 1.141 | 0.048 | 6572.52 | 1626.38 |
| summer_2026_06 | `source_score` | `btc_anchor_score` | 19 | 16 | 2 | 0.743 | 0.987 | 771.24 | 927.39 |
| summer_2026_06 | `source_score` | `btc_anchor_fresh` | 0 | 35 | 20 | 0.714 | -0.716 | 1150.12 | -5547.39 |
| winter_2026_01 | `source_score` | `freshest` | 13 | 14 | 9 | 1.288 | 0.492 | 5383.40 | 4881.70 |
| winter_2026_01 | `source_score` | `moderate_volume` | 8 | 19 | 11 | 1.031 | 0.475 | 5544.83 | 3092.89 |
| winter_2026_01 | `source_score` | `breadth2_score` | 12 | 15 | 6 | 0.634 | 0.987 | 1654.82 | 3612.61 |
| winter_2026_01 | `source_score` | `breadth2_fresh` | 2 | 25 | 14 | 0.902 | 0.993 | 5341.50 | 5401.90 |
| winter_2026_01 | `source_score` | `breadth2_moderate` | 1 | 26 | 12 | 0.709 | 0.957 | 4426.42 | 3922.71 |
| winter_2026_01 | `source_score` | `btc_anchor_score` | 14 | 13 | 5 | 0.689 | 0.987 | 1310.59 | 3973.49 |
| winter_2026_01 | `source_score` | `btc_anchor_fresh` | 4 | 23 | 11 | 0.889 | 0.983 | 4508.44 | 5262.66 |
