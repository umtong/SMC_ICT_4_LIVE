# Candidate 06 v7.0 Causal Inventory Ownership Transfer

Terminal status: `W2_CAUSAL_OWNERSHIP_LOGIC_GATE_FAILED`
Selected: none
Long evaluation authorized: `False`

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|diagnosis|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|ciot_full_ownership_transfer|2|True|False|0.000000%|0|0|0.00%|None|0.00%|OWNERSHIP_EPISODES_DID_NOT_COMPLETE|
|ciot_forced_removal_reversal_only|2|True|False|0.000000%|0|0|0.00%|None|0.00%|OWNERSHIP_EPISODES_DID_NOT_COMPLETE|
|ciot_spot_owned_continuation_only|2|True|False|0.000000%|0|0|0.00%|None|0.00%|OWNERSHIP_EPISODES_DID_NOT_COMPLETE|
|ciot_without_spot_ownership_ablation|2|False|False|0.000000%|0|0|0.00%|None|0.00%|OWNERSHIP_EPISODES_DID_NOT_COMPLETE|
|ciot_without_inventory_confirmation_ablation|2|False|False|0.000000%|0|0|0.00%|None|0.00%|OWNERSHIP_EPISODES_DID_NOT_COMPLETE|

## Fixed causal contract

- Current OI change is compared only with prior completed OI changes.
- Spot and perpetual bars must share the exact completed timestamp.
- The initiating external-liquidity/OI event cannot trade.
- Old-auction ownership, later inventory confirmation, a distinct pullback, and a distinct resumption are mandatory.
- The signal leg owns both its structural stop and a still-live objective.
- Attribution ablations are not selectable.
- Orders, fills, fees, slippage, positions and whole-account NAV remain in NautilusTrader.
- Planned loss remains three percent of current whole-account NAV and one global slot remains unchanged.
