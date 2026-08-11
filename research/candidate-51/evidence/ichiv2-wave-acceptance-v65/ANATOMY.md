# ichiV2 fan-wave acceptance v65

- fresh sampled days: 303
- conclusion: **wave_acceptance_not_supported**

| policy | trades | trades/day | mean net | median net | PF | ex-best net | hard stops | <=10m stops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| continuous_boolean_run | 32 | 0.106 | 0.416% | 1.810% | 1.28 | 0.274% | 9 | 4 |
| fan_wave | 11 | 0.036 | -0.185% | 0.810% | 0.87 | -0.590% | 3 | 1 |
| fan_wave_one_bar_acceptance | 5 | 0.017 | -0.221% | 1.654% | 0.89 | -1.479% | 2 | 0 |

## Predeclared assessment

- wave_clock_reduces_literal_signal_fragmentation: `True`
- acceptance_reduces_immediate_stop_rate: `True`
- acceptance_improves_ex_best_expectancy: `False`
- acceptance_retains_nonzero_fresh_opportunities: `True`

## Next inference

If supported, preserve the fan-wave opportunity clock and acceptance transition as a candidate component, then diagnose its remaining stop/ROI geometry on a new period. If unsupported, retain only v63 favorable-excursion evidence and stop modifying this public family.

## Truth boundary

Fresh-data path anatomy is not a continuous NautilusTrader account and does not meet the final frequency or growth target.
