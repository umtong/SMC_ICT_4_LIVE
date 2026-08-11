# TrendRider pullback-long v1 causal diagnostic

- mechanically valid: False
- decision: `IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION`
- thresholds searched: False
- policy-fresh authorized: False
- integration authorized: False
- long evaluation authorized: False

| stage | expected state | trades | W/L | PF | expectancy USDT | geo/day | return | MDD | signals |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bull_expansion_development | INTENDED_BULL_CONTINUATION | 14 | 6/8 | 3.7613909948843793 | 539.5802185135715 | 0.005215267063152895 | 0.07554123059189988 | 0.03345661368777586 | 17 |
| contrast_development | CONTRAST_STATE | 0 | 0/0 | 0.0 | 0.0 | -0.0002542411843389214 | -0.0035535004477000864 | 0.0069676410764183405 | 1 |

The two intervals are development diagnostics.  The decision is based on whether the frozen branch behaves as a repeated bull-continuation mechanism and naturally reduces exposure in the contrast regime.  A failure closes this exact branch without an indicator or lifecycle parameter search.
