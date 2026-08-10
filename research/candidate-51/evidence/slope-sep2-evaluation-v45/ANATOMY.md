# Frozen Slope sep2 evaluation — corrected audit

The workflow's first aggregate path was empty because downloaded artifacts were flattened. This audit was rebuilt from all seven interval artifacts. Reported NAV includes immediate flatten costs from actual-fill invalidations; valid intended-trade anatomy excludes those invalid fills.

| interval | NAV | return | geo/day | MDD | valid trades | invalid fills | valid wins | valid losses | trailing net | ROI net | source-exit net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| eval_2024_03 | 98,734.60 | -1.265% | -0.091% | 6.304% | 16 | 1 | 11 | 5 | 4,302.10 | 0.00 | -5,480.54 |
| eval_2024_06 | 99,102.23 | -0.898% | -0.064% | 5.059% | 14 | 0 | 8 | 6 | 4,291.09 | -75.50 | -4,593.47 |
| eval_2024_10 | 103,750.15 | 3.750% | 0.263% | 1.624% | 16 | 0 | 15 | 1 | 4,226.26 | 394.10 | -870.21 |
| eval_2025_05 | 102,876.06 | 2.876% | 0.203% | 2.812% | 26 | 0 | 23 | 3 | 6,255.00 | -18.34 | -3,360.61 |
| eval_2025_11 | 99,365.62 | -0.634% | -0.045% | 6.960% | 22 | 2 | 16 | 6 | 7,007.63 | 0.00 | -7,481.75 |
| eval_2026_03 | 96,710.40 | -3.290% | -0.239% | 3.944% | 7 | 1 | 4 | 3 | 804.58 | 863.30 | -4,711.59 |
| eval_2026_07 | 100,103.93 | 0.104% | 0.007% | 2.751% | 8 | 0 | 6 | 2 | 2,250.63 | 287.17 | -2,433.86 |

## Aggregate diagnosis

- Positive intervals: 3/7; negative intervals: 4/7.
- Median interval return: -0.634%; mean interval return: 0.092%.
- Median geometric daily growth: -0.045%; mean: 0.005%.
- Valid intended trades: 109 over 98 calendar days; account closed records: 114; fill invalidations: 4.
- One global position was respected in every interval; no global-position violation occurred.

## Exit-engine anatomy

- **PUBLIC_TRAILING_EXIT**: 81 trades, 80 wins, GP 29,218.09, GL 80.80, net 29,137.29, PF 361.600.
- **PUBLIC_SOURCE_EXIT_SIGNAL**: 23 trades, 0 wins, GP 0.00, GL 28,932.03, net -28,932.03, PF 0.000.
- **PUBLIC_ROI_EXIT**: 5 trades, 3 wins, GP 1,544.57, GL 93.84, net 1,450.74, PF 16.460.

All 23 valid `PUBLIC_SOURCE_EXIT_SIGNAL` trades lost money. Their combined loss was 28,932.03 USDT. Every one was triggered by `range_failure=1` with `ma_failure=0`; no MA-cross source exit occurred. By contrast, the public trailing engine produced 81 valid trades, 80 winners and net 29,137.29 USDT.

## Decision

The frozen sep2 system does not generalize as a complete system: four of seven new intervals were negative and the median daily growth was negative. It is not discarded, because the failure is highly concentrated in one repeatable mechanism. The next development experiment deletes only the rolling-range exit, compares MA-cross-only versus no source exit, and leaves the entry, 2x source-relative geometry, structural risk, trailing and ROI engines unchanged.
