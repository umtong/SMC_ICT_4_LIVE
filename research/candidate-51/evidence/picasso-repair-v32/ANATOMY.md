# Picasso mechanism anatomy

This is not a pass/fail table. It separates entry opportunity, winner management, loss management, risk geometry, and implementation validity.

Independent weekly accounts are never stitched into a claimed continuous NAV.

## Per-account anatomy

| interval | variant | trades | wins | gross profit | gross loss | PF | net | invalid fills | trail exits | source exits | repair exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev_2026_07_22 | `exact_15m_source` | 19 | 12 | 3212.48 | 2975.90 | 1.079 | 236.58 | 1 | 11 | 2 | 0 |
| dev_2026_07_22 | `corrected_15m_source` | 16 | 10 | 2089.73 | 2527.64 | 0.827 | -437.91 | 1 | 10 | 1 | 0 |
| dev_2026_07_22 | `directional_5m_source` | 29 | 20 | 4215.75 | 4414.00 | 0.955 | -198.24 | 0 | 20 | 5 | 0 |
| dev_2026_07_22 | `directional_5m_no_source` | 14 | 9 | 1279.01 | 4241.41 | 0.302 | -2962.40 | 0 | 9 | 0 | 0 |
| dev_2026_07_22 | `directional_5m_structural_source` | 87 | 24 | 36472.31 | 101947.40 | 0.358 | -65475.09 | 0 | 24 | 1 | 0 |
| dev_2026_07_22 | `directional_5m_structural_no_source` | 82 | 24 | 38014.01 | 99489.07 | 0.382 | -61475.06 | 0 | 24 | 0 | 0 |
| dev_2026_07_22 | `directional_5m_structural_lifecycle` | 165 | 34 | 25006.51 | 112605.33 | 0.222 | -87598.83 | 1 | 25 | 0 | 77 |
| dev_2026_07_22 | `directional_5m_structural_progress` | 165 | 34 | 25006.51 | 112605.33 | 0.222 | -87598.83 | 1 | 25 | 0 | 77 |
| dev_2026_07_22 | `directional_5m_midline_lifecycle` | 172 | 34 | 22799.71 | 112954.37 | 0.202 | -90154.67 | 1 | 24 | 0 | 72 |
| spring_2025_03_10 | `exact_15m_source` | 34 | 26 | 5480.09 | 7498.77 | 0.731 | -2018.68 | 3 | 27 | 2 | 0 |
| spring_2025_03_10 | `corrected_15m_source` | 21 | 17 | 2972.13 | 2200.21 | 1.351 | 771.92 | 1 | 17 | 1 | 0 |
| spring_2025_03_10 | `directional_5m_source` | 60 | 45 | 8054.10 | 14852.53 | 0.542 | -6798.44 | 1 | 47 | 8 | 0 |
| spring_2025_03_10 | `directional_5m_no_source` | 28 | 21 | 4952.33 | 9426.71 | 0.525 | -4474.38 | 1 | 23 | 0 | 0 |
| spring_2025_03_10 | `directional_5m_structural_source` | 175 | 86 | 74820.90 | 141201.89 | 0.530 | -66380.99 | 13 | 87 | 0 | 0 |
| spring_2025_03_10 | `directional_5m_structural_no_source` | 175 | 86 | 74820.90 | 141201.89 | 0.530 | -66380.99 | 13 | 87 | 0 | 0 |
| spring_2025_03_10 | `directional_5m_structural_lifecycle` | 217 | 93 | 65219.99 | 139117.04 | 0.469 | -73897.05 | 14 | 92 | 0 | 59 |
| spring_2025_03_10 | `directional_5m_structural_progress` | 217 | 93 | 65219.99 | 139117.04 | 0.469 | -73897.05 | 14 | 92 | 0 | 59 |
| spring_2025_03_10 | `directional_5m_midline_lifecycle` | 228 | 90 | 60636.64 | 139258.15 | 0.435 | -78621.52 | 15 | 89 | 0 | 54 |
| autumn_2025_10_13 | `exact_15m_source` | 24 | 18 | 3523.24 | 3951.00 | 0.892 | -427.76 | 2 | 19 | 0 | 0 |
| autumn_2025_10_13 | `corrected_15m_source` | 27 | 24 | 4468.87 | 3776.31 | 1.183 | 692.56 | 0 | 24 | 0 | 0 |
| autumn_2025_10_13 | `directional_5m_source` | 44 | 33 | 5134.46 | 12042.29 | 0.426 | -6907.84 | 1 | 32 | 6 | 0 |
| autumn_2025_10_13 | `directional_5m_no_source` | 31 | 27 | 5465.02 | 8282.31 | 0.660 | -2817.29 | 1 | 27 | 0 | 0 |
| autumn_2025_10_13 | `directional_5m_structural_source` | 141 | 68 | 63466.61 | 125352.15 | 0.506 | -61885.54 | 7 | 69 | 1 | 0 |
| autumn_2025_10_13 | `directional_5m_structural_no_source` | 140 | 68 | 62942.73 | 125831.76 | 0.500 | -62889.03 | 7 | 69 | 0 | 0 |
| autumn_2025_10_13 | `directional_5m_structural_lifecycle` | 189 | 75 | 76839.17 | 145079.61 | 0.530 | -68240.43 | 9 | 74 | 0 | 60 |
| autumn_2025_10_13 | `directional_5m_structural_progress` | 189 | 75 | 76839.17 | 145079.61 | 0.530 | -68240.43 | 9 | 74 | 0 | 60 |
| autumn_2025_10_13 | `directional_5m_midline_lifecycle` | 199 | 77 | 75485.32 | 148410.31 | 0.509 | -72924.99 | 8 | 76 | 0 | 57 |
| spring_2026_05_11 | `exact_15m_source` | 19 | 13 | 3580.75 | 4220.21 | 0.848 | -639.46 | 0 | 13 | 1 | 0 |
| spring_2026_05_11 | `corrected_15m_source` | 15 | 10 | 2208.93 | 3584.79 | 0.616 | -1375.86 | 0 | 10 | 1 | 0 |
| spring_2026_05_11 | `directional_5m_source` | 34 | 26 | 6732.52 | 2917.80 | 2.307 | 3814.73 | 0 | 26 | 5 | 0 |
| spring_2026_05_11 | `directional_5m_no_source` | 18 | 16 | 4106.59 | 1203.37 | 3.413 | 2903.22 | 0 | 15 | 0 | 0 |
| spring_2026_05_11 | `directional_5m_structural_source` | 87 | 37 | 71802.28 | 107777.40 | 0.666 | -35975.12 | 1 | 37 | 1 | 0 |
| spring_2026_05_11 | `directional_5m_structural_no_source` | 82 | 36 | 67811.11 | 102305.00 | 0.663 | -34493.89 | 1 | 35 | 0 | 0 |
| spring_2026_05_11 | `directional_5m_structural_lifecycle` | 168 | 41 | 45456.35 | 126188.04 | 0.360 | -80731.69 | 2 | 38 | 0 | 69 |
| spring_2026_05_11 | `directional_5m_structural_progress` | 168 | 41 | 45456.35 | 126188.04 | 0.360 | -80731.69 | 2 | 38 | 0 | 69 |
| spring_2026_05_11 | `directional_5m_midline_lifecycle` | 180 | 37 | 39263.34 | 127259.93 | 0.309 | -87996.58 | 3 | 34 | 0 | 59 |

## Paired episode trade-offs

| interval | control | experiment | common | GP preservation | GL reduction | control losers rescued | control winners still positive | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_no_source` | 23 | 1.064 | 0.312 | 2 | 17 | 4090.55 |
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_structural_source` | 35 | 12.361 | -9.409 | 0 | 20 | -54977.70 |
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_structural_no_source` | 35 | 12.259 | -9.449 | 0 | 20 | -55981.19 |
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_structural_lifecycle` | 36 | 14.965 | -11.048 | 0 | 16 | -61332.60 |
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_structural_progress` | 36 | 14.965 | -11.048 | 0 | 16 | -61332.60 |
| autumn_2025_10_13 | `directional_5m_source` | `directional_5m_midline_lifecycle` | 37 | 14.702 | -11.324 | 0 | 16 | -66017.15 |
| autumn_2025_10_13 | `exact_15m_source` | `corrected_15m_source` | 9 | 1.268 | 0.044 | 0 | 7 | 1120.32 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_no_source` | 10 | 0.303 | 0.039 | 0 | 7 | -2764.16 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_structural_source` | 20 | 8.651 | -22.096 | 0 | 8 | -65276.85 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_structural_no_source` | 20 | 9.017 | -21.539 | 0 | 8 | -61276.82 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_structural_lifecycle` | 22 | 5.932 | -24.511 | 0 | 5 | -87400.58 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_structural_progress` | 22 | 5.932 | -24.511 | 0 | 5 | -87400.58 |
| dev_2026_07_22 | `directional_5m_source` | `directional_5m_midline_lifecycle` | 22 | 5.408 | -24.590 | 0 | 5 | -89956.42 |
| dev_2026_07_22 | `exact_15m_source` | `corrected_15m_source` | 5 | 0.651 | 0.151 | 0 | 2 | -674.49 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_no_source` | 22 | 0.615 | 0.365 | 0 | 19 | 2324.06 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_structural_source` | 52 | 9.290 | -8.507 | 0 | 32 | -59582.55 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_structural_no_source` | 52 | 9.290 | -8.507 | 0 | 32 | -59582.55 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_structural_lifecycle` | 51 | 8.098 | -8.367 | 0 | 26 | -67098.62 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_structural_progress` | 51 | 8.098 | -8.367 | 0 | 26 | -67098.62 |
| spring_2025_03_10 | `directional_5m_source` | `directional_5m_midline_lifecycle` | 51 | 7.529 | -8.376 | 0 | 25 | -71823.08 |
| spring_2025_03_10 | `exact_15m_source` | `corrected_15m_source` | 7 | 0.542 | 0.707 | 0 | 7 | 2790.61 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_no_source` | 18 | 0.610 | 0.588 | 3 | 13 | -911.51 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_structural_source` | 28 | 10.665 | -35.938 | 0 | 13 | -39789.84 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_structural_no_source` | 25 | 10.072 | -34.062 | 1 | 12 | -38308.62 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_structural_lifecycle` | 30 | 6.752 | -42.248 | 0 | 9 | -84546.41 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_structural_progress` | 30 | 6.752 | -42.248 | 0 | 9 | -84546.41 |
| spring_2026_05_11 | `directional_5m_source` | `directional_5m_midline_lifecycle` | 30 | 5.832 | -42.615 | 0 | 9 | -91811.31 |
| spring_2026_05_11 | `exact_15m_source` | `corrected_15m_source` | 8 | 0.617 | 0.151 | 0 | 6 | -736.40 |
