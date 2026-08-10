# Causal state-family decision audit

This audit does not rank parameter combinations. It checks whether frozen causal claims survive chronology, assets, tail removal and one-slot episode arbitration.

## v56 recovered 4h squeeze release

- causal observations: 49
- status: **not_promoted_as_is**
- 60m post-publication mean is non-positive
- 60m sign flips from source history to post-publication
- 120m post-publication mean is non-positive
- 120m sign flips from source history to post-publication
- 240m post-publication mean is non-positive
- 240m sign flips from source history to post-publication
- 480m post-publication mean is non-positive
- 480m sign flips from source history to post-publication
- 720m post-publication mean is non-positive
- 720m sign flips from source history to post-publication
- 1440m post-publication mean is non-positive
- 1440m sign flips from source history to post-publication

| horizon | historical mean bp | post-publication mean bp | post PF |
|---:|---:|---:|---:|
| 60m | 95.35 | -31.92 | 0.09 |
| 120m | 30.71 | -23.59 | 0.31 |
| 240m | 41.33 | -6.06 | 0.61 |
| 480m | 84.34 | -3.03 | 0.77 |
| 720m | 125.86 | -20.84 | 0.45 |
| 1440m | 200.54 | -172.38 | 0.10 |

Pre-existing entry modes are retained in the JSON/CSV audit. A pooled historical win is not promoted when the same causal policy reverses sign after publication.

## v57 derivatives sponsorship and transition routes

- raw signal records: 377
- unique causal episodes: 224
- status: **route_exists_for_scenario_geometry**

| route | horizon | episodes | mean bp | post-publication bp | one-slot trades/day | status |
|---|---:|---:|---:|---:|---:|---|
| sponsored_build_direct_continuation | 120m | 58 | -41.78 | -6.78 | 0.300 | causal_prediction_contradicted |
| sponsored_build_direct_continuation | 240m | 58 | -54.71 | -11.69 | 0.293 | causal_prediction_contradicted |
| sponsored_build_direct_continuation | 480m | 58 | -61.21 | -47.91 | 0.279 | causal_prediction_contradicted |
| sponsored_build_direct_continuation | 720m | 58 | -88.56 | -57.70 | 0.257 | causal_prediction_contradicted |
| sponsored_build_delayed_continuation | 120m | 58 | -38.93 | -25.15 | 0.300 | causal_prediction_contradicted |
| sponsored_build_delayed_continuation | 240m | 58 | -36.26 | -30.85 | 0.293 | causal_prediction_contradicted |
| sponsored_build_delayed_continuation | 480m | 58 | -52.21 | -53.31 | 0.279 | causal_prediction_contradicted |
| sponsored_build_delayed_continuation | 720m | 58 | -79.29 | -67.14 | 0.257 | causal_prediction_contradicted |
| forced_unwind_accepted_direct_continuation | 120m | 84 | 37.75 | -0.10 | 0.300 | unstable_or_tail_fragile |
| forced_unwind_accepted_direct_continuation | 240m | 84 | 57.04 | 21.20 | 0.286 | provisional_mechanism_signal |
| forced_unwind_accepted_direct_continuation | 480m | 84 | 121.77 | 13.18 | 0.279 | provisional_mechanism_signal |
| forced_unwind_accepted_direct_continuation | 720m | 84 | 131.21 | -34.09 | 0.264 | unstable_or_tail_fragile |
| forced_unwind_accepted_delayed_continuation | 120m | 84 | 2.51 | 2.71 | 0.300 | unstable_or_tail_fragile |
| forced_unwind_accepted_delayed_continuation | 240m | 84 | 26.06 | 14.49 | 0.286 | provisional_mechanism_signal |
| forced_unwind_accepted_delayed_continuation | 480m | 84 | 77.85 | 8.42 | 0.279 | provisional_mechanism_signal |
| forced_unwind_accepted_delayed_continuation | 720m | 84 | 96.12 | -35.19 | 0.264 | unstable_or_tail_fragile |
| forced_unwind_rejected_delayed_reversal | 120m | 17 | 19.18 | -39.95 | 0.107 | unstable_or_tail_fragile |
| forced_unwind_rejected_delayed_reversal | 240m | 17 | 6.97 | -41.06 | 0.100 | unstable_or_tail_fragile |
| forced_unwind_rejected_delayed_reversal | 480m | 17 | -111.68 | -99.17 | 0.100 | causal_prediction_contradicted |
| forced_unwind_rejected_delayed_reversal | 720m | 17 | -136.44 | -97.34 | 0.100 | causal_prediction_contradicted |
| persistent_15_delayed_continuation | 120m | 104 | 23.62 | -5.17 | 0.386 | unstable_or_tail_fragile |
| persistent_15_delayed_continuation | 240m | 104 | 31.43 | -5.12 | 0.364 | unstable_or_tail_fragile |
| persistent_15_delayed_continuation | 480m | 104 | 77.12 | -12.65 | 0.321 | unstable_or_tail_fragile |
| persistent_15_delayed_continuation | 720m | 104 | 135.08 | -31.31 | 0.314 | unstable_or_tail_fragile |
| rejected_15_delayed_reversal | 120m | 17 | 80.50 | -12.98 | 0.114 | unstable_or_tail_fragile |
| rejected_15_delayed_reversal | 240m | 17 | 105.64 | -19.64 | 0.114 | unstable_or_tail_fragile |
| rejected_15_delayed_reversal | 480m | 17 | 108.46 | -141.58 | 0.107 | unstable_or_tail_fragile |
| rejected_15_delayed_reversal | 720m | 17 | 103.53 | -185.48 | 0.100 | unstable_or_tail_fragile |

## Decision contract

A negative aggregate does not erase useful components, but a causal prediction is not accepted merely because another unintended subgroup made money. A candidate route must preserve its predicted direction across chronological partitions, remain after the best episode is removed, and then survive executable scenario geometry and continuous-account arbitration.
