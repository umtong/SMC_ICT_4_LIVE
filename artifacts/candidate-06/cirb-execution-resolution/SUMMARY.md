# Candidate 06 CIRB parent-frozen response-resolution ablation

Terminal status: `FIVE_SECOND_IMPLEMENTATION_OR_DATA_FAILURE`
Selected: `None`

|kind|week|gate|geom/day|trades|wins|win rate|PF|max DD|child candidates|rescued|RR-eroded|
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|cirb_full_1m_baseline|2|False|0.000000%|0|0|0.00%|None|0.00%||||
|cirb_full_5s_response_resolution|2|None|0.000000%|None|None|0.00%|None|0.00%||||
|cirb_discharge_only_5s_attribution|2|None|0.000000%|None|None|0.00%|None|0.00%||||

## Fixed interpretation

- The one-minute Nautilus run determines the parent-event population before 5-second data is scored.
- Five-second bars cannot create, remove, relabel, or reverse a parent crowding branch.
- The event bar cannot trade; only a later completed five-second response may arm an entry.
- Stop, objective family, fees, adverse ticks, 3% whole-NAV planned loss and one global slot remain unchanged.
- Discharge-only is attribution evidence and is not selectable in this experiment.
