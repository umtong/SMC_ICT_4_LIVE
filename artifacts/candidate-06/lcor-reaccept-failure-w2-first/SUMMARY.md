# Candidate 06 v8.2 LCOR Reaccept-Failure Router

Terminal status: `W2_LCOR_REACCEPT_FAILURE_MECHANISM_REJECTED`
Selected: none
W2 mechanism expansion authorized: `False`
Long evaluation authorized: `False`

|variant|week|eligible|mechanism|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|lcor_reaccept_failure_cross_venue_flow|2|True|False|0.000000%|0|0|0.00%|None|0.00%|SECOND_FAILURE_ENTRY_EXECUTION_ABSTAINED|
|lcor_reaccept_failure_price_only_attribution|2|False|False|0.000000%|0|0|0.00%|None|0.00%|SECOND_FAILURE_ENTRY_EXECUTION_ABSTAINED|

## Fixed causal contract

- The first accepted cross-venue ownership failure is context only and cannot trade.
- The original direction must later reaccept both cash and perpetual boundaries with matching completed-bar flow.
- Only a strictly later second cross-venue failure may open the reversal leg.
- The recovery-test extreme plus the unchanged ATR buffer defines invalidation.
- The same live opposite objective family, structural RR, net delayed RR, fees and slippage remain unchanged.
- W2 uses a mechanism gate only to authorize untouched W1 and W3 execution; it is not a success claim.
- The frozen three-week aggregate retains the existing >=1% geometric daily NAV, trade count, win-rate, drawdown and concentration gate.
- The price-only branch is attribution evidence and cannot select.
- Orders, fills, positions, commissions and whole-account NAV remain in NautilusTrader.
- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.
