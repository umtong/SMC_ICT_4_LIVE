# TrendRider exact public MTF v2 source-fidelity diagnostic

- mechanically valid: True
- decision: `EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING`
- thresholds searched: False
- policy-fresh authorized: False
- integration authorized: False
- long evaluation authorized: False

| stage | case | trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| november_winner_development | fallback_control | 21 | 9/12 | 3.082107303938166 | 474.23471445571425 | 0.0068042398941499105 | 0.09958929003570005 | 0.03345674072933891 |
| november_winner_development | exact_public_mtf | 19 | 8/11 | 3.229479621725664 | 454.5879470589474 | 0.005934930705454811 | 0.08637170994120003 | 0.03345745590708549 |
| june_failure_development | fallback_control | 18 | 8/10 | 0.706450798080196 | -47.76298699888889 | -0.0003083277416694452 | -0.008597337659799975 | 0.01990891244066284 |
| june_failure_development | exact_public_mtf | 16 | 6/10 | 0.6689834026737363 | -63.30779755187499 | -0.0003635373869791403 | -0.01012924760830003 | 0.020118965498078678 |

## Predeclared causal predictions

`{"best_june_winner_preserved": true, "exact_context_changed_state": true, "june_4h_confidence_rejections": 2, "june_daily_rejections": 5, "june_improved": false, "june_positive": false, "june_unresolved_total": 2335, "november_positive": true, "november_winner_engine_preserved": false, "selective_loss_rejection": false}`

These intervals are consumed diagnostics.  Only the exact policy frozen before this result can move to the predeclared October policy-fresh interval, and only when the transaction-level predictions—not merely aggregate return—are satisfied.
