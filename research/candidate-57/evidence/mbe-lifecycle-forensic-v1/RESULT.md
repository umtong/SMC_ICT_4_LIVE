# MBE2 lifecycle forensic v1

- parity pass: True
- mechanically valid: True
- decision: `MBE_LIFECYCLE_RECROSS_HYPOTHESIS_REJECTED_NO_RETUNING`
- thresholds searched: False
- supported source horizons: []

The direct invalidation is fixed: estimated after-cost R is non-positive, the entry symbol has re-crossed to RSI ≥ 70, and TEMA slope is positive.

| period | horizon | ROI winners preserved | all negative trades captured | observable losses |
|---|---:|---:|---:|---:|
| march_2024 | 15 | 0.8 | 0.3076923076923077 | 7 |
| march_2024 | 41 | 1.0 | 0.07692307692307693 | 6 |
| march_2024 | 114 | 1.0 | 0.0 | 6 |
| march_2024 | 180 | 1.0 | 0.0 | 6 |
| march_2024 | 420 | 1.0 | 0.0 | 4 |
| april_2026 | 15 | 0.92 | 0.0 | 7 |
| april_2026 | 41 | 0.92 | 0.125 | 7 |
| april_2026 | 114 | 1.0 | 0.125 | 7 |
| april_2026 | 180 | 1.0 | 0.0 | 7 |
| april_2026 | 420 | 1.0 | 0.0 | 4 |

A fresh policy is authorized only when the same source-defined horizon captures at least half of all negative trades in both months while preserving at least 80% of ROI winners. The severe-stop subset is diagnostic only.
