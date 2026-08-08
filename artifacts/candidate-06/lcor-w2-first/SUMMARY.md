# Candidate 06 v8.0 Liquidation-to-Cash Ownership Relay

Terminal status: `W2_LIQUIDATION_CASH_OWNERSHIP_GATE_FAILED`
Selected: none
Long evaluation authorized: `False`

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|lcor_full_cash_ownership_relay|2|True|False|0.000000%|0|0|0.00%|None|0.00%|NO_RENEWED_CASH_OWNED_INITIATIVE|
|lcor_without_material_removal_retention_ablation|2|False|False|0.000000%|0|0|0.00%|None|0.00%|NO_RENEWED_CASH_OWNED_INITIATIVE|

## Fixed causal contract

- The current OI loss is compared only with prior completed OI losses.
- The initiating perpetual sweep must occur before any accepted spot discovery.
- Spot acceptance must be later than the initiating event; perpetual acceptance must be later still.
- Material forced-OI removal, cash ownership, a distinct pullback, a distinct resumption, the stop and a still-live objective belong to one episode.
- The retained-removal ablation is attribution-only and cannot select.
- Orders, fills, fees, slippage, positions and whole-account NAV remain in NautilusTrader.
- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.
