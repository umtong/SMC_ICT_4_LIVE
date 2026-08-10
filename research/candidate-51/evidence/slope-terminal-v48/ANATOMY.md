# Slope-is-Dope system anatomy

This is not a gate. Opportunity, direction, winner engine, exit engine, risk geometry and implementation are shown separately.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev_2024_01 | `ma_control` | 18 | 13 | 3574.29 | 5385.28 | 0.664 | -1810.99 | 1 | 10 | 6 | 1 |
| dev_2024_01 | `ma_progress_nonterminal` | 35 | 14 | 4525.91 | 12990.55 | 0.348 | -8464.64 | 2 | 14 | 0 | 0 |
| dev_2024_01 | `ma_progress_terminal` | 33 | 13 | 4160.41 | 12000.08 | 0.347 | -7839.67 | 2 | 13 | 0 | 0 |
| dev_2024_01 | `ma_condition_terminal` | 32 | 13 | 4147.63 | 12405.28 | 0.334 | -8257.65 | 2 | 13 | 0 | 0 |
| dev_2024_01 | `ma_any_loss_terminal` | 18 | 13 | 3574.29 | 5385.28 | 0.664 | -1810.99 | 1 | 10 | 6 | 1 |
| dev_2024_01 | `ma_progress_any_loss` | 33 | 13 | 4160.41 | 12000.08 | 0.347 | -7839.67 | 2 | 13 | 0 | 0 |
| dev_2024_01 | `no_signal_any_loss` | 18 | 13 | 3554.80 | 6080.52 | 0.585 | -2525.72 | 1 | 10 | 6 | 0 |
| dev_2024_01 | `no_signal_progress_any_loss` | 33 | 13 | 4160.41 | 12000.08 | 0.347 | -7839.67 | 2 | 13 | 0 | 0 |
| dev_2024_08 | `ma_control` | 33 | 30 | 9514.15 | 4215.63 | 2.257 | 5298.52 | 3 | 29 | 2 | 2 |
| dev_2024_08 | `ma_progress_nonterminal` | 44 | 28 | 10659.46 | 8412.15 | 1.267 | 2247.31 | 4 | 28 | 0 | 0 |
| dev_2024_08 | `ma_progress_terminal` | 43 | 29 | 11153.44 | 7721.13 | 1.445 | 3432.30 | 4 | 29 | 0 | 0 |
| dev_2024_08 | `ma_condition_terminal` | 42 | 28 | 10637.19 | 8630.55 | 1.233 | 2006.64 | 4 | 28 | 0 | 0 |
| dev_2024_08 | `ma_any_loss_terminal` | 33 | 30 | 9514.15 | 4215.63 | 2.257 | 5298.52 | 3 | 29 | 2 | 2 |
| dev_2024_08 | `ma_progress_any_loss` | 43 | 29 | 11153.44 | 7721.13 | 1.445 | 3432.30 | 4 | 29 | 0 | 0 |
| dev_2024_08 | `no_signal_any_loss` | 33 | 30 | 9507.91 | 4600.59 | 2.067 | 4907.32 | 3 | 29 | 2 | 0 |
| dev_2024_08 | `no_signal_progress_any_loss` | 43 | 29 | 11153.44 | 7721.13 | 1.445 | 3432.30 | 4 | 29 | 0 | 0 |
| dev_2025_01 | `ma_control` | 14 | 10 | 3046.95 | 7142.34 | 0.427 | -4095.39 | 0 | 9 | 2 | 1 |
| dev_2025_01 | `ma_progress_nonterminal` | 39 | 15 | 5949.09 | 11826.49 | 0.503 | -5877.40 | 0 | 15 | 0 | 0 |
| dev_2025_01 | `ma_progress_terminal` | 38 | 15 | 5982.65 | 11290.23 | 0.530 | -5307.58 | 0 | 15 | 0 | 0 |
| dev_2025_01 | `ma_condition_terminal` | 33 | 13 | 4749.25 | 10805.40 | 0.440 | -6056.15 | 0 | 13 | 0 | 0 |
| dev_2025_01 | `ma_any_loss_terminal` | 14 | 10 | 3046.95 | 7142.34 | 0.427 | -4095.39 | 0 | 9 | 2 | 1 |
| dev_2025_01 | `ma_progress_any_loss` | 38 | 15 | 5982.65 | 11290.23 | 0.530 | -5307.58 | 0 | 15 | 0 | 0 |
| dev_2025_01 | `no_signal_any_loss` | 14 | 10 | 3045.17 | 7613.90 | 0.400 | -4568.73 | 0 | 9 | 2 | 0 |
| dev_2025_01 | `no_signal_progress_any_loss` | 38 | 15 | 5982.65 | 11290.23 | 0.530 | -5307.58 | 0 | 15 | 0 | 0 |
| dev_2025_04 | `ma_control` | 19 | 15 | 5325.35 | 8413.30 | 0.633 | -3087.95 | 0 | 15 | 1 | 1 |
| dev_2025_04 | `ma_progress_nonterminal` | 26 | 16 | 6327.77 | 8807.05 | 0.718 | -2479.29 | 0 | 16 | 0 | 0 |
| dev_2025_04 | `ma_progress_terminal` | 26 | 16 | 6284.07 | 9150.89 | 0.687 | -2866.82 | 0 | 16 | 0 | 0 |
| dev_2025_04 | `ma_condition_terminal` | 24 | 15 | 6223.57 | 8792.66 | 0.708 | -2569.09 | 0 | 15 | 0 | 0 |
| dev_2025_04 | `ma_any_loss_terminal` | 19 | 15 | 5325.35 | 8413.30 | 0.633 | -3087.95 | 0 | 15 | 1 | 1 |
| dev_2025_04 | `ma_progress_any_loss` | 26 | 16 | 6284.07 | 9150.89 | 0.687 | -2866.82 | 0 | 16 | 0 | 0 |
| dev_2025_04 | `no_signal_any_loss` | 19 | 15 | 5378.67 | 7462.59 | 0.721 | -2083.92 | 0 | 15 | 1 | 0 |
| dev_2025_04 | `no_signal_progress_any_loss` | 26 | 16 | 6284.07 | 9150.89 | 0.687 | -2866.82 | 0 | 16 | 0 | 0 |
| dev_2025_07 | `ma_control` | 13 | 9 | 2471.90 | 5702.71 | 0.433 | -3230.81 | 0 | 9 | 2 | 1 |
| dev_2025_07 | `ma_progress_nonterminal` | 23 | 9 | 2984.25 | 6079.25 | 0.491 | -3095.00 | 0 | 9 | 0 | 0 |
| dev_2025_07 | `ma_progress_terminal` | 23 | 9 | 3540.37 | 6086.85 | 0.582 | -2546.49 | 0 | 9 | 0 | 0 |
| dev_2025_07 | `ma_condition_terminal` | 22 | 9 | 2975.79 | 5900.54 | 0.504 | -2924.75 | 0 | 9 | 0 | 0 |
| dev_2025_07 | `ma_any_loss_terminal` | 13 | 9 | 2471.90 | 5702.71 | 0.433 | -3230.81 | 0 | 9 | 2 | 1 |
| dev_2025_07 | `ma_progress_any_loss` | 23 | 9 | 3540.37 | 6086.85 | 0.582 | -2546.49 | 0 | 9 | 0 | 0 |
| dev_2025_07 | `no_signal_any_loss` | 13 | 9 | 2466.67 | 5953.55 | 0.414 | -3486.88 | 0 | 9 | 2 | 0 |
| dev_2025_07 | `no_signal_progress_any_loss` | 23 | 9 | 3540.37 | 6086.85 | 0.582 | -2546.49 | 0 | 9 | 0 | 0 |
| dev_2025_10 | `ma_control` | 21 | 16 | 5536.95 | 8442.96 | 0.656 | -2906.01 | 0 | 16 | 2 | 1 |
| dev_2025_10 | `ma_progress_nonterminal` | 34 | 18 | 5680.76 | 9952.77 | 0.571 | -4272.01 | 0 | 18 | 0 | 0 |
| dev_2025_10 | `ma_progress_terminal` | 34 | 17 | 5488.59 | 10137.09 | 0.541 | -4648.50 | 0 | 17 | 0 | 0 |
| dev_2025_10 | `ma_condition_terminal` | 32 | 17 | 5472.70 | 10260.55 | 0.533 | -4787.85 | 0 | 17 | 0 | 0 |
| dev_2025_10 | `ma_any_loss_terminal` | 21 | 16 | 5536.95 | 8442.96 | 0.656 | -2906.01 | 0 | 16 | 2 | 1 |
| dev_2025_10 | `ma_progress_any_loss` | 34 | 17 | 5488.59 | 10137.09 | 0.541 | -4648.50 | 0 | 17 | 0 | 0 |
| dev_2025_10 | `no_signal_any_loss` | 21 | 16 | 5634.42 | 6344.06 | 0.888 | -709.64 | 0 | 16 | 3 | 0 |
| dev_2025_10 | `no_signal_progress_any_loss` | 34 | 17 | 5488.59 | 10137.09 | 0.541 | -4648.50 | 0 | 17 | 0 | 0 |
| dev_2026_02 | `ma_control` | 48 | 45 | 14589.84 | 3371.33 | 4.328 | 11218.51 | 0 | 45 | 2 | 0 |
| dev_2026_02 | `ma_progress_nonterminal` | 59 | 47 | 14911.93 | 6911.80 | 2.157 | 8000.14 | 0 | 47 | 0 | 0 |
| dev_2026_02 | `ma_progress_terminal` | 59 | 47 | 14829.94 | 6680.11 | 2.220 | 8149.83 | 0 | 47 | 0 | 0 |
| dev_2026_02 | `ma_condition_terminal` | 57 | 46 | 13905.95 | 7067.53 | 1.968 | 6838.42 | 0 | 46 | 0 | 0 |
| dev_2026_02 | `ma_any_loss_terminal` | 48 | 45 | 14589.84 | 3371.33 | 4.328 | 11218.51 | 0 | 45 | 2 | 0 |
| dev_2026_02 | `ma_progress_any_loss` | 59 | 47 | 14829.94 | 6680.11 | 2.220 | 8149.83 | 0 | 47 | 0 | 0 |
| dev_2026_02 | `no_signal_any_loss` | 48 | 45 | 14589.84 | 3371.33 | 4.328 | 11218.51 | 0 | 45 | 2 | 0 |
| dev_2026_02 | `no_signal_progress_any_loss` | 59 | 47 | 14829.94 | 6680.11 | 2.220 | 8149.83 | 0 | 47 | 0 | 0 |
| dev_2026_05 | `ma_control` | 4 | 1 | 544.00 | 2005.26 | 0.271 | -1461.26 | 0 | 1 | 2 | 1 |
| dev_2026_05 | `ma_progress_nonterminal` | 7 | 0 | 0.00 | 3638.03 | 0.000 | -3638.03 | 0 | 0 | 0 | 0 |
| dev_2026_05 | `ma_progress_terminal` | 7 | 0 | 0.00 | 3638.03 | 0.000 | -3638.03 | 0 | 0 | 0 | 0 |
| dev_2026_05 | `ma_condition_terminal` | 7 | 0 | 0.00 | 3638.03 | 0.000 | -3638.03 | 0 | 0 | 0 | 0 |
| dev_2026_05 | `ma_any_loss_terminal` | 4 | 1 | 544.00 | 2005.26 | 0.271 | -1461.26 | 0 | 1 | 2 | 1 |
| dev_2026_05 | `ma_progress_any_loss` | 7 | 0 | 0.00 | 3638.03 | 0.000 | -3638.03 | 0 | 0 | 0 | 0 |
| dev_2026_05 | `no_signal_any_loss` | 3 | 1 | 541.56 | 2446.05 | 0.221 | -1904.49 | 0 | 1 | 1 | 0 |
| dev_2026_05 | `no_signal_progress_any_loss` | 7 | 0 | 0.00 | 3638.03 | 0.000 | -3638.03 | 0 | 0 | 0 | 0 |

## Paired mechanism trade-offs

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev_2024_01 | `ma_control` | `ma_progress_nonterminal` | 18 | 0 | 17 | 1.266 | -1.412 | -4924.14 | -6653.65 |
| dev_2024_01 | `ma_control` | `ma_progress_terminal` | 18 | 0 | 15 | 1.164 | -1.228 | -4284.26 | -6028.68 |
| dev_2024_01 | `ma_control` | `ma_condition_terminal` | 18 | 0 | 14 | 1.160 | -1.304 | -3731.95 | -6446.66 |
| dev_2024_01 | `ma_control` | `ma_any_loss_terminal` | 18 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2024_01 | `ma_control` | `ma_progress_any_loss` | 18 | 0 | 15 | 1.164 | -1.228 | -4284.26 | -6028.68 |
| dev_2024_01 | `ma_control` | `no_signal_any_loss` | 18 | 0 | 0 | 0.995 | -0.129 | 0.00 | -714.73 |
| dev_2024_01 | `ma_control` | `no_signal_progress_any_loss` | 18 | 0 | 15 | 1.164 | -1.228 | -4284.26 | -6028.68 |
| dev_2024_08 | `ma_control` | `ma_progress_nonterminal` | 32 | 1 | 12 | 1.120 | -0.995 | -345.70 | -3051.20 |
| dev_2024_08 | `ma_control` | `ma_progress_terminal` | 32 | 1 | 11 | 1.172 | -0.832 | 832.80 | -1866.21 |
| dev_2024_08 | `ma_control` | `ma_condition_terminal` | 32 | 1 | 10 | 1.118 | -1.047 | -303.90 | -3291.88 |
| dev_2024_08 | `ma_control` | `ma_any_loss_terminal` | 33 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2024_08 | `ma_control` | `ma_progress_any_loss` | 32 | 1 | 11 | 1.172 | -0.832 | 832.80 | -1866.21 |
| dev_2024_08 | `ma_control` | `no_signal_any_loss` | 33 | 0 | 0 | 0.999 | -0.091 | 0.00 | -391.19 |
| dev_2024_08 | `ma_control` | `no_signal_progress_any_loss` | 32 | 1 | 11 | 1.172 | -0.832 | 832.80 | -1866.21 |
| dev_2025_01 | `ma_control` | `ma_progress_nonterminal` | 13 | 1 | 26 | 1.952 | -0.656 | -2160.47 | -1782.01 |
| dev_2025_01 | `ma_control` | `ma_progress_terminal` | 12 | 2 | 26 | 1.963 | -0.581 | -3041.15 | -1212.19 |
| dev_2025_01 | `ma_control` | `ma_condition_terminal` | 14 | 0 | 19 | 1.559 | -0.513 | -1902.89 | -1960.76 |
| dev_2025_01 | `ma_control` | `ma_any_loss_terminal` | 14 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2025_01 | `ma_control` | `ma_progress_any_loss` | 12 | 2 | 26 | 1.963 | -0.581 | -3041.15 | -1212.19 |
| dev_2025_01 | `ma_control` | `no_signal_any_loss` | 14 | 0 | 0 | 0.999 | -0.066 | 0.00 | -473.34 |
| dev_2025_01 | `ma_control` | `no_signal_progress_any_loss` | 12 | 2 | 26 | 1.963 | -0.581 | -3041.15 | -1212.19 |
| dev_2025_04 | `ma_control` | `ma_progress_nonterminal` | 19 | 0 | 7 | 1.188 | -0.047 | -4167.62 | 608.66 |
| dev_2025_04 | `ma_control` | `ma_progress_terminal` | 19 | 0 | 7 | 1.180 | -0.088 | -4549.24 | 221.13 |
| dev_2025_04 | `ma_control` | `ma_condition_terminal` | 19 | 0 | 5 | 1.169 | -0.045 | -3324.27 | 518.86 |
| dev_2025_04 | `ma_control` | `ma_any_loss_terminal` | 19 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2025_04 | `ma_control` | `ma_progress_any_loss` | 19 | 0 | 7 | 1.180 | -0.088 | -4549.24 | 221.13 |
| dev_2025_04 | `ma_control` | `no_signal_any_loss` | 19 | 0 | 0 | 1.010 | 0.113 | 0.00 | 1004.03 |
| dev_2025_04 | `ma_control` | `no_signal_progress_any_loss` | 19 | 0 | 7 | 1.180 | -0.088 | -4549.24 | 221.13 |
| dev_2025_07 | `ma_control` | `ma_progress_nonterminal` | 12 | 1 | 11 | 1.207 | -0.066 | 637.82 | 135.81 |
| dev_2025_07 | `ma_control` | `ma_progress_terminal` | 12 | 1 | 11 | 1.432 | -0.067 | 1188.54 | 684.32 |
| dev_2025_07 | `ma_control` | `ma_condition_terminal` | 12 | 1 | 10 | 1.204 | -0.035 | 877.70 | 306.06 |
| dev_2025_07 | `ma_control` | `ma_any_loss_terminal` | 13 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2025_07 | `ma_control` | `ma_progress_any_loss` | 12 | 1 | 11 | 1.432 | -0.067 | 1188.54 | 684.32 |
| dev_2025_07 | `ma_control` | `no_signal_any_loss` | 13 | 0 | 0 | 0.998 | -0.044 | 0.00 | -256.07 |
| dev_2025_07 | `ma_control` | `no_signal_progress_any_loss` | 12 | 1 | 11 | 1.432 | -0.067 | 1188.54 | 684.32 |
| dev_2025_10 | `ma_control` | `ma_progress_nonterminal` | 19 | 2 | 15 | 1.026 | -0.179 | -2984.01 | -1366.00 |
| dev_2025_10 | `ma_control` | `ma_progress_terminal` | 19 | 2 | 15 | 0.991 | -0.201 | -3365.44 | -1742.50 |
| dev_2025_10 | `ma_control` | `ma_condition_terminal` | 19 | 2 | 13 | 0.988 | -0.215 | -3279.14 | -1881.84 |
| dev_2025_10 | `ma_control` | `ma_any_loss_terminal` | 21 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2025_10 | `ma_control` | `ma_progress_any_loss` | 19 | 2 | 15 | 0.991 | -0.201 | -3365.44 | -1742.50 |
| dev_2025_10 | `ma_control` | `no_signal_any_loss` | 21 | 0 | 0 | 1.018 | 0.249 | 0.00 | 2196.37 |
| dev_2025_10 | `ma_control` | `no_signal_progress_any_loss` | 19 | 2 | 15 | 0.991 | -0.201 | -3365.44 | -1742.50 |
| dev_2026_02 | `ma_control` | `ma_progress_nonterminal` | 47 | 1 | 12 | 1.022 | -1.050 | 141.87 | -3218.38 |
| dev_2026_02 | `ma_control` | `ma_progress_terminal` | 47 | 1 | 12 | 1.016 | -0.981 | 291.02 | -3068.68 |
| dev_2026_02 | `ma_control` | `ma_condition_terminal` | 47 | 1 | 10 | 0.953 | -1.096 | 294.99 | -4380.09 |
| dev_2026_02 | `ma_control` | `ma_any_loss_terminal` | 48 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2026_02 | `ma_control` | `ma_progress_any_loss` | 47 | 1 | 12 | 1.016 | -0.981 | 291.02 | -3068.68 |
| dev_2026_02 | `ma_control` | `no_signal_any_loss` | 48 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2026_02 | `ma_control` | `no_signal_progress_any_loss` | 47 | 1 | 12 | 1.016 | -0.981 | 291.02 | -3068.68 |
| dev_2026_05 | `ma_control` | `ma_progress_nonterminal` | 4 | 0 | 3 | 0.000 | -0.814 | -1549.99 | -2176.77 |
| dev_2026_05 | `ma_control` | `ma_progress_terminal` | 4 | 0 | 3 | 0.000 | -0.814 | -1549.99 | -2176.77 |
| dev_2026_05 | `ma_control` | `ma_condition_terminal` | 4 | 0 | 3 | 0.000 | -0.814 | -1549.99 | -2176.77 |
| dev_2026_05 | `ma_control` | `ma_any_loss_terminal` | 4 | 0 | 0 | 1.000 | 0.000 | 0.00 | 0.00 |
| dev_2026_05 | `ma_control` | `ma_progress_any_loss` | 4 | 0 | 3 | 0.000 | -0.814 | -1549.99 | -2176.77 |
| dev_2026_05 | `ma_control` | `no_signal_any_loss` | 3 | 1 | 0 | 0.996 | -0.220 | 0.00 | -443.24 |
| dev_2026_05 | `ma_control` | `no_signal_progress_any_loss` | 4 | 0 | 3 | 0.000 | -0.814 | -1549.99 | -2176.77 |
