# Core FAR structural risk-transfer ablation

This directory is an explicit `TEMPORARY_TEST`. It answers one mechanism
question on already-opened D1-D3 data and cannot advance a candidate, authorize
fresh validation or claim alpha.

## Frozen comparison

The exact nine baseline FAR scenario IDs, entries, initial structural stops,
targets, costs, current-NAV 3% sizing and global one-slot contract are retained.
Only post-entry risk ownership changes:

```text
POSITION_OPEN
  -> first post-fill favorable five-minute pivot is right-confirmed
  -> pivot remains beyond entry and the reclaimed pool
  -> existing STOP_MARKET trigger is modified behind pivot + frozen ATR buffer
```

There is no MFE threshold, fixed breakeven percentage, new target, peer-based
entry filter or fitted buffer. NautilusTrader owns the stop modification and all
subsequent fills, fees, positions and NAV.

The comparison is paired by scenario ID and fails closed if the scenario set,
direction, account reconciliation, execution safety or resolution-tail contract
changes. Even the strongest possible result here would only retain the mechanism
for a new development candidate with new opened blocks; it would not validate the
current system.
