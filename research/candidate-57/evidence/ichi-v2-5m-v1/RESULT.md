# Public ichiV2 five-minute tournament

Every row is the project's four-symbol, one-slot, cost-after account. Case JSON files contain every completed trade and its entry-state diagnostics.

## development

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| report_long_level | 17 | 5/12 | 0.39500766455585173 | -0.0035817655221964673 | -0.048993836737100116 | 0.06699987946224861 | -288.19903963 | 25 |
| report_long_edge | 17 | 5/12 | 0.39500766455585173 | -0.0035817655221964673 | -0.048993836737100116 | 0.06699987946224861 | -288.19903963 | 25 |
| report_short_level | 13 | 4/9 | 1.0586031115000085 | 0.00012752634131807383 | 0.001786849463700113 | 0.026018404874499135 | 13.744995874615379 | 22 |
| report_both_level | 30 | 9/21 | 0.5563818077928885 | -0.0036837331158990905 | -0.050355416265300046 | 0.08037638389895374 | -167.851387551 | 47 |
| source_v2_long_level | 15 | 4/11 | 0.3905320326761171 | -0.0012490361237781356 | -0.017345244294100004 | 0.027652721954031123 | -115.63496196066667 | 20 |
| source_v2_5_long_level | 38 | 8/30 | 0.3377910135439622 | -0.004187894942405568 | -0.05706095914789999 | 0.07004229555178199 | -150.16041881026317 | 69 |

## untouched

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| report_short_level | 7 | 4/3 | 1.9681970317230102 | 0.0017743854887162236 | 0.012487011618800059 | 0.01671438671163794 | 178.3858802685714 | 9 |
| source_v2_5_long_level | 16 | 4/12 | 1.249652904351903 | 0.0013460801994471527 | 0.00946069744630007 | 0.024609047587161093 | 59.12935903937499 | 23 |

## continuous_30d

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| report_short_level | 32 | 15/17 | 1.4384645193414491 | 0.001626941656724945 | 0.04997734592389991 | 0.04016310575982118 | 156.1792060121875 | 53 |

## Allocation

- development survivors: ['report_short_level', 'source_v2_5_long_level']
- positive untouched survivors: ['report_short_level', 'source_v2_5_long_level']
- continuous winner: report_short_level
- strict project pass: False

Development allocation preserves both the best quality candidate and a different high-opportunity candidate when present; it is not a binary truth gate.
