# Slope-is-Dope system anatomy

This is not a gate. Opportunity, direction, winner engine, exit engine, risk geometry and implementation are shown separately.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| winter_2025_02 | `structural_control` | 31 | 25 | 12196.12 | 13203.20 | 0.924 | -1007.08 | 0 | 25 | 0 | 5 |
| winter_2025_02 | `sep1_control` | 31 | 25 | 10590.70 | 11500.39 | 0.921 | -909.69 | 0 | 25 | 0 | 6 |
| winter_2025_02 | `sep2_control` | 31 | 27 | 12204.65 | 7003.01 | 1.743 | 5201.64 | 0 | 27 | 0 | 4 |
| winter_2025_02 | `sep1_progress` | 44 | 27 | 13389.47 | 13352.83 | 1.003 | 36.65 | 0 | 27 | 0 | 0 |
| winter_2025_02 | `sep2_condition_loss` | 38 | 29 | 13514.92 | 8340.37 | 1.620 | 5174.55 | 0 | 29 | 0 | 0 |
| winter_2025_02 | `sep2_progress` | 40 | 28 | 13323.89 | 8926.11 | 1.493 | 4397.78 | 0 | 28 | 0 | 0 |
| winter_2025_02 | `sep2_no_signal_condition_loss` | 38 | 29 | 13514.92 | 8340.37 | 1.620 | 5174.55 | 0 | 29 | 0 | 0 |
| winter_2025_02 | `sep2_no_signal_progress` | 40 | 28 | 13323.89 | 8926.11 | 1.493 | 4397.78 | 0 | 28 | 0 | 0 |
| summer_2025_08 | `structural_control` | 35 | 26 | 12400.47 | 12883.31 | 0.963 | -482.85 | 0 | 26 | 1 | 7 |
| summer_2025_08 | `sep1_control` | 33 | 25 | 12113.51 | 10356.64 | 1.170 | 1756.88 | 0 | 25 | 1 | 7 |
| summer_2025_08 | `sep2_control` | 32 | 26 | 10652.87 | 7269.36 | 1.465 | 3383.52 | 0 | 26 | 1 | 5 |
| summer_2025_08 | `sep1_progress` | 56 | 29 | 14006.97 | 18048.88 | 0.776 | -4041.91 | 0 | 29 | 0 | 1 |
| summer_2025_08 | `sep2_condition_loss` | 48 | 32 | 13279.97 | 11630.53 | 1.142 | 1649.44 | 0 | 32 | 0 | 1 |
| summer_2025_08 | `sep2_progress` | 49 | 29 | 12621.44 | 13475.19 | 0.937 | -853.76 | 0 | 29 | 0 | 1 |
| summer_2025_08 | `sep2_no_signal_condition_loss` | 48 | 32 | 13279.97 | 11630.53 | 1.142 | 1649.44 | 0 | 32 | 0 | 0 |
| summer_2025_08 | `sep2_no_signal_progress` | 49 | 29 | 12621.44 | 13475.19 | 0.937 | -853.76 | 0 | 29 | 0 | 0 |
| winter_2026_01 | `structural_control` | 37 | 29 | 20229.08 | 14420.99 | 1.403 | 5808.09 | 0 | 28 | 1 | 5 |
| winter_2026_01 | `sep1_control` | 31 | 25 | 13851.37 | 7768.16 | 1.783 | 6083.22 | 0 | 24 | 1 | 5 |
| winter_2026_01 | `sep2_control` | 26 | 22 | 10187.09 | 3967.81 | 2.567 | 6219.28 | 0 | 22 | 0 | 4 |
| winter_2026_01 | `sep1_progress` | 48 | 21 | 10509.01 | 15913.97 | 0.660 | -5404.97 | 1 | 21 | 0 | 1 |
| winter_2026_01 | `sep2_condition_loss` | 37 | 18 | 7267.00 | 10062.81 | 0.722 | -2795.81 | 0 | 18 | 0 | 1 |
| winter_2026_01 | `sep2_progress` | 40 | 20 | 8257.93 | 10355.25 | 0.797 | -2097.32 | 0 | 20 | 0 | 1 |
| winter_2026_01 | `sep2_no_signal_condition_loss` | 37 | 18 | 7267.00 | 10062.81 | 0.722 | -2795.81 | 0 | 18 | 0 | 0 |
| winter_2026_01 | `sep2_no_signal_progress` | 40 | 20 | 8257.93 | 10355.25 | 0.797 | -2097.32 | 0 | 20 | 0 | 0 |
| summer_2026_06 | `structural_control` | 38 | 32 | 16034.88 | 12308.57 | 1.303 | 3726.31 | 2 | 31 | 1 | 5 |
| summer_2026_06 | `sep1_control` | 35 | 30 | 13642.77 | 9344.29 | 1.460 | 4298.48 | 2 | 29 | 1 | 5 |
| summer_2026_06 | `sep2_control` | 33 | 29 | 13634.48 | 8299.51 | 1.643 | 5334.97 | 2 | 28 | 1 | 3 |
| summer_2026_06 | `sep1_progress` | 49 | 33 | 13052.63 | 11126.00 | 1.173 | 1926.64 | 6 | 33 | 0 | 1 |
| summer_2026_06 | `sep2_condition_loss` | 45 | 32 | 12689.28 | 9835.73 | 1.290 | 2853.55 | 2 | 32 | 0 | 2 |
| summer_2026_06 | `sep2_progress` | 47 | 32 | 11882.72 | 10723.02 | 1.108 | 1159.70 | 5 | 32 | 0 | 2 |
| summer_2026_06 | `sep2_no_signal_condition_loss` | 45 | 32 | 12689.28 | 9835.73 | 1.290 | 2853.55 | 2 | 32 | 0 | 0 |
| summer_2026_06 | `sep2_no_signal_progress` | 47 | 32 | 11882.72 | 10723.02 | 1.108 | 1159.70 | 5 | 32 | 0 | 0 |

## Paired mechanism trade-offs

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| summer_2025_08 | `sep2_control` | `sep2_condition_loss` | 30 | 2 | 18 | 1.247 | -0.600 | -234.00 | -1734.07 |
| summer_2025_08 | `sep2_control` | `sep2_progress` | 28 | 4 | 21 | 1.185 | -0.854 | -493.06 | -4237.27 |
| summer_2025_08 | `sep2_control` | `sep2_no_signal_condition_loss` | 30 | 2 | 18 | 1.247 | -0.600 | -234.00 | -1734.07 |
| summer_2025_08 | `sep2_control` | `sep2_no_signal_progress` | 28 | 4 | 21 | 1.185 | -0.854 | -493.06 | -4237.27 |
| summer_2025_08 | `structural_control` | `sep1_control` | 30 | 5 | 3 | 0.977 | 0.196 | -456.57 | 2239.72 |
| summer_2025_08 | `structural_control` | `sep2_control` | 22 | 13 | 10 | 0.859 | 0.436 | 1466.78 | 3866.36 |
| summer_2025_08 | `structural_control` | `sep1_progress` | 26 | 9 | 30 | 1.130 | -0.401 | -2873.71 | -3559.07 |
| summer_2025_08 | `structural_control` | `sep2_condition_loss` | 21 | 14 | 27 | 1.071 | 0.097 | 1747.35 | 2132.29 |
| summer_2025_08 | `structural_control` | `sep2_progress` | 19 | 16 | 30 | 1.018 | -0.046 | 1464.67 | -370.91 |
| summer_2025_08 | `structural_control` | `sep2_no_signal_condition_loss` | 21 | 14 | 27 | 1.071 | 0.097 | 1747.35 | 2132.29 |
| summer_2025_08 | `structural_control` | `sep2_no_signal_progress` | 19 | 16 | 30 | 1.018 | -0.046 | 1464.67 | -370.91 |
| summer_2026_06 | `sep2_control` | `sep2_condition_loss` | 30 | 3 | 15 | 0.931 | -0.185 | -800.99 | -2481.42 |
| summer_2026_06 | `sep2_control` | `sep2_progress` | 30 | 3 | 17 | 0.872 | -0.292 | -0.69 | -4175.27 |
| summer_2026_06 | `sep2_control` | `sep2_no_signal_condition_loss` | 30 | 3 | 15 | 0.931 | -0.185 | -800.99 | -2481.42 |
| summer_2026_06 | `sep2_control` | `sep2_no_signal_progress` | 30 | 3 | 17 | 0.872 | -0.292 | -0.69 | -4175.27 |
| summer_2026_06 | `structural_control` | `sep1_control` | 33 | 5 | 2 | 0.851 | 0.241 | -568.71 | 572.17 |
| summer_2026_06 | `structural_control` | `sep2_control` | 30 | 8 | 3 | 0.850 | 0.326 | -3032.19 | 1608.66 |
| summer_2026_06 | `structural_control` | `sep1_progress` | 30 | 8 | 19 | 0.814 | 0.096 | 404.51 | -1799.67 |
| summer_2026_06 | `structural_control` | `sep2_condition_loss` | 28 | 10 | 17 | 0.791 | 0.201 | -2708.32 | -872.76 |
| summer_2026_06 | `structural_control` | `sep2_progress` | 28 | 10 | 19 | 0.741 | 0.129 | -1881.01 | -2566.61 |
| summer_2026_06 | `structural_control` | `sep2_no_signal_condition_loss` | 28 | 10 | 17 | 0.791 | 0.201 | -2708.32 | -872.76 |
| summer_2026_06 | `structural_control` | `sep2_no_signal_progress` | 28 | 10 | 19 | 0.741 | 0.129 | -1881.01 | -2566.61 |
| winter_2025_02 | `sep2_control` | `sep2_condition_loss` | 31 | 0 | 7 | 1.107 | -0.191 | -558.43 | -27.09 |
| winter_2025_02 | `sep2_control` | `sep2_progress` | 31 | 0 | 9 | 1.092 | -0.275 | -1986.88 | -803.86 |
| winter_2025_02 | `sep2_control` | `sep2_no_signal_condition_loss` | 31 | 0 | 7 | 1.107 | -0.191 | -558.43 | -27.09 |
| winter_2025_02 | `sep2_control` | `sep2_no_signal_progress` | 31 | 0 | 9 | 1.092 | -0.275 | -1986.88 | -803.86 |
| winter_2025_02 | `structural_control` | `sep1_control` | 24 | 7 | 7 | 0.868 | 0.129 | -3117.98 | 97.39 |
| winter_2025_02 | `structural_control` | `sep2_control` | 21 | 10 | 10 | 1.001 | 0.470 | -850.67 | 6208.72 |
| winter_2025_02 | `structural_control` | `sep1_progress` | 24 | 7 | 20 | 1.098 | -0.011 | -1598.15 | 1043.73 |
| winter_2025_02 | `structural_control` | `sep2_condition_loss` | 21 | 10 | 17 | 1.108 | 0.368 | 2011.37 | 6181.63 |
| winter_2025_02 | `structural_control` | `sep2_progress` | 21 | 10 | 19 | 1.092 | 0.324 | 1003.88 | 5404.86 |
| winter_2025_02 | `structural_control` | `sep2_no_signal_condition_loss` | 21 | 10 | 17 | 1.108 | 0.368 | 2011.37 | 6181.63 |
| winter_2025_02 | `structural_control` | `sep2_no_signal_progress` | 21 | 10 | 19 | 1.092 | 0.324 | 1003.88 | 5404.86 |
| winter_2026_01 | `sep2_control` | `sep2_condition_loss` | 25 | 1 | 12 | 0.713 | -1.536 | -1788.61 | -9015.09 |
| winter_2026_01 | `sep2_control` | `sep2_progress` | 25 | 1 | 15 | 0.811 | -1.610 | -1092.43 | -8316.60 |
| winter_2026_01 | `sep2_control` | `sep2_no_signal_condition_loss` | 25 | 1 | 12 | 0.713 | -1.536 | -1788.61 | -9015.09 |
| winter_2026_01 | `sep2_control` | `sep2_no_signal_progress` | 25 | 1 | 15 | 0.811 | -1.610 | -1092.43 | -8316.60 |
| winter_2026_01 | `structural_control` | `sep1_control` | 30 | 7 | 1 | 0.685 | 0.461 | 958.64 | 275.13 |
| winter_2026_01 | `structural_control` | `sep2_control` | 17 | 20 | 9 | 0.504 | 0.725 | 3611.69 | 411.19 |
| winter_2026_01 | `structural_control` | `sep1_progress` | 28 | 9 | 20 | 0.519 | -0.104 | -1162.48 | -11213.06 |
| winter_2026_01 | `structural_control` | `sep2_condition_loss` | 16 | 21 | 21 | 0.359 | 0.302 | -2197.45 | -8603.90 |
| winter_2026_01 | `structural_control` | `sep2_progress` | 16 | 21 | 24 | 0.408 | 0.282 | -1327.75 | -7905.41 |
| winter_2026_01 | `structural_control` | `sep2_no_signal_condition_loss` | 16 | 21 | 21 | 0.359 | 0.302 | -2197.45 | -8603.90 |
| winter_2026_01 | `structural_control` | `sep2_no_signal_progress` | 16 | 21 | 24 | 0.408 | 0.282 | -1327.75 | -7905.41 |
