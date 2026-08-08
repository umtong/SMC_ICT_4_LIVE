# Candidate 06 v8.3 LCOR Reaccept-Failure Half-Back

Terminal status: `W2_LCOR_HALF_BACK_EXECUTION_MECHANISM_REJECTED`
Selected: none
W2 mechanism expansion authorized: `False`
Long evaluation authorized: `False`

|variant|week|eligible|mechanism|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|lcor_reaccept_failure_half_back_limit|2|True|False|0.000000%|0|0|0.00%|None|0.00%|PASSIVE_LIMIT_UNFILLED_OR_UNCLOSED|
|lcor_reaccept_failure_market_attribution|2|False|False|0.000000%|0|0|0.00%|None|0.00%|PLACEMENT_REJECTED_BEFORE_NATIVE_ORDER|

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
