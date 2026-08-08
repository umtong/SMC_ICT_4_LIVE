# Candidate 06 v8.1 LCOR Failed-Ownership Router

Terminal status: `W2_LCOR_FAILURE_ROUTER_GATE_FAILED`
Selected: none
Long evaluation authorized: `False`

|variant|week|eligible|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|lcor_failure_router_cross_venue_flow|2|True|0.000000%|0|0|0.00%|None|0.00%|FAILURE_ENTRY_ARMED_BUT_EXECUTION_ABSTAINED|
|lcor_failure_router_price_only_attribution|2|False|0.000000%|0|0|0.00%|None|0.00%|FAILURE_ENTRY_ARMED_BUT_EXECUTION_ABSTAINED|

## Fixed causal contract

- The initiating OI contraction is compared only with prior completed OI losses.
- Cash acceptance must be later than the liquidation-led event; perpetual acceptance must be later still.
- A reversal exists only after the accepted cash boundary and accepted perpetual boundary both fail on a completed bar.
- The eligible branch also requires adverse cash flow, matching perpetual flow, a directional body and opposite close location.
- The failure bar opens a new auction leg; its extreme plus the unchanged ATR buffer defines invalidation.
- Every target remains beyond the completed failure bar and must satisfy the unchanged structural and net RR contracts.
- The price-only branch is attribution evidence and cannot select.
- Orders, fills, fees, slippage, positions and whole-account NAV remain in NautilusTrader.
- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.
