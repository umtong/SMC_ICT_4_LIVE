# Candidate 60 quarter-hour synchronized order-flow diagnostic

This is a causal forward-path diagnostic, not a fill or NAV backtest. The target is the first-ten-second order-flow sign at true quarter-hour boundaries; minute-07 is the fixed phase-shift placebo.

## Development — 2026-07-20 to 2026-07-26

| phase | horizon min | observations | day-balanced signed bps | asset positive | selector bps | independent selector bps |
|---|---:|---:|---:|---:|---:|---:|
| quarter_hour | 60 | 2688 | 0.33988135891676796 | 2 | 0.43504421142667316 | nan |
| quarter_hour | 240 | 2688 | 0.06847715142288033 | 2 | 0.4774628101944585 | nan |
| quarter_hour | 480 | 2688 | -0.38810301864103536 | 2 | -0.8100051812950623 | nan |
| quarter_hour | 720 | 2688 | 1.9243441299783133 | 3 | 0.4422194223049519 | nan |
| shifted_placebo | 60 | 2688 | -1.7928601575727023 | 1 | -4.712339753154062 | nan |
| shifted_placebo | 240 | 2688 | -4.016130773437447 | 0 | -5.8899616070287975 | nan |
| shifted_placebo | 480 | 2688 | -3.38785983505505 | 0 | -3.4679406037711678 | nan |
| shifted_placebo | 720 | 2688 | -1.4798964785863027 | 1 | -3.702619261570232 | nan |

## Development decision

- eligible for predeclared fresh diagnostic: `False`
- data_complete=True, all_assets_observed=True, at_least_two_medium_horizons_positive=True, three_of_four_assets_positive_at_240m=False, selector_positive_at_240m=True, independent_selector_positive_at_240m=False, quarter_exceeds_placebo_at_240m=True, no_threshold_search=True

## Policy-fresh

Not consumed because the raw-sign mechanism did not satisfy the frozen development interpretation.
