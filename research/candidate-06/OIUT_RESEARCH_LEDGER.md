# OIUT Research Ledger

## Hypothesis

OIDB and OIIR show that completed OI contraction contains genuine information,
whereas fresh OI expansion continuation was negative. OIUT therefore removes
BUILD contexts entirely so they cannot occupy the single scenario slot and
block independent unwind events.

```text
prior-only OI-drop distribution
→ completed extreme OI contraction + aligned price/taker flow
→ no event-bar entry
→ next completed OI keeps contracting + price discovery
   OR completed OI re-expands + opposite reclaim
→ structural stop/objective
→ NautilusTrader orders, fills, fees, positions and NAV
```

## Fixed matrix

1. Full unwind transfer — eligible.
2. Unwind continuation only — branch attribution.
3. Reversal without counter-inventory rebuild — core-variable ablation.

All thresholds, costs, stop/target rules, signal timing, 3% risk, BTC weeks and
Nautilus execution contracts remain identical to OIIR. Only BUILD event creation
is removed. The first week must pass before the two sealed weeks open. Long
evaluation is forbidden unless all three pass unchanged.
