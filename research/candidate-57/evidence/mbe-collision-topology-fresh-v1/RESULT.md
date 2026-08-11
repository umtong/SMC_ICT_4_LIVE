# MBE2 collision topology fresh comparison

- parity pass: True
- mechanically valid: True
- decision: `MBE_COLLISION_TOPOLOGY_HYPOTHESIS_REJECTED_NO_RETUNING`
- strict project target: False
- causal support: False
- thresholds searched: False

Fresh interval: `2024-03-01` through `2024-03-31` UTC (31 days).

| mode | trades | W/L | win rate | PF | expectancy | geo/day | return | MDD | ROI exits | stop-like |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ge2_control | 24 | 11/13 | 0.4583333333333333 | 0.5302735332381697 | -160.2592886308333 | -0.00126440743330003 | -0.03846222927139997 | 0.08106615408084339 | 12 | 1 |
| exact2 | 19 | 8/11 | 0.42105263157894735 | 0.33836993028862994 | -280.1074595121052 | -0.0017626049813642686 | -0.05322041730729998 | 0.07729700436829123 | 10 | 1 |
| ge3plus | 8 | 5/3 | 0.625 | 1.3794394081926067 | 85.38427040125002 | 0.00021962146209397737 | 0.006830741632100068 | 0.036852957470843606 | 4 | 0 |

## Predeclared prediction results

`{"exact2_expectancy_pf_geo_improved_vs_control": false, "exact2_positive": false, "ge3plus_contrast_supported": false, "roi_engine_preserved_and_not_outlier_dominated": true}`

A lower trade count is not evidence by itself. Exact-two is supported only if its per-trade and account growth improve while the three-plus contrast behaves as the hypothesized market-wide state.
