# Candidate 60 fresh-position build and release — untouched result

The candidate requires source-leg net contract creation, reversal-side aggressive flow at confirmation, and the unchanged two-bar price reclaim. No threshold, interval, source detector, geometry or management value is changed after observing this interval.

| cell | trades | W/L | PF | geo/day | return | MDD | state checks | OI rejects | flow rejects | accepts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| price_confirmation_control | 5 | 2/3 | 0.16268792266947285 | -0.004957950102055686 | -0.06721816870550001 | 0.12544236847523815 | 0 | 0 | 0 | 0 |
| position_build_reversal_flow | 1 | 0/1 | 0.0 | -0.0020877427022834016 | -0.028835052281499962 | 0.031368852523101065 | 7 | 4 | 2 | 1 |

## Frozen interpretation

- component evidence: `False`
- mechanics_valid=True, changed_actual_decisions=True, at_least_two_completed_trades=False, continuous_return_improved=True, drawdown_not_worse=True, causal_trade_effect=False, best_positive_control_trade_preserved=False, not_single_winner_dependent=False
- removed control trades: 4; removed negative/positive: 2/2; removed sum R: -1.3167785992874297
- shared trades: 1; shared sum delta R: -0.0001388511214739374
