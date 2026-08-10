# Public ichiV2_1 system anatomy

This is not a promotion gate. It separates opportunity, winner, loss, risk, direction and execution mechanisms.

| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | ROI exits | EMA exits | progress/lifecycle exits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| autumn_2024_10 | `source_exit` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| autumn_2024_10 | `roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| autumn_2024_10 | `roi_progress` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| autumn_2024_10 | `structural_roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| winter_2025_01 | `source_exit` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| winter_2025_01 | `roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| winter_2025_01 | `roi_progress` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| winter_2025_01 | `structural_roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| spring_2025_05 | `source_exit` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| spring_2025_05 | `roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| spring_2025_05 | `roi_progress` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| spring_2025_05 | `structural_roi_only` | 0 | 0 | 0.00 | 0.00 | 0.000 | 0.00 | 0 | 0 | 0 | 0 |
| winter_2026_02 | `source_exit` | 1 | 0 | 0.00 | 2975.00 | 0.000 | -2975.00 | 0 | 0 | 0 | 0 |
| winter_2026_02 | `roi_only` | 1 | 0 | 0.00 | 2975.00 | 0.000 | -2975.00 | 0 | 0 | 0 | 0 |
| winter_2026_02 | `roi_progress` | 1 | 0 | 0.00 | 225.99 | 0.000 | -225.99 | 0 | 0 | 0 | 1 |
| winter_2026_02 | `structural_roi_only` | 1 | 0 | 0.00 | 2975.00 | 0.000 | -2975.00 | 0 | 0 | 0 | 0 |

## Paired mechanism trade-offs

| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| autumn_2024_10 | `roi_only` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| autumn_2024_10 | `roi_only` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| autumn_2024_10 | `source_exit` | `roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| autumn_2024_10 | `source_exit` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| autumn_2024_10 | `source_exit` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| spring_2025_05 | `roi_only` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| spring_2025_05 | `roi_only` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| spring_2025_05 | `source_exit` | `roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| spring_2025_05 | `source_exit` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| spring_2025_05 | `source_exit` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2025_01 | `roi_only` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2025_01 | `roi_only` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2025_01 | `source_exit` | `roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2025_01 | `source_exit` | `roi_progress` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2025_01 | `source_exit` | `structural_roi_only` | 0 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2026_02 | `roi_only` | `roi_progress` | 1 | 0 | 0 | 0.000 | 0.924 | 0.00 | 2749.02 |
| winter_2026_02 | `roi_only` | `structural_roi_only` | 1 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2026_02 | `source_exit` | `roi_only` | 1 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
| winter_2026_02 | `source_exit` | `roi_progress` | 1 | 0 | 0 | 0.000 | 0.924 | 0.00 | 2749.02 |
| winter_2026_02 | `source_exit` | `structural_roi_only` | 1 | 0 | 0 | 0.000 | 0.000 | 0.00 | 0.00 |
