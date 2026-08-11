# TrendRider pullback-long v1 end-flat implementation rerun

The entry, state, stop, ROI, trailing and lifecycle policy are unchanged.  Only two days of data runoff are supplied while new entries are frozen at the original stage boundary.

- mechanically valid: True
- decision: `MECHANISM_PROMISING_POLICY_FRESH_REQUIRED`
- thresholds searched: False
- policy-fresh authorized: True
- integration authorized: False
- long evaluation authorized: False

| stage | trades | W/L | PF | expectancy USDT | entry-window geo/day | return | MDD | signals | end open |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bull_expansion_development | 14 | 6/8 | 3.7613909948843793 | 539.5802185135715 | 0.005215267063152895 | 0.07554123059189988 | 0.03345661368777586 | 17 | 0 |
| contrast_development | 1 | 0/1 | 0.0 | -700.00431081 | -0.0005016354494844499 | -0.007000043108100096 | 0.007408258027962855 | 1 | 0 |

A mechanically valid positive development result does not authorize integration or long evaluation.  It authorizes at most one predeclared policy-fresh interval.
