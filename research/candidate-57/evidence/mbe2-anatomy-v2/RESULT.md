# Candidate 57 — public MBE2 strategy anatomy

This campaign is an evidence map, not a binary gate. Every source leg and predeclared management/risk-geometry ablation was replayed on both seven-day intervals under the same continuous four-symbol, one-position NautilusTrader account.

| variant | trades | win rate | expectancy R | PF | robust daily growth | worst DD | avg cost bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| both_avg646_source | 56 | 66.071% | -0.0493 | 0.4769 | -1.017% | 9.121% | 14.9818 |
| long_avg646_source | 24 | 54.167% | -0.1281 | 0.2505 | -0.983% | 7.746% | 14.9761 |
| short_avg646_source | 39 | 74.359% | -0.0157 | 0.7187 | -0.253% | 5.052% | 14.9932 |
| both_cap10_source | 72 | 59.722% | -0.0301 | 0.5565 | -0.809% | 8.427% | 14.9978 |
| both_avg646_roi_only | 44 | 75.000% | 0.0062 | 1.0498 | -0.172% | 5.655% | 14.9748 |
| both_avg646_trail_only | 47 | 68.085% | -0.0763 | 0.4222 | -1.053% | 8.233% | 14.9821 |
| both_avg646_roi114_011 | 59 | 71.186% | -0.0294 | 0.5926 | -0.596% | 7.451% | 14.9868 |

## Role-preserving interpretation

- Pareto frontier: `both_avg646_roi114_011, both_avg646_roi_only, both_avg646_source, both_avg646_trail_only, both_cap10_source, short_avg646_source`
- Quality anchor: `both_avg646_roi_only`
- Low-frequency quality anchor: `both_avg646_roi_only`
- Growth/robustness anchor: `both_avg646_roi_only`
- Frequency reference (not automatic endorsement): `both_cap10_source`
- Role-balanced next-stage shortlist: `both_avg646_roi_only, both_avg646_roi114_011, both_avg646_source, both_avg646_trail_only`

Detailed symbol, direction, exit, hold-time, session, router-score, RSI-cross, TEMA-gap, trend, momentum, volatility, collision, MFE/MAE, cost and risk-budget slices are stored in each case JSON. Component deltas are measured against the source-faithful both-side 6.46x control.
