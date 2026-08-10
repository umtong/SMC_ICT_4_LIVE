# EDTMA system anatomy

The tables separate opportunity generation, re-entry policy, exit engine and stop geometry. They are not a binary promotion gate.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source/rolling exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| spring_2025_03 | `b2_control` | 20 | 14 | 5175.36 | 6975.05 | 0.742 | -1799.69 | 2 | 18 | 0 | 0 |
| spring_2025_03 | `b2_context_loss` | 20 | 12 | 4302.14 | 5532.37 | 0.778 | -1230.23 | 2 | 16 | 0 | 0 |
| spring_2025_03 | `b2_condition_context` | 20 | 12 | 4302.14 | 5532.37 | 0.778 | -1230.23 | 2 | 16 | 0 | 0 |
| spring_2025_03 | `b2_progress_context` | 21 | 12 | 4095.49 | 6492.09 | 0.631 | -2396.60 | 2 | 15 | 0 | 0 |
| spring_2025_03 | `b2_btc_control` | 18 | 12 | 4516.03 | 6951.00 | 0.650 | -2434.98 | 2 | 16 | 0 | 0 |
| spring_2025_03 | `b2_btc_context` | 20 | 12 | 4382.61 | 3713.11 | 1.180 | 669.50 | 2 | 16 | 0 | 0 |
| spring_2025_03 | `b3_control` | 14 | 9 | 3165.25 | 6879.76 | 0.460 | -3714.51 | 1 | 12 | 0 | 0 |
| spring_2025_03 | `b3_context_loss` | 14 | 7 | 2379.05 | 5379.58 | 0.442 | -3000.54 | 1 | 10 | 0 | 0 |
| autumn_2025_09 | `b2_control` | 4 | 3 | 1606.06 | 39.97 | 40.181 | 1566.09 | 0 | 3 | 1 | 0 |
| autumn_2025_09 | `b2_context_loss` | 5 | 0 | 0.00 | 1244.89 | 0.000 | -1244.89 | 0 | 0 | 0 | 0 |
| autumn_2025_09 | `b2_condition_context` | 5 | 0 | 0.00 | 1365.25 | 0.000 | -1365.25 | 0 | 0 | 0 | 0 |
| autumn_2025_09 | `b2_progress_context` | 5 | 0 | 0.00 | 1365.25 | 0.000 | -1365.25 | 0 | 0 | 0 | 0 |
| autumn_2025_09 | `b2_btc_control` | 2 | 2 | 1355.88 | 0.00 | 0.000 | 1355.88 | 0 | 2 | 0 | 0 |
| autumn_2025_09 | `b2_btc_context` | 2 | 0 | 0.00 | 380.36 | 0.000 | -380.36 | 0 | 0 | 0 | 0 |
| autumn_2025_09 | `b3_control` | 1 | 1 | 819.01 | 0.00 | 0.000 | 819.01 | 0 | 1 | 0 | 0 |
| autumn_2025_09 | `b3_context_loss` | 1 | 0 | 0.00 | 279.14 | 0.000 | -279.14 | 0 | 0 | 0 | 0 |
| winter_2026_01 | `b2_control` | 18 | 15 | 4151.32 | 79.65 | 52.117 | 4071.66 | 1 | 16 | 2 | 0 |
| winter_2026_01 | `b2_context_loss` | 20 | 11 | 2367.61 | 2968.77 | 0.798 | -601.16 | 1 | 12 | 0 | 0 |
| winter_2026_01 | `b2_condition_context` | 20 | 11 | 2367.61 | 2968.77 | 0.798 | -601.16 | 1 | 12 | 0 | 0 |
| winter_2026_01 | `b2_progress_context` | 20 | 11 | 2366.45 | 3707.66 | 0.638 | -1341.21 | 1 | 11 | 0 | 0 |
| winter_2026_01 | `b2_btc_control` | 18 | 15 | 4046.11 | 79.62 | 50.819 | 3966.49 | 1 | 16 | 2 | 0 |
| winter_2026_01 | `b2_btc_context` | 20 | 11 | 2265.26 | 2966.47 | 0.764 | -701.21 | 1 | 12 | 0 | 0 |
| winter_2026_01 | `b3_control` | 11 | 8 | 2481.59 | 79.23 | 31.322 | 2402.37 | 0 | 9 | 2 | 0 |
| winter_2026_01 | `b3_context_loss` | 12 | 6 | 1661.77 | 3080.65 | 0.539 | -1418.88 | 0 | 6 | 0 | 0 |
| summer_2026_06 | `b2_control` | 27 | 26 | 9045.80 | 48.06 | 188.222 | 8997.74 | 6 | 26 | 1 | 0 |
| summer_2026_06 | `b2_context_loss` | 33 | 20 | 5500.85 | 9275.81 | 0.593 | -3774.97 | 8 | 20 | 0 | 0 |
| summer_2026_06 | `b2_condition_context` | 33 | 20 | 5494.69 | 9377.55 | 0.586 | -3882.86 | 8 | 20 | 0 | 0 |
| summer_2026_06 | `b2_progress_context` | 34 | 20 | 5494.69 | 8983.54 | 0.612 | -3488.84 | 8 | 21 | 0 | 0 |
| summer_2026_06 | `b2_btc_control` | 20 | 19 | 7503.99 | 48.06 | 156.140 | 7455.93 | 6 | 19 | 1 | 0 |
| summer_2026_06 | `b2_btc_context` | 23 | 11 | 2824.14 | 9712.00 | 0.291 | -6887.86 | 8 | 11 | 0 | 0 |
| summer_2026_06 | `b3_control` | 27 | 26 | 9520.22 | 48.06 | 198.093 | 9472.16 | 6 | 26 | 1 | 0 |
| summer_2026_06 | `b3_context_loss` | 29 | 16 | 3954.11 | 9813.06 | 0.403 | -5858.95 | 8 | 16 | 0 | 0 |

## Paired mechanism comparisons

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| autumn_2025_09 | `b2_btc_control` | `b2_btc_context` | 2 | 0 | 0 | 0.000 | 0.000 | 0.00 | -1736.24 |
| autumn_2025_09 | `b2_control` | `b2_context_loss` | 4 | 0 | 1 | 0.000 | -30.145 | -294.48 | -2810.98 |
| autumn_2025_09 | `b2_control` | `b2_condition_context` | 4 | 0 | 1 | 0.000 | -33.156 | -294.48 | -2931.34 |
| autumn_2025_09 | `b2_control` | `b2_progress_context` | 4 | 0 | 1 | 0.000 | -33.156 | -294.48 | -2931.34 |
| autumn_2025_09 | `b2_control` | `b2_btc_control` | 2 | 2 | 0 | 0.844 | 1.000 | 0.00 | -210.21 |
| autumn_2025_09 | `b2_control` | `b2_btc_context` | 2 | 2 | 0 | 0.000 | -8.516 | 0.00 | -1946.45 |
| autumn_2025_09 | `b2_control` | `b3_control` | 1 | 3 | 0 | 0.510 | 1.000 | 0.00 | -747.08 |
| autumn_2025_09 | `b2_control` | `b3_context_loss` | 1 | 3 | 0 | 0.000 | -5.983 | 0.00 | -1845.23 |
| autumn_2025_09 | `b3_control` | `b3_context_loss` | 1 | 0 | 0 | 0.000 | 0.000 | 0.00 | -1098.15 |
| spring_2025_03 | `b2_btc_control` | `b2_btc_context` | 18 | 0 | 2 | 0.970 | 0.466 | 38.74 | 3104.48 |
| spring_2025_03 | `b2_control` | `b2_context_loss` | 20 | 0 | 0 | 0.831 | 0.207 | 0.00 | 569.46 |
| spring_2025_03 | `b2_control` | `b2_condition_context` | 20 | 0 | 0 | 0.831 | 0.207 | 0.00 | 569.46 |
| spring_2025_03 | `b2_control` | `b2_progress_context` | 20 | 0 | 1 | 0.791 | 0.069 | 449.54 | -596.91 |
| spring_2025_03 | `b2_control` | `b2_btc_control` | 17 | 3 | 1 | 0.873 | 0.003 | -2937.31 | -635.28 |
| spring_2025_03 | `b2_control` | `b2_btc_context` | 19 | 1 | 1 | 0.847 | 0.468 | -1073.31 | 2469.19 |
| spring_2025_03 | `b2_control` | `b3_control` | 13 | 7 | 1 | 0.612 | 0.014 | -2937.31 | -1914.82 |
| spring_2025_03 | `b2_control` | `b3_context_loss` | 12 | 8 | 2 | 0.460 | 0.229 | -2396.77 | -1200.85 |
| spring_2025_03 | `b3_control` | `b3_context_loss` | 13 | 1 | 1 | 0.752 | 0.218 | -1323.46 | 713.97 |
| summer_2026_06 | `b2_btc_control` | `b2_btc_context` | 20 | 0 | 3 | 0.376 | -201.084 | 790.76 | -14343.79 |
| summer_2026_06 | `b2_control` | `b2_context_loss` | 27 | 0 | 6 | 0.608 | -192.008 | -606.17 | -12772.71 |
| summer_2026_06 | `b2_control` | `b2_condition_context` | 27 | 0 | 6 | 0.607 | -194.125 | -605.54 | -12880.60 |
| summer_2026_06 | `b2_control` | `b2_progress_context` | 27 | 0 | 7 | 0.607 | -185.926 | -211.53 | -12486.59 |
| summer_2026_06 | `b2_control` | `b2_btc_control` | 19 | 8 | 1 | 0.830 | 0.000 | 602.26 | -1541.81 |
| summer_2026_06 | `b2_control` | `b2_btc_context` | 19 | 8 | 4 | 0.312 | -201.084 | -1374.04 | -15885.61 |
| summer_2026_06 | `b2_control` | `b3_control` | 26 | 1 | 1 | 1.052 | 0.000 | 613.63 | 474.42 |
| summer_2026_06 | `b2_control` | `b3_context_loss` | 26 | 1 | 3 | 0.437 | -203.186 | -1587.72 | -14856.70 |
| summer_2026_06 | `b3_control` | `b3_context_loss` | 27 | 0 | 2 | 0.415 | -203.186 | 601.35 | -15331.11 |
| winter_2026_01 | `b2_btc_control` | `b2_btc_context` | 18 | 0 | 2 | 0.560 | -36.259 | -132.74 | -4667.70 |
| winter_2026_01 | `b2_control` | `b2_context_loss` | 18 | 0 | 2 | 0.570 | -36.271 | -132.99 | -4672.83 |
| winter_2026_01 | `b2_control` | `b2_condition_context` | 18 | 0 | 2 | 0.570 | -36.271 | -132.99 | -4672.83 |
| winter_2026_01 | `b2_control` | `b2_progress_context` | 18 | 0 | 2 | 0.570 | -45.547 | -132.99 | -5412.87 |
| winter_2026_01 | `b2_control` | `b2_btc_control` | 17 | 1 | 1 | 0.975 | 0.000 | 119.62 | -105.17 |
| winter_2026_01 | `b2_control` | `b2_btc_context` | 17 | 1 | 3 | 0.546 | -36.242 | -14.20 | -4772.88 |
| winter_2026_01 | `b2_control` | `b3_control` | 10 | 8 | 1 | 0.598 | 0.005 | 343.16 | -1669.30 |
| winter_2026_01 | `b2_control` | `b3_context_loss` | 10 | 8 | 2 | 0.400 | -37.676 | 162.96 | -5490.54 |
| winter_2026_01 | `b3_control` | `b3_context_loss` | 11 | 0 | 1 | 0.670 | -37.884 | 295.72 | -3821.25 |
