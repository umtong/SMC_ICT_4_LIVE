# Candidate 11 second-scale cross-asset gap diagnostic

**CROSS_ASSET_GAP_MECHANISM_REJECT**

- diagnostic_gate_passed: `False`
- total_events: `0`
- target / stop / timeout: `0 / 0 / 0`
- pooled_target_first_rate: `0.000000`
- pooled_mean_realized_r_diagnostic: `None`
- positive_mean_weeks: `0`

## Precommitted checks
- minimum_total_events: `False`
- minimum_events_per_week: `False`
- minimum_pooled_target_first_rate: `False`
- minimum_pooled_mean_realized_r_diagnostic: `False`
- minimum_positive_mean_weeks: `False`
- require_all_three_data_manifests: `True`

## Weekly mechanism evidence
- G1: events=0, target_rate=0.000000, mean_R=None, followers={}
- G2: events=0, target_rate=0.000000, mean_R=None, followers={}
- G3: events=0, target_rate=0.000000, mean_R=None, followers={}

## Decision
Reject the second-scale cross-asset gap mechanism. Do not tune G1-G3 thresholds or horizons.
