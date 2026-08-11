# Candidate 60 forced-flow release result

A price reclaim is tested against target-contract OI accounting and a neutral-crossing taker-flow transition. No threshold, date, source jump, geometry or management value is searched after outcomes.

## Development — 2026-04-06 to 2026-04-19

| cell | trades | W/L | PF | geo/day | return | MDD | force checks | OI rejects | flow rejects | accepts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| price_confirmation_control | 6 | 3/3 | 1.441692076146582 | 0.001154132537309449 | 0.01627963088469997 | 0.06139080815263509 | 0 | 0 | 0 | 0 |
| oi_unwind | 1 | 0/1 | 0.0 | -0.0021257910090587417 | -0.029353322654999903 | 0.04023756572830273 | 10 | 9 | 0 | 1 |
| oi_unwind_flow_flip | 0 | 0/0 | 0.0 | 0.0 | 0.0 | 0.0 | 11 | 9 | 7 | 0 |

## Development eligibility

- `oi_unwind`: eligible=False; mechanics_valid=True, changed_actual_decisions=True, at_least_two_completed_trades=False, continuous_return_improved=False, drawdown_not_worse=True, causal_trade_effect=True, best_positive_control_trade_preserved=False
- `oi_unwind_flow_flip`: eligible=False; mechanics_valid=True, changed_actual_decisions=True, at_least_two_completed_trades=False, continuous_return_improved=False, drawdown_not_worse=True, causal_trade_effect=False, best_positive_control_trade_preserved=False

## Policy-fresh

Not consumed because no forced-flow cell earned causal development eligibility.
