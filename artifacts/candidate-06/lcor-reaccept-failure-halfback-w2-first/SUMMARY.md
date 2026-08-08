# Candidate 06 v8.3 LCOR Reaccept-Failure Half-Back

Terminal status: `FROZEN_THREE_WEEK_LCOR_HALF_BACK_TARGET_NOT_REPLICATED`
Selected: `lcor_reaccept_failure_half_back_limit`
W2 mechanism expansion authorized: `True`
Long evaluation authorized: `False`

|variant|week|eligible|mechanism|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|lcor_reaccept_failure_half_back_limit|2|True|True|0.289414%|1|1|100.00%|None|0.28%|W2_HALF_BACK_EXECUTION_MECHANISM_PASSED|
|lcor_reaccept_failure_market_attribution|2|False|False|0.000000%|0|0|0.00%|None|0.00%|PLACEMENT_REJECTED_BEFORE_NATIVE_ORDER|
|lcor_reaccept_failure_half_back_limit|1|True|False|0.000000%|0|0|0.00%|None|0.00%|NO_REACCEPT_FAILURE_SIGNAL|
|lcor_reaccept_failure_half_back_limit|3|True|False|0.000000%|0|0|0.00%|None|0.00%|NO_REACCEPT_FAILURE_SIGNAL|

## Frozen three-week aggregate

- Evaluation days: `21.0`
- Trades: `1`
- Wins: `1`
- Win rate: `100.00%`
- Pooled geometric NAV growth/day: `0.096378%`
- Positive weeks: `1/3`
- Worst weekly max drawdown: `0.28%`

## Fixed causal and execution contract

- The LCOR v8.2 context, direction, first failure, reacceptance and second failure are unchanged.
- The second-failure close remains the signal timestamp; the event bar cannot fill the later limit retroactively.
- Entry is the exact 50% equilibrium between that completed close and the pre-existing failed ownership boundary.
- The limit is post-only and expires at the end of the same 15-minute LCOR auction.
- Recovery-test invalidation, live objective, structural RR, minimum 0.60 post-cost delayed RR, fees and one-tick slippage are unchanged.
- The market-at-second-failure branch is attribution only and cannot select.
- W2 may only unlock untouched W1/W3. Final success still requires the existing frozen three-week >=1% geometric daily NAV and robustness gate.
- Orders, fills, positions, commissions and whole-account NAV remain in NautilusTrader.
- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.
